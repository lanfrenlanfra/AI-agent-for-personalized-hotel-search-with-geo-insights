"""Внешние API: запрос, кэш на диске и честный статус ответа.

Принцип: проект должен работать без интернета. Поэтому каждый ответ внешнего источника сохраняется в
data/raw/ext/<источник>/<ключ>.json вместе с адресом запроса и временем получения, и кэш можно коммитить в репозиторий.

Статусы ответа (поле status в meta):
  fresh        — только что получено из сети
  cached       — взято из кэша, свежее max_age_days
  stale_cache  — сеть недоступна/не разрешена, показан старый кэш (возраст указан)
  offline_miss — сеть не разрешена и кэша нет
  unavailable  — сеть не отвечает и кэша нет

cache_key — стабильный ключ кэша для запросов с «плавающими» датами: без него ответ нельзя найти офлайн на следующий день.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import requests

from .config import settings

HEADERS = {"User-Agent": "lupa-hackathon/1.0 (hotel hidden characteristics; contact via project repository)", "Accept": "application/json"}
BREAKER_FAILS = 2          # подряд неудач, после которых хост считается недоступным
BREAKER_PAUSE_S = 120.0

_lock = threading.Lock()
_fails: dict[str, tuple[int, float]] = {}


def root() -> Path:
    return settings.data_dir / "raw" / "ext"


def full_url(url: str, params: dict | None) -> str:
    return url + ("?" + urlencode(params, doseq=True, safe=",|:") if params else "")


def key_for(url: str, params: dict | None) -> str:
    return hashlib.sha1(full_url(url, params).encode("utf-8")).hexdigest()[:20]


def _path(source: str, key: str) -> Path:
    return root() / source / f"{key}.json"


def load(source: str, key: str) -> dict | None:
    p = _path(source, key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(source: str, key: str, entry: dict) -> None:
    p = _path(source, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entry, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def age_days(entry: dict) -> int:
    try:
        return max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(entry["fetched_at"])).days)
    except Exception:  # noqa: BLE001
        return 10**6


def _host(url: str) -> str:
    return url.split("/")[2] if "//" in url else url


def host_dead(url: str) -> bool:
    with _lock:
        n, ts = _fails.get(_host(url), (0, 0.0))
    return n >= BREAKER_FAILS and time.time() - ts < BREAKER_PAUSE_S


def _mark(url: str, ok: bool) -> None:
    with _lock:
        h = _host(url)
        if ok:
            _fails.pop(h, None)
        else:
            n, _ = _fails.get(h, (0, 0.0))
            _fails[h] = (n + 1, time.time())


def reset_breakers() -> None:
    with _lock:
        _fails.clear()


def http_json(url: str, params: dict | None, timeout: float, headers: dict | None = None) -> Any:
    """Единственное место, где реально ходим в сеть (тесты подменяют именно его)."""
    r = requests.get(url, params=params, timeout=timeout, headers={**HEADERS, **(headers or {})})
    r.raise_for_status()
    return r.json()


def fetch_json(source: str, url: str, params: dict | None = None, *, online: bool, max_age_days: int,
               reduce: Callable[[Any], Any] | None = None, headers: dict | None = None, timeout: float | None = None,
               cache_key: str | None = None) -> tuple[Any, dict]:
    """→ (данные или None, meta). reduce сжимает сырой ответ перед записью в кэш (чтобы кэш в репозитории был небольшим)."""
    key = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()[:20] if cache_key else key_for(url, params)
    shown = full_url(url, {k: v for k, v in (params or {}).items() if k.lower() not in ("key", "apikey", "api_key")})
    cached = load(source, key)
    meta: dict = {"source": source, "url": shown}
    if cached and age_days(cached) <= max_age_days:
        return cached["payload"], {**meta, "status": "cached", "fetched_at": cached["fetched_at"], "age_days": age_days(cached)}
    err = None
    if online and not host_dead(url):
        try:
            raw = http_json(url, params, timeout or settings.probe_timeout, headers)
            payload = reduce(raw) if reduce else raw
            entry = {"url": shown, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "payload": payload}
            _mark(url, True)
            try:
                save(source, key, entry)
            except OSError:
                pass                                   # нельзя записать кэш (read-only том) — данные всё равно используем
            return payload, {**meta, "status": "fresh", "fetched_at": entry["fetched_at"], "age_days": 0}
        except Exception as e:  # noqa: BLE001
            _mark(url, False)
            err = f"{type(e).__name__}: {str(e)[:160]}"
    elif online:
        err = "хост не отвечал несколько раз подряд, повторим позже"
    if cached:
        return cached["payload"], {**meta, "status": "stale_cache", "fetched_at": cached["fetched_at"], "age_days": age_days(cached), "error": err}
    return None, {**meta, "status": "unavailable" if online else "offline_miss", "error": err}
