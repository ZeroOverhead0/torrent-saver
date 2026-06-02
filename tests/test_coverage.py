"""Tests for Phase-2 fleet-coverage: decorrelated selection, participation, dedup floor."""
from __future__ import annotations

from types import SimpleNamespace

import app.dedup as dedup
from app import actions, config, decorrelate
from app.config import PROFILES
from app.curator import plan_rescues


def _cand(ih, endangerment, size=1000):
    return SimpleNamespace(endangerment=endangerment, size_bytes=size,
                           normalised_hash=(lambda ih=ih: ih))


def _on(monkeypatch, seed=42, sharpness=1.0, max_skip=0.5):
    monkeypatch.setattr(config, "get_bool", lambda k: True)            # sampling + participation on
    monkeypatch.setattr(config, "get_float", lambda k, d=0.0:
                        {"rescue_sampling_sharpness": sharpness,
                         "participation_max_skip": max_skip}.get(k, d))
    monkeypatch.setattr(decorrelate.config, "install_seed", lambda: seed)
    decorrelate._live = None


def test_order_key_never_skips_critical(monkeypatch):
    _on(monkeypatch)
    key = actions._build_order_key(SimpleNamespace(max_torrents=100))
    skipped, _score, _size = key(_cand("c" * 40, 100.0))
    assert skipped is False                                            # endangerment 100 never skipped


def test_order_key_decorrelates_across_installs(monkeypatch):
    cands = [_cand(f"{i:040x}", 50.0) for i in range(12)]              # equal endangerment

    def order(seed):
        _on(monkeypatch, seed=seed)
        k = actions._build_order_key(SimpleNamespace(max_torrents=100))
        return [c.normalised_hash() for c in sorted(cands, key=k)]

    assert order(1) != order(2)                                        # two installs differ


def test_order_key_stable_for_one_install(monkeypatch):
    cands = [_cand(f"{i:040x}", 50.0) for i in range(12)]
    _on(monkeypatch, seed=5)
    k = actions._build_order_key(SimpleNamespace(max_torrents=100))
    a = [c.normalised_hash() for c in sorted(cands, key=k)]
    k2 = actions._build_order_key(SimpleNamespace(max_torrents=100))
    b = [c.normalised_hash() for c in sorted(cands, key=k2)]
    assert a == b                                                      # deterministic per install


def test_participation_skips_low_endangerment_more(monkeypatch):
    _on(monkeypatch, seed=7)
    key = actions._build_order_key(SimpleNamespace(max_torrents=100))

    def skipped_count(e):
        return sum(1 for i in range(200) if key(_cand(f"{i:040x}", e))[0])

    assert skipped_count(10.0) > skipped_count(90.0)                   # low demand skipped far more


def test_order_key_none_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "get_bool", lambda k: False)
    assert actions._build_order_key(SimpleNamespace(max_torrents=100)) is None


# --------------------------------------------------------------------------- #
# plan_rescues honours an injected order, and still fills/caps
# --------------------------------------------------------------------------- #
def _full(ih, endang=50.0):
    return SimpleNamespace(normalised_hash=(lambda ih=ih: ih), rescuable=True,
                           seeders=1, legal=True, redundancy=0, endangerment=endang,
                           size_bytes=1000, infohash=ih)


def test_plan_rescues_respects_order_key():
    a, b, c = _full("a" * 40), _full("b" * 40), _full("c" * 40)
    rank = {"c" * 40: 0, "a" * 40: 1, "b" * 40: 2}                     # force c, a, b
    decisions = plan_rescues([a, b, c], 0, PROFILES["large"], legal_only=True,
                             min_endangerment=0.0,
                             order_key=lambda x: rank[x.normalised_hash()])
    accepted = [d.candidate.normalised_hash()[0] for d in decisions if d.rescue]
    assert accepted == ["c", "a", "b"]


# --------------------------------------------------------------------------- #
# Honest dedup: raised healthy floor
# --------------------------------------------------------------------------- #
def _clone(ih, seeders, name="Ubuntu 24.04.1 LTS desktop amd64", size=1000):
    return SimpleNamespace(infohash=ih, name=name, size_bytes=size, seeders=seeders)


def test_redundancy_healthy_floor():
    cand = _clone("a" * 40, 0)
    others = [_clone("b" * 40, 12), _clone("c" * 40, 20)]
    assert dedup.redundancy(cand, others, healthy_seeders=15) == 1     # only the 20-seeder clone
    assert dedup.redundancy(cand, others, healthy_seeders=10) == 2     # both at the old floor
