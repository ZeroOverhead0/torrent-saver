"""Eviction — free space when the rescue library outgrows the budget.

We evict the *least valuable* torrents first. A torrent's keep-value is its live
endangerment: one whose seeder count has recovered scores low (others now hold
it — our job is done) and is evicted before anything still on the brink. Eviction
is triggered by exceeding the disk budget, dropping below the volume's reserve
free space, or breaching the torrent-count cap.
"""
from __future__ import annotations

from typing import List, Optional

from app.config import MachineProfile
from app.endangerment import endangerment_score
from app.models import EvictionDecision


def _keep_value(t, max_seeders_endangered: int) -> float:
    """Higher = more worth keeping. Recompute from live seeders so recovered
    torrents (now safe in the swarm) fall to the bottom."""
    return endangerment_score(
        getattr(t, "seeders", 0), getattr(t, "leechers", 0),
        max_seeders_endangered=max_seeders_endangered)


def plan_evictions(tracked: List, profile: MachineProfile, *,
                   volume_free_bytes: Optional[int] = None,
                   max_seeders_endangered: int = 5,
                   recovered_seeders: int = 15,
                   protect_endangerment: float = 75.0) -> List[EvictionDecision]:
    """Return EvictionDecision objects (evict=True for the chosen victims).

    `volume_free_bytes` (if known) lets us also honour the profile reserve.
    Torrents above `protect_endangerment` are never evicted unless that is the
    only way to get back under budget.
    """
    live = [t for t in tracked if not getattr(t, "evicted_at", None)]
    current_total = sum((getattr(t, "size_bytes", 0) or 0) for t in live)

    need_to_free = max(0, current_total - profile.max_disk_bytes)
    if volume_free_bytes is not None and profile.reserve_bytes:
        shortfall = profile.reserve_bytes - volume_free_bytes
        need_to_free = max(need_to_free, shortfall)
    over_count = max(0, len(live) - profile.max_torrents)

    decisions: List[EvictionDecision] = []
    if need_to_free <= 0 and over_count <= 0:
        return decisions

    # Lowest keep-value first; tie-break by largest size (frees more per eviction).
    ranked = sorted(
        live,
        key=lambda t: (_keep_value(t, max_seeders_endangered),
                       -(getattr(t, "size_bytes", 0) or 0)),
    )

    freed = 0
    evicted_count = 0
    protected_skipped: List = []
    for t in ranked:
        kv = _keep_value(t, max_seeders_endangered)
        size = getattr(t, "size_bytes", 0) or 0
        need_more_space = freed < need_to_free
        need_fewer = (len(live) - evicted_count) > profile.max_torrents
        if not need_more_space and not need_fewer:
            break
        if kv >= protect_endangerment and need_more_space and not need_fewer:
            protected_skipped.append(t)
            continue                                   # try to spare the critical ones

        if getattr(t, "seeders", 0) >= recovered_seeders:
            reason = f"recovered ({t.seeders} seeders) — swarm is healthy again"
        elif need_fewer and not need_more_space:
            reason = "over torrent-count cap"
        else:
            reason = f"lowest value (keep-score {kv}) — freeing budget"
        decisions.append(EvictionDecision(
            infohash=getattr(t, "infohash", ""), name=getattr(t, "name", ""),
            evict=True, reason=reason, freed_bytes=size))
        freed += size
        evicted_count += 1

    # Still short on space? We must dip into the protected (critical) set.
    if freed < need_to_free:
        for t in protected_skipped:
            if freed >= need_to_free:
                break
            size = getattr(t, "size_bytes", 0) or 0
            decisions.append(EvictionDecision(
                infohash=getattr(t, "infohash", ""), name=getattr(t, "name", ""),
                evict=True,
                reason="forced: over budget even after evicting low-value torrents",
                freed_bytes=size))
            freed += size

    return decisions
