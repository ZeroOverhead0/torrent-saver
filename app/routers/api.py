"""JSON API + hub badge endpoint."""
from __future__ import annotations

from fastapi import APIRouter

from app import config
from app.db import get_db, recent_events
from app.qbittorrent import QBitError, get_client

router = APIRouter()


def _stats() -> dict:
    with get_db() as db:
        queued = db.execute("SELECT COUNT(*) c FROM candidates WHERE status='queued'").fetchone()["c"]
        skipped = db.execute("SELECT COUNT(*) c FROM candidates WHERE status='skipped'").fetchone()["c"]
        dead = db.execute("SELECT COUNT(*) c FROM candidates WHERE status='dead'").fetchone()["c"]
        rescued = db.execute("SELECT COUNT(*) c FROM candidates WHERE status='rescued'").fetchone()["c"]
        trow = db.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(size_bytes),0) b FROM tracked WHERE evicted_at IS NULL"
        ).fetchone()
        demand = db.execute(
            "SELECT COUNT(*) c FROM tracked WHERE evicted_at IS NULL AND mode='demand'"
        ).fetchone()["c"]
        anchored = db.execute(
            "SELECT COUNT(*) c FROM tracked WHERE evicted_at IS NULL AND mode='anchor'"
        ).fetchone()["c"]
        # "Am I helping or redundant?" — did we join a genuinely thin swarm?
        thr = config.get_int("endangered_max_seeders", 5)
        h = db.execute(
            "SELECT "
            "SUM(CASE WHEN live_seeders_before IS NOT NULL AND live_seeders_before < ? THEN 1 ELSE 0 END) helped, "
            "SUM(CASE WHEN live_seeders_before IS NOT NULL AND live_seeders_before >= ? THEN 1 ELSE 0 END) redundant "
            "FROM tracked WHERE evicted_at IS NULL", (thr, thr)).fetchone()
        vpn = db.execute("SELECT * FROM vpn_checks ORDER BY ts DESC LIMIT 1").fetchone()
    from app import ark
    profile = config.machine_profile()
    return {
        "queued": queued, "skipped": skipped, "dead": dead, "rescued": rescued,
        "tracked": trow["c"], "library_bytes": trow["b"], "demand": demand,
        "anchored": anchored, "helped": h["helped"] or 0, "redundant": h["redundant"] or 0,
        "archived": ark.count_ark(),
        "disk_budget_bytes": profile.max_disk_bytes, "profile": profile.key,
        "vpn": dict(vpn) if vpn else {},
    }


@router.get("/api/stats")
async def api_stats():
    return _stats()


@router.get("/api/status")
async def api_status():
    s = _stats()
    s["paused"] = config.is_paused()
    s["legal_mode"] = config.legal_mode()
    s["auto_rescue"] = config.get_bool("auto_rescue")
    s["demand_seed_enabled"] = config.get_bool("demand_seed_enabled")
    try:
        h = await get_client().health()        # 'ok' | 'auth' | 'down'
        s["qbit_available"] = (h == "ok")
        s["qbit_health"] = h
    except QBitError:
        s["qbit_available"] = False
        s["qbit_health"] = "down"
    return s


@router.get("/api/events")
async def api_events(limit: int = 100):
    return {"events": recent_events(limit)}


@router.get("/api/badge")
async def api_badge():
    s = _stats()
    vpn = s.get("vpn") or {}
    paused = config.is_paused()
    if paused:
        return {"text": "paused", "tone": "warn"}
    tone = "ok"
    if config.get_bool("vpn_required") and not vpn.get("killswitch"):
        tone = "warn"
    if vpn.get("leaking"):
        tone = "err"
    return {"text": f"{s['tracked']} seeding · {s['queued']} queued", "tone": tone}
