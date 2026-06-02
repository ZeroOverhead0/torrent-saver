"""VPN detection, kill-switch binding and leak checking.

Torrent Saver will not seed *unrestricted* content unless a VPN is up, the
kill-switch is engaged (qBittorrent bound to the VPN interface, so traffic dies
if the tunnel drops) and no IP leak is detected. Legal/whitelisted content is
exempt — Internet Archive etc. are meant to be seeded openly.

Honest scope: we verify the tunnel interface exists and carries the egress IP,
and we bind qBittorrent to it. We cannot prove from outside qBittorrent that a
peer connection never escaped the tunnel, so we fail *closed* on every check.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import subprocess
from typing import Optional

import httpx

from app.models import VpnStatus

# Interface name prefixes that indicate a VPN / tunnel device.
TUNNEL_PREFIXES = ("utun", "tun", "tap", "wg", "ppp", "ipsec", "nordlynx",
                   "proton", "wgpia", "mullvad")

IP_ECHO_SERVICES = ("https://api.ipify.org", "https://ifconfig.me/ip",
                    "https://icanhazip.com")


# --------------------------------------------------------------------------- #
# Interface enumeration (blocking; call via asyncio.to_thread)
# --------------------------------------------------------------------------- #
def list_interfaces() -> dict:
    """Return {interface_name: ipv4_address} for all up interfaces with an IP."""
    # Linux first (iproute2), then macOS/BSD ifconfig.
    try:
        out = subprocess.run(["ip", "-o", "-4", "addr", "show"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return _parse_ip_addr(out.stdout)
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return _parse_ifconfig(out.stdout)
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return {}


def _parse_ip_addr(text: str) -> dict:
    result = {}
    for line in text.splitlines():
        # "5: wg0    inet 10.0.0.2/32 brd ... scope global wg0"
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "inet":
            name = parts[1]
            ip = parts[3].split("/")[0]
            result[name] = ip
    return result


def _parse_ifconfig(text: str) -> dict:
    result = {}
    current = None
    for line in text.splitlines():
        if line and not line[0].isspace():
            current = line.split(":", 1)[0].strip()
        elif current:
            m = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)", line)
            if m:
                result[current] = m.group(1)
    return result


def tunnel_interfaces() -> dict:
    """Subset of list_interfaces() that look like VPN tunnels."""
    return {name: ip for name, ip in list_interfaces().items()
            if name.lower().startswith(TUNNEL_PREFIXES)}


def pick_interface(configured: str = "") -> tuple:
    """Return (interface_name, ipv4) to bind to.

    Prefers an explicitly-configured interface if it is currently up; otherwise
    the first detected tunnel interface. Returns ("", "") if none.
    """
    ifaces = list_interfaces()
    if configured and configured in ifaces:
        return configured, ifaces[configured]
    tunnels = {n: ip for n, ip in ifaces.items()
               if n.lower().startswith(TUNNEL_PREFIXES)}
    if tunnels:
        name = sorted(tunnels)[0]
        return name, tunnels[name]
    return "", ""


def _is_private(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# Public IP (async)
# --------------------------------------------------------------------------- #
async def get_public_ip(timeout: float = 6.0) -> str:
    async with httpx.AsyncClient(timeout=timeout) as client:
        for url in IP_ECHO_SERVICES:
            try:
                resp = await client.get(url)
                ip = (resp.text or "").strip()
                ipaddress.ip_address(ip)               # validate
                return ip
            except Exception:
                continue
    return ""


# --------------------------------------------------------------------------- #
# Assessment + kill-switch
# --------------------------------------------------------------------------- #
def assess(*, interface: str, iface_ip: str, public_ip: str, baseline_ip: str,
           qbit_bound_interface: Optional[str]) -> VpnStatus:
    """Build a VpnStatus from gathered facts. Pure — easy to unit test."""
    connected = bool(interface and iface_ip)
    killswitch = bool(interface and qbit_bound_interface == interface)

    leaking = False
    # Can we actually verify there's no leak? Only with a baseline AND a public IP.
    leak_check_possible = bool(baseline_ip and public_ip)
    detail_bits = []
    if not connected:
        detail_bits.append("no VPN/tunnel interface detected")
    if leak_check_possible:
        if public_ip == baseline_ip:
            leaking = True
            detail_bits.append("public IP equals your baseline ISP IP — traffic is NOT on the VPN")
        else:
            detail_bits.append("public IP differs from baseline (good)")
    elif not baseline_ip:
        detail_bits.append("leak detection DISABLED — set your real ISP IP as the baseline; "
                           "unrestricted content will NOT seed until then")
    else:
        detail_bits.append("leak detection unavailable — could not determine your public IP")
    if interface and not killswitch:
        detail_bits.append(f"qBittorrent not yet bound to {interface} (kill-switch off)")

    return VpnStatus(
        connected=connected, interface=interface, public_ip=public_ip,
        killswitch_engaged=killswitch, leaking=leaking,
        leak_check_possible=leak_check_possible,
        detail="; ".join(detail_bits))


async def check(qbit_client=None, *, configured_interface: str = "",
                baseline_ip: str = "") -> VpnStatus:
    """Gather interface state, qBittorrent binding and public IP into a status."""
    interface, iface_ip = await asyncio.to_thread(pick_interface, configured_interface)
    public_ip = await get_public_ip()
    bound = None
    if qbit_client is not None:
        try:
            prefs = await qbit_client.get_preferences()
            bound = prefs.get("current_network_interface", "") or None
        except Exception:
            bound = None
    return assess(interface=interface, iface_ip=iface_ip, public_ip=public_ip,
                  baseline_ip=baseline_ip, qbit_bound_interface=bound)


async def engage_killswitch(qbit_client, interface: str) -> bool:
    """Bind qBittorrent to `interface`. If the VPN drops, the interface goes
    away and qBittorrent can no longer send any peer traffic — the kill-switch.
    """
    if not interface:
        return False
    return await qbit_client.set_preferences({
        "current_network_interface": interface,
        # Clear any pinned address so qBit follows the interface, not a stale IP.
        "current_interface_address": "",
        "announce_to_all_trackers": False,
    })
