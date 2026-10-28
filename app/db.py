"""SQLite: простое хранилище без внешних сервисов.

Каждое значение характеристики — это *наблюдение* (observations): что узнали,
откуда, с какой уверенностью и когда. Итог по характеристике (facts) собирается
из наблюдений модулем reconcile.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterable

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS hotels (
  id TEXT PRIMARY KEY,
  master_id TEXT,
  slug TEXT,
  name TEXT NOT NULL,
  city TEXT,
  address TEXT,
  lat REAL, lon REAL,
  stars INTEGER,
  rating REAL,
  reviews_count INTEGER,
  price_from REAL,
  url TEXT,
  photos TEXT,
  card TEXT,
  collected_at TEXT
);
CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY,
  hotel_id TEXT NOT NULL,
  date TEXT,
  rating REAL,
  plus TEXT, minus TEXT, text TEXT,
  room TEXT,
  traveller_type TEXT,
  images TEXT
);
CREATE INDEX IF NOT EXISTS reviews_hotel ON reviews(hotel_id, date);
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  hotel_id TEXT NOT NULL,
  feature TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'hotel',
  value TEXT NOT NULL,
  source TEXT NOT NULL,
  confidence REAL NOT NULL,
  observed_at TEXT NOT NULL,
  collected_at TEXT NOT NULL,
  evidence TEXT,
  method TEXT,
  simulated INTEGER NOT NULL DEFAULT 0,
  superseded INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS obs_hotel ON observations(hotel_id, feature);
CREATE TABLE IF NOT EXISTS facts (
  hotel_id TEXT NOT NULL,
  feature TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'hotel',
  value TEXT NOT NULL,
  status TEXT NOT NULL,
  confidence REAL NOT NULL,
  observed_at TEXT,
  expires_at TEXT,
  explanation TEXT,
  obs_ids TEXT,
  conflict TEXT,
  PRIMARY KEY (hotel_id, feature, scope)
);
CREATE TABLE IF NOT EXISTS features (
  key TEXT PRIMARY KEY,
  spec TEXT NOT NULL,
  builtin INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  hotel_id TEXT NOT NULL,
  feature TEXT NOT NULL,
  text TEXT NOT NULL,
  reason TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL,
  answer_text TEXT,
  answered_at TEXT,
  parsed TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  hotel_id TEXT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            settings.runtime_dir.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(settings.db_path), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA foreign_keys=ON")
        return _conn


def reset_connection(path: str | None = None) -> sqlite3.Connection:
    """Для тестов: открыть другую базу (например ':memory:')."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = sqlite3.connect(path or str(settings.db_path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        return _conn


def init_schema() -> None:
    with _lock:
        conn().executescript(SCHEMA)
        conn().commit()


@contextmanager
def tx():
    with _lock:
        c = conn()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def rows(sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
    with _lock:
        return conn().execute(sql, tuple(args)).fetchall()


def row(sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
    with _lock:
        return conn().execute(sql, tuple(args)).fetchone()


def execute(sql: str, args: Iterable[Any] = ()) -> int:
    with tx() as c:
        cur = c.execute(sql, tuple(args))
        return cur.lastrowid or 0


def dumps(o: Any) -> str:
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def loads(s: str | None, default: Any = None) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return default
