"""Background loops that run inside the FastAPI process.

  discover_loop  — scan sources for endangered torrents (and auto-rescue if enabled)
  monitor_loop   — refresh live stats from qBittorrent, then evict overflow
  vpn_loop       — re-check the VPN / kill-switch and re-bind qBittorrent if needed
  demand_loop    — top up demand-seeding when enabled

Each loop reads its interval from settings every iteration (so changes take
effect without a restart), guards on the global pause flag, and never lets one
failed cycle kill the loop.
"""
from __future__ import annotations

import asyncio
import logging

from app import actions, config, notify, scanner, seeder

log = logging.getLogger("torrentsaver.scheduler")

MIN_SLEEP = 30.0          # never spin faster than this, whatever the setting says


async def _sleep_minutes(key: str, default: int) -> None:
    minutes = config.get_int(key, default)
    await asyncio.sleep(max(MIN_SLEEP, minutes * 60))


async def startup_tasks() -> None:
    """One-shot work at boot: harden qBittorrent and engage the kill-switch."""
    await asyncio.sleep(3)        # let the server settle
    try:
        if config.get_bool("harden_on_start"):
            res = await actions.apply_hardening_now()
            if not res.get("ok"):
                log.info("startup hardening skipped: %s", res.get("error"))
        await actions.ensure_killswitch()
    except Exception as e:  # noqa: BLE001
        log.info("startup tasks error: %s", e)


async def discover_loop() -> None:
    while True:
        try:
            if not config.is_paused():
                await scanner.run_scan()
                if config.get_bool("auto_rescue"):
                    await actions.rescue_queued()
        except Exception as e:  # noqa: BLE001
            notify.error("scan", f"discover loop error: {e}")
        await _sleep_minutes("scan_interval_min", 60)


async def monitor_loop() -> None:
    while True:
        try:
            if not config.is_paused():
                await actions.sync_tracked()
                await actions.evict_overflow()
        except Exception as e:  # noqa: BLE001
            notify.error("evict", f"monitor loop error: {e}")
        await _sleep_minutes("monitor_interval_min", 15)


async def vpn_loop() -> None:
    last_safe = None
    while True:
        try:
            status = await actions.current_vpn_status(persist=True)
            if config.get_bool("vpn_killswitch"):
                await actions.ensure_killswitch()
            # Active fail-closed: pause unrestricted torrents while the tunnel is
            # unsafe; resume them when it recovers.
            if config.get_bool("vpn_required"):
                if not status.safe_to_seed:
                    await actions.enforce_vpn_gate(status)
                elif last_safe is False:
                    await actions.resume_unrestricted()
            if last_safe is not None and status.safe_to_seed != last_safe:
                notify.decision(
                    "vpn",
                    "VPN now SAFE to seed" if status.safe_to_seed
                    else f"VPN UNSAFE — seeding paused ({status.detail})")
            last_safe = status.safe_to_seed
        except Exception as e:  # noqa: BLE001
            log.info("vpn loop error: %s", e)
        await _sleep_minutes("vpn_check_interval_min", 5)


async def demand_loop() -> None:
    while True:
        try:
            if config.get_bool("demand_seed_enabled") and not config.is_paused():
                await seeder.run_demand_seed()
        except Exception as e:  # noqa: BLE001
            notify.error("seed", f"demand loop error: {e}")
        await _sleep_minutes("scan_interval_min", 60)


async def deathwatch_loop() -> None:
    """Re-scrape known torrents and catch ones sliding toward death."""
    while True:
        try:
            if not config.is_paused() and config.get_bool("deathwatch_enabled"):
                await actions.rescan_watchlist()
        except Exception as e:  # noqa: BLE001
            notify.error("deathwatch", f"death-watch loop error: {e}")
        await _sleep_minutes("deathwatch_interval_min", 360)


async def reannounce_loop() -> None:
    """Re-announce complete torrents to trackers/DHT so they re-seed the swarm."""
    while True:
        try:
            if not config.is_paused() and config.get_bool("reannounce_enabled"):
                await actions.reannounce_complete()
        except Exception as e:  # noqa: BLE001
            notify.error("seed", f"reannounce loop error: {e}")
        await _sleep_minutes("reannounce_interval_min", 30)
