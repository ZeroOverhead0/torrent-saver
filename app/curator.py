"""Curator — choose which endangered candidates to rescue within the budget.

Given scored + deduped candidates and the active machine profile, decide what to
download. Greedy by endangerment (rescue the most-at-risk first), tie-broken by
smaller size so a fixed budget saves as many torrents as possible. Honours the
legal filter, per-torrent size cap, redundancy ceiling, endangerment floor, the
disk budget and the torrent-count cap.
"""
from __future__ import annotations

from typing import List

from app.config import MachineProfile
from app.models import Candidate, RescueDecision


def plan_rescues(candidates: List[Candidate], current_library_bytes: int,
                 profile: MachineProfile, *, legal_only: bool = True,
                 max_seeders_endangered: int = 5, min_seeders_to_rescue: int = 1,
                 max_redundancy: int = 2, min_endangerment: float = 35.0,
                 current_torrent_count: int = 0,
                 already_tracked: set | None = None,
                 order_key=None) -> List[RescueDecision]:
    """Return a RescueDecision for every candidate, accepted ones first.

    `already_tracked` is a set of infohashes we already seed (skipped silently).
    `order_key`, when given, replaces the default strict-endangerment ordering —
    the caller injects a decorrelated sort (per-install sampling + participation)
    so a fleet of installs spreads across the long tail instead of dogpiling.
    """
    already_tracked = already_tracked or set()
    budget_remaining = max(0, profile.max_disk_bytes - current_library_bytes)
    slots_remaining = max(0, profile.max_torrents - current_torrent_count)

    accepted: List[RescueDecision] = []
    rejected: List[RescueDecision] = []
    running = current_library_bytes

    # Sort: most endangered first; for equal risk prefer the smaller torrent so
    # the budget rescues more distinct content. A caller-supplied order_key takes
    # over to decorrelate selection across the fleet (still slot/budget-capped).
    if order_key is not None:
        ordered = sorted(candidates, key=order_key)
    else:
        ordered = sorted(candidates, key=lambda c: (-c.endangerment, c.size_bytes or 0))

    for c in ordered:
        ih = c.normalised_hash()
        if ih in already_tracked:
            rejected.append(RescueDecision(c, False, "already seeding"))
            continue
        if not c.rescuable:
            # Not downloadable: 0 seeders and no webseed.
            rejected.append(RescueDecision(c, False, "no live seeder or webseed — cannot download"))
            continue
        if c.seeders > max_seeders_endangered:
            rejected.append(RescueDecision(c, False,
                            f"healthy ({c.seeders} seeders) — not endangered"))
            continue
        if legal_only and not c.legal:
            rejected.append(RescueDecision(c, False, "blocked by legal-only mode"))
            continue
        if c.redundancy >= max_redundancy:
            rejected.append(RescueDecision(c, False,
                            f"{c.redundancy} healthy duplicate(s) already exist"))
            continue
        if c.endangerment < min_endangerment:
            rejected.append(RescueDecision(c, False,
                            f"endangerment {c.endangerment} below floor {min_endangerment}"))
            continue
        size = c.size_bytes or 0
        if size and profile.max_torrent_size_bytes and size > profile.max_torrent_size_bytes:
            rejected.append(RescueDecision(c, False, "larger than per-torrent size cap"))
            continue
        if slots_remaining <= 0:
            rejected.append(RescueDecision(c, False, "torrent-count cap reached"))
            continue
        # Unknown size: reserve the per-torrent cap so the budget can never be
        # over-filled by a torrent whose real size we don't yet know.
        budget_size = size if size else profile.max_torrent_size_bytes
        if budget_size and running + budget_size > profile.max_disk_bytes:
            rejected.append(RescueDecision(c, False, "would exceed disk budget"))
            continue

        running += budget_size
        slots_remaining -= 1
        budget_remaining = max(0, profile.max_disk_bytes - running)
        accepted.append(RescueDecision(c, True,
                        f"rescue: {c.endangerment} endangerment, {c.seeders} seeder(s)",
                        projected_total_bytes=running))

    return accepted + rejected


def library_bytes(tracked: list) -> int:
    """Sum sizes of currently-seeded (non-evicted) torrents."""
    total = 0
    for t in tracked:
        if getattr(t, "evicted_at", None):
            continue
        total += getattr(t, "size_bytes", 0) or 0
    return total
