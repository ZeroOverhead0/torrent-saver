"""Shared template environment, filters and small DB read helpers for routers."""
from __future__ import annotations

import time
import urllib.parse
from pathlib import Path

from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.db import get_db
from app.endangerment import endangerment_band

ROOT = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))


# --------------------------------------------------------------------------- #
# Formatting filters
# --------------------------------------------------------------------------- #
def fmt_bytes(n) -> str:
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024.0:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} EB"


def fmt_speed(n) -> str:
    return fmt_bytes(n) + "/s"


def fmt_ago(ts) -> str:
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return "—"
    if ts <= 0:
        return "—"
    delta = max(0, time.time() - ts)
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    return f"{int(delta / 86400)}d ago"


def fmt_pct(x) -> str:
    try:
        return f"{float(x) * 100:.0f}%"
    except (TypeError, ValueError):
        return "0%"


templates.env.filters["bytes"] = fmt_bytes
templates.env.filters["speed"] = fmt_speed
templates.env.filters["ago"] = fmt_ago
templates.env.filters["pct"] = fmt_pct
templates.env.globals["band"] = endangerment_band
templates.env.globals["app_version"] = __version__


# --------------------------------------------------------------------------- #
# DB read helpers (used by base.html nav + dashboards)
# --------------------------------------------------------------------------- #
def nav_counts() -> dict:
    with get_db() as db:
        queued = db.execute(
            "SELECT COUNT(*) c FROM candidates WHERE status='queued'").fetchone()["c"]
        tracked = db.execute(
            "SELECT COUNT(*) c FROM tracked WHERE evicted_at IS NULL").fetchone()["c"]
    return {"queued": queued, "tracked": tracked}


def latest_vpn() -> dict:
    with get_db() as db:
        row = db.execute("SELECT * FROM vpn_checks ORDER BY ts DESC LIMIT 1").fetchone()
    return dict(row) if row else {}


# Expose nav_counts to every template.
templates.env.globals["nav_counts"] = nav_counts


def render(request, name: str, **ctx):
    ctx.setdefault("request", request)
    return templates.TemplateResponse(name, ctx)


def redirect(request, path: str, msg: str = ""):
    root = request.scope.get("root_path", "") or ""
    url = root + path
    if msg:
        sep = "&" if "?" in url else "?"
        url += sep + "msg=" + urllib.parse.quote(msg)
    return RedirectResponse(url, status_code=303)
