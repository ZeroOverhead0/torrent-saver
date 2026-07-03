"""Dashboard."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request

from app import config
from app.db import get_db, recent_events
from app.qbittorrent import QBitError, get_client
from app.routers._common import latest_vpn, redirect, render

router = APIRouter()


@router.get("/")
async def home(request: Request, msg: str = ""):
    with get_db() as db:
        queued = db.execute("SELECT COUNT(*) c FROM candidates WHERE status='queued'").fetchone()["c"]
        rescued = db.execute("SELECT COUNT(*) c FROM candidates WHERE status='rescued'").fetchone()["c"]
        dead = db.execute("SELECT COUNT(*) c FROM candidates WHERE status='dead'").fetchone()["c"]
        trow = db.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(size_bytes),0) b FROM tracked WHERE evicted_at IS NULL"
        ).fetchone()
        demand = db.execute(
            "SELECT COUNT(*) c FROM tracked WHERE evicted_at IS NULL AND mode='demand'"
        ).fetchone()["c"]
        top = db.execute(
            "SELECT * FROM candidates WHERE status='queued' "
            "ORDER BY endangerment DESC LIMIT 8").fetchall()
    profile = config.machine_profile()
    try:
        qbit_ok = await get_client().available()
    except QBitError:
        qbit_ok = False
    from app import qbit_service
    qbit_agent_running = qbit_service.is_running()

    return render(request, "home.html",
                  msg=msg,
                  paused=config.is_paused(),
                  legal_mode=config.legal_mode(),
                  auto_rescue=config.get_bool("auto_rescue"),
                  demand_enabled=config.get_bool("demand_seed_enabled"),
                  queued=queued, rescued=rescued, dead=dead,
                  tracked=trow["c"], library_bytes=trow["b"], demand=demand,
                  profile=profile, qbit_ok=qbit_ok,
                  qbit_agent_running=qbit_agent_running,
                  vpn=latest_vpn(),
                  vpn_required=config.get_bool("vpn_required"),
                  top=[dict(r) for r in top],
                  events=recent_events(15))


@router.get("/ark")
async def ark_page(request: Request, msg: str = ""):
    from app import ark
    return render(request, "ark.html", msg=msg, items=ark.list_ark())


@router.post("/pause")
async def toggle_pause(request: Request, paused: str = Form("")):
    from app import settings as S
    new = "0" if config.is_paused() else "1"
    S.set_setting("paused", new)
    return redirect(request, "/", "Paused" if new == "1" else "Resumed")


@router.post("/qbittorrent/full-stop")
async def qbittorrent_full_stop(request: Request):
    """Fully stop qBittorrent — boot out + disable its KeepAlive LaunchAgent so
    it cannot respawn. Note: this drops any active seeds until restarted."""
    from app import qbit_service
    ok, msg = qbit_service.full_stop()
    return redirect(request, "/", msg)


@router.post("/qbittorrent/start")
async def qbittorrent_start(request: Request):
    """Re-enable + bootstrap the qBittorrent LaunchAgent (undo a full stop)."""
    from app import qbit_service
    ok, msg = qbit_service.start()
    return redirect(request, "/", msg)
