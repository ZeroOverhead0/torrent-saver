"""Tests for AI worth-curation: blend, JSON extraction, graceful-degrade, ordering."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app import actions, broker, config, curator_ai, decorrelate


def test_blend_no_worth_is_identity():
    assert curator_ai.blend(50.0, None) == 50.0
    assert curator_ai.blend(0.0, None) == 0.0


def test_blend_weights():
    assert curator_ai.blend(40.0, 80, weight=0.5) == 60.0
    assert curator_ai.blend(40.0, 80, weight=0.0) == 40.0
    assert curator_ai.blend(40.0, 80, weight=1.0) == 80.0


def test_extract_json_from_prose():
    assert broker._extract_json('sure: {"worth": 70, "reason": "rare dataset"} done') \
        == {"worth": 70, "reason": "rare dataset"}
    assert broker._extract_json("no json here") is None
    assert broker._extract_json("{bad json}") is None


def test_annotate_worth_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "get_bool", lambda k: False)   # ai_curation_enabled off
    cands = [SimpleNamespace(normalised_hash=lambda: "a" * 40, worth=None)]
    assert asyncio.run(curator_ai.annotate_worth(cands)) == 0
    assert cands[0].worth is None                              # untouched


def test_annotate_worth_empty_is_zero():
    assert asyncio.run(curator_ai.annotate_worth([])) == 0


def test_order_key_worth_raises_priority(monkeypatch):
    # participation on (so a key exists), sampling off (so the score is the
    # deterministic blended priority — isolates worth's effect).
    monkeypatch.setattr(config, "get_bool", lambda k: k == "participation")
    monkeypatch.setattr(config, "get_float", lambda k, d=0.0:
                        {"ai_curation_blend_weight": 0.5, "participation_max_skip": 0.5}.get(k, d))
    monkeypatch.setattr(decorrelate.config, "install_seed", lambda: 1)
    decorrelate._live = None
    key = actions._build_order_key(SimpleNamespace(max_torrents=100))
    hi = SimpleNamespace(endangerment=40.0, worth=90, size_bytes=0,
                         normalised_hash=lambda: "a" * 40)
    lo = SimpleNamespace(endangerment=40.0, worth=None, size_bytes=0,
                         normalised_hash=lambda: "b" * 40)
    # tuple is (skipped, -score, size); higher blended priority -> more-negative -score -> sorts first
    assert key(hi)[1] < key(lo)[1]
