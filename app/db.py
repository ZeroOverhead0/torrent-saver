"""SQLite schema + connection helpers for Torrent Saver.

Mirrors the deal-finder pattern: a WAL-mode connection with foreign keys on,
a `get_db()` context manager that commits/rolls back, and an idempotent
`init_db()` that creates the schema and seeds default settings.

Run `python -m app.db` to initialise the database (used by install.sh).
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
DB_PATH = Path(os.environ.get("TORRENTSAVER_DB", str(ROOT / "data" / "torrents.db")))


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS secrets (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Endangered torrents we have discovered from sources (the candidate pool).
CREATE TABLE IF NOT EXISTS candidates (
    infohash      TEXT PRIMARY KEY,           -- lowercase 40-char SHA1 hex
    name          TEXT NOT NULL,
    source        TEXT NOT NULL,
    magnet        TEXT,
    torrent_url   TEXT,
    size_bytes    INTEGER DEFAULT 0,
    seeders       INTEGER DEFAULT 0,
    leechers      INTEGER DEFAULT 0,
    completed     INTEGER DEFAULT 0,          -- num_complete reported by tracker
    category      TEXT DEFAULT '',
    legal         INTEGER DEFAULT 0,          -- 1 = from a legal/whitelisted source
    endangerment  REAL DEFAULT 0,             -- 0..100, higher = more at risk
    redundancy    INTEGER DEFAULT 0,          -- count of healthy near-duplicates
    rescuable     INTEGER DEFAULT 1,          -- 0 if seeders==0 (can't download)
    status        TEXT DEFAULT 'discovered',  -- discovered|queued|rescued|skipped|dead
    skip_reason   TEXT DEFAULT '',
    trackers      TEXT DEFAULT '[]',          -- JSON list (for death-watch re-scrape)
    webseeds      TEXT DEFAULT '[]',          -- JSON list (BEP 19 webseeds)
    watched       INTEGER DEFAULT 1,          -- 1 = re-scrape on the death-watch loop
    archived      INTEGER DEFAULT 0,          -- 1 = metadata saved to the ark
    first_seen    REAL NOT NULL,
    last_seen     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates(status);
CREATE INDEX IF NOT EXISTS idx_candidates_endanger ON candidates(endangerment DESC);

-- Torrents we have actually added to qBittorrent and are re-seeding.
CREATE TABLE IF NOT EXISTS tracked (
    infohash       TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    source         TEXT DEFAULT '',
    size_bytes     INTEGER DEFAULT 0,
    category       TEXT DEFAULT '',
    legal          INTEGER DEFAULT 0,
    mode           TEXT DEFAULT 'rescue',     -- rescue | demand
    qbit_state     TEXT DEFAULT '',           -- cached state string from qBit
    seeders        INTEGER DEFAULT 0,
    leechers       INTEGER DEFAULT 0,
    ratio          REAL DEFAULT 0,
    uploaded_bytes INTEGER DEFAULT 0,
    progress       REAL DEFAULT 0,
    endangerment   REAL DEFAULT 0,            -- last computed live endangerment
    added_at       REAL NOT NULL,
    last_checked   REAL DEFAULT 0,
    evicted_at     REAL,
    evict_reason   TEXT DEFAULT '',
    swarm_revived_at REAL,                     -- set when a 0-seeder swarm gains a real peer
    archived       INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tracked_mode ON tracked(mode);
CREATE INDEX IF NOT EXISTS idx_tracked_evicted ON tracked(evicted_at);

-- Append-only audit / activity log surfaced in the UI.
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL NOT NULL,
    level    TEXT DEFAULT 'info',             -- info|warn|error|decision
    kind     TEXT DEFAULT '',                 -- scan|rescue|evict|vpn|seed|hardening|error
    infohash TEXT DEFAULT '',
    message  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);

-- History of VPN / kill-switch checks (most recent row = current status).
CREATE TABLE IF NOT EXISTS vpn_checks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    connected   INTEGER DEFAULT 0,
    interface   TEXT DEFAULT '',
    public_ip   TEXT DEFAULT '',
    killswitch  INTEGER DEFAULT 0,
    leaking     INTEGER DEFAULT 0,
    detail      TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_vpn_ts ON vpn_checks(ts DESC);
"""


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #
def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Events helper (importable everywhere; cheap)
# --------------------------------------------------------------------------- #
def log_event(kind: str, message: str, *, level: str = "info", infohash: str = "") -> None:
    """Append a row to the events table. Never raises (best-effort audit log)."""
    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO events(ts, level, kind, infohash, message) VALUES (?,?,?,?,?)",
                (time.time(), level, kind, infohash, message),
            )
    except Exception:
        pass


def recent_events(limit: int = 100) -> list:
    with get_db() as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()]


# --------------------------------------------------------------------------- #
# Init
# --------------------------------------------------------------------------- #
# Columns added after v1.0.0 — applied to existing DBs idempotently.
_MIGRATIONS = {
    "candidates": [
        ("trackers", "TEXT DEFAULT '[]'"),
        ("webseeds", "TEXT DEFAULT '[]'"),
        ("watched", "INTEGER DEFAULT 1"),
        ("archived", "INTEGER DEFAULT 0"),
    ],
    "tracked": [
        ("swarm_revived_at", "REAL"),
        ("archived", "INTEGER DEFAULT 0"),
    ],
}


def _migrate(db) -> None:
    for table, cols in _MIGRATIONS.items():
        existing = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def init_db() -> None:
    """Create schema (idempotent), migrate existing DBs, seed missing settings."""
    with get_db() as db:
        db.executescript(SCHEMA)
        _migrate(db)
    # Seed defaults without clobbering user-set values.
    from app.config import DEFAULT_SETTINGS  # local import to avoid cycle
    with get_db() as db:
        for key, value in DEFAULT_SETTINGS.items():
            db.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO NOTHING",
                (key, str(value)),
            )


if __name__ == "__main__":
    init_db()
    print(f"Torrent Saver DB initialised at {DB_PATH}")
