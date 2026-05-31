"""Academic Torrents — research datasets that beg to be re-seeded (best-effort).

academictorrents.com is the ideal mission fit: huge scholarly datasets that go
unseeded once a paper's moment passes. Unfortunately the site fronts its listings
with a JavaScript "checking your browser" challenge, so a plain HTTP client can't
enumerate them. Rather than ship fake data, this source fails loudly with a clear
reason (it surfaces on the Sources page) and stays disabled by default. To rescue
a specific AT dataset, copy its magnet into the Manual watchlist.
"""
from __future__ import annotations

from typing import List

from app.models import Candidate
from app.sources.base import FetchContext, Source, register

BROWSE_URL = "https://academictorrents.com/browse.php?sort=seeders&order=asc"


@register
class AcademicTorrents(Source):
    name = "academic_torrents"
    enabled_key = "src_academic_torrents_enabled"
    kind = "scrape"
    legal = True
    label = "Academic Torrents (datasets)"

    async def fetch(self, ctx: FetchContext) -> List[Candidate]:
        resp = await ctx.client.get(
            BROWSE_URL, timeout=30,
            headers={"User-Agent": "Mozilla/5.0 TorrentSaver"})
        body = resp.text.lower()
        if "checking" in body and "browser" in body:
            raise RuntimeError(
                "Academic Torrents serves a browser challenge to automated "
                "clients — listings can't be fetched headlessly. Add specific "
                "AT magnets via the Manual watchlist instead.")
        # If the challenge is ever lifted, parse infohash links here.
        import re
        out: List[Candidate] = []
        seen: set = set()
        for m in re.finditer(r'/details/([0-9a-fA-F]{40})', resp.text):
            ih = m.group(1).lower()
            if ih in seen:
                continue
            seen.add(ih)
            out.append(Candidate(infohash=ih, name=ih, source=self.name,
                                 torrent_url=f"https://academictorrents.com/download/{ih}.torrent",
                                 legal=True, category="dataset"))
            if len(out) >= ctx.limit:
                break
        if not out:
            raise RuntimeError("Academic Torrents returned no parseable torrents.")
        return out
