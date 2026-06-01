"""Async qBittorrent Web API (v2) client.

Covers everything Torrent Saver needs: auth, add/delete, info/properties/trackers,
share-limit + upload-limit control, category/tag management, pause/resume
(handles the v5 stop/start rename), transfer stats and preferences (used by the
hardening + VPN-binding modules). Degrades gracefully when qBittorrent is absent.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import httpx

log = logging.getLogger("torrentsaver.qbit")


class QBitError(Exception):
    """Base class for qBittorrent client errors."""


class QBitUnavailable(QBitError):
    """qBittorrent could not be reached."""


class QBitAuthError(QBitError):
    """Login failed (bad credentials or banned)."""


class QBittorrentClient:
    def __init__(self, url: str, username: str = "", password: str = "",
                 timeout: float = 20.0):
        self.base = url.rstrip("/")
        self.username = username
        self.password = password
        self._client = httpx.AsyncClient(base_url=self.base, timeout=timeout,
                                          follow_redirects=True)
        self._logged_in = False
        self._lock = asyncio.Lock()
        self._api_major: Optional[int] = None

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ #
    # Auth + low-level request
    # ------------------------------------------------------------------ #
    async def login(self) -> None:
        async with self._lock:
            try:
                resp = await self._client.post(
                    "/api/v2/auth/login",
                    data={"username": self.username, "password": self.password},
                    headers={"Referer": self.base},
                )
            except httpx.HTTPError as e:
                raise QBitUnavailable(f"cannot reach qBittorrent at {self.base}: {e}") from e
            if resp.status_code == 403:
                raise QBitAuthError("qBittorrent refused login (IP banned — too many attempts)")
            text = (resp.text or "").strip()
            # Classic qBittorrent returns 200 "Ok." with a session cookie. qBittorrent
            # v5 with WebUI\LocalHostAuth=false (loopback auth bypass) instead returns
            # 204 No Content with a QBT_SID cookie and an empty body — also a success.
            # A wrong password returns 200 "Fails." with no cookie, which still fails.
            authed = any("SID" in name for name in resp.cookies.keys())
            if not (resp.is_success and (text.lower() == "ok." or authed)):
                raise QBitAuthError("qBittorrent login failed — check username/password")
            self._logged_in = True

    async def _request(self, method: str, path: str, *, retry: bool = True, **kw) -> httpx.Response:
        if not self._logged_in:
            await self.login()
        try:
            resp = await self._client.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise QBitUnavailable(f"qBittorrent request failed: {e}") from e
        if resp.status_code == 403 and retry:
            # Session expired — log in again and retry exactly once.
            self._logged_in = False
            await self.login()
            return await self._request(method, path, retry=False, **kw)
        return resp

    async def _get(self, path: str, **kw) -> httpx.Response:
        return await self._request("GET", path, **kw)

    async def _post(self, path: str, **kw) -> httpx.Response:
        return await self._request("POST", path, **kw)

    # ------------------------------------------------------------------ #
    # App / health
    # ------------------------------------------------------------------ #
    async def version(self) -> str:
        resp = await self._get("/api/v2/app/version")
        return (resp.text or "").strip()

    async def api_major(self) -> int:
        if self._api_major is None:
            try:
                v = await self.version()           # e.g. "v5.0.3"
                self._api_major = int(v.lstrip("v").split(".")[0])
            except Exception:
                self._api_major = 4
        return self._api_major

    async def available(self) -> bool:
        try:
            await self.version()
            return True
        except QBitError:
            return False
        except Exception:
            return False

    async def get_preferences(self) -> dict:
        resp = await self._get("/api/v2/app/preferences")
        return resp.json()

    async def set_preferences(self, prefs: dict) -> bool:
        resp = await self._post("/api/v2/app/setPreferences",
                                data={"json": json.dumps(prefs)})
        return resp.status_code == 200

    async def transfer_info(self) -> dict:
        resp = await self._get("/api/v2/transfer/info")
        try:
            return resp.json()
        except Exception:
            return {}

    # ------------------------------------------------------------------ #
    # Torrent listing / properties
    # ------------------------------------------------------------------ #
    async def torrents_info(self, *, category: Optional[str] = None,
                            hashes: Optional[list] = None,
                            filter: Optional[str] = None) -> list:
        params: dict = {}
        if category is not None:
            params["category"] = category
        if hashes:
            params["hashes"] = "|".join(h.lower() for h in hashes)
        if filter:
            params["filter"] = filter
        resp = await self._get("/api/v2/torrents/info", params=params)
        try:
            return resp.json()
        except Exception:
            return []

    async def properties(self, infohash: str) -> dict:
        resp = await self._get("/api/v2/torrents/properties",
                               params={"hash": infohash.lower()})
        try:
            return resp.json()
        except Exception:
            return {}

    async def trackers(self, infohash: str) -> list:
        resp = await self._get("/api/v2/torrents/trackers",
                               params={"hash": infohash.lower()})
        try:
            return resp.json()
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    # Add / remove
    # ------------------------------------------------------------------ #
    async def add(self, *, urls: Optional[list] = None,
                  torrent_files: Optional[list] = None,
                  save_path: str = "", category: str = "", tags: str = "",
                  paused: bool = False, ratio_limit: Optional[float] = None,
                  seeding_time_limit: Optional[int] = None,
                  upload_limit: Optional[int] = None,
                  skip_checking: bool = False) -> bool:
        """Add by magnet/URL and/or by raw .torrent bytes.

        torrent_files: list of (filename, bytes). Returns True on 'Ok.'.
        """
        data: dict = {}
        if urls:
            data["urls"] = "\n".join(urls)
        if save_path:
            data["savepath"] = save_path
        if category:
            data["category"] = category
        if tags:
            data["tags"] = tags
        data["paused"] = "true" if paused else "false"
        data["stopped"] = data["paused"]              # v5 alias
        data["skip_checking"] = "true" if skip_checking else "false"
        if ratio_limit is not None:
            data["ratioLimit"] = str(ratio_limit)
        if seeding_time_limit is not None:
            data["seedingTimeLimit"] = str(seeding_time_limit)
        if upload_limit is not None:
            data["upLimit"] = str(upload_limit)

        files = None
        if torrent_files:
            files = [("torrents", (name, blob, "application/x-bittorrent"))
                     for name, blob in torrent_files]

        resp = await self._post("/api/v2/torrents/add", data=data, files=files)
        text = (resp.text or "").strip()
        ok = False
        if resp.is_success:
            if text.lower() in ("ok.", ""):
                ok = True                       # classic builds: 200 "Ok." (or empty body)
            else:
                # qBittorrent v5 returns 202 Accepted + a JSON summary, e.g.
                #   {"added_torrent_ids":[...],"failure_count":0,"pending_count":1,"success_count":0}
                # A URL add is fetched asynchronously (pending_count), so treat the
                # response as accepted unless qBittorrent reports an outright failure.
                try:
                    info = json.loads(text)
                    ok = int(info.get("failure_count", 0)) == 0
                except (ValueError, TypeError, AttributeError):
                    ok = True                   # 2xx with an unrecognized body — assume accepted
        if not ok:
            log.warning("qbit add failed: %s %s", resp.status_code, resp.text[:200])
        return ok

    async def delete(self, hashes: list, delete_files: bool = True) -> bool:
        resp = await self._post("/api/v2/torrents/delete", data={
            "hashes": "|".join(h.lower() for h in hashes),
            "deleteFiles": "true" if delete_files else "false",
        })
        return resp.status_code == 200

    # ------------------------------------------------------------------ #
    # Limits / categories / tags
    # ------------------------------------------------------------------ #
    async def set_share_limits(self, hashes: list, *, ratio_limit: float = -1,
                               seeding_time_limit: int = -1,
                               inactive_seeding_time_limit: int = -1) -> bool:
        resp = await self._post("/api/v2/torrents/setShareLimits", data={
            "hashes": "|".join(h.lower() for h in hashes),
            "ratioLimit": str(ratio_limit),
            "seedingTimeLimit": str(seeding_time_limit),
            "inactiveSeedingTimeLimit": str(inactive_seeding_time_limit),
        })
        return resp.status_code == 200

    async def set_upload_limit(self, hashes: list, limit_bytes: int) -> bool:
        resp = await self._post("/api/v2/torrents/setUploadLimit", data={
            "hashes": "|".join(h.lower() for h in hashes),
            "limit": str(limit_bytes),
        })
        return resp.status_code == 200

    async def create_category(self, name: str, save_path: str = "") -> bool:
        resp = await self._post("/api/v2/torrents/createCategory",
                                data={"category": name, "savePath": save_path})
        # 409 simply means it already exists.
        return resp.status_code in (200, 409)

    async def set_category(self, hashes: list, category: str) -> bool:
        resp = await self._post("/api/v2/torrents/setCategory", data={
            "hashes": "|".join(h.lower() for h in hashes),
            "category": category,
        })
        return resp.status_code == 200

    async def add_tags(self, hashes: list, tags: str) -> bool:
        resp = await self._post("/api/v2/torrents/addTags", data={
            "hashes": "|".join(h.lower() for h in hashes),
            "tags": tags,
        })
        return resp.status_code == 200

    # ------------------------------------------------------------------ #
    # Pause / resume (v5 renamed pause→stop, resume→start)
    # ------------------------------------------------------------------ #
    async def pause(self, hashes: list) -> bool:
        endpoint = "stop" if await self.api_major() >= 5 else "pause"
        resp = await self._post(f"/api/v2/torrents/{endpoint}",
                                data={"hashes": "|".join(h.lower() for h in hashes)})
        return resp.status_code == 200

    async def resume(self, hashes: list) -> bool:
        endpoint = "start" if await self.api_major() >= 5 else "resume"
        resp = await self._post(f"/api/v2/torrents/{endpoint}",
                                data={"hashes": "|".join(h.lower() for h in hashes)})
        return resp.status_code == 200

    async def reannounce(self, hashes: list) -> bool:
        resp = await self._post("/api/v2/torrents/reannounce",
                                data={"hashes": "|".join(h.lower() for h in hashes)})
        return resp.status_code == 200


# --------------------------------------------------------------------------- #
# Settings-backed singleton
# --------------------------------------------------------------------------- #
_CLIENT: Optional[QBittorrentClient] = None
_CLIENT_SIG: tuple = ()


def get_client() -> QBittorrentClient:
    """Return a client built from current settings; rebuilds if creds changed."""
    global _CLIENT, _CLIENT_SIG
    from app import config
    from app import settings as S
    url = config.get_str("qbit_url")
    user = config.get_str("qbit_username")
    pw = S.get_secret("qbit_password") or ""
    sig = (url, user, pw)
    if _CLIENT is None or sig != _CLIENT_SIG:
        _CLIENT = QBittorrentClient(url, user, pw)
        _CLIENT_SIG = sig
    return _CLIENT


def invalidate_client() -> None:
    """Drop the cached client (call after qBittorrent settings/secrets change)."""
    global _CLIENT, _CLIENT_SIG
    _CLIENT = None
    _CLIENT_SIG = ()
