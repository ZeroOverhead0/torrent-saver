"""Source registry. Importing a source module registers it via @register."""
from __future__ import annotations

import logging

from app.sources.base import (FetchContext, Source, SOURCES,  # noqa: F401
                              all_sources, enabled_sources, get_source, register)

log = logging.getLogger("torrentsaver.sources")

# Import each source so it self-registers. Guarded so a source whose optional
# deps/config are missing doesn't break the others.
for _mod in ("internet_archive", "linuxtracker", "academic_torrents",
             "prowlarr", "manual"):
    try:
        __import__(f"app.sources.{_mod}")
    except Exception as e:  # noqa: BLE001
        log.info("source %s not loaded: %s", _mod, e)
