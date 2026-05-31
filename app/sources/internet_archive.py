"""Internet Archive — the flagship legal source.

archive.org hosts millions of public-domain / openly-licensed items, most served
as BitTorrent with archive.org web-seeds. A huge long tail of them is barely
seeded — exactly what this app exists to rescue. The advancedsearch API gives us
identifiers + sizes; the scanner then downloads each item's `_archive.torrent`,
reads the real infohash + trackers, and scrapes the trackers for live seeders.
"""
from __future__ import annotations

from typing import List

from app import config
from app.models import Candidate
from app.sources.base import FetchContext, Source, register

ADVANCED_SEARCH = "https://archive.org/advancedsearch.php"


@register
class InternetArchive(Source):
    name = "internet_archive"
    enabled_key = "src_internet_archive_enabled"
    kind = "api"
    legal = True
    label = "Internet Archive"

    async def fetch(self, ctx: FetchContext) -> List[Candidate]:
        query = config.get_str(
            "internet_archive_query",
            "mediatype:(texts OR audio OR movies) AND format:(Archive BitTorrent)")
        params = [
            ("q", query),
            ("fl[]", "identifier"),
            ("fl[]", "title"),
            ("fl[]", "item_size"),
            ("fl[]", "mediatype"),
            ("rows", str(ctx.limit)),
            ("page", "1"),
            ("output", "json"),
        ]
        resp = await ctx.client.get(ADVANCED_SEARCH, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        docs = data.get("response", {}).get("docs", [])
        out: List[Candidate] = []
        for d in docs:
            ident = d.get("identifier")
            if not ident:
                continue
            title = d.get("title") or ident
            if isinstance(title, list):
                title = title[0] if title else ident
            size = int(d.get("item_size") or 0)
            torrent_url = f"https://archive.org/download/{ident}/{ident}_archive.torrent"
            out.append(Candidate(
                infohash="",                       # filled by the scanner from the .torrent
                name=str(title),
                source=self.name,
                torrent_url=torrent_url,
                size_bytes=size,
                category=str(d.get("mediatype") or ""),
                legal=True,
                raw={"identifier": ident},
            ))
        return out
