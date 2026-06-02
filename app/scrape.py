"""Magnet parsing + tracker scraping — the endangerment oracle.

To know whether a torrent is *dying* we need live seeder/leecher counts. Sources
that don't report them (Internet Archive, raw magnets) get enriched here by
scraping their trackers directly: HTTP (BEP 48) and UDP (BEP 15), pure stdlib.

All functions here are blocking; call them from async code via asyncio.to_thread.
"""
from __future__ import annotations

import base64
import ipaddress
import os
import socket
import struct
import urllib.parse
import urllib.request
from typing import Optional

from app import bencode
from app.hardening import is_suspicious_tracker

UDP_PROTOCOL_ID = 0x41727101980
DEFAULT_TIMEOUT = 8.0


def _resolve_safe_ip(host: Optional[str]) -> Optional[str]:
    """Resolve `host` to a public, routable IP ONCE and return it (or None).

    Blocks SSRF *including DNS rebinding*: callers MUST connect to the returned
    IP and never re-resolve the hostname, so a name that passes the check can't
    then rebind to localhost/a private range before the real request goes out.
    """
    if not host:
        return None
    try:
        ip = socket.gethostbyname(host)
        addr = ipaddress.ip_address(ip)
    except (OSError, ValueError):
        return None
    if (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
        return None
    return ip


def _host_is_safe(host: Optional[str]) -> bool:
    """Boolean wrapper around _resolve_safe_ip (magnet-tracker hygiene)."""
    return _resolve_safe_ip(host) is not None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse 3xx: a redirect could bounce a validated request to a private host."""

    def redirect_request(self, *args, **kwargs):
        return None


# --------------------------------------------------------------------------- #
# Magnet parsing
# --------------------------------------------------------------------------- #
def parse_magnet(magnet: str) -> dict:
    """Return {infohash, name, trackers} from a magnet URI, or raise ValueError."""
    if not magnet.startswith("magnet:"):
        raise ValueError("not a magnet URI")
    qs = urllib.parse.urlparse(magnet).query
    params = urllib.parse.parse_qs(qs)
    xts = params.get("xt", [])
    infohash = ""
    for xt in xts:
        if xt.lower().startswith("urn:btih:"):
            raw = xt.split(":", 2)[2]
            infohash = _normalise_infohash(raw)
            break
    if not infohash:
        raise ValueError("magnet has no urn:btih infohash")
    name = params.get("dn", [""])[0]
    # Drop loopback/private/non-BT trackers up front (SSRF hygiene).
    trackers = [t for t in params.get("tr", []) if not is_suspicious_tracker(t)]
    return {"infohash": infohash, "name": name, "trackers": trackers}


def _normalise_infohash(raw: str) -> str:
    raw = raw.strip()
    if len(raw) == 40:                                  # already hex
        int(raw, 16)                                    # validate
        return raw.lower()
    if len(raw) == 32:                                  # base32
        return base64.b16encode(base64.b32decode(raw.upper())).decode().lower()
    raise ValueError(f"bad infohash length {len(raw)}")


def build_magnet(infohash: str, name: str = "", trackers: Optional[list] = None) -> str:
    parts = [f"magnet:?xt=urn:btih:{infohash}"]
    if name:
        parts.append("dn=" + urllib.parse.quote(name))
    for tr in (trackers or []):
        parts.append("tr=" + urllib.parse.quote(tr))
    return parts[0] + ("&" + "&".join(parts[1:]) if len(parts) > 1 else "")


# --------------------------------------------------------------------------- #
# HTTP scrape (BEP 48)
# --------------------------------------------------------------------------- #
def http_scrape(announce_url: str, infohash_hex: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[dict]:
    parsed = urllib.parse.urlparse(announce_url)
    ip = _resolve_safe_ip(parsed.hostname)
    if ip is None:
        return None
    path = parsed.path
    # Scrape is only defined when the last path segment is "announce".
    last = path.rsplit("/", 1)[-1]
    if not last.startswith("announce"):
        return None
    scrape_path = path[: -len(last)] + "scrape" + last[len("announce"):]
    raw = bytes.fromhex(infohash_hex)
    query = "info_hash=" + urllib.parse.quote_from_bytes(raw, safe="")
    if parsed.query:
        query = parsed.query + "&" + query
    # Connect to the validated IP literal (never re-resolve), carrying the real
    # hostname in the Host header for vhost routing. Brackets for IPv6.
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    ip_host = f"[{ip}]" if ":" in ip else ip
    scrape_url = urllib.parse.urlunparse(
        (parsed.scheme, f"{ip_host}:{port}", scrape_path, "", query, ""))
    try:
        req = urllib.request.Request(scrape_url, headers={
            "User-Agent": "TorrentSaver/1.0", "Host": parsed.netloc})
        # Custom opener refuses redirects (no rebind via a 3xx into a private host).
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=timeout) as resp:
            body = resp.read()
        data = bencode.decode(body)
        files = data.get(b"files", {})
        stats = files.get(raw)
        if stats is None and files:
            stats = next(iter(files.values()))          # some trackers key differently
        if not stats:
            return None
        return {
            "seeders": int(stats.get(b"complete", 0)),
            "leechers": int(stats.get(b"incomplete", 0)),
            "completed": int(stats.get(b"downloaded", 0)),
            "tracker": parsed.netloc,
        }
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# UDP scrape (BEP 15)
# --------------------------------------------------------------------------- #
def udp_scrape(announce_url: str, infohash_hex: str, timeout: float = DEFAULT_TIMEOUT) -> Optional[dict]:
    parsed = urllib.parse.urlparse(announce_url)
    if not parsed.hostname or not parsed.port:
        return None
    ip = _resolve_safe_ip(parsed.hostname)
    if ip is None:
        return None
    raw = bytes.fromhex(infohash_hex)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        # Send to the validated IP — never a second gethostbyname (rebind window).
        addr = (ip, parsed.port)
        # --- connect handshake ---
        tid = struct.unpack(">I", os.urandom(4))[0]
        req = struct.pack(">QII", UDP_PROTOCOL_ID, 0, tid)
        sock.sendto(req, addr)
        resp = sock.recv(16)
        if len(resp) < 16:
            return None
        action, r_tid = struct.unpack(">II", resp[:8])
        if action != 0 or r_tid != tid:
            return None
        connection_id = struct.unpack(">Q", resp[8:16])[0]
        # --- scrape request ---
        tid2 = struct.unpack(">I", os.urandom(4))[0]
        req2 = struct.pack(">QII", connection_id, 2, tid2) + raw
        sock.sendto(req2, addr)
        resp2 = sock.recv(8 + 12)
        if len(resp2) < 8:
            return None
        action2, r_tid2 = struct.unpack(">II", resp2[:8])
        if action2 != 2 or r_tid2 != tid2 or len(resp2) < 20:
            return None
        seeders, completed, leechers = struct.unpack(">III", resp2[8:20])
        return {
            "seeders": int(seeders),
            "leechers": int(leechers),
            "completed": int(completed),
            "tracker": parsed.netloc,
        }
    except Exception:
        return None
    finally:
        sock.close()


# --------------------------------------------------------------------------- #
# Aggregate
# --------------------------------------------------------------------------- #
def scrape_infohash(infohash_hex: str, trackers: list, timeout: float = DEFAULT_TIMEOUT,
                    max_trackers: int = 6) -> Optional[dict]:
    """Scrape an infohash across its trackers; return the healthiest result.

    The most-connected tracker reports the highest seeder count, so we take the
    max — a torrent is only truly endangered if *every* tracker shows it dying.
    """
    best: Optional[dict] = None
    tried = 0
    for tr in trackers:
        if tried >= max_trackers:
            break
        tried += 1
        scheme = urllib.parse.urlparse(tr).scheme.lower()
        if scheme.startswith("udp"):
            result = udp_scrape(tr, infohash_hex, timeout)
        elif scheme.startswith("http"):
            result = http_scrape(tr, infohash_hex, timeout)
        else:
            result = None
        if result is None:
            continue
        if best is None or result["seeders"] > best["seeders"]:
            best = result
    return best
