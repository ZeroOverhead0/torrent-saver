"""Discovery scan: sources -> enrich -> dedup -> score -> persist candidates.

A scan asks every enabled source for candidates, fills in missing infohashes and
sizes by downloading .torrent metadata, scrapes trackers for live seeder counts,
computes redundancy (healthy duplicates) and an endangerment score, then writes
the results to the `candidates` table tagged queued / skipped / dead.

Discovery only — actually adding torrents to qBittorrent happens in app.actions,
gated by the budget, the VPN kill-switch and hardening checks.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import List, Optional

import httpx

from app import bencode, config, dedup, notify
from app.db import get_db
from app.endangerment import endangerment_score, is_rescuable
from app.models import Candidate
from app.scrape import scrape_infohash
from app.sources import FetchContext, enabled_sources

ENRICH_CONCURRENCY = 8


# --------------------------------------------------------------------------- #
# Torrent metadata parsing
# --------------------------------------------------------------------------- #
def _trackers_from_meta(meta: dict) -> List[str]:
    trackers: List[str] = []
    announce = meta.get(b"announce")
    if announce:
        trackers.append(announce.decode("utf-8", "replace"))
    for tier in meta.get(b"announce-list", []) or []:
        for tr in tier:
            try:
                trackers.append(tr.decode("utf-8", "replace"))
            except Exception:
                continue
    # de-dupe, keep order
    seen: set = set()
    out = []
    for t in trackers:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _webseeds_from_meta(meta: dict) -> List[str]:
    """Parse BEP 19 `url-list` (HTTP/FTP webseeds). May be a single string or a
    list of strings."""
    ul = meta.get(b"url-list")
    if isinstance(ul, bytes):
        ul = [ul]
    if not isinstance(ul, list):
        return []
    return [w.decode("utf-8", "replace") for w in ul if isinstance(w, bytes) and w]


async def _enrich_metadata(cand: Candidate, client: httpx.AsyncClient) -> None:
    """Download .torrent metadata to fill infohash/name/size/trackers."""
    if cand.infohash or not cand.torrent_url:
        return
    try:
        resp = await client.get(cand.torrent_url, timeout=30,
                                headers={"User-Agent": "TorrentSaver/1.0"})
        resp.raise_for_status()
        data = resp.content
        meta = bencode.decode(data)
        info = meta[b"info"]
        cand.infohash = bencode.infohash_from_torrent(data)
        if not cand.name or cand.name == cand.torrent_url:
            cand.name = info.get(b"name", b"").decode("utf-8", "replace") or cand.name
        parsed = bencode.info_from_torrent(data)
        cand.size_bytes = cand.size_bytes or parsed["size_bytes"]
        cand.trackers = cand.trackers or _trackers_from_meta(meta)
        cand.webseeds = cand.webseeds or _webseeds_from_meta(meta)
    except Exception as e:  # noqa: BLE001
        notify.warn("scan", f"metadata fetch failed for {cand.name[:60]}: {e}")


async def _enrich_seeders(cand: Candidate) -> None:
    """Scrape trackers for live seeder/leecher counts (in a worker thread)."""
    if not cand.infohash or not cand.trackers:
        return
    result = await asyncio.to_thread(scrape_infohash, cand.infohash, cand.trackers)
    if result:
        cand.seeders = result["seeders"]
        cand.leechers = result["leechers"]
        cand.completed = result["completed"] or cand.completed


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def _decide_status(cand: Candidate, *, legal_only: bool, max_seeders: int,
                   max_redundancy: int, floor: float, min_history: int) -> tuple:
    has_webseed = bool(cand.webseeds)
    if not cand.rescuable:
        return "dead", "truly dead — 0 seeders and no webseed, can't download"
    # Filter never-seeded junk: a recoverable swarm torrent that has never been
    # downloaded and nobody is downloading now is likely abandoned, not "dying".
    if (min_history > 0 and not has_webseed and cand.leechers == 0
            and (cand.completed or 0) < min_history):
        return "skipped", f"no download history (<{min_history} grabs) — likely abandoned/junk"
    if cand.seeders > max_seeders:
        return "skipped", f"healthy ({cand.seeders} seeders) — not endangered"
    if legal_only and not cand.legal:
        return "skipped", "blocked by legal-only mode"
    if cand.redundancy >= max_redundancy:
        return "skipped", f"{cand.redundancy} healthy duplicate(s) exist"
    if cand.endangerment < floor:
        return "skipped", f"endangerment {cand.endangerment} below floor {floor}"
    return "queued", ""


def _persist(cands: List[Candidate], *, legal_only: bool, max_seeders: int,
             max_redundancy: int, floor: float, min_history: int) -> int:
    now = time.time()
    queued = 0
    with get_db() as db:
        for c in cands:
            ih = c.normalised_hash()
            if not ih:
                continue
            status, reason = _decide_status(
                c, legal_only=legal_only, max_seeders=max_seeders,
                max_redundancy=max_redundancy, floor=floor, min_history=min_history)
            if status == "queued":
                queued += 1
            db.execute(
                """
                INSERT INTO candidates
                  (infohash,name,source,magnet,torrent_url,size_bytes,seeders,
                   leechers,completed,category,legal,endangerment,redundancy,
                   rescuable,status,skip_reason,trackers,webseeds,first_seen,last_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(infohash) DO UPDATE SET
                  name=excluded.name,
                  source=excluded.source,
                  magnet=COALESCE(excluded.magnet, candidates.magnet),
                  torrent_url=COALESCE(excluded.torrent_url, candidates.torrent_url),
                  size_bytes=excluded.size_bytes,
                  seeders=excluded.seeders,
                  leechers=excluded.leechers,
                  completed=excluded.completed,
                  category=excluded.category,
                  legal=excluded.legal,
                  endangerment=excluded.endangerment,
                  redundancy=excluded.redundancy,
                  rescuable=excluded.rescuable,
                  -- never downgrade an already-rescued candidate
                  status=CASE WHEN candidates.status='rescued'
                              THEN 'rescued' ELSE excluded.status END,
                  skip_reason=excluded.skip_reason,
                  trackers=CASE WHEN excluded.trackers!='[]' THEN excluded.trackers ELSE candidates.trackers END,
                  webseeds=CASE WHEN excluded.webseeds!='[]' THEN excluded.webseeds ELSE candidates.webseeds END,
                  last_seen=excluded.last_seen
                """,
                (ih, c.name, c.source, c.magnet, c.torrent_url, c.size_bytes,
                 c.seeders, c.leechers, c.completed, c.category, int(c.legal),
                 c.endangerment, c.redundancy, int(c.rescuable), status, reason,
                 json.dumps(c.trackers or []), json.dumps(c.webseeds or []),
                 now, now),
            )
    return queued


# --------------------------------------------------------------------------- #
# Scan orchestration
# --------------------------------------------------------------------------- #
async def run_scan(per_source_limit: Optional[int] = None) -> dict:
    legal_only = config.legal_mode()
    max_seeders = config.get_int("endangered_max_seeders", 5)
    min_seeders = config.get_int("min_seeders_to_rescue", 1)
    max_redundancy = config.get_int("max_redundancy", 2)
    healthy = config.get_int("redundancy_healthy_seeders", 10)
    sim = config.get_float("dedup_title_similarity", 0.82)
    size_tol = config.get_float("dedup_size_tolerance", 0.10)
    floor = config.get_float("min_endangerment_to_queue", 35.0)
    require_recoverable = config.get_bool("require_recoverable")
    min_history = config.get_int("min_download_history", 0)
    scrape_enabled = config.get_bool("tracker_scrape_enabled")
    limit = per_source_limit or 50

    summary = {"sources": {}, "found": 0, "enriched": 0, "queued": 0, "errors": []}
    all_cands: List[Candidate] = []

    async with httpx.AsyncClient(follow_redirects=True) as client:
        ctx = FetchContext(client=client, limit=limit, legal_only=legal_only,
                           max_seeders_endangered=max_seeders)
        for source in enabled_sources():
            try:
                cands = await source.fetch(ctx)
                summary["sources"][source.name] = {"found": len(cands), "ok": True}
                all_cands.extend(cands)
            except Exception as e:  # noqa: BLE001
                summary["sources"][source.name] = {"found": 0, "ok": False, "error": str(e)}
                summary["errors"].append(f"{source.name}: {e}")
                notify.warn("scan", f"source {source.name} failed: {e}")

        summary["found"] = len(all_cands)

        # --- enrich metadata (infohash/name/size) for url-only candidates --- #
        sem = asyncio.Semaphore(ENRICH_CONCURRENCY)

        async def _meta(c):
            async with sem:
                await _enrich_metadata(c, client)

        await asyncio.gather(*[_meta(c) for c in all_cands if not c.infohash and c.torrent_url])

        # Drop candidates we still couldn't identify.
        all_cands = [c for c in all_cands if c.normalised_hash()]

        # --- enrich seeders via tracker scrape ------------------------------ #
        if scrape_enabled:
            async def _seed(c):
                async with sem:
                    # Prowlarr already reports live seeders; everything else
                    # (IA, LinuxTracker, manual) needs a tracker scrape.
                    if c.source != "prowlarr":
                        await _enrich_seeders(c)

            await asyncio.gather(*[_seed(c) for c in all_cands if c.trackers])
        summary["enriched"] = sum(1 for c in all_cands if c.seeders or c.trackers)

    # --- dedup / redundancy + endangerment (CPU only, no network) ----------- #
    for c in all_cands:
        c.redundancy = dedup.redundancy(
            c, all_cands, healthy_seeders=healthy,
            sim_threshold=sim, size_tolerance=size_tol)
        c.rescuable = is_rescuable(c.seeders, min_seeders_to_rescue=min_seeders,
                                   has_webseed=bool(c.webseeds),
                                   include_dead=not require_recoverable)
        c.endangerment = endangerment_score(
            c.seeders, c.leechers, completed=c.completed,
            redundancy=c.redundancy, max_seeders_endangered=max_seeders)

    summary["queued"] = _persist(
        all_cands, legal_only=legal_only, max_seeders=max_seeders,
        max_redundancy=max_redundancy, floor=floor, min_history=min_history)

    notify.info("scan",
                f"scan complete: {summary['found']} found, "
                f"{summary['queued']} queued for rescue")
    return summary
