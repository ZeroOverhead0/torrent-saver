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
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import __version__
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
