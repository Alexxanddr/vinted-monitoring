import fcntl
import json
import os
import sqlite3
import time
from pathlib import Path

from .models import Query


class Store:
    """One process owns this database, including MCP reads and manual scans."""

    def __init__(self, path: str):
        path = str(Path(path).expanduser().resolve())
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lease = open(
            os.open(path + ".lockfile", os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600), "a"
        )
        try:
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lease.close()
            raise RuntimeError("state is already owned by another process") from None
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 2:
            self.close()
            raise RuntimeError("database is newer than this application")
        if version == 0:
            self.db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE watches (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, query TEXT NOT NULL,
                    interval INTEGER NOT NULL, paused INTEGER NOT NULL DEFAULT 0,
                    baseline INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0,
                    last_success REAL, error TEXT, failures INTEGER NOT NULL DEFAULT 0,
                    failure_since REAL, alerted INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE seen (
                    watch_id INTEGER REFERENCES watches(id) ON DELETE CASCADE,
                    item_id TEXT NOT NULL, PRIMARY KEY(watch_id,item_id)
                );
                CREATE TABLE notifications (
                    item_id TEXT NOT NULL, destination TEXT NOT NULL, payload TEXT NOT NULL,
                    delivered REAL, due REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT, PRIMARY KEY(item_id,destination)
                );
                CREATE TABLE matches (
                    item_id TEXT NOT NULL, destination TEXT NOT NULL, watch_name TEXT NOT NULL,
                    PRIMARY KEY(item_id,destination,watch_name),
                    FOREIGN KEY(item_id,destination) REFERENCES notifications(item_id,destination)
                );
                PRAGMA user_version=1;
                COMMIT;
            """)

        if version < 2:
            self.db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                PRAGMA user_version=2;
                COMMIT;
            """)

    def cooldown(self, key):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return float(row[0]) if row else 0

    def set_cooldown(self, key, until):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (key, str(until)))

    def suspended(self):
        return (
            self.db.execute("SELECT 1 FROM metadata WHERE key='vinted_blocked'").fetchone()
            is not None
        )

    def suspend(self):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES('vinted_blocked','1')")

    def clear_block(self):
        with self.db:
            self.db.execute("DELETE FROM metadata WHERE key='vinted_blocked'")

    def close(self):
        self.db.close()
        self.lease.close()

    def watches(self):
        return [
            dict(row) | {"query": json.loads(row["query"])}
            for row in self.db.execute("SELECT * FROM watches ORDER BY id")
        ]

    def watch(self, watch_id):
        row = self.db.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
        if row is None:
            raise ValueError("watch does not exist")
        return dict(row)

    def create(self, name: str, query: Query, interval: int):
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO watches(name,query,interval) VALUES(?,?,?)",
                (name, query.model_dump_json(), interval),
            )
        return {"id": cursor.lastrowid, "baseline": "pending; silent on first complete scan"}

    def update(self, watch_id, query=None, interval=None, name=None):
        watch = self.watch(watch_id)
        changed = query is not None and query.model_dump_json() != watch["query"]
        with self.db:
            self.db.execute(
                "UPDATE watches SET query=?,interval=?,name=?,baseline=?,due=0 WHERE id=?",
                (
                    query.model_dump_json() if query is not None else watch["query"],
                    interval if interval is not None else watch["interval"],
                    name if name is not None else watch["name"],
                    0 if changed else watch["baseline"],
                    watch_id,
                ),
            )
            if changed:
                self.db.execute("DELETE FROM seen WHERE watch_id=?", (watch_id,))
        return {
            "id": watch_id,
            "baseline_reset": changed,
            "queued_notifications": "previously queued notifications are retained",
        }

    def pause(self, watch_id, paused):
        self.watch(watch_id)
        with self.db:
            self.db.execute("UPDATE watches SET paused=?,due=0 WHERE id=?", (paused, watch_id))
        return {"id": watch_id, "paused": paused}

    def delete(self, watch_id):
        self.watch(watch_id)
        with self.db:
            self.db.execute("DELETE FROM watches WHERE id=?", (watch_id,))
        return {"id": watch_id, "deleted": True, "queued_notifications": "retained"}

    def commit_scan(self, watch, items, destination):
        # Baseline and all associations commit together, only after complete coverage.
        with self.db:
            for item in items:
                new = self.db.execute(
                    "INSERT OR IGNORE INTO seen VALUES(?,?)", (watch["id"], item.id)
                ).rowcount
                if watch["baseline"] and new:
                    self.db.execute(
                        """INSERT OR IGNORE INTO notifications
                        (item_id,destination,payload) VALUES(?,?,?)""",
                        (item.id, destination, item.model_dump_json()),
                    )
                if not watch["baseline"]:
                    self.db.execute(
                        """INSERT OR IGNORE INTO notifications
                        (item_id,destination,payload,delivered) VALUES(?,?,?,?)""",
                        (item.id, destination, item.model_dump_json(), time.time()),
                    )
                exists = self.db.execute(
                    "SELECT 1 FROM notifications WHERE item_id=? AND destination=?",
                    (item.id, destination),
                ).fetchone()
                if exists:
                    self.db.execute(
                        "INSERT OR IGNORE INTO matches VALUES(?,?,?)",
                        (item.id, destination, watch["name"]),
                    )
            self.db.execute(
                """UPDATE watches SET baseline=1,last_success=?,due=?,error=NULL,
                failures=0,failure_since=NULL,alerted=0 WHERE id=?""",
                (time.time(), time.time() + watch["interval"], watch["id"]),
            )

    def status(self):
        pending = self.db.execute("""SELECT count(*),min(due),max(attempts),
            sum(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END)
            FROM notifications WHERE delivered IS NULL""").fetchone()
        return {
            "delivery": {
                "next_due": pending[1],
                "max_attempts": pending[2] or 0,
                "failed_pending": pending[3] or 0,
            },
            "coverage": "bounded mutable pages; not a timestamp window or exhaustive snapshot",
            "backlog": self.db.execute(
                "SELECT count(*) FROM notifications WHERE delivered IS NULL"
            ).fetchone()[0],
            "watches": [
                {
                    k: row[k]
                    for k in ("id", "baseline", "paused", "last_success", "error", "failures")
                }
                for row in self.watches()
            ],
        }
