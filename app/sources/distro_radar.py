"""Distro radar — legal, in-demand Linux distro torrents for demand-seeding.

The opposite end of the mission from rescue: surface CURRENTLY POPULAR legal
content (freshly-released / most-active Linux distros) so a user who wants to
"help the network" can pour upload at the torrents with the most leechers
waiting. Every candidate here is inherently legal:
  * the LinuxTracker active listing (reuses the rescue source's scrape), plus
  * an optional user-curated list of distro magnets.
Candidates carry no live counts — the demand seeder tracker-scrapes them to rank
by leechers (demand). Best-effort: returns what it can, never crashes the loop.
"""
from __future__ import annotations

import html
import json
from typing import List

from app import config
from app.models import Candidate
from app.scrape import build_magnet, parse_magnet
from app.sources.base import FetchContext, Source, register
from app.sources.linuxtracker import (LISTING_URL, PUBLIC_TRACKERS, _DETAIL_RE,
                                       _TAG_RE)


@register
class DistroRadar(Source):
    name = "distro_radar"
    enabled_key = "src_distro_radar_enabled"
    kind = "scrape"
    legal = True
    label = "Distro radar (popular legal distros)"

    async def fetch(self, ctx: FetchContext) -> List[Candidate]:
        out: List[Candidate] = []
        seen: set = set()

        # 1) Curated official-distro magnets (user-extensible; always legal).
        for entry in self._curated():
            try:
                meta = parse_magnet(entry)
            except Exception:
                continue
            ih = meta["infohash"]
            if ih in seen:
                continue
            seen.add(ih)
            out.append(Candidate(
                infohash=ih, name=meta.get("name") or "(distro)", source=self.name,
                magnet=entry, trackers=meta.get("trackers") or list(PUBLIC_TRACKERS),
                legal=True, category="linux"))

        # 2) LinuxTracker active listing — many distros, the popular ones included.
        try:
            resp = await ctx.client.get(
                LISTING_URL, timeout=30,
                headers={"User-Agent": "Mozilla/5.0 TorrentSaver"})
            resp.raise_for_status()
            for m in _DETAIL_RE.finditer(resp.text):
                ih = m.group(1).lower()
                if ih in seen:
                    continue
                seen.add(ih)
                title = html.unescape(_TAG_RE.sub("", m.group(2))).strip()
                if not title:
                    continue
                out.append(Candidate(
                    infohash=ih, name=title, source=self.name,
                    magnet=build_magnet(ih, title, PUBLIC_TRACKERS),
                    trackers=list(PUBLIC_TRACKERS), legal=True, category="linux"))
                if len(out) >= ctx.limit:
                    break
        except Exception:  # noqa: BLE001
            pass
        return out[:ctx.limit]

    @staticmethod
    def _curated() -> list:
        """Parse the user's extra distro magnets (JSON list of magnet: strings)."""
        try:
            data = json.loads(config.get_str("distro_radar_torrents") or "[]")
        except Exception:  # noqa: BLE001
            return []
        return [x for x in data if isinstance(x, str) and x.startswith("magnet:")]
