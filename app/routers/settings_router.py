"""Settings — every customisation knob, the machine profile, and qBittorrent creds."""
from __future__ import annotations

from fastapi import APIRouter, Request

from app import config
from app import settings as S
from app.config import PROFILES
from app.qbittorrent import QBitError, get_client, invalidate_client
from app.routers._common import redirect, render

router = APIRouter()

# Checkbox-style settings (absent in the form == off).
BOOL_KEYS = [
    "legal_mode", "auto_rescue", "tracker_scrape_enabled", "demand_seed_enabled",
    "vpn_required", "vpn_killswitch", "harden_on_start", "drop_suspicious_trackers",
    "require_recoverable",
    "deathwatch_enabled", "reannounce_enabled", "metadata_ark_enabled",
    "resurrect_search_enabled",
]

# Free-text / numeric settings copied straight from the form when present.
STR_KEYS = [
    "machine_profile",
    "custom_max_disk_gb", "custom_reserve_gb", "custom_max_torrents",
    "custom_max_torrent_size_gb", "custom_upload_limit_kbps", "custom_ratio_limit",
    "custom_seeding_time_limit_min", "custom_max_connections",
    "endangered_max_seeders", "min_seeders_to_rescue", "max_redundancy",
    "redundancy_healthy_seeders", "dedup_title_similarity", "dedup_size_tolerance",
    "scan_interval_min", "monitor_interval_min", "min_endangerment_to_queue",
    "min_download_history",
    "deathwatch_interval_min", "deathwatch_alert_seeders", "reannounce_interval_min",
    "demand_min_upload_mbps", "demand_max_torrents", "demand_min_seeders",
    "demand_min_leechers", "vpn_check_interval_min",
    "qbit_url", "qbit_username", "qbit_category", "qbit_save_path",
]


@router.get("/settings")
async def settings_page(request: Request, msg: str = ""):
    return render(request, "settings.html",
                  msg=msg,
                  s=config,                    # typed getters available in template
                  all_s=S.all_settings(),
                  profiles=PROFILES,
                  profile=config.machine_profile(),
                  qbit_password_set=bool(config.qbit_password()))


@router.post("/settings")
async def save_settings(request: Request):
    form = await request.form()
    updates = {}
    for key in STR_KEYS:
        if key in form:
            updates[key] = str(form.get(key))
    for key in BOOL_KEYS:
        updates[key] = "1" if form.get(key) else "0"
    S.set_settings(updates)

    # Secrets only when a new value is supplied.
    pw = (form.get("qbit_password") or "").strip()
    if pw:
        S.set_secret("qbit_password", pw)
    if form.get("clear_qbit_password"):
        S.clear_secret("qbit_password")

    invalidate_client()   # creds/url may have changed
    return redirect(request, "/settings", "Settings saved")


@router.post("/settings/qbit-test")
async def qbit_test(request: Request):
    client = get_client()
    try:
        await client.login()
        ver = await client.version()
        msg = f"Connected to qBittorrent {ver}"
    except QBitError as e:
        msg = f"qBittorrent connection failed: {e}"
    return redirect(request, "/settings", msg)
