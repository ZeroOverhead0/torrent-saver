"""Auto-detect a running qBittorrent so a new user doesn't have to hand-enter
the Web UI URL.

We probe the usual local addresses for the qBittorrent Web API. A 200 means it's
reachable (auth bypassed for localhost); a 403 means qBittorrent is there but
wants a password — either way we've *found* it and can point the app at it.
"""
from __future__ import annotations

from typing import List, Optional

import httpx

# Probed in order. The currently-configured URL is tried first by the caller.
CANDIDATE_URLS = [
    "http://127.0.0.1:8080",
    "http://localhost:8080",
    "http://127.0.0.1:8081",
    "http://127.0.0.1:9090",
]


async def _is_qbittorrent(url: str, timeout: float = 2.0) -> bool:
    try:
        async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=timeout) as c:
            r = await c.get("/api/v2/app/version")
    except httpx.HTTPError:
        return False
    # 200 = reachable (localhost bypass); 403 = present but auth-gated.
    return r.status_code in (200, 403)


async def detect(extra: Optional[List[str]] = None) -> Optional[str]:
    """Return the first URL that answers the qBittorrent Web API, or None."""
    seen = set()
    for url in (extra or []) + CANDIDATE_URLS:
        u = (url or "").rstrip("/")
        if not u or u in seen:
            continue
        seen.add(u)
        if await _is_qbittorrent(u):
            return u
    return None
