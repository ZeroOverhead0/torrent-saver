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
