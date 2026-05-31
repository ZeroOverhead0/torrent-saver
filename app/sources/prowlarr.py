"""Prowlarr — bring-your-own indexer aggregator (optional, gated).

Prowlarr proxies whatever indexers the user has configured and exposes a single
search API with seeder/leecher counts. Because those indexers can include
non-legal content, this source is OFF by default and, under legal-only mode,
contributes nothing unless the user explicitly asserts their indexers are legal
(`prowlarr_trust_legal`). Needs `prowlarr_url` + an API key (stored as a secret).
"""
from __future__ import annotations

from typing import List

from app import config
from app.models import Candidate
from app.scrape import parse_magnet
from app.sources.base import FetchContext, Source, register


@register
class Prowlarr(Source):
    name = "prowlarr"
    enabled_key = "src_prowlarr_enabled"
    kind = "indexer"
    legal = False                       # depends on the user's indexers
    label = "Prowlarr (your indexers)"

    async def fetch(self, ctx: FetchContext) -> List[Candidate]:
        base = config.get_str("prowlarr_url").rstrip("/")
        api_key = config.prowlarr_api_key()
        if not base or not api_key:
            raise RuntimeError("Prowlarr URL or API key not set (Settings → Sources).")

        queries = [q.strip() for q in config.get_str("prowlarr_queries").split(",") if q.strip()]
        if not queries:
            raise RuntimeError(
                "Prowlarr needs at least one search term in 'prowlarr_queries' "
                "(it has no browse-all endpoint).")

        trust_legal = config.get_bool("prowlarr_trust_legal")
        headers = {"X-Api-Key": api_key}
        seen: set = set()
        out: List[Candidate] = []

        for q in queries:
            params = {"query": q, "type": "search", "limit": str(ctx.limit)}
            resp = await ctx.client.get(f"{base}/api/v1/search", params=params,
                                        headers=headers, timeout=45)
            resp.raise_for_status()
            for r in resp.json():
                infohash = (r.get("infoHash") or "").lower()
                magnet = r.get("magnetUrl") or ""
                torrent_url = r.get("downloadUrl") or r.get("guid") or ""
                if not infohash and magnet:
                    try:
                        infohash = parse_magnet(magnet)["infohash"]
                    except Exception:
                        infohash = ""
                key = infohash or magnet or torrent_url
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(Candidate(
                    infohash=infohash,
                    name=r.get("title") or "(untitled)",
                    source=self.name,
                    magnet=magnet or None,
                    torrent_url=torrent_url or None,
                    size_bytes=int(r.get("size") or 0),
                    seeders=int(r.get("seeders") or 0),
                    leechers=int(r.get("leechers") or 0),
                    completed=int(r.get("grabs") or 0),
                    category=str((r.get("categories") or [{}])[0].get("name", "")
                                 if r.get("categories") else ""),
                    legal=trust_legal,
                    raw={"indexer": r.get("indexer"), "indexerId": r.get("indexerId")},
                ))
                if len(out) >= ctx.limit:
                    return out
        return out
