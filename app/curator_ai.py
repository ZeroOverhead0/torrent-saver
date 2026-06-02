"""Optional AI worth-curation.

Re-rank the *eligible* rescue set toward content worth preserving, using the
shared ollama broker. Strictly optional and graceful: no broker / disabled /
any failure → worth stays None and ordering falls back to pure endangerment, so
the app is byte-identical to the no-LLM build. It only ever tilts ORDER — it
never opens or closes an eligibility gate (those still run on raw signals).
"""
from __future__ import annotations

import asyncio
from typing import List

from app import broker, config
from app.db import get_db


def blend(endangerment: float, worth, weight: float = 0.5) -> float:
    """Combine endangerment with the LLM worth into a rescue priority.

    worth is None (not judged / no broker) → priority == endangerment."""
    if worth is None:
        return endangerment or 0.0
    return round((1.0 - weight) * (endangerment or 0.0) + weight * float(worth), 2)


async def annotate_worth(cands: List) -> int:
    """Fill c.worth / c.worth_reason for eligible candidates — cache first, then
    a budgeted batch of broker calls. Returns how many were freshly judged.
    No-op and safe when the feature is off or the broker is unreachable."""
    if not config.get_bool("ai_curation_enabled") or not cands:
        return 0
    model = config.get_str("ai_curation_model", "qwen3.5:9b")
    min_e = config.get_float("ai_curation_min_endangerment", 20.0)
    max_red = config.get_int("max_redundancy", 2)

    # 1) Reuse cached worth (same model) so we never re-pay the LLM for a title.
    hashes = [c.normalised_hash() for c in cands if c.normalised_hash()]
    cache = {}
    if hashes:
        with get_db() as db:
            rows = db.execute(
                "SELECT infohash,worth,worth_reason,worth_model FROM candidates "
                "WHERE infohash IN (%s)" % ",".join("?" * len(hashes)), hashes).fetchall()
            cache = {r["infohash"]: r for r in rows}

    misses = []
    for c in cands:
        r = cache.get(c.normalised_hash())
        if r is not None and r["worth"] is not None and (r["worth_model"] or "") == model:
            c.worth, c.worth_reason = int(r["worth"]), r["worth_reason"] or ""
            continue
        # Only judge candidates that could actually be rescued — don't spend the
        # LLM on near-safe / undownloadable / already-redundant ones.
        if c.rescuable and (c.endangerment or 0) >= min_e and (c.redundancy or 0) < max_red:
            misses.append(c)

    if not misses or not broker.available():        # health-gate once per scan
        return 0

    misses.sort(key=lambda c: c.endangerment or 0.0, reverse=True)
    misses = misses[: config.get_int("ai_curation_max_per_scan", 25)]
    temp = config.get_float("ai_curation_temperature", 0.4)
    timeout = config.get_float("ai_curation_timeout_s", 8.0)
    sem = asyncio.Semaphore(max(1, config.get_int("ai_curation_concurrency", 2)))

    async def _judge(c):
        async with sem:
            try:
                res = await asyncio.to_thread(
                    broker.judge_worth, c.name, c.category, c.source,
                    c.size_bytes, c.legal, model=model, temperature=temp, timeout=timeout)
            except Exception:  # noqa: BLE001
                res = None
        if res:
            c.worth, c.worth_reason = int(res[0]), res[1]

    try:
        await asyncio.wait_for(asyncio.gather(*[_judge(c) for c in misses]),
                               timeout=max(timeout * 2, 60.0))
    except asyncio.TimeoutError:
        pass                                        # partial is fine — a tilt, not a gate
    return sum(1 for c in misses if c.worth is not None)
