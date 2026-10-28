"""Реестр характеристик: встроенные из YAML + добавленные пользователем.

Новая характеристика = запись в registry.yaml или POST /api/features.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from . import db

REGISTRY_FILE = Path(__file__).parent / "features" / "registry.yaml"
KINDS = ("bool", "score", "number", "text")

_cache: dict[str, dict[str, Any]] | None = None


def _normalize(spec: dict[str, Any]) -> dict[str, Any]:
    s = dict(spec)
    s.setdefault("group", "custom")
    s.setdefault("kind", "bool")
    s.setdefault("ttl_days", 365)
    s.setdefault("importance", 0.3)
    s.setdefault("synonyms", [])
    s.setdefault("description", "")
    s.setdefault("computed", False)
    s.setdefault("good", "high")
    review = s.get("review") or {}
    s["review"] = {"pos": list(review.get("pos") or []), "neg": list(review.get("neg") or [])}
    if s["kind"] not in KINDS:
        raise ValueError(f"kind должен быть одним из {KINDS}")
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", str(s.get("key", ""))):
        raise ValueError("key: латиница, цифры и _, до 41 символа")
    if not s.get("title"):
        raise ValueError("нужно название (title)")
    return s


def load_builtin() -> list[dict[str, Any]]:
    data = yaml.safe_load(REGISTRY_FILE.read_text(encoding="utf-8"))
    return [_normalize(f) for f in data["features"]]


def sync_builtin() -> None:
    """Записать встроенные характеристики в БД (пользовательские не трогаем)."""
    with db.tx() as c:
        for spec in load_builtin():
            c.execute(
                "INSERT INTO features(key, spec, builtin) VALUES(?,?,1) "
                "ON CONFLICT(key) DO UPDATE SET spec=excluded.spec, builtin=1",
                (spec["key"], db.dumps(spec)),
            )
    invalidate()


def invalidate() -> None:
    global _cache
    _cache = None


def all_features() -> dict[str, dict[str, Any]]:
    global _cache
    if _cache is None:
        res: dict[str, dict[str, Any]] = {}
        try:
            for r in db.rows("SELECT key, spec, builtin FROM features ORDER BY builtin DESC, key"):
                spec = db.loads(r["spec"], {})
                spec["builtin"] = bool(r["builtin"])
                res[r["key"]] = spec
        except Exception:
            res = {}
        if not res:
            res = {f["key"]: {**f, "builtin": True} for f in load_builtin()}
        _cache = res
    return _cache


def get(key: str) -> dict[str, Any] | None:
    return all_features().get(key)


def add_custom(spec: dict[str, Any]) -> dict[str, Any]:
    s = _normalize(spec)
    if get(s["key"]) and get(s["key"]).get("builtin"):
        raise ValueError("такая встроенная характеристика уже есть")
    s["computed"] = False
    with db.tx() as c:
        c.execute(
            "INSERT INTO features(key, spec, builtin) VALUES(?,?,0) "
            "ON CONFLICT(key) DO UPDATE SET spec=excluded.spec",
            (s["key"], db.dumps(s)),
        )
    invalidate()
    return s


def delete_custom(key: str) -> bool:
    f = get(key)
    if not f or f.get("builtin"):
        return False
    with db.tx() as c:
        c.execute("DELETE FROM features WHERE key=? AND builtin=0", (key,))
        c.execute("DELETE FROM observations WHERE feature=?", (key,))
        c.execute("DELETE FROM facts WHERE feature=?", (key,))
    invalidate()
    return True


GROUP_TITLES = {
    "view": "Вид из окна",
    "noise": "Шум и сон",
    "surroundings": "Окружение",
    "guests": "В номере и сервис",
    "custom": "Свои характеристики",
}
