"""Settings & secrets accessors backed by the SQLite `settings`/`secrets` tables.

Settings are user-tunable knobs (stored as TEXT, coerced by app.config).
Secrets are credentials (qBittorrent password, Prowlarr API key) kept out of
config files and never rendered back to the UI.
"""
from __future__ import annotations

from typing import Optional

from app.db import get_db


def _ensure_secrets_table(db) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS secrets (key TEXT PRIMARY KEY, value TEXT)")


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with get_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with get_db() as db:
        db.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def set_settings(items: dict) -> None:
    """Bulk upsert. Keys absent from `items` are left untouched."""
    with get_db() as db:
        for key, value in items.items():
            db.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )


def all_settings() -> dict:
    with get_db() as db:
        return {r["key"]: r["value"]
                for r in db.execute("SELECT key, value FROM settings").fetchall()}


# --------------------------------------------------------------------------- #
# Secrets
# --------------------------------------------------------------------------- #
def get_secret(key: str) -> Optional[str]:
    try:
        with get_db() as db:
            _ensure_secrets_table(db)
            row = db.execute("SELECT value FROM secrets WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None
    except Exception:
        return None


def set_secret(key: str, value: str) -> None:
    with get_db() as db:
        _ensure_secrets_table(db)
        db.execute(
            "INSERT INTO secrets(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def has_secret(key: str) -> bool:
    return bool(get_secret(key))


def clear_secret(key: str) -> None:
    with get_db() as db:
        _ensure_secrets_table(db)
        db.execute("DELETE FROM secrets WHERE key=?", (key,))
