"""Find-me-a-body — resurrect a truly-dead torrent from a downloadable twin.

When a torrent is gone (0 seeders, no webseed) the data isn't on its swarm any
more — but the *same content* may live under a different infohash somewhere that
still has seeders or a webseed. This searches the Internet Archive by title for
such a twin, confirms it's really the same thing via the dedup similarity test,
and returns it as a fresh, rescuable candidate.
"""
from __future__ import annotations

from typing import List, Optional

import httpx

from app import dedup
from app.models import Candidate

ADVANCED_SEARCH = "https://archive.org/advancedsearch.php"
_STOP = {"the", "a", "an", "of", "and", "for", "to", "in", "on", "with"}


def _query_terms(name: str, limit: int = 5) -> str:
    """Pick the most distinctive title tokens for an IA title search."""
    tokens = [t for t in dedup.normalise_title(name) if t not in _STOP]
    tokens = sorted(tokens, key=len, reverse=True)[:limit]
    return " ".join(tokens)


async def find_body(name: str, infohash: str = "",
                    client: Optional[httpx.AsyncClient] = None,
                    sim_threshold: float = 0.8) -> List[Candidate]:
    """Return downloadable Internet Archive twins of `name` (best matches first).

    Excludes the original infohash. Each twin is a Candidate with a torrent_url
    the scanner can enrich + scrape.
    """
    terms = _query_terms(name)
    if not terms:
        return []
    own = (infohash or "").lower()
    close = client is None
    client = client or httpx.AsyncClient(follow_redirects=True)
    try:
        params = [
            ("q", f'title:({terms}) AND format:"Archive BitTorrent"'),
            ("fl[]", "identifier"), ("fl[]", "title"), ("fl[]", "item_size"),
            ("rows", "20"), ("output", "json"),
        ]
        resp = await client.get(ADVANCED_SEARCH, params=params, timeout=30)
        resp.raise_for_status()
        docs = resp.json().get("response", {}).get("docs", [])
    except Exception:
        return []
    finally:
        if close:
            await client.aclose()

    out: List[Candidate] = []
    for d in docs:
        ident = d.get("identifier")
        title = d.get("title") or ident
        if isinstance(title, list):
            title = title[0] if title else ident
        if not ident:
            continue
        if dedup.title_similarity(name, str(title)) < sim_threshold:
            continue
        cand = Candidate(
            infohash="",                       # enriched from the .torrent later
            name=str(title), source="resurrect",
            torrent_url=f"https://archive.org/download/{ident}/{ident}_archive.torrent",
            size_bytes=int(d.get("item_size") or 0), legal=True,
            category="resurrected", raw={"identifier": ident, "twin_of": own})
        out.append(cand)
    # Best title match first.
    out.sort(key=lambda c: dedup.title_similarity(name, c.name), reverse=True)
    return out
