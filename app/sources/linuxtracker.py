"""LinuxTracker — legal Linux distribution ISOs.

linuxtracker.org publishes torrents for hundreds of Linux distros; old/niche
releases are frequently down to a couple of seeders. The site is plain HTML (no
public JSON API), so we scrape the torrents listing for the 40-hex infohash
(it's the `torrent-details` id) plus the title, then hand each candidate to the
scanner for tracker-scrape enrichment. Best-effort: returns [] if the markup
changes rather than crashing the scan.
"""
from __future__ import annotations

import html
import re
from typing import List

from app.models import Candidate
from app.scrape import build_magnet
from app.sources.base import FetchContext, Source, register

LISTING_URL = "https://linuxtracker.org/index.php?page=torrents&active=1"

# Public trackers to attach so the scanner can scrape live seeder counts.
PUBLIC_TRACKERS = [
    "http://tracker.linuxtracker.org:2710/00000000000000000000000000000000/announce",
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
]

_DETAIL_RE = re.compile(
    r'page=torrent-details&(?:amp;)?id=([0-9a-fA-F]{40})"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


@register
class LinuxTracker(Source):
    name = "linuxtracker"
    enabled_key = "src_linuxtracker_enabled"
    kind = "scrape"
    legal = True
    label = "LinuxTracker (distro ISOs)"

    async def fetch(self, ctx: FetchContext) -> List[Candidate]:
        resp = await ctx.client.get(LISTING_URL, timeout=30,
                                    headers={"User-Agent": "Mozilla/5.0 TorrentSaver"})
        resp.raise_for_status()
        body = resp.text
        seen: set = set()
        out: List[Candidate] = []
        for m in _DETAIL_RE.finditer(body):
            infohash = m.group(1).lower()
            if infohash in seen:
                continue
            seen.add(infohash)
            title = html.unescape(_TAG_RE.sub("", m.group(2))).strip()
            if not title:
                continue
            out.append(Candidate(
                infohash=infohash,
                name=title,
                source=self.name,
                magnet=build_magnet(infohash, title, PUBLIC_TRACKERS),
                trackers=list(PUBLIC_TRACKERS),
                legal=True,
                category="linux",
            ))
            if len(out) >= ctx.limit:
                break
        return out
