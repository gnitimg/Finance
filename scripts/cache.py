from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .config import DATA_DIR


class Cache:
    PRUNE_INTERVAL = 600
    # Industry classifications are intentionally cached for seven days. Keep
    # entries one extra day so pruning cannot remove a still-valid mapping.
    RETENTION_SECONDS = 8 * 86_400

    def __init__(self, path: Path | None = None):
        self.path = path or DATA_DIR / "cache.sqlite3"
        self._lock = threading.Lock()
        self._last_prune = 0.0
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, created REAL NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def get(self, key: str, ttl: float, stale_ttl: float = 0) -> tuple[dict | None, dict]:
        with self._lock, self._connect() as db:
            row = db.execute("SELECT value, created FROM cache WHERE key=?", (key,)).fetchone()
        if not row:
            return None, {"cached": False, "stale": False, "age_seconds": None}
        age = max(0.0, time.time() - row[1])
        if age <= ttl or (stale_ttl and age <= stale_ttl):
            return json.loads(row[0]), {"cached": True, "stale": age > ttl, "age_seconds": round(age, 2)}
        return None, {"cached": False, "stale": False, "age_seconds": round(age, 2)}

    def set(self, key: str, value: dict) -> None:
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        now = time.time()
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO cache(key,value,created) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,created=excluded.created", (key, payload, now))
            if now - self._last_prune > self.PRUNE_INTERVAL:
                self._last_prune = now
                db.execute("DELETE FROM cache WHERE created < ?", (now - self.RETENTION_SECONDS,))

    def count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM cache").fetchone()[0])


CACHE = Cache()
