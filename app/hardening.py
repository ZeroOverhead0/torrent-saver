"""Hardening — resist hacks and forced-seeding abuse.

Two jobs:
  1. Lock down qBittorrent's preferences (auth, CSRF/clickjacking, anonymous
     mode, no UPnP hole-punching, encryption preferred) and cap resource use so
     a hostile swarm can't make the machine bleed bandwidth or connections
     forever (share-ratio + seeding-time caps, global upload/connection caps).
  2. Validate every torrent before it's added — reject oversized payloads and
     magnets whose trackers look abusive — so the app can't be tricked into
     hauling something it shouldn't.
"""
from __future__ import annotations

import ipaddress
import urllib.parse
from typing import Optional

from app.config import MachineProfile


# --------------------------------------------------------------------------- #
# qBittorrent lockdown preferences
# --------------------------------------------------------------------------- #
def recommended_preferences(profile: MachineProfile, *,
                            vpn_interface: str = "",
                            disable_dht_for_private: bool = True) -> dict:
    """Return the qBittorrent preference dict to apply for a hardened, bounded
    seeding setup under the given machine profile."""
    prefs: dict = {
        # --- WebUI / auth security -------------------------------------- #
        "bypass_local_auth": False,
        "bypass_auth_subnet_whitelist_enabled": False,
        "web_ui_clickjacking_protection_enabled": True,
        "web_ui_csrf_protection_enabled": True,
        "web_ui_host_header_validation_enabled": True,
        "web_ui_secure_cookie_enabled": True,
        "web_ui_max_auth_fail_count": 5,
        "web_ui_ban_duration": 3600,
        "web_ui_session_timeout": 3600,

        # --- Privacy / anti-fingerprint --------------------------------- #
        "anonymous_mode": True,
        "encryption": 0,                 # 0 = prefer encryption (don't force-off peers)

        # --- No automatic hole-punching (rely on VPN port-forward) ------ #
        "upnp": False,

        # --- Public-swarm discovery: needed to revive near-dead torrents - #
        # (find the handful of remaining peers / re-seed via DHT).
        "dht": True,
        "pex": True,
        "lsd": True,

        # --- Anti forced-seeding: cap ratio + seeding time -------------- #
        "max_ratio_enabled": True,
        "max_ratio": float(profile.ratio_limit),
        "max_ratio_act": 0,              # 0 = pause torrent when ratio hit (stop bleeding)

        # --- Resource caps ---------------------------------------------- #
        "max_connec": profile.max_connections,
        "max_connec_per_torrent": max(20, profile.max_connections // 8),
        "max_uploads": max(4, profile.max_torrents // 4),
        "max_uploads_per_torrent": 4,
        "queueing_enabled": True,
        "max_active_torrents": profile.max_torrents,
        "max_active_uploads": profile.max_torrents,
        "max_active_downloads": max(3, profile.max_torrents // 5),
        "dont_count_slow_torrents": True,
    }

    # Seeding-time cap (forced-seed bound). -1 means "no cap".
    if profile.seeding_time_limit_min and profile.seeding_time_limit_min > 0:
        prefs["max_seeding_time_enabled"] = True
        prefs["max_seeding_time"] = int(profile.seeding_time_limit_min)
        prefs["max_seeding_time_act"] = 0
    else:
        prefs["max_seeding_time_enabled"] = False

    # Global upload cap (bytes/s; 0 = unlimited).
    if profile.upload_limit_bytes_s and profile.upload_limit_bytes_s > 0:
        prefs["up_limit"] = int(profile.upload_limit_bytes_s)
    else:
        prefs["up_limit"] = 0

    # Bind to the VPN interface (kill-switch) when one is provided.
    if vpn_interface:
        prefs["current_network_interface"] = vpn_interface
        prefs["current_interface_address"] = ""

    return prefs


async def apply_hardening(qbit_client, profile: MachineProfile, *,
                          vpn_interface: str = "") -> bool:
    prefs = recommended_preferences(profile, vpn_interface=vpn_interface)
    return await qbit_client.set_preferences(prefs)


# --------------------------------------------------------------------------- #
# Input validation (before adding a torrent)
# --------------------------------------------------------------------------- #
def is_suspicious_tracker(tracker_url: str) -> bool:
    """Flag trackers that point at local/loopback/private hosts or use a
    non-BitTorrent scheme — classic forced-seed / SSRF bait."""
    try:
        p = urllib.parse.urlparse(tracker_url)
    except ValueError:
        return True
    if p.scheme.lower() not in ("http", "https", "udp"):
        return True
    host = p.hostname or ""
    if not host:
        return True
    if host in ("localhost",) or host.endswith(".local"):
        return True
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_reserved:
            return True
    except ValueError:
        pass                                            # hostname, not a literal IP — fine
    return False


def validate_candidate(size_bytes: int, trackers: list, profile: MachineProfile, *,
                       drop_suspicious: bool = True) -> tuple:
    """Return (ok: bool, reason: str). Reject oversized torrents and, when every
    tracker is suspicious, the torrent itself."""
    if profile.max_torrent_size_bytes and size_bytes and size_bytes > profile.max_torrent_size_bytes:
        return False, "exceeds per-torrent size cap for this machine profile"
    if drop_suspicious and trackers:
        if all(is_suspicious_tracker(t) for t in trackers):
            return False, "all trackers look abusive (loopback/private/non-BT)"
    return True, "ok"
