"""Source registry — pluggable endangered-torrent discovery connectors.

Each Source yields `Candidate` objects. Some sources report seeder counts
directly (Prowlarr); others only point at a .torrent/identifier and the scanner
enriches them (download metadata + tracker-scrape) afterwards. A source declares
whether it is inherently *legal* (whitelisted) so the legal-only filter can act
before any download happens.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import List

import httpx

from app import config
from app.models import Candidate


@dataclass
class FetchContext:
    client: httpx.AsyncClient
    limit: int = 50                     # max candidates a source should return per scan
    legal_only: bool = True
    max_seeders_endangered: int = 5


class Source(abc.ABC):
    name: str = ""
    enabled_key: str = ""               # settings key for the on/off toggle
    kind: str = "api"                   # api | scrape | indexer | manual
    legal: bool = False                 # True = inherently legal/whitelisted content
    label: str = ""                     # human label for the UI

    def is_enabled(self) -> bool:
        return config.get_bool(self.enabled_key) if self.enabled_key else False

    @abc.abstractmethod
    async def fetch(self, ctx: FetchContext) -> List[Candidate]:
        """Return a list of Candidate. May raise — the scanner records the error
        against the source and continues with the others."""
        raise NotImplementedError


SOURCES: dict = {}


def register(cls):
    """Class decorator — add a Source subclass to the registry by name."""
    SOURCES[cls.name] = cls
    return cls


def get_source(name: str):
    cls = SOURCES.get(name)
    return cls() if cls else None


def all_sources() -> list:
    return [cls() for cls in SOURCES.values()]


def enabled_sources() -> list:
    return [s for s in all_sources() if s.is_enabled()]
