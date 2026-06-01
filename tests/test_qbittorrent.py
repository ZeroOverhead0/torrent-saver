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
