import os
import sqlite3
import tempfile

from app import db


def test_migration_adds_lifecycle_columns():
    path = os.path.join(tempfile.mkdtemp(), "m.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Simulate an old v1.0.0 DB without the lifecycle columns.
    conn.execute("CREATE TABLE candidates (infohash TEXT PRIMARY KEY, name TEXT)")
    conn.execute("CREATE TABLE tracked (infohash TEXT PRIMARY KEY, name TEXT)")

    db._migrate(conn)

    cand = {r["name"] for r in conn.execute("PRAGMA table_info(candidates)")}
    trk = {r["name"] for r in conn.execute("PRAGMA table_info(tracked)")}
    assert {"trackers", "webseeds", "watched", "archived"} <= cand
    assert {"swarm_revived_at", "archived"} <= trk

    db._migrate(conn)              # idempotent — must not raise
    conn.close()
