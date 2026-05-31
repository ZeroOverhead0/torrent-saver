"""Demand-seeding mode — for users with big upload who want to help the swarm.

The opposite of rescue: instead of saving near-dead torrents, find the most
in-demand healthy torrents (lots of leechers waiting) and seed those, so a fat
upload pipe does the most good. Sourced from Prowlarr (the only connector that
reports live seeder/leecher counts for popular content) and gated behind the
same VPN kill-switch as any unrestricted seeding.
"""
from __future__ import annotations

import time

import httpx

from app import config, notify
from app.actions import _seeding_allowed, current_vpn_status, live_tracked
from app.db import get_db
from app.qbittorrent import QBitError, get_client
from app.sources.base import FetchContext
from app.sources.prowlarr import Prowlarr


def _demand_count(tracked) -> int:
    return sum(1 for t in tracked if t.mode == "demand")


async def run_demand_seed() -> dict:
    if not config.get_bool("demand_seed_enabled"):
        return {"ok": False, "error": "demand-seeding disabled"}
    if config.is_paused():
        return {"ok": False, "error": "paused"}

    if not config.get_str("prowlarr_url") or not config.prowlarr_api_key():
        return {"ok": False,
                "error": "demand-seeding needs Prowlarr configured (URL + API key + queries)"}

    min_seeders = config.get_int("demand_min_seeders", 50)
    min_leechers = config.get_int("demand_min_leechers", 20)
    max_torrents = config.get_int("demand_max_torrents", 20)

    tracked = live_tracked()
    slots = max(0, max_torrents - _demand_count(tracked))
    if slots <= 0:
        return {"ok": True, "seeded": 0, "note": "demand-seed slots full"}

    # Pull popular candidates from Prowlarr and keep the high-demand healthy ones.
    candidates = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        ctx = FetchContext(client=client, limit=100,
                           legal_only=config.legal_mode(),
                           max_seeders_endangered=config.get_int("endangered_max_seeders", 5))
        try:
            results = await Prowlarr().fetch(ctx)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"Prowlarr fetch failed: {e}"}
    for c in results:
        if c.seeders >= min_seeders and c.leechers >= min_leechers:
            candidates.append(c)
    candidates.sort(key=lambda c: c.leechers, reverse=True)
    candidates = candidates[:slots]
    if not candidates:
        return {"ok": True, "seeded": 0, "note": "no high-demand torrents found"}

    # VPN gate (these are unrestricted unless the user trusts their indexers).
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
    existing = {t.infohash for t in tracked}
    seeded = 0
    for c in candidates:
        if c.infohash and c.infohash in existing:
            continue
        allowed, why = _seeding_allowed(vpn_status, c.legal)
        if not allowed:
            notify.warn("seed", f"demand-seed held: {why}")
            break
        url = c.magnet or c.torrent_url
        if not url:
            continue
        try:
            ok = await qbit.add(
                urls=[url], category=config.get_str("qbit_category"),
                tags="torrent-saver,demand",
                save_path=config.get_str("qbit_save_path"),
                ratio_limit=profile.ratio_limit,
                seeding_time_limit=(profile.seeding_time_limit_min
                                    if profile.seeding_time_limit_min > 0 else -1),
                upload_limit=(profile.upload_limit_bytes_s
                              if profile.upload_limit_bytes_s > 0 else None))
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
                    (c.infohash, c.name, "demand", c.size_bytes,
                     config.get_str("qbit_category"), int(c.legal), "demand",
                     c.seeders, c.leechers, now, now))
            seeded += 1
    if seeded:
        notify.info("seed", f"demand-seeding {seeded} high-demand torrent(s)")
    return {"ok": True, "seeded": seeded}
