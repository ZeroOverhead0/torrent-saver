"""Torrent Saver — FastAPI application entrypoint.

Creates the app, mounts static files, initialises the DB, seeds first-run config
from env/TOML, includes the routers and starts the background loops. Designed to
run standalone (`uvicorn app.main:app`) or be spawned by the Claude dashboard hub
with `--root-path /torrentsaver`.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app import __version__, auth
from app.db import APP_DIR, init_db, resource_dir

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("torrentsaver")


# --------------------------------------------------------------------------- #
# First-run bootstrap (optional, non-destructive)
# --------------------------------------------------------------------------- #
def _bootstrap() -> None:
    from app import settings as S
    from app.config import DEFAULT_SETTINGS

    # Seed qBittorrent connection from env if still at defaults.
    env_map = {"QBIT_URL": "qbit_url", "QBIT_USERNAME": "qbit_username"}
    for env_key, setting_key in env_map.items():
        val = os.environ.get(env_key)
        if val and S.get_setting(setting_key) in (None, DEFAULT_SETTINGS.get(setting_key)):
            S.set_setting(setting_key, val)
    pw = os.environ.get("QBIT_PASSWORD")
    if pw and not S.get_secret("qbit_password"):
        S.set_secret("qbit_password", pw)

    # Seed from config.toml on first run only.
    toml_path = APP_DIR.parent / "config.toml"
    if toml_path.exists() and not S.get_setting("_toml_bootstrapped"):
        data = _read_toml(toml_path)
        if data:
            _apply_toml(data, S)
            S.set_setting("_toml_bootstrapped", "1")


def _read_toml(path: Path):
    try:
        import tomllib  # py3.11+
        with open(path, "rb") as f:
            return tomllib.load(f)
    except ModuleNotFoundError:
        try:
            import tomli  # backport
            with open(path, "rb") as f:
                return tomli.load(f)
        except Exception:
            return None
    except Exception:
        return None


def _apply_toml(data: dict, S) -> None:
    flat = {
        "legal_mode": ("general", "legal_mode"),
        "auto_rescue": ("general", "auto_rescue"),
        "machine_profile": ("general", "machine_profile"),
        "endangered_max_seeders": ("discovery", "endangered_max_seeders"),
        "min_seeders_to_rescue": ("discovery", "min_seeders_to_rescue"),
        "max_redundancy": ("discovery", "max_redundancy"),
        "scan_interval_min": ("discovery", "scan_interval_min"),
        "src_internet_archive_enabled": ("sources", "internet_archive"),
        "src_academic_torrents_enabled": ("sources", "academic_torrents"),
        "src_linuxtracker_enabled": ("sources", "linuxtracker"),
        "src_prowlarr_enabled": ("sources", "prowlarr"),
        "prowlarr_url": ("sources", "prowlarr_url"),
        "demand_seed_enabled": ("demand_seeding", "enabled"),
        "demand_min_upload_mbps": ("demand_seeding", "min_upload_mbps"),
        "demand_max_torrents": ("demand_seeding", "max_torrents"),
        "vpn_required": ("vpn", "required"),
        "vpn_provider": ("vpn", "provider"),
        "vpn_interface": ("vpn", "interface"),
        "vpn_killswitch": ("vpn", "killswitch"),
        "vpn_baseline_ip": ("vpn", "baseline_ip"),
        "qbit_url": ("qbittorrent", "url"),
        "qbit_username": ("qbittorrent", "username"),
        "qbit_category": ("qbittorrent", "category"),
    }
    for setting_key, (section, field) in flat.items():
        sec = data.get(section, {})
        if field in sec:
            v = sec[field]
            if isinstance(v, bool):
                v = "1" if v else "0"
            S.set_setting(setting_key, str(v))


# --------------------------------------------------------------------------- #
# Lifespan — background loops
# --------------------------------------------------------------------------- #
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialise the DB + seed first-run config at startup rather than at import
    # time, so importing the package (pip install, PyInstaller analysis, tests)
    # has no filesystem side effects.
    init_db()
    _bootstrap()

    # Access gate: engage a password ONLY when the server is reachable off-box
    # (Docker's 0.0.0.0 publish, or --host on a LAN/public IP). Inert on loopback
    # — the Claude hub reverse-proxy and a local 127.0.0.1 user are unaffected.
    # Must run AFTER init_db(): the secrets table doesn't exist before it.
    bind = os.environ.get("TORRENTSAVER_BIND") or os.environ.get("HOST") or "127.0.0.1"
    app.state.access_exposed = auth.bound_offbox(bind)
    app.state.access_pw = auth.ensure_access_secret() if app.state.access_exposed else ""
    app.state.access_token = secrets.token_urlsafe(24) if app.state.access_exposed else ""
    if app.state.access_exposed:
        log.warning("Access gate ENABLED — bound to %s (reachable off-box).", bind)

    tasks: list = []

    def _spawn(modpath: str, fn: str) -> None:
        try:
            mod = __import__(modpath, fromlist=[fn])
            tasks.append(asyncio.create_task(getattr(mod, fn)()))
        except Exception as e:  # noqa: BLE001
            log.info("loop %s.%s not started: %s", modpath, fn, e)

    _spawn("app.scheduler", "startup_tasks")
    _spawn("app.scheduler", "discover_loop")
    _spawn("app.scheduler", "monitor_loop")
    _spawn("app.scheduler", "vpn_loop")
    _spawn("app.scheduler", "demand_loop")
    _spawn("app.scheduler", "deathwatch_loop")
    _spawn("app.scheduler", "reannounce_loop")

    async def _autodetect_qbit() -> None:
        # One-shot: if qBittorrent isn't reachable with the current URL, probe the
        # usual local ports and point at whatever's running. No-op (one cheap
        # version check) when already connected, so it's free on the live install.
        try:
            from app import config, qbit_detect
            from app import settings as S
            from app.qbittorrent import get_client, invalidate_client
            if await get_client().available():
                return
            found = await qbit_detect.detect(extra=[config.get_str("qbit_url")])
            if found and found != config.get_str("qbit_url"):
                S.set_setting("qbit_url", found)
                invalidate_client()
                log.info("Auto-detected qBittorrent at %s", found)
        except Exception:  # noqa: BLE001
            pass
    tasks.append(asyncio.create_task(_autodetect_qbit()))

    log.info("Torrent Saver %s started — %d background task(s)", __version__, len(tasks))
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
app = FastAPI(title="Torrent Saver", version=__version__, lifespan=lifespan)


class AccessGate(BaseHTTPMiddleware):
    """Require the access password when exposed off-box; otherwise transparent.

    Defense-in-depth: gate on BOTH the startup bind (app.state.access_exposed)
    AND the real socket peer — a connection from a loopback client (the hub, the
    local user) is always allowed even on a deliberately-exposed box. We trust
    request.client.host (the real peer), never a spoofable X-Forwarded-For."""

    async def dispatch(self, request: Request, call_next):
        st = request.app.state
        if not getattr(st, "access_exposed", False):
            return await call_next(request)
        peer = request.client.host if request.client else ""
        if auth.is_loopback(peer):
            return await call_next(request)
        root = request.scope.get("root_path", "") or ""
        path = request.url.path
        if path.startswith(root + "/static") or path == root + "/login":
            return await call_next(request)
        cookie = request.cookies.get("ts_session", "")
        if cookie and st.access_token and secrets.compare_digest(cookie, st.access_token):
            return await call_next(request)
        if auth.basic_ok(request.headers.get("Authorization", ""), st.access_pw):
            return await call_next(request)
        return Response("Authentication required.", status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="Torrent Saver"'})


app.add_middleware(AccessGate)


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    root = request.scope.get("root_path", "") or ""
    return (
        "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Sign in · Torrent Saver</title>"
        "<form method=post action='" + root + "/login' "
        "style='max-width:320px;margin:14vh auto;font-family:system-ui;text-align:center'>"
        "<h2>Torrent Saver</h2><p>Enter the access password (user: <b>admin</b>).</p>"
        "<input type=password name=password autofocus "
        "style='width:100%;padding:.6em;margin:.5em 0;box-sizing:border-box'>"
        "<button style='padding:.6em 1.4em'>Sign in</button></form>")


@app.post("/login")
async def login_submit(request: Request, password: str = Form("")):
    st = request.app.state
    root = request.scope.get("root_path", "") or ""
    if getattr(st, "access_exposed", False) and st.access_pw and \
            secrets.compare_digest(password, st.access_pw):
        resp = RedirectResponse(root + "/", status_code=303)
        resp.set_cookie("ts_session", st.access_token, httponly=True, samesite="strict")
        return resp
    return RedirectResponse(root + "/login", status_code=303)


STATIC_DIR = resource_dir() / "static"
with contextlib.suppress(OSError):
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

from app.routers import api, discover, home, settings_router, sources_router, torrents, vpn_router  # noqa: E402

app.include_router(home.router)
app.include_router(discover.router)
app.include_router(torrents.router)
app.include_router(sources_router.router)
app.include_router(settings_router.router)
app.include_router(vpn_router.router)
app.include_router(api.router)
