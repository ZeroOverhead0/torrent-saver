"""Tracked torrents — what we're seeding, plus manual controls."""
from __future__ import annotations

import time

from fastapi import APIRouter, Request

from app import actions, config
from app.db import get_db
from app.qbittorrent import QBitError, get_client
from app.routers._common import redirect, render

router = APIRouter()


@router.get("/torrents")
async def torrents(request: Request, view: str = "live", msg: str = ""):
    with get_db() as db:
        if view == "evicted":
            rows = db.execute(
                "SELECT * FROM tracked WHERE evicted_at IS NOT NULL "
                "ORDER BY evicted_at DESC LIMIT 200").fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM tracked WHERE evicted_at IS NULL "
                "ORDER BY endangerment DESC").fetchall()
        live_total = db.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(size_bytes),0) b "
            "FROM tracked WHERE evicted_at IS NULL").fetchone()
    return render(request, "torrents.html",
                  msg=msg, view=view, rows=[dict(r) for r in rows],
                  count=live_total["c"], library_bytes=live_total["b"],
                  profile=config.machine_profile())


@router.post("/torrents/sync")
async def sync(request: Request):
    res = await actions.sync_tracked()
    msg = f"Synced {res.get('count', 0)} torrents" if res.get("ok") else f"Sync failed: {res.get('error')}"
    return redirect(request, "/torrents", msg)


@router.post("/torrents/evict-now")
async def evict_now(request: Request):
    res = await actions.evict_overflow()
    msg = f"Evicted {res.get('evicted', 0)}" if res.get("ok") else f"Evict failed: {res.get('error')}"
    return redirect(request, "/torrents", msg)


@router.post("/torrent/{infohash}/evict")
async def evict_one(request: Request, infohash: str):
    infohash = infohash.lower()
    client = get_client()
    try:
        ok = await client.delete([infohash], delete_files=True)
    except QBitError as e:
        return redirect(request, "/torrents", f"Evict failed: {e}")
    if ok:
        with get_db() as db:
            db.execute("UPDATE tracked SET evicted_at=?, evict_reason=? WHERE infohash=?",
                       (time.time(), "manually evicted", infohash))
    return redirect(request, "/torrents", "Evicted" if ok else "qBittorrent rejected evict")


@router.post("/torrent/{infohash}/pause")
async def pause_one(request: Request, infohash: str):
    try:
        await get_client().pause([infohash.lower()])
    except QBitError as e:
        return redirect(request, "/torrents", f"Pause failed: {e}")
    return redirect(request, "/torrents", "Paused")


@router.post("/torrent/{infohash}/resume")
async def resume_one(request: Request, infohash: str):
    try:
        await get_client().resume([infohash.lower()])
    except QBitError as e:
        return redirect(request, "/torrents", f"Resume failed: {e}")
    return redirect(request, "/torrents", "Resumed")
