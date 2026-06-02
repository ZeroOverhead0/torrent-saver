"""Bind-aware access gate.

The web UI has no accounts — it's a single-user local tool. But once it's
published, people will run it in Docker (which publishes to 0.0.0.0) or with
``--host`` on a LAN, exposing a torrent-client controller to anyone who can
reach the port. So: stay completely INERT when the only reachable clients are
local (the Claude hub reverse-proxy on loopback, or a 127.0.0.1 user), and
REQUIRE a password the moment the server is reachable off-box.

Fail-closed-but-usable: if exposed without a configured password we generate one
and log it, rather than refusing to boot.
"""
from __future__ import annotations

import base64
import ipaddress
import logging
import os
import secrets

from app import settings as S

log = logging.getLogger("torrentsaver.auth")


def is_loopback(host: str) -> bool:
    if not host:
        return False
    h = host.strip().lower().strip("[]")
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def bound_offbox(bind_host: str) -> bool:
    """True if the server's BIND address is reachable from another machine.

    A non-loopback literal, a wildcard (0.0.0.0/::), or any hostname counts as
    off-box; only loopback/localhost is treated as local-only."""
    if not bind_host:
        return False
    h = bind_host.strip().lower().strip("[]")
    if h in ("0.0.0.0", "::", "*", ""):
        return True
    return not is_loopback(h)


def ensure_access_secret() -> str:
    """The access password: TORRENTSAVER_PASSWORD env > stored secret > generated."""
    env = os.environ.get("TORRENTSAVER_PASSWORD")
    if env:
        return env
    pw = S.get_secret("access_password")
    if pw:
        return pw
    pw = secrets.token_urlsafe(18)
    S.set_secret("access_password", pw)
    log.warning(
        "Web UI is reachable off-box; generated an access password. "
        "Sign in as user 'admin' with password: %s  "
        "(override via the TORRENTSAVER_PASSWORD env var)", pw)
    return pw


def basic_ok(authorization: str, password: str) -> bool:
    """Validate an HTTP Basic header against the access password (user 'admin')."""
    if not authorization or not authorization.lower().startswith("basic "):
        return False
    try:
        raw = base64.b64decode(authorization.split(" ", 1)[1]).decode("utf-8", "replace")
    except Exception:
        return False
    user, _, pw = raw.partition(":")
    return user == "admin" and secrets.compare_digest(pw, password)
