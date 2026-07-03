"""Manage the qBittorrent **launchd agent** — the process torrent-saver talks to
but does not own.

torrent-saver only *connects* to a running qBittorrent over its Web UI API
(see ``app.qbittorrent``). The qBittorrent process itself is kept alive by a
user LaunchAgent (``KeepAlive=true``), so ``kill``-ing it is futile — launchd
respawns it instantly. A genuine "full stop" therefore means booting the agent
out of the GUI domain and disabling it so it cannot return at next login.

This module wraps those ``launchctl`` calls. It only ever runs ``launchctl``
with fixed arguments (the label below) — no caller input is interpolated — so
there is no shell-injection surface.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# The LaunchAgent that runs qBittorrent. Overridable via env for non-default
# installs, but these are the stack's conventional values.
LABEL = os.environ.get("TS_QBIT_LAUNCHD_LABEL", "com.user.qbittorrent-web")
PLIST = Path(
    os.environ.get(
        "TS_QBIT_LAUNCHD_PLIST",
        str(Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"),
    )
)

_TIMEOUT = 15  # seconds — launchctl is local and fast; cap so a route never hangs


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
    )


def is_running() -> bool:
    """True if the launchd agent is currently loaded/running."""
    if shutil.which("launchctl") is None:
        return False
    try:
        # `launchctl print <domain>/<label>` exits 0 only when the job exists.
        return _run("print", f"{_domain()}/{LABEL}").returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def full_stop() -> tuple[bool, str]:
    """Boot the agent out of the GUI domain and disable it so KeepAlive cannot
    respawn qBittorrent and it stays down across logins. Reversible via
    :func:`start`."""
    if shutil.which("launchctl") is None:
        return False, "launchctl not found (not a macOS launchd host)"
    if not PLIST.exists():
        return False, f"LaunchAgent plist missing: {PLIST}"
    try:
        # bootout stops the running job; disable prevents RunAtLoad next login.
        # bootout returns non-zero if already unloaded — tolerate that.
        _run("bootout", f"{_domain()}/{LABEL}")
        dis = _run("disable", f"{_domain()}/{LABEL}")
    except subprocess.TimeoutExpired:
        return False, "launchctl timed out"
    except OSError as e:
        return False, f"launchctl failed: {e}"
    if dis.returncode != 0 and dis.stderr.strip():
        return False, f"disable failed: {dis.stderr.strip()}"
    if is_running():
        return False, "qBittorrent agent still running after stop"
    return True, "qBittorrent fully stopped (agent booted out + disabled)"


def start() -> tuple[bool, str]:
    """Re-enable and bootstrap the agent. KeepAlive + RunAtLoad resume."""
    if shutil.which("launchctl") is None:
        return False, "launchctl not found (not a macOS launchd host)"
    if not PLIST.exists():
        return False, f"LaunchAgent plist missing: {PLIST}"
    try:
        _run("enable", f"{_domain()}/{LABEL}")
        boot = _run("bootstrap", _domain(), str(PLIST))
    except subprocess.TimeoutExpired:
        return False, "launchctl timed out"
    except OSError as e:
        return False, f"launchctl failed: {e}"
    # bootstrap returns non-zero if already loaded — treat a running agent as success.
    if boot.returncode != 0 and not is_running():
        return False, f"start failed: {boot.stderr.strip() or 'bootstrap error'}"
    return True, "qBittorrent agent started"
