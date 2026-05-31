"""Endangerment scoring — how badly does a torrent need rescuing?

Higher score (0..100) = closer to extinction and more worth saving. The shape:
  * fewer seeders  -> exponentially more endangered
  * leechers waiting on a near-dead torrent -> demand boost (people want it, it's dying)
  * healthy duplicates already exist -> redundancy penalty (someone else holds it)
  * old + barely-seeded -> small age boost (long-tail content quietly vanishing)

Pure functions only — no DB or network — so the logic is unit-testable.
"""
from __future__ import annotations

import math


def endangerment_score(seeders: int, leechers: int = 0, *, completed: int = 0,
                       age_days: float | None = None, redundancy: int = 0,
                       max_seeders_endangered: int = 5) -> float:
    """Return an endangerment score in [0, 100]."""
    # --- base: exponential decay in seeder count ---------------------------- #
    k = max(1.0, max_seeders_endangered / 1.6)
    if seeders <= 0:
        base = 100.0                       # effectively gone (and undownloadable)
    else:
        base = 100.0 * math.exp(-(seeders - 1) / k)

    # --- demand boost: leechers waiting on a dying torrent ------------------ #
    ratio = leechers / (seeders + 1)
    demand_boost = 20.0 * (1.0 - math.exp(-ratio / 3.0))

    # --- age boost: long-tail content that's slowly disappearing ------------ #
    age_boost = 0.0
    if age_days is not None and seeders <= max_seeders_endangered:
        age_boost = min(8.0, age_days / 120.0)

    # --- history boost: it was downloaded a lot but the swarm has collapsed - #
    # "recently/once popular, now dying" outranks "nobody ever wanted it".
    history_boost = 0.0
    if completed and seeders <= max_seeders_endangered:
        history_boost = min(8.0, math.log10(completed + 1) * 2.0)

    # --- redundancy penalty: healthy clones make this less urgent ----------- #
    redundancy_penalty = min(base + demand_boost, redundancy * 30.0)

    score = base + demand_boost + age_boost + history_boost - redundancy_penalty
    return round(max(0.0, min(100.0, score)), 1)


def endangerment_band(score: float) -> str:
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 40:
        return "moderate"
    if score >= 20:
        return "low"
    return "safe"


def is_endangered(seeders: int, *, max_seeders_endangered: int = 5) -> bool:
    """A torrent counts as endangered when its seeder count is at or below the
    configured threshold (0 seeders is endangered but separately unrescuable)."""
    return seeders <= max_seeders_endangered


def is_rescuable(seeders: int, *, min_seeders_to_rescue: int = 1,
                 has_webseed: bool = False, include_dead: bool = False) -> bool:
    """Can we actually obtain this torrent's data?

    True if it has a live seeder, or a webseed (BEP 19 — served over HTTP even
    when the swarm is at zero; the canonical 'alive only on one host' case).
    A 0-seeder/no-webseed torrent is **truly dead** — qBittorrent would search
    forever and never complete — so it's only rescuable when the user opts into
    long-shots via `include_dead` (qBittorrent may still find a peer over DHT)."""
    if has_webseed:
        return True
    if seeders >= max(1, min_seeders_to_rescue):
        return True
    return bool(include_dead)
