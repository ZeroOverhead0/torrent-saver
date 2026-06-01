"""Tests for graduation — releasing torrents whose swarm has fully recovered."""
from __future__ import annotations

from types import SimpleNamespace

from app.actions import graduation_victims


def _t(seeders: int, progress: float = 1.0, mode: str = "rescue"):
    return SimpleNamespace(infohash="a" * 40, name="t", mode=mode,
                           progress=progress, seeders=seeders)


def test_graduates_recovered_complete_rescue():
    assert len(graduation_victims([_t(10)], threshold=10)) == 1


def test_below_threshold_not_graduated():
    assert graduation_victims([_t(9)], threshold=10) == []


def test_incomplete_download_not_graduated():
    # Even a healthy swarm should not release a copy we never finished rescuing.
    assert graduation_victims([_t(50, progress=0.5)], threshold=10) == []


def test_demand_mode_seed_not_graduated():
    assert graduation_victims([_t(50, mode="demand")], threshold=10) == []


def test_mixed_selection():
    ts = [_t(10), _t(5), _t(20, progress=0.9), _t(99, mode="demand"), _t(11)]
    victims = graduation_victims(ts, threshold=10)
    assert sorted(t.seeders for t in victims) == [10, 11]
