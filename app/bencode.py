"""Minimal pure-Python bencode encode/decode + .torrent infohash extraction.

Just enough to: decode a .torrent file, re-encode its `info` dict to compute the
SHA1 infohash, and decode bencoded tracker-scrape responses. No third-party deps.
"""
from __future__ import annotations

import hashlib
from typing import Any, Tuple


# --------------------------------------------------------------------------- #
# Decode
# --------------------------------------------------------------------------- #
def decode(data: bytes) -> Any:
    """Decode a bencoded byte string into Python objects.

    dicts use bytes keys (so we can re-encode `info` byte-exactly).
    """
    value, index = _decode(data, 0)
    return value


def _decode(data: bytes, i: int) -> Tuple[Any, int]:
    c = data[i:i + 1]
    if c == b"i":
        end = data.index(b"e", i)
        return int(data[i + 1:end]), end + 1
    if c == b"l":
        i += 1
        out = []
        while data[i:i + 1] != b"e":
            value, i = _decode(data, i)
            out.append(value)
        return out, i + 1
    if c == b"d":
        i += 1
        out: dict = {}
        while data[i:i + 1] != b"e":
            key, i = _decode(data, i)
            value, i = _decode(data, i)
            out[key] = value
        return out, i + 1
    if c.isdigit():
        colon = data.index(b":", i)
        length = int(data[i:colon])
        start = colon + 1
        return data[start:start + length], start + length
    raise ValueError(f"invalid bencode at byte {i}: {c!r}")


# --------------------------------------------------------------------------- #
# Encode
# --------------------------------------------------------------------------- #
def encode(value: Any) -> bytes:
    out: list[bytes] = []
    _encode(value, out)
    return b"".join(out)


def _encode(value: Any, out: list[bytes]) -> None:
    if isinstance(value, bool):
        raise TypeError("bencode has no boolean type")
    if isinstance(value, int):
        out.append(b"i%de" % value)
    elif isinstance(value, bytes):
        out.append(b"%d:" % len(value) + value)
    elif isinstance(value, str):
        b = value.encode("utf-8")
        out.append(b"%d:" % len(b) + b)
    elif isinstance(value, (list, tuple)):
        out.append(b"l")
        for item in value:
            _encode(item, out)
        out.append(b"e")
    elif isinstance(value, dict):
        out.append(b"d")
        # Keys must be sorted byte-wise for a canonical encoding.
        for key in sorted(value.keys(), key=lambda k: k if isinstance(k, bytes) else str(k).encode()):
            kb = key if isinstance(key, bytes) else str(key).encode("utf-8")
            out.append(b"%d:" % len(kb) + kb)
            _encode(value[key], out)
        out.append(b"e")
    else:
        raise TypeError(f"cannot bencode {type(value)}")


# --------------------------------------------------------------------------- #
# .torrent helpers
# --------------------------------------------------------------------------- #
def infohash_from_torrent(data: bytes) -> str:
    """Return the lowercase 40-char hex SHA1 infohash of a .torrent file."""
    meta = decode(data)
    info = meta[b"info"]
    return hashlib.sha1(encode(info)).hexdigest().lower()


def info_from_torrent(data: bytes) -> dict:
    """Return (name, total_size_bytes) parsed from a .torrent's info dict."""
    meta = decode(data)
    info = meta[b"info"]
    name = info.get(b"name", b"").decode("utf-8", "replace")
    if b"length" in info:                       # single-file torrent
        size = int(info[b"length"])
    else:                                        # multi-file torrent
        size = sum(int(f[b"length"]) for f in info.get(b"files", []))
    return {"name": name, "size_bytes": size}
