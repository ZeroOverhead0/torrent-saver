"""Plain dataclasses shared across modules (no DB / network deps)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Candidate:
    """A torrent discovered from a source, before any rescue decision."""
    infohash: str                       # lowercase 40-char SHA1 hex
    name: str
    source: str
    magnet: Optional[str] = None
    torrent_url: Optional[str] = None
    size_bytes: int = 0
    seeders: int = 0
    leechers: int = 0
    completed: int = 0                  # tracker's num_complete (lifetime downloads)
    category: str = ""
    legal: bool = False
    trackers: list = field(default_factory=list)
    webseeds: list = field(default_factory=list)   # url-list (BEP 19) — downloadable w/o peers
    endangerment: float = 0.0
    redundancy: int = 0
    rescuable: bool = True              # False when seeders == 0 (undownloadable)
    worth: Optional[int] = None         # 0-100 LLM "worth rescuing" score; None = not judged
    worth_reason: str = ""              # short LLM rationale for the worth score
    raw: dict = field(default_factory=dict)

    def normalised_hash(self) -> str:
        return (self.infohash or "").strip().lower()


@dataclass
class TrackedTorrent:
    """A torrent we have added to qBittorrent and are (or were) seeding."""
    infohash: str
    name: str
    source: str = ""
    size_bytes: int = 0
    category: str = ""
    legal: bool = False
    mode: str = "rescue"                # rescue | demand
    qbit_state: str = ""
    seeders: int = 0
    leechers: int = 0
    ratio: float = 0.0
    uploaded_bytes: int = 0
    progress: float = 0.0
    endangerment: float = 0.0
    added_at: float = field(default_factory=time.time)
    last_checked: float = 0.0
    evicted_at: Optional[float] = None
    evict_reason: str = ""


@dataclass
class VpnStatus:
    connected: bool = False
    interface: str = ""
    public_ip: str = ""
    killswitch_engaged: bool = False    # qBit bound to the VPN interface
    leaking: bool = False               # public IP matches the configured baseline ISP IP
    leak_check_possible: bool = True    # False when we can't verify a leak (no baseline / no public IP)
    checked_at: float = field(default_factory=time.time)
    detail: str = ""

    @property
    def safe_to_seed(self) -> bool:
        """True only when a VPN is up, the kill-switch is engaged, AND we could
        actually verify there's no leak. Fails CLOSED: if leak detection can't
        run (no baseline ISP IP, or no public IP), we never claim it's safe."""
        return (self.connected and self.killswitch_engaged
                and self.leak_check_possible and not self.leaking)


@dataclass
class RescueDecision:
    candidate: Candidate
    rescue: bool
    reason: str = ""
    projected_total_bytes: int = 0      # running library size if this is rescued


@dataclass
class EvictionDecision:
    infohash: str
    name: str
    evict: bool
    reason: str = ""
    freed_bytes: int = 0
