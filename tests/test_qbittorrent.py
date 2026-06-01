"""Tests for the qBittorrent Web API client auth handling.

Regression coverage for the login-response shapes qBittorrent actually returns:
  * classic builds  -> 200 "Ok."            + session cookie
  * v5 + LocalHostAuth=false (loopback) -> 204 No Content + QBT_SID cookie, empty body
  * bad credentials -> 200 "Fails."         + no cookie
  * rate-limit ban  -> 403

The client is built inside the running loop (its constructor creates an
asyncio.Lock, which on Python 3.9 needs a current event loop).
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.qbittorrent import QBittorrentClient, QBitAuthError


async def _login(handler, password: str = ""):
    c = QBittorrentClient("http://127.0.0.1:8080", "admin", password)
    # Swap the real transport for an in-memory mock; keep the same base_url so
    # the Referer header logic and cookie jar behave exactly as in production.
    c._client = httpx.AsyncClient(base_url=c.base, transport=httpx.MockTransport(handler))
    await c.login()
    return c


def test_login_classic_ok():
    def handler(req):
        return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=abc; path=/"})

    c = asyncio.run(_login(handler))
    assert c._logged_in is True


def test_login_v5_localhost_bypass_204():
    # qBittorrent v5 with WebUI\LocalHostAuth=false returns 204 + cookie, empty body.
    def handler(req):
        return httpx.Response(204, headers={"set-cookie": "QBT_SID_8080=xyz; path=/; HttpOnly"})

    c = asyncio.run(_login(handler))
    assert c._logged_in is True


def test_login_wrong_password_fails():
    # Wrong credentials: 200 with body "Fails." and no session cookie.
    def handler(req):
        return httpx.Response(200, text="Fails.")

    with pytest.raises(QBitAuthError):
        asyncio.run(_login(handler, password="wrong"))


def test_login_banned_403():
    def handler(req):
        return httpx.Response(403, text="")

    with pytest.raises(QBitAuthError):
        asyncio.run(_login(handler))


async def _add(add_status: int, add_body: str) -> bool:
    def handler(req):
        if req.url.path.endswith("/auth/login"):
            return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=x; path=/"})
        if req.url.path.endswith("/torrents/add"):
            return httpx.Response(add_status, text=add_body)
        return httpx.Response(404)

    c = QBittorrentClient("http://127.0.0.1:8080", "admin", "")
    c._client = httpx.AsyncClient(base_url=c.base, transport=httpx.MockTransport(handler))
    return await c.add(urls=["magnet:?xt=urn:btih:abc"])


def test_add_v5_202_pending_accepted():
    # qBittorrent v5: 202 Accepted, URL fetched asynchronously (pending_count=1).
    body = '{"added_torrent_ids":[],"failure_count":0,"pending_count":1,"success_count":0}'
    assert asyncio.run(_add(202, body)) is True


def test_add_v5_failure_rejected():
    body = '{"added_torrent_ids":[],"failure_count":1,"pending_count":0,"success_count":0}'
    assert asyncio.run(_add(202, body)) is False


def test_add_classic_ok():
    assert asyncio.run(_add(200, "Ok.")) is True


# --------------------------------------------------------------------------- #
# Action endpoints: any 2xx is success (v5 may return 202/204, not just 200)
# --------------------------------------------------------------------------- #
def _login_resp(req):
    return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=x; path=/"})


async def _build(handler):
    c = QBittorrentClient("http://127.0.0.1:8080", "admin", "")
    c._client = httpx.AsyncClient(base_url=c.base, transport=httpx.MockTransport(handler))
    return c


def _action(method_name, status, *, capture=None):
    async def go():
        def handler(req):
            if req.url.path.endswith("/auth/login"):
                return _login_resp(req)
            if capture is not None:
                capture["body"] = req.content.decode()
            return httpx.Response(status)
        c = await _build(handler)
        return await getattr(c, method_name)(["A" * 40], "x") if method_name in ("set_category", "add_tags") \
            else await getattr(c, method_name)(["A" * 40])
    return asyncio.run(go())


def test_action_accepts_204():
    assert _action("pause", 204) is True
    assert _action("resume", 204) is True
    assert _action("reannounce", 204) is True


def test_action_accepts_202():
    assert _action("delete", 202) is True
    assert _action("set_category", 202) is True
    assert _action("add_tags", 202) is True


def test_action_rejects_4xx_5xx():
    assert _action("pause", 400) is False
    assert _action("delete", 403) is False
    assert _action("reannounce", 500) is False


def test_set_share_limits_includes_v5_action_param():
    cap = {}
    async def go():
        def handler(req):
            if req.url.path.endswith("/auth/login"):
                return _login_resp(req)
            cap["body"] = req.content.decode()
            return httpx.Response(200)
        c = await _build(handler)
        return await c.set_share_limits(["A" * 40], ratio_limit=2.0)
    assert asyncio.run(go()) is True
    assert "shareLimitAction" in cap["body"]   # v5 requires it (else HTTP 400)


def test_create_category_409_is_success():
    async def go():
        def handler(req):
            if req.url.path.endswith("/auth/login"):
                return _login_resp(req)
            return httpx.Response(409)
        c = await _build(handler)
        return await c.create_category("cat")
    assert asyncio.run(go()) is True


# --------------------------------------------------------------------------- #
# health(): distinguish bad-password from unreachable
# --------------------------------------------------------------------------- #
def test_health_ok():
    async def go():
        def handler(req):
            if req.url.path.endswith("/auth/login"):
                return _login_resp(req)
            return httpx.Response(200, text="v5.2.1")
        return await (await _build(handler)).health()
    assert asyncio.run(go()) == "ok"


def test_health_auth_when_login_rejected():
    async def go():
        def handler(req):
            return httpx.Response(200, text="Fails.")   # login fails, no cookie
        return await (await _build(handler)).health()
    assert asyncio.run(go()) == "auth"


def test_health_down_when_unreachable():
    async def go():
        def handler(req):
            raise httpx.ConnectError("connection refused")
        return await (await _build(handler)).health()
    assert asyncio.run(go()) == "down"


def test_login_double_checked_lock_skips_second_login():
    # Once logged in, a second login() must early-return (no second POST) —
    # the guard that prevents a cold-start herd of simultaneous logins.
    async def go():
        n = {"logins": 0}
        def handler(req):
            if req.url.path.endswith("/auth/login"):
                n["logins"] += 1
                return _login_resp(req)
            return httpx.Response(200, text="v5")
        c = await _build(handler)
        await c.login()
        await c.login()
        return n["logins"]
    assert asyncio.run(go()) == 1
