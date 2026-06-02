"""Tests for the public-release security pass."""
from __future__ import annotations

import base64

import app.scrape as scrape
from app import actions, auth
from app.models import VpnStatus
from app.vpn import assess


# --------------------------------------------------------------------------- #
# Access gate (bind-aware)
# --------------------------------------------------------------------------- #
def test_bound_offbox():
    for off in ("0.0.0.0", "::", "*", "192.168.1.5", "10.0.0.2", "example.com", "8.8.8.8"):
        assert auth.bound_offbox(off) is True, off
    for local in ("127.0.0.1", "localhost", "::1", "", "127.0.0.5"):
        assert auth.bound_offbox(local) is False, local


def test_is_loopback():
    assert auth.is_loopback("127.0.0.1")
    assert auth.is_loopback("::1")
    assert auth.is_loopback("localhost")
    assert not auth.is_loopback("10.0.0.1")
    assert not auth.is_loopback("8.8.8.8")
    assert not auth.is_loopback("")


def test_basic_ok():
    good = "Basic " + base64.b64encode(b"admin:secret").decode()
    assert auth.basic_ok(good, "secret") is True
    assert auth.basic_ok(good, "wrong") is False
    assert auth.basic_ok("Basic " + base64.b64encode(b"root:secret").decode(), "secret") is False
    assert auth.basic_ok("", "secret") is False
    assert auth.basic_ok("Bearer x", "secret") is False


def test_access_gate_inert_on_loopback_required_offbox(monkeypatch):
    monkeypatch.setenv("TORRENTSAVER_BIND", "0.0.0.0")
    monkeypatch.setenv("TORRENTSAVER_PASSWORD", "testpw123")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:                       # client.host == 'testclient' (off-box)
        assert c.get("/", follow_redirects=False).status_code == 401
        assert c.get("/login").status_code == 200    # login page is open
        hdr = {"Authorization": "Basic " + base64.b64encode(b"admin:testpw123").decode()}
        assert c.get("/api/status", headers=hdr).status_code == 200
        assert c.get("/static/app.js").status_code in (200, 404)  # static is open (200 or missing)


# --------------------------------------------------------------------------- #
# SSRF: resolve-once rejects private / loopback / reserved
# --------------------------------------------------------------------------- #
def test_resolve_safe_ip(monkeypatch):
    def res(ip):
        monkeypatch.setattr(scrape.socket, "gethostbyname", lambda h: ip)
    res("127.0.0.1"); assert scrape._resolve_safe_ip("evil.test") is None
    res("10.0.0.5"); assert scrape._resolve_safe_ip("evil.test") is None
    res("169.254.1.1"); assert scrape._resolve_safe_ip("evil.test") is None
    res("8.8.8.8"); assert scrape._resolve_safe_ip("dns.google") == "8.8.8.8"
    assert scrape._resolve_safe_ip("") is None


# --------------------------------------------------------------------------- #
# VPN leak detection fails CLOSED
# --------------------------------------------------------------------------- #
def test_safe_to_seed_requires_verifiable_no_leak():
    base = dict(connected=True, killswitch_engaged=True)
    assert VpnStatus(**base, leak_check_possible=False).safe_to_seed is False  # can't verify
    assert VpnStatus(**base, leak_check_possible=True, leaking=False).safe_to_seed is True
    assert VpnStatus(**base, leak_check_possible=True, leaking=True).safe_to_seed is False


def test_assess_no_baseline_disables_leak_check():
    st = assess(interface="utun4", iface_ip="10.0.0.2", public_ip="1.2.3.4",
                baseline_ip="", qbit_bound_interface="utun4")
    assert st.leak_check_possible is False
    assert st.safe_to_seed is False
    st2 = assess(interface="utun4", iface_ip="10.0.0.2", public_ip="5.6.7.8",
                 baseline_ip="1.2.3.4", qbit_bound_interface="utun4")
    assert st2.leak_check_possible is True
    assert st2.safe_to_seed is True


# --------------------------------------------------------------------------- #
# Prowlarr 'trust_legal' must NOT exempt content from the VPN gate
# --------------------------------------------------------------------------- #
def test_prowlarr_not_vpn_exempt(monkeypatch):
    # vpn_required on, no usable VPN status -> only whitelisted sources may seed.
    monkeypatch.setattr(actions.config, "get_bool",
                        lambda k: True if k == "vpn_required" else False)
    assert actions._seeding_allowed(None, True, "internet_archive")[0] is True
    assert actions._seeding_allowed(None, True, "linuxtracker")[0] is True
    assert actions._seeding_allowed(None, True, "prowlarr")[0] is False   # the bypass is closed
    assert actions._seeding_allowed(None, True, "manual")[0] is False
