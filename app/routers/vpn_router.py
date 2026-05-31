"""VPN / kill-switch control + hardening trigger."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from app import actions, config, vpn
from app import settings as S
from app.config import VPN_PROVIDERS
from app.routers._common import latest_vpn, redirect, render

router = APIRouter()


@router.get("/vpn")
async def vpn_page(request: Request, msg: str = ""):
    interfaces = await asyncio.to_thread(vpn.list_interfaces)
    tunnels = {n: ip for n, ip in interfaces.items()
               if n.lower().startswith(vpn.TUNNEL_PREFIXES)}
    return render(request, "vpn.html",
                  msg=msg,
                  interfaces=interfaces, tunnels=tunnels,
                  providers=VPN_PROVIDERS,
                  status=latest_vpn(),
                  vpn_provider=config.get_str("vpn_provider"),
                  vpn_interface=config.get_str("vpn_interface"),
                  vpn_killswitch=config.get_bool("vpn_killswitch"),
                  vpn_required=config.get_bool("vpn_required"),
                  vpn_baseline_ip=config.get_str("vpn_baseline_ip"),
                  profile=config.machine_profile())


@router.post("/vpn/save")
async def vpn_save(request: Request):
    form = await request.form()
    S.set_settings({
        "vpn_provider": str(form.get("vpn_provider", "")),
        "vpn_interface": str(form.get("vpn_interface", "")).strip(),
        "vpn_baseline_ip": str(form.get("vpn_baseline_ip", "")).strip(),
        "vpn_killswitch": "1" if form.get("vpn_killswitch") else "0",
        "vpn_required": "1" if form.get("vpn_required") else "0",
    })
    return redirect(request, "/vpn", "VPN settings saved")


@router.post("/vpn/check")
async def vpn_check(request: Request):
    status = await actions.current_vpn_status(persist=True)
    return redirect(request, "/vpn",
                    "VPN check: " + ("SAFE to seed" if status.safe_to_seed
                                     else status.detail or "not safe"))


@router.post("/vpn/killswitch")
async def vpn_killswitch(request: Request):
    ok = await actions.ensure_killswitch()
    return redirect(request, "/vpn",
                    "Kill-switch engaged" if ok else "Could not engage kill-switch (no VPN interface or qBittorrent down)")


@router.post("/vpn/harden")
async def vpn_harden(request: Request):
    res = await actions.apply_hardening_now()
    msg = ("Hardening applied" + (f", bound to {res.get('interface')}" if res.get("interface") else "")
           if res.get("ok") else f"Hardening failed: {res.get('error')}")
    return redirect(request, "/vpn", msg)
