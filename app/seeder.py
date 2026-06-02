"""Demand-seeding mode — "help the network": seed the most in-demand torrents.

The opposite of rescue. Instead of saving near-dead torrents, find the torrents
with the most leechers waiting (e.g. a just-released Linux distro) and seed those
so a fat upload pipe does the most good. Candidates come from the legal **distro
radar** (popular distros, zero-config) and, optionally, Prowlarr. With the
"help the network" toggle (``demand_max_upload``) the chosen torrents are added
with NO upload / ratio / seed-time cap. Non-legal content stays behind the VPN
kill-switch.
"""
from __future__ import annotations

import asyncio
import time

import httpx

from app import config, notify
from app.actions import _seeding_allowed, current_vpn_status, live_tracked
from app.db import get_db
from app.qbittorrent import QBitError, get_client
from app.scrape import scrape_infohash
from app.sources.base import FetchContext
from app.sources.distro_radar import DistroRadar
from app.sources.prowlarr import Prowlarr


def _demand_count(tracked) -> int:
    return sum(1 for t in tracked if t.mode == "demand")


def _demand_add_limits(profile, max_upload: bool) -> dict:
    """The qBittorrent add() limits for a demand torrent.

    With "help the network" on, uncap everything (unlimited upload, no ratio cap,
    seed forever); otherwise honour the active machine profile."""
    if max_upload:
        return {"ratio_limit": -1.0, "seeding_time_limit": -1, "upload_limit": None}
    return {
        "ratio_limit": profile.ratio_limit,
        "seeding_time_limit": (profile.seeding_time_limit_min
                               if profile.seeding_time_limit_min > 0 else -1),
        "upload_limit": (profile.upload_limit_bytes_s
                         if profile.upload_limit_bytes_s > 0 else None),
    }


def _select_demand(cands, *, min_seeders: int, min_leechers: int,
                   legal_only: bool, slots: int) -> list:
    """Pure selection: the most in-demand candidates that pass the filters.

    Demand = leechers. We require the torrent to be downloadable (>= min_seeders)
    and to have real demand (>= min_leechers), then take the highest-leecher ones."""
    picked, seen = [], set()
    for c in cands:
        ih = c.infohash or ""
        if not ih or ih in seen:
            continue
        if legal_only and not c.legal:
            continue
        if (c.leechers or 0) >= min_leechers and (c.seeders or 0) >= min_seeders:
            seen.add(ih)
            picked.append(c)
    picked.sort(key=lambda c: (c.leechers or 0), reverse=True)
    return picked[:slots]


async def _enrich_demand(cands, limit: int, *, timeout: float = 6.0,
                         concurrency: int = 8) -> None:
    """Tracker-scrape candidates with no live counts (distro radar) so they can
    be ranked by demand. Concurrent + bounded; best-effort."""
    todo = [c for c in cands
            if (c.leechers or 0) == 0 and (c.seeders or 0) == 0 and c.trackers][:limit]
    if not todo:
        return
    sem = asyncio.Semaphore(concurrency)

    async def _one(c):
        async with sem:
            try:
                res = await asyncio.to_thread(scrape_infohash, c.infohash, c.trackers, timeout)
            except Exception:  # noqa: BLE001
                res = None
        if res:
            c.seeders = int(res.get("seeders", 0) or 0)
            c.leechers = int(res.get("leechers", 0) or 0)

    await asyncio.gather(*[_one(c) for c in todo])


async def run_demand_seed() -> dict:
    if not config.get_bool("demand_seed_enabled"):
        return {"ok": False, "error": "demand-seeding disabled"}
    if config.is_paused():
        return {"ok": False, "error": "paused"}

    min_seeders = config.get_int("demand_min_seeders", 1)
    min_leechers = config.get_int("demand_min_leechers", 10)
    max_torrents = config.get_int("demand_max_torrents", 20)
    legal_only = config.get_bool("demand_legal_only")
    scan_limit = config.get_int("demand_scan_limit", 40)
    max_upload = config.get_bool("demand_max_upload")

    tracked = live_tracked()
    slots = max(0, max_torrents - _demand_count(tracked))
    if slots <= 0:
        return {"ok": True, "seeded": 0, "note": "demand-seed slots full"}

    # Gather candidates: the legal distro radar always; Prowlarr only when the
    # user has opted out of legal-only AND configured it.
    cands: list = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        ctx = FetchContext(client=client, limit=scan_limit, legal_only=legal_only,
                           max_seeders_endangered=config.get_int("endangered_max_seeders", 5))
        if config.get_bool("src_distro_radar_enabled"):
            try:
                cands += await DistroRadar().fetch(ctx)
            except Exception as e:  # noqa: BLE001
                notify.warn("seed", f"distro radar failed: {e}")
        if not legal_only and config.get_str("prowlarr_url") and config.prowlarr_api_key():
            try:
                cands += await Prowlarr().fetch(ctx)
            except Exception as e:  # noqa: BLE001
                notify.warn("seed", f"Prowlarr demand fetch failed: {e}")
    if not cands:
        return {"ok": True, "seeded": 0,
                "note": "no demand candidates (enable distro radar or configure Prowlarr)"}

    # Distro-radar candidates have no live counts — scrape so we can rank by demand.
    await _enrich_demand(cands, scan_limit)

    existing = {t.infohash for t in tracked}
    picks = _select_demand([c for c in cands if (c.infohash or "") not in existing],
                           min_seeders=min_seeders, min_leechers=min_leechers,
                           legal_only=legal_only, slots=slots)
    if not picks:
        return {"ok": True, "seeded": 0, "note": "no high-demand torrents found right now"}

    vpn_status = await current_vpn_status(persist=True)
    qbit = get_client()
    try:
        if not await qbit.available():
            return {"ok": False, "error": "qBittorrent unreachable"}
        await qbit.create_category(config.get_str("qbit_category"),
                                   config.get_str("qbit_save_path"))
    except QBitError as e:
        return {"ok": False, "error": str(e)}

    profile = config.machine_profile()
    limits = _demand_add_limits(profile, max_upload)
    seeded = 0
    for c in picks:
        allowed, why = _seeding_allowed(vpn_status, c.legal, c.source)
        if not allowed:
            notify.warn("seed", f"demand-seed held: {why}")
            break
        url = c.magnet or c.torrent_url
        if not url:
            continue
        try:
            ok = await qbit.add(
                urls=[url], category=config.get_str("qbit_category"),
                tags="torrent-saver,demand", save_path=config.get_str("qbit_save_path"),
                **limits)
        except QBitError:
            break
        if ok and c.infohash:
            now = time.time()
            with get_db() as db:
                db.execute(
                    """INSERT INTO tracked
                         (infohash,name,source,size_bytes,category,legal,mode,
                          seeders,leechers,added_at,last_checked)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(infohash) DO UPDATE SET mode='demand',
                          evicted_at=NULL, evict_reason=''""",
                    (c.infohash, c.name, c.source, c.size_bytes,
                     config.get_str("qbit_category"), int(c.legal), "demand",
                     c.seeders, c.leechers, now, now))
            seeded += 1
    if seeded:
        how = "max-upload" if max_upload else "profile-capped"
        notify.info("seed", f"demand-seeding {seeded} in-demand torrent(s) [{how}]")
    return {"ok": True, "seeded": seeded}
