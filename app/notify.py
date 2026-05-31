"""Activity logging — always to the local events table, optionally to Obsidian.

Every meaningful action (scan, rescue, eviction, VPN state change, hardening,
errors) is recorded in the SQLite `events` table for the UI. If the Claude
stack's shared obsidian_logger is reachable, a one-line signal is also mirrored
to today's daily note. Fully offline-safe.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from app.db import log_event

log = logging.getLogger("torrentsaver.notify")

_SUBAPP = "torrent-saver"
_obs = None
_tried = False

_CANDIDATE_SHARED_DIRS = [
    os.environ.get("CLAUDE_SHARED_DIR", ""),
    str(Path.home() / "Library" / "Application Support" / "Claude" / ".shared"),
    str(Path.home() / "Documents" / "Claude" / ".shared"),
]


def _load_obsidian():
    global _obs, _tried
    if _tried:
        return _obs
    _tried = True
    for d in _CANDIDATE_SHARED_DIRS:
        if d and os.path.isdir(d) and d not in sys.path:
            sys.path.insert(0, d)
    try:
        import obsidian_logger  # type: ignore
        _obs = obsidian_logger
    except Exception:
        _obs = None
    return _obs


def event(kind: str, message: str, *, level: str = "info", infohash: str = "",
          to_obsidian: bool = False) -> None:
    """Record an event. Set to_obsidian=True for noteworthy signals only."""
    log_event(kind, message, level=level, infohash=infohash)
    if level in ("warn", "error"):
        log.warning("%s: %s", kind, message)
    else:
        log.info("%s: %s", kind, message)
    if to_obsidian:
        obs = _load_obsidian()
        if obs is not None:
            try:
                obs.signal(_SUBAPP, message, severity=level)
            except Exception:
                pass


# Convenience wrappers ------------------------------------------------------- #
def info(kind: str, message: str, **kw):
    event(kind, message, level="info", **kw)


def warn(kind: str, message: str, **kw):
    event(kind, message, level="warn", **kw)


def error(kind: str, message: str, **kw):
    event(kind, message, level="error", to_obsidian=True, **kw)


def decision(kind: str, message: str, **kw):
    event(kind, message, level="decision", to_obsidian=True, **kw)
