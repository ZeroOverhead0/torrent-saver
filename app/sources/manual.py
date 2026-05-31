"""Manual watchlist — user-supplied magnets / .torrent URLs / infohashes.

Lets the user point Torrent Saver at a specific endangered torrent to rescue,
regardless of source coverage. Entries are stored as JSON in the settings table.
Each entry carries a user-asserted `legal` flag so it can pass (or be filtered
by) legal-only mode. Magnets are parsed locally; .torrent URLs are enriched by
the scanner.
"""
from __future__ import annotations

import json
import time
from typing import List

from app import settings as S
from app.models import Candidate
from app.scrape import build_magnet, parse_magnet
from app.sources.base import FetchContext, Source, register

_KEY = "manual_entries"


# --------------------------------------------------------------------------- #
# Watchlist storage
# --------------------------------------------------------------------------- #
def list_entries() -> List[dict]:
    try:
        return json.loads(S.get_setting(_KEY, "[]") or "[]")
    except Exception:
        return []


def add_entry(value: str, legal: bool = False) -> dict:
    value = value.strip()
    if not value:
        raise ValueError("empty entry")
    entries = list_entries()
    if any(e["value"] == value for e in entries):
        raise ValueError("already on the watchlist")
    entry = {"value": value, "legal": bool(legal), "added": time.time()}
    entries.append(entry)
    S.set_setting(_KEY, json.dumps(entries))
    return entry


def remove_entry(value: str) -> None:
    entries = [e for e in list_entries() if e["value"] != value]
    S.set_setting(_KEY, json.dumps(entries))


# --------------------------------------------------------------------------- #
# Source
# --------------------------------------------------------------------------- #
def _entry_to_candidate(entry: dict) -> Candidate:
    value = entry["value"].strip()
    legal = bool(entry.get("legal"))
    if value.startswith("magnet:"):
        parsed = parse_magnet(value)
        return Candidate(infohash=parsed["infohash"], name=parsed["name"] or parsed["infohash"],
                         source="manual", magnet=value,
                         trackers=parsed["trackers"], legal=legal)
    if value.startswith("http://") or value.startswith("https://"):
        return Candidate(infohash="", name=value.rsplit("/", 1)[-1] or value,
                         source="manual", torrent_url=value, legal=legal)
    # bare 40-char infohash
    v = value.lower()
    if len(v) == 40 and all(c in "0123456789abcdef" for c in v):
        return Candidate(infohash=v, name=v, source="manual",
                         magnet=build_magnet(v), legal=legal)
    raise ValueError(f"unrecognised manual entry: {value[:60]}")


@register
class Manual(Source):
    name = "manual"
    enabled_key = "src_manual_enabled"
    kind = "manual"
    legal = False                       # per-entry legal flag instead
    label = "Manual watchlist"

    async def fetch(self, ctx: FetchContext) -> List[Candidate]:
        out: List[Candidate] = []
        for entry in list_entries():
            try:
                out.append(_entry_to_candidate(entry))
            except Exception:
                continue
        return out
