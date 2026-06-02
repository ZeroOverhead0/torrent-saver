"""Tests for demand-seeding ('help the network') — selection, max-upload, distro radar."""
from __future__ import annotations

import json
from types import SimpleNamespace

from app import config
from app.actions import _VPN_EXEMPT_SOURCES, _seeding_allowed
from app.seeder import _demand_add_limits, _select_demand
from app.sources.distro_radar import DistroRadar


def _c(ih, leechers, seeders=5, legal=True):
    return SimpleNamespace(infohash=ih, leechers=leechers, seeders=seeders, legal=legal)


def test_select_demand_ranks_by_leechers_and_filters():
    cands = [_c("a" * 40, 5), _c("b" * 40, 200), _c("c" * 40, 50), _c("d" * 40, 0)]
    picks = _select_demand(cands, min_seeders=1, min_leechers=10, legal_only=True, slots=2)
    assert [p.infohash[0] for p in picks] == ["b", "c"]      # 200, then 50; 5 and 0 filtered


def test_select_demand_respects_legal_only():
    cands = [_c("a" * 40, 100, legal=False), _c("b" * 40, 50, legal=True)]
    assert [p.infohash[0] for p in _select_demand(
        cands, min_seeders=1, min_leechers=10, legal_only=True, slots=5)] == ["b"]
    assert [p.infohash[0] for p in _select_demand(
        cands, min_seeders=1, min_leechers=10, legal_only=False, slots=5)] == ["a", "b"]


def test_select_demand_requires_downloadable():
    cands = [_c("a" * 40, 100, seeders=0)]                   # demand, but not downloadable
    assert _select_demand(cands, min_seeders=1, min_leechers=10, legal_only=True, slots=5) == []


def test_demand_add_limits_max_upload_uncaps():
    assert _demand_add_limits(config.PROFILES["small"], True) == {
        "ratio_limit": -1.0, "seeding_time_limit": -1, "upload_limit": None}


def test_demand_add_limits_profile_capped():
    p = config.PROFILES["small"]
    lim = _demand_add_limits(p, False)
    assert lim["ratio_limit"] == p.ratio_limit
    assert lim["upload_limit"] == p.upload_limit_bytes_s     # small caps at 1 MiB/s
    assert lim["seeding_time_limit"] == p.seeding_time_limit_min


def test_unlimited_profile_is_uncapped():
    p = config.PROFILES["unlimited"]
    assert p.upload_limit_bytes_s == 0 and p.ratio_limit == -1.0 and p.seeding_time_limit_min == -1
    lim = _demand_add_limits(p, False)                       # even without the toggle
    assert lim == {"ratio_limit": -1.0, "seeding_time_limit": -1, "upload_limit": None}


def test_distro_radar_is_legal_and_vpn_exempt():
    assert DistroRadar.legal is True
    assert "distro_radar" in _VPN_EXEMPT_SOURCES
    assert _seeding_allowed(None, True, "distro_radar")[0] is True     # seeds without VPN


def test_distro_radar_curated_parses_only_magnets(monkeypatch):
    mag = "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=Test+Distro"
    monkeypatch.setattr(config, "get_str",
                        lambda k, d="": json.dumps([mag, "https://not-a-magnet"]) if k == "distro_radar_torrents" else d)
    assert DistroRadar._curated() == [mag]
