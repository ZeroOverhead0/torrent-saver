"""Clone / near-duplicate detection.

The user's rule: don't waste disk re-seeding something that already has several
healthy copies. We detect when two torrents are *the same content* by normalising
their titles (stripping release-tag noise), comparing token overlap, and checking
their sizes match within a tolerance. `redundancy()` then counts how many *healthy*
duplicates already exist — the curator skips a candidate whose redundancy is high.

Pure functions; an optional LLM tie-breaker lives in app.broker and is only used
when these heuristics are ambiguous.
"""
from __future__ import annotations

import re
from typing import Iterable

# Release-tag noise that says nothing about *what* the content is.
_NOISE = {
    "720p", "1080p", "2160p", "480p", "4k", "uhd", "hd", "sd",
    "bluray", "blu", "ray", "brrip", "bdrip", "webrip", "web", "dl", "webdl",
    "hdtv", "hdrip", "dvdrip", "dvd", "remux", "x264", "x265", "h264", "h265",
    "hevc", "avc", "xvid", "divx", "aac", "ac3", "dts", "ddp", "dd", "flac",
    "mp3", "10bit", "8bit", "hdr", "sdr", "proper", "repack", "internal",
    "extended", "unrated", "limited", "complete", "multi", "dual", "audio",
    "subs", "subbed", "dubbed", "retail", "iso", "rarbg", "yify", "yts",
    "ettv", "eztv", "fgt", "the", "a", "an", "of", "and",
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalise_title(title: str) -> set:
    """Return a set of meaningful lowercase tokens from a torrent title."""
    title = title.lower()
    title = re.sub(r"\[[^\]]*\]", " ", title)          # drop [bracketed] tags
    title = re.sub(r"\([^)]*\)", " ", title)           # drop (parenthetical) tags
    tokens = _TOKEN_RE.findall(title)
    out = set()
    for t in tokens:
        if t in _NOISE:
            continue
        if len(t) <= 1:
            continue
        out.add(t)
    return out


def title_similarity(a: str, b: str) -> float:
    """Token-set similarity in [0, 1] combining Jaccard and containment."""
    sa, sb = normalise_title(a), normalise_title(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    if inter == 0:
        return 0.0
    union = len(sa | sb)
    containment = inter / min(len(sa), len(sb))
    jaccard = inter / union
    return round(max(jaccard, 0.6 * containment + 0.4 * jaccard), 4)


def _size_match(size_a: int, size_b: int, tolerance: float) -> bool | None:
    """True/False if both sizes known; None when at least one is unknown."""
    if not size_a or not size_b:
        return None
    bigger = max(size_a, size_b)
    return abs(size_a - size_b) / bigger <= tolerance


def is_same_content(a, b, *, sim_threshold: float = 0.82,
                    size_tolerance: float = 0.10) -> bool:
    """Decide whether candidates `a` and `b` represent the same content.

    `a` and `b` may be Candidate dataclasses or any object exposing
    .infohash, .name and .size_bytes.
    """
    if getattr(a, "infohash", None) and a.infohash == getattr(b, "infohash", None):
        return True                                    # byte-identical torrent
    sim = title_similarity(a.name, b.name)
    size_ok = _size_match(getattr(a, "size_bytes", 0), getattr(b, "size_bytes", 0),
                          size_tolerance)
    if size_ok is True:
        return sim >= sim_threshold
    if size_ok is False:
        return False                                   # same name, very different size
    # Size unknown for one side — demand a stronger title match.
    return sim >= min(0.95, sim_threshold + 0.1)


def find_clones(candidate, others: Iterable, *, sim_threshold: float = 0.82,
                size_tolerance: float = 0.10) -> list:
    """Return the subset of `others` that is the same content as `candidate`."""
    out = []
    for o in others:
        if getattr(o, "infohash", None) == getattr(candidate, "infohash", None):
            continue
        if is_same_content(candidate, o, sim_threshold=sim_threshold,
                            size_tolerance=size_tolerance):
            out.append(o)
    return out


def redundancy(candidate, others: Iterable, *, healthy_seeders: int = 10,
               sim_threshold: float = 0.82, size_tolerance: float = 0.10) -> int:
    """Count how many *healthy* duplicates of `candidate` already exist.

    A high redundancy means the content is safe in other hands — not worth
    spending our limited budget on.
    """
    n = 0
    for clone in find_clones(candidate, others, sim_threshold=sim_threshold,
                             size_tolerance=size_tolerance):
        if getattr(clone, "seeders", 0) >= healthy_seeders:
            n += 1
    return n
