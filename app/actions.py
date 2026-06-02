"""Side-effecting operations: sync, rescue, evict, harden, VPN.

This is the only module that both reads the domain logic (curator, eviction,
hardening, vpn) and drives qBittorrent. The scheduler and the web routes call
these; nothing here loops on its own.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from typing import Optional

from app import ark, config, curator, decorrelate, eviction, hardening, notify, resurrect, vpn
from app.db import DB_PATH, get_db
from app.endangerment import endangerment_score, evaluate_graduation, is_rescuable
from app.models import Candidate, TrackedTorrent
from app.qbittorrent import QBitError, get_client
from app.scrape import scrape_infohash


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #
def _json_col(r, key):
    try:
        return json.loads(r[key] or "[]")
    except (KeyError, IndexError, TypeError, ValueError):
        return []


def _candidate_from_row(r) -> Candidate:
    return Candidate(
        infohash=r["infohash"], name=r["name"], source=r["source"],
        magnet=r["magnet"], torrent_url=r["torrent_url"],
        size_bytes=r["size_bytes"] or 0, seeders=r["seeders"] or 0,
        leechers=r["leechers"] or 0, completed=r["completed"] or 0,
        category=r["category"] or "", legal=bool(r["legal"]),
        trackers=_json_col(r, "trackers"), webseeds=_json_col(r, "webseeds"),
        endangerment=r["endangerment"] or 0.0, redundancy=r["redundancy"] or 0,
        rescuable=bool(r["rescuable"]))


def _tracked_from_row(r) -> TrackedTorrent:
    return TrackedTorrent(
        infohash=r["infohash"], name=r["name"], source=r["source"] or "",
        size_bytes=r["size_bytes"] or 0, category=r["category"] or "",
        legal=bool(r["legal"]), mode=r["mode"] or "rescue",
        qbit_state=r["qbit_state"] or "", seeders=r["seeders"] or 0,
        leechers=r["leechers"] or 0, ratio=r["ratio"] or 0.0,
        uploaded_bytes=r["uploaded_bytes"] or 0, progress=r["progress"] or 0.0,
        endangerment=r["endangerment"] or 0.0, added_at=r["added_at"] or 0.0,
        last_checked=r["last_checked"] or 0.0, evicted_at=r["evicted_at"],
        evict_reason=r["evict_reason"] or "")


def live_tracked() -> list:
    with get_db() as db:
        rows = db.execute("SELECT * FROM tracked WHERE evicted_at IS NULL").fetchall()
    return [_tracked_from_row(r) for r in rows]


def tracked_infohashes() -> set:
    with get_db() as db:
        return {r["infohash"] for r in
                db.execute("SELECT infohash FROM tracked WHERE evicted_at IS NULL").fetchall()}


# --------------------------------------------------------------------------- #
# VPN
# --------------------------------------------------------------------------- #
async def current_vpn_status(persist: bool = True):
    client = None
    try:
        client = get_client()
        if not await client.available():
            client = None
    except Exception:
        client = None
    status = await vpn.check(
        client, configured_interface=config.get_str("vpn_interface"),
        baseline_ip=config.get_str("vpn_baseline_ip"))
    if persist:
        with get_db() as db:
            db.execute(
                "INSERT INTO vpn_checks(ts,connected,interface,public_ip,"
                "killswitch,leaking,detail) VALUES (?,?,?,?,?,?,?)",
                (status.checked_at, int(status.connected), status.interface,
                 status.public_ip, int(status.killswitch_engaged),
                 int(status.leaking), status.detail))
    return status


async def ensure_killswitch() -> bool:
    """If a VPN interface is present and the kill-switch is on, bind qBittorrent
    to it. Returns True when the kill-switch is engaged afterwards."""
    if not config.get_bool("vpn_killswitch"):
        return False
    interface, _ip = await __import__("asyncio").to_thread(
        vpn.pick_interface, config.get_str("vpn_interface"))
    if not interface:
        return False
    try:
        client = get_client()
        if not await client.available():
            return False
        prefs = await client.get_preferences()
        if prefs.get("current_network_interface") != interface:
            await vpn.engage_killswitch(client, interface)
            notify.decision("vpn", f"kill-switch engaged — qBittorrent bound to {interface}")
        return True
    except QBitError:
        return False


async def enforce_vpn_gate(status) -> int:
    """Fail-closed: pause unrestricted (non-legal) torrents while the VPN is
    unsafe. Legal content keeps seeding. Returns how many were paused."""
    if not config.get_bool("vpn_required") or status.safe_to_seed:
        return 0
    with get_db() as db:
        hashes = [r["infohash"] for r in db.execute(
            "SELECT infohash FROM tracked WHERE evicted_at IS NULL AND legal=0").fetchall()]
    if not hashes:
        return 0
    try:
        client = get_client()
        if await client.available():
            if await client.pause(hashes):
                notify.warn("vpn", f"VPN unsafe — paused {len(hashes)} unrestricted torrent(s)")
                return len(hashes)
            # Fail-closed must FAIL LOUDLY: if the pause did not take, we are
            # potentially leaking. Never report success we did not achieve.
            notify.error("vpn", f"VPN unsafe but qBittorrent FAILED to pause "
                                f"{len(hashes)} unrestricted torrent(s) — possible leak")
    except QBitError:
        pass
    return 0


async def resume_unrestricted() -> int:
    """Resume unrestricted torrents once the VPN is safe again."""
    with get_db() as db:
        hashes = [r["infohash"] for r in db.execute(
            "SELECT infohash FROM tracked WHERE evicted_at IS NULL AND legal=0").fetchall()]
    if not hashes:
        return 0
    try:
        client = get_client()
        if await client.available():
            if await client.resume(hashes):
                notify.info("vpn", f"VPN safe — resumed {len(hashes)} unrestricted torrent(s)")
                return len(hashes)
    except QBitError:
        pass
    return 0


def _seeding_allowed(status, candidate_legal: bool) -> tuple:
    """Decide whether seeding this content is permitted right now."""
    if candidate_legal:
        return True, "legal content — VPN not required"
    if not config.get_bool("vpn_required"):
        return True, "VPN not required by settings"
    if status and status.safe_to_seed:
        return True, "VPN up, kill-switch engaged, no leak"
    detail = status.detail if status else "no VPN status"
    return False, f"VPN gate failed: {detail}"


# --------------------------------------------------------------------------- #
# Sync tracked torrents from qBittorrent
# --------------------------------------------------------------------------- #
async def sync_tracked() -> dict:
    client = get_client()
    try:
        if not await client.available():
            return {"ok": False, "error": "qBittorrent unreachable"}
    except QBitError as e:
        return {"ok": False, "error": str(e)}

    category = config.get_str("qbit_category")
    max_seeders = config.get_int("endangered_max_seeders", 5)
    hist_enabled = config.get_bool("seeder_history_enabled")
    hist_retain_s = max(config.get_int("graduate_window_min", 360) * 60 * 2, 86400)
    infos = await client.torrents_info(category=category)
    seen = set()
    now = time.time()
    with get_db() as db:
        for t in infos:
            ih = (t.get("hash") or "").lower()
            if not ih:
                continue
            seen.add(ih)
            seeders = int(t.get("num_complete", t.get("num_seeds", 0)) or 0)
            leechers = int(t.get("num_incomplete", t.get("num_leechs", 0)) or 0)
            size = int(t.get("total_size", t.get("size", 0)) or 0)
            endanger = endangerment_score(seeders, leechers, max_seeders_endangered=max_seeders)
            prev = db.execute("SELECT swarm_revived_at FROM tracked WHERE infohash=?", (ih,)).fetchone()
            already_revived = bool(prev and prev["swarm_revived_at"])
            db.execute(
                """
                INSERT INTO tracked
                  (infohash,name,source,size_bytes,category,legal,mode,qbit_state,
                   seeders,leechers,ratio,uploaded_bytes,progress,endangerment,
                   added_at,last_checked)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(infohash) DO UPDATE SET
                  name=excluded.name, size_bytes=excluded.size_bytes,
                  qbit_state=excluded.qbit_state, seeders=excluded.seeders,
                  leechers=excluded.leechers, ratio=excluded.ratio,
                  uploaded_bytes=excluded.uploaded_bytes, progress=excluded.progress,
                  endangerment=excluded.endangerment, last_checked=excluded.last_checked,
                  evicted_at=NULL, evict_reason=''
                """,
                (ih, t.get("name", ih), "qbit", size, category, 0, "rescue",
                 t.get("state", ""), seeders, leechers, float(t.get("ratio", 0) or 0),
                 int(t.get("uploaded", 0) or 0), float(t.get("progress", 0) or 0),
                 endanger, now, now))
            if hist_enabled:
                db.execute(
                    "INSERT OR REPLACE INTO seeder_history"
                    "(infohash,ts,seeders,leechers,num_complete) VALUES (?,?,?,?,?)",
                    (ih, now, seeders, leechers, seeders))
        if hist_enabled:
            db.execute("DELETE FROM seeder_history WHERE ts < ?", (now - hist_retain_s,))
        # Torrents removed from qBittorrent externally -> mark evicted.
        db_rows = db.execute(
            "SELECT infohash FROM tracked WHERE evicted_at IS NULL").fetchall()
        for r in db_rows:
            if r["infohash"] not in seen:
                db.execute(
                    "UPDATE tracked SET evicted_at=?, evict_reason=? WHERE infohash=?",
                    (now, "removed in qBittorrent", r["infohash"]))
    return {"ok": True, "count": len(seen)}


# --------------------------------------------------------------------------- #
# Rescue
# --------------------------------------------------------------------------- #
async def rescue_candidate(infohash: str, vpn_status=None) -> dict:
    infohash = infohash.lower()
    with get_db() as db:
        row = db.execute("SELECT * FROM candidates WHERE infohash=?", (infohash,)).fetchone()
    if not row:
        return {"ok": False, "error": "unknown candidate"}
    cand = _candidate_from_row(row)
    profile = config.machine_profile()

    ok, reason = hardening.validate_candidate(
        cand.size_bytes, cand.trackers, profile,
        drop_suspicious=config.get_bool("drop_suspicious_trackers"))
    if not ok:
        notify.warn("rescue", f"rejected {cand.name[:60]}: {reason}", infohash=infohash)
        return {"ok": False, "error": reason}

    if vpn_status is None and not cand.legal:
        vpn_status = await current_vpn_status(persist=False)
    allowed, why = _seeding_allowed(vpn_status, cand.legal)
    if not allowed:
        notify.warn("rescue", f"held {cand.name[:60]}: {why}", infohash=infohash)
        return {"ok": False, "error": why}

    # Herd guard: a stale "0 seeders" from an old scan must not send a fleet of
    # installs at a torrent that's already been revived. Re-scrape live and skip
    # if the swarm is already saturated. Best-effort — a webseed-only item, no
    # trackers, or a scrape failure all fall through and rescue normally (the swarm
    # count stays 0 for webseeds, so they're never wrongly skipped).
    sat = config.get_int("swarm_saturation_seeders", 4)
    if sat > 0 and cand.trackers:
        try:
            live = await asyncio.to_thread(scrape_infohash, infohash, cand.trackers, 5.0)
        except Exception:  # noqa: BLE001
            live = None
        live_seeders = int((live or {}).get("seeders", 0) or 0)
        if live_seeders >= sat:
            with get_db() as db:
                db.execute(
                    "UPDATE candidates SET status='skipped', skip_reason=? WHERE infohash=?",
                    (f"swarm already healthy ({live_seeders} seeders)", infohash))
            notify.info("rescue",
                        f"skipped {cand.name[:60]}: swarm already has {live_seeders} seeders")
            return {"ok": False, "error": f"swarm already healthy ({live_seeders} seeders)"}

    client = get_client()
    try:
        if not await client.available():
            return {"ok": False, "error": "qBittorrent unreachable"}
        await client.create_category(config.get_str("qbit_category"),
                                     config.get_str("qbit_save_path"))
        url = cand.magnet or cand.torrent_url
        if not url:
            return {"ok": False, "error": "candidate has no magnet or torrent URL"}
        added = await client.add(
            urls=[url],
            category=config.get_str("qbit_category"),
            tags="torrent-saver,rescue",
            save_path=config.get_str("qbit_save_path"),
            ratio_limit=profile.ratio_limit,
            seeding_time_limit=(profile.seeding_time_limit_min
                                if profile.seeding_time_limit_min > 0 else -1),
            upload_limit=(profile.upload_limit_bytes_s
                          if profile.upload_limit_bytes_s > 0 else None),
        )
    except QBitError as e:
        return {"ok": False, "error": str(e)}
    if not added:
        return {"ok": False, "error": "qBittorrent rejected the add"}

    now = time.time()
    with get_db() as db:
        db.execute(
            """INSERT INTO tracked
                 (infohash,name,source,size_bytes,category,legal,mode,seeders,
                  leechers,endangerment,added_at,last_checked)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(infohash) DO UPDATE SET
                 evicted_at=NULL, evict_reason='', mode='rescue'""",
            (infohash, cand.name, cand.source, cand.size_bytes,
             config.get_str("qbit_category"), int(cand.legal), "rescue",
             cand.seeders, cand.leechers, cand.endangerment, now, now))
        db.execute("UPDATE candidates SET status='rescued' WHERE infohash=?", (infohash,))

    # Metadata ark: save the map so this torrent survives even if its bytes don't.
    if config.get_bool("metadata_ark_enabled"):
        try:
            await asyncio.to_thread(ark.archive_metadata, {
                "infohash": infohash, "name": cand.name, "source": cand.source,
                "magnet": cand.magnet, "torrent_url": cand.torrent_url,
                "size_bytes": cand.size_bytes, "trackers": cand.trackers,
                "webseeds": cand.webseeds, "category": cand.category,
                "legal": cand.legal, "endangerment": cand.endangerment})
            with get_db() as db:
                db.execute("UPDATE candidates SET archived=1 WHERE infohash=?", (infohash,))
                db.execute("UPDATE tracked SET archived=1 WHERE infohash=?", (infohash,))
        except Exception:
            pass

    notify.decision("rescue",
                    f"rescuing {cand.name[:70]} "
                    f"(endangerment {cand.endangerment}, {cand.seeders} seeders)",
                    infohash=infohash)
    return {"ok": True}


async def rescue_queued(max_to_add: Optional[int] = None) -> dict:
    if config.is_paused():
        return {"ok": False, "error": "paused"}
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM candidates WHERE status='queued' ORDER BY endangerment DESC"
        ).fetchall()
    cands = [_candidate_from_row(r) for r in rows]
    if not cands:
        return {"ok": True, "rescued": 0, "considered": 0}

    profile = config.machine_profile()
    tracked = live_tracked()
    decisions = curator.plan_rescues(
        cands, curator.library_bytes(tracked), profile,
        legal_only=config.legal_mode(),
        max_seeders_endangered=config.get_int("endangered_max_seeders", 5),
        min_seeders_to_rescue=config.get_int("min_seeders_to_rescue", 1),
        max_redundancy=config.get_int("max_redundancy", 2),
        min_endangerment=config.get_float("min_endangerment_to_queue", 35.0),
        current_torrent_count=len(tracked),
        already_tracked=tracked_infohashes())

    accepted = [d for d in decisions if d.rescue]
    if max_to_add:
        accepted = accepted[:max_to_add]

    vpn_status = await current_vpn_status(persist=True)
    rescued = 0
    for d in accepted:
        # Legal content reuses the batch VPN status; unrestricted content gets a
        # fresh check per torrent so a mid-batch tunnel drop fails closed.
        vs = vpn_status if d.candidate.legal else None
        res = await rescue_candidate(d.candidate.infohash, vpn_status=vs)
        if res.get("ok"):
            rescued += 1
    return {"ok": True, "rescued": rescued, "considered": len(accepted)}


# --------------------------------------------------------------------------- #
# Eviction
# --------------------------------------------------------------------------- #
def _volume_free_bytes() -> Optional[int]:
    path = config.get_str("qbit_save_path") or str(DB_PATH.parent)
    try:
        return shutil.disk_usage(path).free
    except Exception:
        return None


async def evict_overflow() -> dict:
    tracked = live_tracked()
    if not tracked:
        return {"ok": True, "evicted": 0}
    profile = config.machine_profile()
    decisions = eviction.plan_evictions(
        tracked, profile, volume_free_bytes=_volume_free_bytes(),
        max_seeders_endangered=config.get_int("endangered_max_seeders", 5))
    victims = [d for d in decisions if d.evict]
    if not victims:
        return {"ok": True, "evicted": 0}

    client = get_client()
    try:
        if not await client.available():
            return {"ok": False, "error": "qBittorrent unreachable"}
    except QBitError as e:
        return {"ok": False, "error": str(e)}

    now = time.time()
    evicted = 0
    for d in victims:
        try:
            if await client.delete([d.infohash], delete_files=True):
                with get_db() as db:
                    db.execute(
                        "UPDATE tracked SET evicted_at=?, evict_reason=? WHERE infohash=?",
                        (now, d.reason, d.infohash))
                notify.info("evict", f"evicted {d.name[:70]}: {d.reason}",
                            infohash=d.infohash)
                evicted += 1
        except QBitError:
            break
    return {"ok": True, "evicted": evicted}


def graduation_victims(tracked: list, threshold: int) -> list:
    """Pure selection: which tracked torrents have recovered enough to release.

    A torrent graduates when it is a fully-downloaded rescue whose live seeder
    count has climbed to ``threshold`` or more — the swarm is healthy again, so
    our copy is redundant. Demand-mode seeds and partially-downloaded copies are
    left alone.
    """
    return [
        t for t in tracked
        if (getattr(t, "mode", "rescue") or "rescue") == "rescue"
        and (getattr(t, "progress", 0.0) or 0.0) >= 1.0   # only release a copy we fully rescued
        and (getattr(t, "seeders", 0) or 0) >= threshold   # swarm is healthy again
    ]


async def graduate_recovered() -> dict:
    """Release torrents whose swarm has FULLY + sustainedly recovered.

    A rescued torrent graduates only after its (demand-discounted) seeders stay at
    or above a per-install threshold across a multi-hour window without declining,
    and then only after a randomised hold — so a fleet of installs never releases
    in lockstep and collapses the swarm it just saved. We delete the data to free
    the slot/disk; the metadata ark keeps the map for resurrection if it dies
    again. Runs proactively, regardless of the disk budget. See
    endangerment.evaluate_graduation for the (pure, tested) decision."""
    if not config.get_bool("graduate_enabled"):
        return {"ok": True, "graduated": 0}

    # Per-install threshold jitter desyncs WHICH installs let a torrent go.
    jitter = max(0, config.get_int("graduate_seeders_jitter", 6))
    cfg = dict(
        threshold=config.get_int("graduate_seeders", 10)
        + decorrelate.rng("grad_threshold").randint(0, jitter),
        window_min=config.get_int("graduate_window_min", 360),
        min_samples=config.get_int("graduate_min_samples", 6),
        max_decline_per_hr=config.get_float("graduate_max_decline_per_hr", 1.0),
        require_demand=config.get_bool("graduate_require_demand"),
        hard_seeders=config.get_int("graduate_hard_seeders", 40),
        min_tenure_min=config.get_int("graduate_min_tenure_min", 1440),
    )
    hold_max_s = max(0, config.get_int("graduate_hold_max_s", 21600))
    now = time.time()

    candidates = [t for t in live_tracked()
                  if (t.mode or "rescue") == "rescue" and (t.progress or 0.0) >= 1.0]
    if not candidates:
        return {"ok": True, "graduated": 0}

    releases: list = []
    with get_db() as db:
        for t in candidates:
            row = db.execute("SELECT graduate_eligible_at FROM tracked WHERE infohash=?",
                             (t.infohash,)).fetchone()
            eligible_at = row["graduate_eligible_at"] if row else None
            samples = [(r["ts"], r["seeders"], r["leechers"]) for r in db.execute(
                "SELECT ts,seeders,leechers FROM seeder_history WHERE infohash=? AND ts>=?",
                (t.infohash, now - cfg["window_min"] * 60)).fetchall()]
            # Per-install, per-torrent stable hold so releases scatter across hours.
            hold_seconds = decorrelate.rng("grad_hold:" + t.infohash).uniform(0, hold_max_s)
            action = evaluate_graduation(
                samples=samples, cur_seeders=t.seeders or 0, cur_leechers=t.leechers or 0,
                added_at=t.added_at or 0.0, eligible_at=eligible_at, now=now,
                hold_seconds=hold_seconds, **cfg)
            if action == "set_hold":
                db.execute("UPDATE tracked SET graduate_eligible_at=? WHERE infohash=?",
                           (now + hold_seconds, t.infohash))
            elif action == "clear_hold":
                db.execute("UPDATE tracked SET graduate_eligible_at=NULL WHERE infohash=?",
                           (t.infohash,))
            elif action == "release":
                releases.append(t)

    if not releases:
        return {"ok": True, "graduated": 0}

    client = get_client()
    try:
        if not await client.available():
            return {"ok": True, "graduated": 0}
    except QBitError:
        return {"ok": True, "graduated": 0}

    graduated = 0
    for t in releases:
        reason = f"graduated ({t.seeders} seeders, sustained) — swarm healthy, released to make room"
        try:
            if await client.delete([t.infohash], delete_files=True):
                with get_db() as db:
                    db.execute(
                        "UPDATE tracked SET evicted_at=?, evict_reason=?, "
                        "graduate_eligible_at=NULL WHERE infohash=?",
                        (now, reason, t.infohash))
                    db.execute("UPDATE candidates SET status='graduated' WHERE infohash=?",
                               (t.infohash,))
                notify.decision("graduate",
                                f"released {t.name[:70]}: {reason}", infohash=t.infohash)
                graduated += 1
        except QBitError:
            break
    return {"ok": True, "graduated": graduated}


# --------------------------------------------------------------------------- #
# Death-watch — re-scrape known torrents, catch ones sliding toward death
# --------------------------------------------------------------------------- #
async def rescan_watchlist() -> dict:
    if config.is_paused() or not config.get_bool("deathwatch_enabled"):
        return {"ok": False, "error": "paused or death-watch disabled"}
    max_seeders = config.get_int("endangered_max_seeders", 5)
    min_seeders = config.get_int("min_seeders_to_rescue", 1)
    require_recoverable = config.get_bool("require_recoverable")
    floor = config.get_float("min_endangerment_to_queue", 35.0)
    alert_at = config.get_int("deathwatch_alert_seeders", 2)
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT infohash,name,trackers,webseeds,status FROM candidates "
            "WHERE watched=1 AND trackers!='[]' AND status IN ('queued','skipped','discovered') "
            "ORDER BY last_seen ASC LIMIT 200").fetchall()]
    if not rows:
        return {"ok": True, "rechecked": 0, "slipped": 0}

    sem = asyncio.Semaphore(8)

    async def _recheck(r):
        trackers = json.loads(r["trackers"] or "[]")
        if not trackers:
            return None
        async with sem:
            res = await asyncio.to_thread(scrape_infohash, r["infohash"], trackers)
        return (r, res)

    results = [x for x in await asyncio.gather(*[_recheck(r) for r in rows]) if x]
    rechecked = slipped = auto = 0
    for r, res in results:
        rechecked += 1
        seeders = res["seeders"] if res else 0
        leechers = res["leechers"] if res else 0
        completed = res["completed"] if res else 0
        webseeds = json.loads(r["webseeds"] or "[]")
        rescuable = is_rescuable(seeders, min_seeders_to_rescue=min_seeders,
                                 has_webseed=bool(webseeds),
                                 include_dead=not require_recoverable)
        endanger = endangerment_score(seeders, leechers, completed=completed,
                                      max_seeders_endangered=max_seeders)
        if not rescuable:
            new = "dead"
        elif seeders > max_seeders:
            new = "skipped"
        else:
            new = "queued" if endanger >= floor else "skipped"
        with get_db() as db:
            db.execute(
                "UPDATE candidates SET seeders=?,leechers=?,completed=?,endangerment=?,"
                "rescuable=?,status=CASE WHEN status='rescued' THEN 'rescued' ELSE ? END,"
                "last_seen=? WHERE infohash=?",
                (seeders, leechers, completed, endanger, int(rescuable), new,
                 time.time(), r["infohash"]))
        if r["status"] in ("skipped", "discovered") and new == "queued" and seeders <= alert_at:
            slipped += 1
            notify.warn("deathwatch", f"{r['name'][:60]} slipping — now {seeders} seeders",
                        infohash=r["infohash"], to_obsidian=True)
            if config.get_bool("auto_rescue"):
                if (await rescue_candidate(r["infohash"])).get("ok"):
                    auto += 1
    return {"ok": True, "rechecked": rechecked, "slipped": slipped, "auto_rescued": auto}


# --------------------------------------------------------------------------- #
# Become a real seeder — re-announce complete torrents to revive their swarms
# --------------------------------------------------------------------------- #
async def reannounce_complete() -> dict:
    if config.is_paused() or not config.get_bool("reannounce_enabled"):
        return {"ok": False, "error": "paused or reannounce disabled"}
    client = get_client()
    try:
        if not await client.available():
            return {"ok": False, "error": "qBittorrent unreachable"}
    except QBitError as e:
        return {"ok": False, "error": str(e)}
    status = await current_vpn_status(persist=False)
    infos = await client.torrents_info(category=config.get_str("qbit_category"))
    complete = [t.get("hash", "").lower() for t in infos
                if float(t.get("progress", 0) or 0) >= 1.0 and t.get("hash")]
    if not complete:
        return {"ok": True, "reannounced": 0}
    with get_db() as db:
        legal_map = {r["infohash"]: bool(r["legal"]) for r in
                     db.execute("SELECT infohash,legal FROM tracked").fetchall()}
    # Only announce what's allowed to seed right now (VPN gate for unrestricted).
    allowed = [h for h in complete if _seeding_allowed(status, legal_map.get(h, False))[0]]
    if allowed:
        await client.reannounce(allowed)
        notify.info("seed", f"re-announced {len(allowed)} torrent(s) to revive their swarms")
    return {"ok": True, "reannounced": len(allowed)}


# --------------------------------------------------------------------------- #
# Find-me-a-body — resurrect a truly-dead torrent from a downloadable twin
# --------------------------------------------------------------------------- #
async def resurrect_candidate(infohash: str) -> dict:
    import httpx
    from app import scanner
    infohash = infohash.lower()
    with get_db() as db:
        row = db.execute("SELECT name FROM candidates WHERE infohash=?", (infohash,)).fetchone()
    if not row:
        return {"ok": False, "error": "unknown candidate"}
    twins = await resurrect.find_body(row["name"], infohash)
    if not twins:
        return {"ok": True, "found": 0}

    cands = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for t in twins[:3]:
            await scanner._enrich_metadata(t, client)
            if t.normalised_hash() and t.normalised_hash() != infohash:
                await scanner._enrich_seeders(t)
                cands.append(t)
    if not cands:
        return {"ok": True, "found": 0}

    max_seeders = config.get_int("endangered_max_seeders", 5)
    min_seeders = config.get_int("min_seeders_to_rescue", 1)
    require_recoverable = config.get_bool("require_recoverable")
    for c in cands:
        c.rescuable = is_rescuable(c.seeders, min_seeders_to_rescue=min_seeders,
                                   has_webseed=bool(c.webseeds),
                                   include_dead=not require_recoverable)
        c.endangerment = endangerment_score(c.seeders, c.leechers, completed=c.completed,
                                            max_seeders_endangered=max_seeders)
    queued = scanner._persist(
        cands, legal_only=config.legal_mode(), max_seeders=max_seeders,
        max_redundancy=config.get_int("max_redundancy", 2),
        floor=config.get_float("min_endangerment_to_queue", 35.0),
        min_history=config.get_int("min_download_history", 0))
    with get_db() as db:
        db.execute("UPDATE candidates SET skip_reason=? WHERE infohash=?",
                   (f"twin found: {cands[0].infohash[:12]}", infohash))
    notify.info("resurrect", f"found {len(cands)} downloadable twin(s) for {row['name'][:50]}",
                infohash=infohash, to_obsidian=True)
    return {"ok": True, "found": len(cands), "queued": queued}


# --------------------------------------------------------------------------- #
# Hardening
# --------------------------------------------------------------------------- #
async def apply_hardening_now() -> dict:
    client = get_client()
    try:
        if not await client.available():
            return {"ok": False, "error": "qBittorrent unreachable"}
    except QBitError as e:
        return {"ok": False, "error": str(e)}
    profile = config.machine_profile()
    interface = ""
    if config.get_bool("vpn_killswitch"):
        import asyncio
        interface, _ = await asyncio.to_thread(vpn.pick_interface,
                                               config.get_str("vpn_interface"))
    await client.create_category(config.get_str("qbit_category"),
                                 config.get_str("qbit_save_path"))
    ok = await hardening.apply_hardening(client, profile, vpn_interface=interface)
    if ok:
        notify.decision("hardening",
                        f"applied hardened qBittorrent prefs (profile {profile.key}"
                        + (f", bound to {interface}" if interface else "") + ")")
    return {"ok": ok, "interface": interface}
