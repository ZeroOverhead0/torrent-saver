"""Sources — enable/disable connectors, configure them, test them."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Request

from app import config
from app import settings as S
from app.routers._common import redirect, render
from app.sources import all_sources, get_source
from app.sources.base import FetchContext

router = APIRouter()


@router.get("/sources")
async def sources(request: Request, msg: str = ""):
    items = []
    for s in all_sources():
        items.append({
            "name": s.name, "label": s.label or s.name, "kind": s.kind,
            "legal": s.legal, "enabled": s.is_enabled(),
        })
    return render(request, "sources.html",
                  msg=msg, sources=items,
                  ia_query=config.get_str("internet_archive_query"),
                  prowlarr_url=config.get_str("prowlarr_url"),
                  prowlarr_queries=config.get_str("prowlarr_queries"),
                  prowlarr_trust_legal=config.get_bool("prowlarr_trust_legal"),
                  prowlarr_key_set=bool(config.prowlarr_api_key()),
                  legal_mode=config.legal_mode())


@router.post("/sources")
async def save_sources(request: Request):
    form = await request.form()
    # Per-source enable toggles.
    for s in all_sources():
        S.set_setting(s.enabled_key, "1" if form.get(s.enabled_key) else "0")
    # Source-specific config.
    for key in ("internet_archive_query", "prowlarr_url", "prowlarr_queries"):
        if key in form:
            S.set_setting(key, str(form.get(key)))
    S.set_setting("prowlarr_trust_legal", "1" if form.get("prowlarr_trust_legal") else "0")
    api_key = (form.get("prowlarr_api_key") or "").strip()
    if api_key:
        S.set_secret("prowlarr_api_key", api_key)
    return redirect(request, "/sources", "Sources saved")


@router.post("/sources/test/{name}")
async def test_source(request: Request, name: str):
    src = get_source(name)
    if not src:
        return redirect(request, "/sources", f"Unknown source {name}")
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            ctx = FetchContext(client=client, limit=10,
                               legal_only=config.legal_mode(),
                               max_seeders_endangered=config.get_int("endangered_max_seeders", 5))
            results = await src.fetch(ctx)
        msg = f"{src.label or name}: returned {len(results)} candidate(s)"
    except Exception as e:  # noqa: BLE001
        msg = f"{src.label or name} failed: {e}"
    return redirect(request, "/sources", msg)
