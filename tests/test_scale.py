"""Tests for the anti-herd scale layer: decorrelation + honest graduation."""
from __future__ import annotations

import app.decorrelate as decorrelate
from app.endangerment import (effective_seeders, evaluate_graduation,
                              seeder_slope)


# --------------------------------------------------------------------------- #
# Decorrelation
# --------------------------------------------------------------------------- #
def _fixed_seed(monkeypatch, value=1234567):
    monkeypatch.setattr(decorrelate.config, "install_seed", lambda: value)
    decorrelate._live = None   # reset the live jitter stream


def test_rng_is_deterministic_per_salt(monkeypatch):
    _fixed_seed(monkeypatch)
    a = [decorrelate.rng("hold:abc").random() for _ in range(5)]
    b = [decorrelate.rng("hold:abc").random() for _ in range(5)]
    assert a == b                                  # stable across calls


def test_rng_differs_by_salt(monkeypatch):
    _fixed_seed(monkeypatch)
    assert decorrelate.rng("a").random() != decorrelate.rng("b").random()


def test_rng_differs_by_install_seed(monkeypatch):
    monkeypatch.setattr(decorrelate.config, "install_seed", lambda: 111)
    one = decorrelate.rng("x").random()
    monkeypatch.setattr(decorrelate.config, "install_seed", lambda: 222)
    two = decorrelate.rng("x").random()
    assert one != two                              # two installs decorrelate


def test_jitter_within_bounds(monkeypatch):
    _fixed_seed(monkeypatch)
    for _ in range(200):
        f = decorrelate.jitter(0.20)
        assert 0.80 <= f <= 1.20
    assert decorrelate.jitter(0.0) == 1.0


# --------------------------------------------------------------------------- #
# effective_seeders / slope
# --------------------------------------------------------------------------- #
def test_effective_seeders_discounts_no_demand():
    assert effective_seeders(20, leechers=0) == 10     # no leechers -> halved
    assert effective_seeders(20, leechers=5) == 20     # demand present -> full
    assert effective_seeders(0, leechers=0) == 0
    assert effective_seeders(1, leechers=0) == 1       # floor of 1


def test_seeder_slope_sign():
    rising = [(0, 1), (3600, 5), (7200, 9)]
    falling = [(0, 9), (3600, 5), (7200, 1)]
    flat = [(0, 5), (3600, 5)]
    assert seeder_slope(rising) > 0
    assert seeder_slope(falling) < 0
    assert abs(seeder_slope(flat)) < 1e-9
    assert seeder_slope([(0, 5)]) == 0.0


# --------------------------------------------------------------------------- #
# evaluate_graduation state machine
# --------------------------------------------------------------------------- #
NOW = 1_000_000.0
CFG = dict(window_min=360, min_samples=6, max_decline_per_hr=1.0,
           require_demand=True, hard_seeders=40, min_tenure_min=1440)
OLD = NOW - 2 * 86400          # 2 days old -> tenure ok


def _samples(seeders, leechers=2, n=6, span_h=6.0):
    step = span_h * 3600 / (n - 1)
    return [(NOW - span_h * 3600 + i * step, seeders, leechers) for i in range(n)]


def _ev(**over):
    base = dict(samples=_samples(15), cur_seeders=15, cur_leechers=2, added_at=OLD,
                eligible_at=None, now=NOW, threshold=10, hold_seconds=3600, **CFG)
    base.update(over)
    return evaluate_graduation(**base)


def test_set_hold_when_first_qualifying():
    assert _ev() == "set_hold"


def test_wait_while_holding():
    assert _ev(eligible_at=NOW + 500) == "wait"


def test_release_when_hold_elapsed():
    assert _ev(eligible_at=NOW - 1) == "release"


def test_skip_when_not_enough_samples():
    assert _ev(samples=_samples(15, n=3)) == "skip"


def test_skip_when_below_threshold():
    assert _ev(samples=_samples(9)) == "skip"          # 9 < 10


def test_no_demand_discount_blocks_release():
    # 18 seeders but zero leechers -> effective 9 < 10 -> not sustained-healthy.
    assert _ev(samples=_samples(18, leechers=0), cur_seeders=18, cur_leechers=0) == "skip"


def test_declining_swarm_does_not_graduate():
    declining = [(NOW - 6 * 3600 + i * (6 * 3600 / 5), 30 - i * 4, 5) for i in range(6)]
    assert _ev(samples=declining, cur_seeders=10) == "skip"   # slope steeply negative


def test_clear_hold_when_no_longer_qualifying():
    assert _ev(samples=[], eligible_at=NOW + 100) == "clear_hold"


def test_hard_backstop_releases_without_window():
    assert _ev(samples=[], cur_seeders=50, cur_leechers=5) == "release"


def test_young_torrent_never_graduates():
    assert _ev(added_at=NOW - 100) == "skip"           # tenure < 24h
