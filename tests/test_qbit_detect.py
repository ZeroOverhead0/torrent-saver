"""Tests for qBittorrent auto-detection ordering (no real network)."""
from __future__ import annotations

import asyncio

import app.qbit_detect as detector


def test_detect_returns_first_candidate_that_answers(monkeypatch):
    async def fake(url, timeout=2.0):
        return url == "http://127.0.0.1:8081"
    monkeypatch.setattr(detector, "_is_qbittorrent", fake)
    assert asyncio.run(detector.detect()) == "http://127.0.0.1:8081"


def test_detect_tries_extra_url_first(monkeypatch):
    async def fake(url, timeout=2.0):
        return True   # everything answers; order must prefer `extra`
    monkeypatch.setattr(detector, "_is_qbittorrent", fake)
    assert asyncio.run(detector.detect(extra=["http://127.0.0.1:9999"])) == "http://127.0.0.1:9999"


def test_detect_none_when_nothing_answers(monkeypatch):
    async def fake(url, timeout=2.0):
        return False
    monkeypatch.setattr(detector, "_is_qbittorrent", fake)
    assert asyncio.run(detector.detect()) is None


def test_detect_dedupes_extra_against_candidates(monkeypatch):
    calls = []
    async def fake(url, timeout=2.0):
        calls.append(url)
        return False
    monkeypatch.setattr(detector, "_is_qbittorrent", fake)
    asyncio.run(detector.detect(extra=["http://127.0.0.1:8080"]))   # same as a candidate
    assert calls.count("http://127.0.0.1:8080") == 1                # probed once, not twice
