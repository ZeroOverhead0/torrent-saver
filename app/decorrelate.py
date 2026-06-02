"""Per-install decorrelation.

Many independent installs run the SAME deterministic discovery, so without this
they rescue the same torrents and release them at the same instant — collapsing
the swarm they just saved. Every randomised decision is therefore coloured by a
stable per-install seed (``config.install_seed``):

  * ``rng(salt)``  — a *deterministic* generator for per-torrent decisions that
    must be stable across restarts (a torrent's release-hold, its threshold
    jitter). Same install + same salt → same stream, forever.
  * ``jitter(pct)`` — a *fresh* multiplicative factor for timing, drawn from a
    per-install stream, so loops desync across installs without being perfectly
    periodic on any one.

crc32 (not the salted built-in ``hash``) is used so salts hash identically
across processes/restarts.
"""
from __future__ import annotations

import random
import zlib
from typing import Optional

from app import config

_MASK32 = 0xFFFFFFFF
_live: Optional[random.Random] = None


def rng(salt: str) -> random.Random:
    """Deterministic per-(install, salt) generator. Stable across restarts."""
    seed = config.install_seed() ^ (zlib.crc32(salt.encode("utf-8")) & _MASK32)
    return random.Random(seed)


def jitter(pct: float) -> float:
    """A fresh factor in [1-pct, 1+pct] from a per-install stream (for loop sleeps)."""
    global _live
    if _live is None:
        _live = random.Random(config.install_seed() ^ 0x5BD1E995)
    if pct <= 0:
        return 1.0
    return 1.0 + _live.uniform(-pct, pct)
