"""Optional LLM tie-breaker for ambiguous dedup decisions.

If the Claude stack's shared OllamaBroker is reachable it can judge whether two
torrent titles refer to the same content when the token heuristics are on the
fence. Entirely optional and offline-safe: if the broker isn't present this
module degrades to "no opinion" and the heuristic decision stands.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger("torrentsaver.broker")

_CALLER = "torrentsaver"
_broker = None
_tried = False

_CANDIDATE_SHARED_DIRS = [
    os.environ.get("CLAUDE_SHARED_DIR", ""),
    str(Path.home() / "Library" / "Application Support" / "Claude" / ".shared"),
    str(Path.home() / "Documents" / "Claude" / ".shared"),
]


def _load():
    global _broker, _tried
    if _tried:
        return _broker
    _tried = True
    for d in _CANDIDATE_SHARED_DIRS:
        if d and os.path.isdir(d) and d not in sys.path:
            sys.path.insert(0, d)
    try:
        from ollama_broker import get_broker  # type: ignore
        _broker = get_broker(_CALLER, priority=5)
        log.info("ollama_broker available — LLM dedup tie-break enabled")
    except Exception as e:  # noqa: BLE001
        log.info("ollama_broker not available (%s) — heuristic dedup only", e)
        _broker = None
    return _broker


def available() -> bool:
    return _load() is not None and _healthy()


def _healthy() -> bool:
    try:
        return bool(_broker and _broker.health())
    except Exception:
        return False


def judge_same_content(title_a: str, title_b: str,
                       model: str = "qwen3.5:9b") -> Optional[bool]:
    """Return True/False if the broker is confident, else None.

    Only call this for genuinely ambiguous pairs — it costs an LLM round-trip.
    """
    broker = _load()
    if broker is None:
        return None
    prompt = (
        "You are deduplicating torrents. Do these two titles refer to the SAME "
        "underlying content (same film/album/dataset/distro), ignoring release "
        "group, codec and resolution?\n"
        f"A: {title_a}\nB: {title_b}\n"
        "Answer with exactly one word: YES or NO."
    )
    try:
        resp = broker.generate(model, prompt, options={"temperature": 0.0},
                               timeout=30)
        text = (resp.get("response") or "").strip().upper()
        if text.startswith("YES"):
            return True
        if text.startswith("NO"):
            return False
    except Exception as e:  # noqa: BLE001
        log.info("broker dedup judge failed: %s", e)
    return None


def _extract_json(text: str):
    """Pull the first {...} object out of an LLM reply (it may wrap it in prose)."""
    import json
    import re
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None


def judge_worth(name: str, category: str, source: str, size_bytes: int, legal: bool,
                *, model: str = "qwen3.5:9b", temperature: float = 0.4,
                timeout: float = 8.0) -> Optional[tuple]:
    """Rate how WORTH RESCUING a torrent is (0-100) + a short reason, or None.

    Fed only descriptive metadata — never seeders/endangerment — so worth is an
    axis orthogonal to "is it dying": archival/irreplaceability value, not
    popularity. temperature > 0 so independent installs decorrelate instead of
    ranking identically. Returns None on any failure (caller = 'no opinion')."""
    broker = _load()
    if broker is None:
        return None
    size_gb = (size_bytes or 0) / (1024 ** 3)
    prompt = (
        "Rate how WORTH RESCUING + preserving this torrent is, 0-100, by its "
        "archival / historical / scientific value and irreplaceability — NOT its "
        "popularity. High (80-100): irreplaceable primary sources, datasets, gov/"
        "legal records, abandonware, out-of-print or regional/minority-language "
        "works, archival footage. Mid (~50): niche but mirrored elsewhere. Low "
        "(<20): mainstream commercial media, junk, trivially re-downloadable.\n"
        f"Title: {name}\nCategory: {category or 'unknown'}\nSource: {source}\n"
        f"Size: {size_gb:.1f} GB\nLegal/whitelisted: {bool(legal)}\n"
        'Reply with ONLY JSON: {"worth": <int 0-100>, "reason": "<=12 words"}'
    )
    try:
        resp = broker.generate(model, prompt, options={"temperature": temperature},
                               timeout=timeout)
        data = _extract_json((resp.get("response") or "").strip())
        if not data or "worth" not in data:
            return None
        worth = max(0, min(100, int(data["worth"])))
        reason = str(data.get("reason", ""))[:120]
        return (worth, reason)
    except Exception as e:  # noqa: BLE001
        log.info("broker worth judge failed: %s", e)
        return None
