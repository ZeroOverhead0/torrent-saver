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


# --------------------------------------------------------------------------- #
# Honest graduation — keep the seeder signal trustworthy at fleet scale.
# A swarm of fellow rescuers inflates the instantaneous count, so a torrent only
# graduates after SUSTAINED, demand-backed, non-declining health, and even then
# release is staggered (see actions.graduate_recovered).
# --------------------------------------------------------------------------- #
def effective_seeders(seeders: int, leechers: int = 0,
                      organic_floor_ratio: float = 0.05) -> int:
    """Discount a seeder count that shows no demand.

    With no leechers we can't tell organic seeders from fellow rescuers, so we
    halve the count — deflating the rescuer-reflection that would otherwise
    declare a torrent "saved" the instant a fleet piles on."""
    if seeders <= 0:
        return 0
    if (leechers / (seeders + 1)) >= organic_floor_ratio:
        return seeders
    return max(1, int(seeders * 0.5))


def seeder_slope(points) -> float:
    """Least-squares slope of seeders over time, in seeders per hour.

    `points` = iterable of (timestamp_seconds, seeders). 0.0 for < 2 points."""
    pts = list(points)
    n = len(pts)
    if n < 2:
        return 0.0
    t0 = min(p[0] for p in pts)
    xs = [(p[0] - t0) / 3600.0 for p in pts]
    ys = [float(p[1]) for p in pts]
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return num / denom


def _sustained_healthy(samples, threshold, window_min, min_samples,
                       max_decline_per_hr, require_demand, now) -> bool:
    win = [s for s in samples if s[0] >= now - window_min * 60]
    if len(win) < min_samples:
        return False
    span = max(s[0] for s in win) - min(s[0] for s in win)
    if span < window_min * 60 * 0.9:                 # must actually span the window
        return False
    effs = [effective_seeders(s[1], s[2]) if require_demand else max(0, s[1]) for s in win]
    if min(effs) < threshold:                        # never dipped below the bar
        return False
    if seeder_slope([(s[0], s[1]) for s in win]) < -abs(max_decline_per_hr):
        return False                                 # swarm is sliding — keep seeding
    return True


def evaluate_graduation(*, samples, cur_seeders, cur_leechers, added_at, eligible_at,
                        now, threshold, window_min, min_samples, max_decline_per_hr,
                        require_demand, hard_seeders, min_tenure_min, hold_seconds) -> str:
    """Pure decision for whether a recovered torrent may be released.

    Returns one of:
      'release'    — delete it now (free the slot/disk).
      'set_hold'   — it qualifies; caller should stamp eligible_at = now + hold_seconds.
      'wait'       — qualifying and holding; do nothing this cycle.
      'clear_hold' — no longer qualifies; clear a previously-set hold.
      'skip'       — not eligible; nothing to do.

    `samples` = list of (ts, seeders, leechers). Every degrade path biases toward
    KEEP SEEDING."""
    tenure_ok = (now - added_at) >= min_tenure_min * 60
    cur_eff = effective_seeders(cur_seeders, cur_leechers) if require_demand else max(0, cur_seeders)

    # Overwhelming health (and old enough): release regardless of jitter/hold.
    if tenure_ok and cur_eff >= hard_seeders:
        return "release"

    qualifies = _sustained_healthy(samples, threshold, window_min, min_samples,
                                   max_decline_per_hr, require_demand, now)
    if not (qualifies and tenure_ok):
        return "clear_hold" if eligible_at else "skip"
    if eligible_at is None:
        return "set_hold"
    if now >= eligible_at:
        return "release"
    return "wait"
