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
        vpn = db.execute("SELECT * FROM vpn_checks ORDER BY ts DESC LIMIT 1").fetchone()
    from app import ark
    profile = config.machine_profile()
    return {
        "queued": queued, "skipped": skipped, "dead": dead, "rescued": rescued,
        "tracked": trow["c"], "library_bytes": trow["b"], "demand": demand,
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
        s["qbit_available"] = await get_client().available()
    except QBitError:
        s["qbit_available"] = False
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
