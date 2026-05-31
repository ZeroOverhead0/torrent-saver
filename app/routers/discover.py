"""Discover — endangered candidates, scanning, rescue actions, manual watchlist."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Form, Request

from app import actions, config, scanner
from app.db import get_db
from app.routers._common import redirect, render
from app.sources import manual

router = APIRouter()


@router.get("/discover")
async def discover(request: Request, status: str = "queued", msg: str = ""):
    valid = ("queued", "skipped", "dead", "rescued", "all")
    status = status if status in valid else "queued"
    where = "" if status == "all" else "WHERE status=?"
    args = () if status == "all" else (status,)
    with get_db() as db:
        rows = db.execute(
            f"SELECT * FROM candidates {where} ORDER BY endangerment DESC LIMIT 300",
            args).fetchall()
        counts = {s: db.execute(
            "SELECT COUNT(*) c FROM candidates WHERE status=?", (s,)).fetchone()["c"]
            for s in ("queued", "skipped", "dead", "rescued")}
    return render(request, "discover.html",
                  msg=msg, status=status, counts=counts,
                  candidates=[dict(r) for r in rows],
                  watchlist=manual.list_entries(),
                  legal_mode=config.legal_mode(),
                  auto_rescue=config.get_bool("auto_rescue"))


@router.post("/scan")
async def trigger_scan(request: Request):
    # Run in the background so the request returns immediately.
    asyncio.create_task(scanner.run_scan())
    return redirect(request, "/discover", "Scan started — refresh in a minute")


@router.post("/rescue-all")
async def rescue_all(request: Request):
    asyncio.create_task(actions.rescue_queued())
    return redirect(request, "/discover", "Rescuing queued candidates within budget")


@router.post("/rescue/{infohash}")
async def rescue_one(request: Request, infohash: str):
    res = await actions.rescue_candidate(infohash)
    msg = "Rescue started" if res.get("ok") else f"Could not rescue: {res.get('error')}"
    return redirect(request, "/discover", msg)


@router.post("/candidate/{infohash}/find-body")
async def find_body(request: Request, infohash: str):
    res = await actions.resurrect_candidate(infohash)
    if not res.get("ok"):
        msg = f"Search failed: {res.get('error')}"
    elif res.get("found"):
        msg = f"Found {res['found']} downloadable twin(s) — added to the queue"
    else:
        msg = "No downloadable twin found for this one"
    return redirect(request, "/discover?status=dead", msg)


@router.post("/candidate/{infohash}/skip")
async def skip_candidate(request: Request, infohash: str):
    with get_db() as db:
        db.execute("UPDATE candidates SET status='skipped', skip_reason='manually skipped' "
                   "WHERE infohash=? AND status!='rescued'", (infohash.lower(),))
    return redirect(request, "/discover", "Skipped")


@router.post("/watch/add")
async def watch_add(request: Request, value: str = Form(...), legal: str = Form("")):
    try:
        manual.add_entry(value, legal == "on" or legal == "1")
        msg = "Added to watchlist — run a scan to pick it up"
    except Exception as e:  # noqa: BLE001
        msg = f"Could not add: {e}"
    return redirect(request, "/discover", msg)


@router.post("/watch/remove")
async def watch_remove(request: Request, value: str = Form(...)):
    manual.remove_entry(value)
    return redirect(request, "/discover", "Removed from watchlist")
