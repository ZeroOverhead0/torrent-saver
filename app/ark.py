"""Metadata ark — save the *map* to a torrent so it survives even if the data doesn't.

For every rescued torrent we write `data/ark/<infohash>/meta.json` (name, magnet,
trackers, webseeds, source, size, endangerment) and, best-effort, the `.torrent`
file itself. If a torrent later dies for good, the ark still knows what it was and
how to re-find or re-create it — a dying torrent's deepest fear is being forgotten,
not just unseeded.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import List, Optional

from app.db import DATA_DIR

ARK_DIR = DATA_DIR / "ark"


def _dir_for(infohash: str) -> Path:
    return ARK_DIR / infohash.lower()


def is_archived(infohash: str) -> bool:
    return (_dir_for(infohash) / "meta.json").exists()


def archive_metadata(meta: dict, *, fetch_torrent: bool = True) -> Optional[str]:
    """Persist a torrent's metadata (and .torrent when possible) to the ark.

    `meta` needs at least `infohash` + `name`. Sync (blocking I/O) — call from
    async code via asyncio.to_thread. Returns the ark directory path, or None.
    """
    infohash = (meta.get("infohash") or "").lower()
    if not infohash:
        return None
    d = _dir_for(infohash)
    d.mkdir(parents=True, exist_ok=True)

    record = {
        "infohash": infohash,
        "name": meta.get("name", ""),
        "source": meta.get("source", ""),
        "magnet": meta.get("magnet"),
        "torrent_url": meta.get("torrent_url"),
        "size_bytes": meta.get("size_bytes", 0),
        "trackers": meta.get("trackers") or [],
        "webseeds": meta.get("webseeds") or [],
        "category": meta.get("category", ""),
        "legal": bool(meta.get("legal")),
        "endangerment": meta.get("endangerment", 0),
        "archived_at": time.time(),
    }
    (d / "meta.json").write_text(json.dumps(record, indent=2))

    # Best-effort grab of the actual .torrent so the content is re-creatable.
    if fetch_torrent and meta.get("torrent_url") and not (d / f"{infohash}.torrent").exists():
        try:
            req = urllib.request.Request(meta["torrent_url"],
                                         headers={"User-Agent": "TorrentSaver/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                blob = r.read()
            if blob[:1] == b"d":                       # looks like bencode
                (d / f"{infohash}.torrent").write_bytes(blob)
        except Exception:
            pass
    return str(d)


def list_ark() -> List[dict]:
    """Return every archived record (most recent first)."""
    if not ARK_DIR.exists():
        return []
    out = []
    for sub in ARK_DIR.iterdir():
        mj = sub / "meta.json"
        if mj.exists():
            try:
                rec = json.loads(mj.read_text())
                rec["has_torrent"] = (sub / f"{rec['infohash']}.torrent").exists()
                out.append(rec)
            except Exception:
                continue
    out.sort(key=lambda r: r.get("archived_at", 0), reverse=True)
    return out


def count_ark() -> int:
    if not ARK_DIR.exists():
        return 0
    return sum(1 for sub in ARK_DIR.iterdir() if (sub / "meta.json").exists())
