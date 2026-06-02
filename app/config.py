"""Configuration: machine-size profiles, VPN presets, defaults and typed getters.

Everything the user can tune lives in the `settings` table as TEXT. This module
owns the canonical default values and coerces them into typed Python objects so
the rest of the app never parses raw strings.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

from app import settings as S

KiB = 1024
MiB = 1024 * 1024
GiB = 1024 * 1024 * 1024
TiB = 1024 * 1024 * 1024 * 1024


# --------------------------------------------------------------------------- #
# Machine-size profiles
# --------------------------------------------------------------------------- #
@dataclass
class MachineProfile:
    key: str
    label: str
    max_disk_bytes: int           # ceiling for the whole rescue library
    reserve_bytes: int            # free space to always leave on the volume
    max_torrents: int             # max simultaneously seeded torrents
    max_torrent_size_bytes: int   # reject any single torrent larger than this
    upload_limit_bytes_s: int     # global upload cap; 0 = unlimited
    ratio_limit: float            # stop seeding past this share ratio (anti-bleed)
    seeding_time_limit_min: int   # stop seeding past this many minutes; -1 = no cap
    max_connections: int          # global connection cap (anti-resource-exhaustion)

    def to_dict(self) -> dict:
        return asdict(self)


PROFILES: dict[str, MachineProfile] = {
    "potato": MachineProfile(
        key="potato", label="Potato (Raspberry Pi / tiny VPS)",
        max_disk_bytes=5 * GiB, reserve_bytes=2 * GiB, max_torrents=5,
        max_torrent_size_bytes=2 * GiB, upload_limit_bytes_s=256 * KiB,
        ratio_limit=1.0, seeding_time_limit_min=60 * 24 * 7, max_connections=80),
    "small": MachineProfile(
        key="small", label="Small (laptop / 256GB SSD)",
        max_disk_bytes=25 * GiB, reserve_bytes=5 * GiB, max_torrents=25,
        max_torrent_size_bytes=8 * GiB, upload_limit_bytes_s=1 * MiB,
        ratio_limit=2.0, seeding_time_limit_min=60 * 24 * 14, max_connections=150),
    "medium": MachineProfile(
        key="medium", label="Medium (desktop / 1TB)",
        max_disk_bytes=150 * GiB, reserve_bytes=20 * GiB, max_torrents=100,
        max_torrent_size_bytes=25 * GiB, upload_limit_bytes_s=5 * MiB,
        ratio_limit=4.0, seeding_time_limit_min=60 * 24 * 30, max_connections=300),
    "large": MachineProfile(
        key="large", label="Large (NAS / 8TB)",
        max_disk_bytes=2 * TiB, reserve_bytes=100 * GiB, max_torrents=500,
        max_torrent_size_bytes=100 * GiB, upload_limit_bytes_s=20 * MiB,
        ratio_limit=8.0, seeding_time_limit_min=-1, max_connections=800),
    "server": MachineProfile(
        key="server", label="Server (seedbox / unmetered)",
        max_disk_bytes=20 * TiB, reserve_bytes=200 * GiB, max_torrents=5000,
        max_torrent_size_bytes=512 * GiB, upload_limit_bytes_s=0,
        ratio_limit=20.0, seeding_time_limit_min=-1, max_connections=2000),
}
DEFAULT_PROFILE = "small"


# --------------------------------------------------------------------------- #
# VPN provider presets (interface-name hints for the kill-switch binding)
# --------------------------------------------------------------------------- #
# These help the UI suggest the right network interface. The actual binding is
# done against whatever interface name the user confirms.
VPN_PROVIDERS = [
    {"key": "mullvad",   "label": "Mullvad (WireGuard)", "iface_hint": "utun,wg", "note": "WireGuard app creates utunN on macOS, wg0 on Linux."},
    {"key": "proton",    "label": "Proton VPN",          "iface_hint": "utun,proton,wg", "note": "WireGuard/OpenVPN; check `ifconfig` for the active tunnel."},
    {"key": "nordvpn",   "label": "NordVPN",             "iface_hint": "utun,nordlynx", "note": "NordLynx (WireGuard) = nordlynx on Linux, utunN on macOS."},
    {"key": "pia",       "label": "Private Internet Access", "iface_hint": "utun,tun,pia", "note": "OpenVPN = tunN; WireGuard = utunN/wgpiaN."},
    {"key": "airvpn",    "label": "AirVPN",              "iface_hint": "utun,tun", "note": "Strong port-forwarding support for seeding."},
    {"key": "wireguard", "label": "Generic WireGuard",   "iface_hint": "wg,utun", "note": "wg-quick names the interface after the config (e.g. wg0)."},
    {"key": "openvpn",   "label": "Generic OpenVPN",     "iface_hint": "tun,utun", "note": "Usually tun0 on Linux, utunN on macOS."},
    {"key": "other",     "label": "Other / manual",      "iface_hint": "", "note": "Enter the interface name manually below."},
]


# --------------------------------------------------------------------------- #
# Default settings (all TEXT in the DB)
# --------------------------------------------------------------------------- #
DEFAULT_SETTINGS: dict[str, str] = {
    # Master switches
    "paused": "0",                       # 1 = stop all background activity
    "legal_mode": "1",                   # 1 = only rescue from legal/whitelisted sources
    "auto_rescue": "0",                  # 1 = scheduler may add torrents unattended; 0 = queue only

    # Machine profile
    "machine_profile": DEFAULT_PROFILE,  # potato|small|medium|large|server|custom
    # Custom profile fields (used only when machine_profile == 'custom')
    "custom_max_disk_gb": "25",
    "custom_reserve_gb": "5",
    "custom_max_torrents": "25",
    "custom_max_torrent_size_gb": "8",
    "custom_upload_limit_kbps": "1024",  # 0 = unlimited
    "custom_ratio_limit": "2.0",
    "custom_seeding_time_limit_min": "20160",  # 14 days; -1 = no cap
    "custom_max_connections": "150",

    # Discovery / endangerment
    "endangered_max_seeders": "5",       # a torrent is "endangered" at <= this many seeders
    "min_seeders_to_rescue": "1",        # need >=1 seeder to actually download it
    "max_redundancy": "2",               # skip if >= this many healthy near-duplicates exist
    "redundancy_healthy_seeders": "10",  # a duplicate is "healthy" at >= this many seeders
    "dedup_title_similarity": "0.82",    # 0..1 token-set ratio to treat two titles as the same thing
    "dedup_size_tolerance": "0.10",      # +/- fraction on size to treat as the same content
    "scan_interval_min": "60",
    "monitor_interval_min": "15",
    "tracker_scrape_enabled": "1",       # enrich seeder counts via tracker scrape
    "min_endangerment_to_queue": "35",   # 0..100 floor before a candidate is queued

    # Rescue scope — how far down the "dying" scale to reach
    "require_recoverable": "1",          # 1 = skip TRULY DEAD (0 seeders AND no webseed — can't download). 0 = attempt them as long-shots.
    "min_download_history": "0",         # require tracker 'downloaded' >= N for swarm torrents (filters never-seeded junk); 0 = off; webseed torrents are exempt

    # Sources (per-source enable + cadence)
    "src_internet_archive_enabled": "1",
    "src_academic_torrents_enabled": "0",  # AT serves a browser challenge to bots; best-effort only
    "src_linuxtracker_enabled": "1",
    "src_prowlarr_enabled": "0",         # off by default (can index non-legal content)
    "src_manual_enabled": "1",
    "prowlarr_url": "",                  # e.g. http://127.0.0.1:9696
    "prowlarr_queries": "",              # comma-separated search terms (Prowlarr has no browse-all)
    "prowlarr_trust_legal": "0",         # 1 = treat your Prowlarr indexers as legal under legal-mode
    "internet_archive_query": "mediatype:software AND format:\"Archive BitTorrent\"",
    "academic_torrents_categories": "",  # blank = all
    "manual_entries": "[]",              # JSON list of {value, legal, added}

    # Demand-seeding mode (for users with big upload)
    "demand_seed_enabled": "0",
    "demand_min_upload_mbps": "50",      # only engage demand-seeding above this upload speed
    "demand_max_torrents": "20",
    "demand_min_seeders": "50",          # "in demand" = healthy + many leechers
    "demand_min_leechers": "20",

    # VPN / kill-switch
    "vpn_required": "1",                 # refuse to seed unrestricted content without a verified VPN
    "vpn_provider": "wireguard",
    "vpn_interface": "",                 # confirmed tunnel interface (e.g. utun4, wg0)
    "vpn_killswitch": "1",               # bind qBittorrent to the VPN interface
    "vpn_baseline_ip": "",               # your real ISP IP (set once); used for leak detection
    "vpn_check_interval_min": "5",

    # qBittorrent connection
    "qbit_url": "http://127.0.0.1:8080",
    "qbit_username": "admin",
    "qbit_category": "torrent-saver",
    "qbit_save_path": "",                # blank = qBittorrent default

    # Rescue lifecycle (find → revive → remember → resurrect)
    "deathwatch_enabled": "1",           # re-scrape known torrents; catch ones sliding toward death
    "deathwatch_interval_min": "360",    # how often to re-check the watchlist
    "deathwatch_alert_seeders": "2",     # alert/act when a watched torrent drops to <= this
    "reannounce_enabled": "1",           # re-announce complete torrents to trackers/DHT (revive the swarm)
    "reannounce_interval_min": "30",
    "metadata_ark_enabled": "1",         # save .torrent + metadata to disk so the map survives the data
    "resurrect_search_enabled": "0",     # for truly-dead torrents, search indexers for a downloadable twin

    # Graduation — let a rescued torrent go once its swarm has FULLY recovered.
    # Honest + staggered so a fleet of installs never releases in lockstep.
    "graduate_enabled": "1",             # release (delete + free disk) torrents the swarm no longer needs us for
    "graduate_seeders": "10",            # base healthy-seeder bar
    "graduate_seeders_jitter": "6",      # per-install random add → effective bar 10-16 (desyncs WHO releases)
    "graduate_window_min": "360",        # health must hold for this long (6h) before release
    "graduate_min_samples": "6",         # ...across at least this many monitor samples
    "graduate_max_decline_per_hr": "1.0",# block release if the swarm is sliding (seeders/hr)
    "graduate_require_demand": "1",      # discount silent seeders (no leechers) — they may be fellow rescuers
    "graduate_hold_max_s": "21600",      # randomised 0-6h hold before release (desyncs WHEN)
    "graduate_hard_seeders": "40",       # overwhelming health → release regardless of jitter/hold
    "graduate_min_tenure_min": "1440",   # never graduate a torrent rescued < 24h ago
    "seeder_history_enabled": "1",       # record per-cycle seeder counts (powers honest graduation)
    "swarm_saturation_seeders": "4",     # at rescue time, skip if the live swarm already has >= this many

    # Decorrelation — a stable per-install random seed colours every randomised
    # decision so independent installs don't dogpile + release in lockstep.
    "install_seed": "",                  # auto-generated once on first use
    "loop_jitter_pct": "0.20",           # ±20% jitter on every background-loop sleep
    "startup_spread_s": "300",           # one-time random boot phase offset (0-300s)

    # Hardening
    "harden_on_start": "1",              # apply qBit lockdown prefs on startup
    "disable_dht_for_private": "1",
    "global_max_ratio_enforce": "1",
    "drop_suspicious_trackers": "1",     # reject magnets whose only trackers look abusive
}


# --------------------------------------------------------------------------- #
# Typed getters
# --------------------------------------------------------------------------- #
def _raw(key: str) -> Optional[str]:
    return S.get_setting(key, DEFAULT_SETTINGS.get(key))


def get_bool(key: str) -> bool:
    v = _raw(key)
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(float(_raw(key)))
    except (TypeError, ValueError):
        return default


def get_float(key: str, default: float = 0.0) -> float:
    try:
        return float(_raw(key))
    except (TypeError, ValueError):
        return default


def get_str(key: str, default: str = "") -> str:
    v = _raw(key)
    return v if v is not None else default


def install_seed() -> int:
    """A stable per-install 63-bit seed, generated + persisted on first use.

    Every randomised decorrelation decision derives a stream from this, so two
    independent installs make different choices (and don't dogpile or release in
    lockstep) — while a single install stays self-consistent across restarts."""
    raw = get_str("install_seed")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    import secrets
    val = secrets.randbits(63)
    S.set_setting("install_seed", str(val))
    return val


# Convenience semantic accessors -------------------------------------------- #
def is_paused() -> bool:
    return get_bool("paused")


def legal_mode() -> bool:
    return get_bool("legal_mode")


def machine_profile() -> MachineProfile:
    key = get_str("machine_profile", DEFAULT_PROFILE)
    if key == "custom":
        return MachineProfile(
            key="custom", label="Custom",
            max_disk_bytes=int(get_float("custom_max_disk_gb", 25) * GiB),
            reserve_bytes=int(get_float("custom_reserve_gb", 5) * GiB),
            max_torrents=get_int("custom_max_torrents", 25),
            max_torrent_size_bytes=int(get_float("custom_max_torrent_size_gb", 8) * GiB),
            upload_limit_bytes_s=get_int("custom_upload_limit_kbps", 1024) * KiB,
            ratio_limit=get_float("custom_ratio_limit", 2.0),
            seeding_time_limit_min=get_int("custom_seeding_time_limit_min", 20160),
            max_connections=get_int("custom_max_connections", 150),
        )
    return PROFILES.get(key, PROFILES[DEFAULT_PROFILE])


def qbit_password() -> Optional[str]:
    return S.get_secret("qbit_password")


def prowlarr_api_key() -> Optional[str]:
    return S.get_secret("prowlarr_api_key")
