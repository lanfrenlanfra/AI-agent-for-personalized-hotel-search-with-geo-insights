"""Наблюдение — единица знания: что узнали, откуда, насколько уверены, когда."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import db

SOURCES = {
    "ostrovok_card": {"title": "Карточка Островка", "reliability": 0.55, "hint": "данные, которые вносит сам отель; часто устаревают"},
    "ostrovok_ratings": {"title": "Оценки гостей Островка", "reliability": 0.6, "hint": "средние оценки по всем отзывам"},
    "reviews": {"title": "Отзывы гостей", "reliability": 0.65, "hint": "упоминания в текстах отзывов, свежие весомее"},
    "review_ratings": {"title": "Оценки в отзывах", "reliability": 0.7, "hint": "числовые оценки гостей по категориям"},
    "osm_geo": {"title": "Геоданные OpenStreetMap", "reliability": 0.75, "hint": "расчёт по зданиям, дорогам и объектам вокруг"},
    "astronomy": {"title": "Астрономический расчёт", "reliability": 0.95, "hint": "положение солнца по широте и дате"},
    "photo_vision": {"title": "Фотографии", "reliability": 0.5, "hint": "что видно на фото отеля и гостей"},
    "hotelier": {"title": "Отельер", "reliability": 0.9, "hint": "ответ владельца или администратора"},
    "hotelier_sim": {"title": "Отельер (имитация)", "reliability": 0.55, "hint": "демо-ответ имитатора, не данные отеля"},
}


SIDE_KEYS = {"север": "north", "северо-восток": "northeast", "восток": "east", "юго-восток": "southeast",
             "юг": "south", "юго-запад": "southwest", "запад": "west", "северо-запад": "northwest"}
SCOPE_TITLES = {
    "hotel": "в целом по отелю",
    "room:north": "окна на север", "room:northeast": "окна на северо-восток", "room:east": "окна на восток", "room:southeast": "окна на юго-восток",
    "room:south": "окна на юг", "room:southwest": "окна на юго-запад", "room:west": "окна на запад", "room:northwest": "окна на северо-запад",
    "room:yard": "номера во двор", "room:street": "номера на улицу", "room:water": "номера на воду",
}


@dataclass
class Obs:
    feature: str
    value: dict
    source: str
    confidence: float
    observed_at: str                       # когда справедливо само свидетельство (YYYY-MM-DD)
    method: str = ""
    evidence: list = field(default_factory=list)
    scope: str = "hotel"
    simulated: bool = False

    def __post_init__(self):
        self.confidence = max(0.0, min(0.99, float(self.confidence)))


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_observations(hotel_id: str, observations: list[Obs]) -> list[dict]:
    """Записать наблюдения; прежние от того же источника по той же характеристике помечаются superseded.
    Возвращает список изменений (значение поменялось) — это сигнал для актуализации."""
    changes: list[dict] = []
    ts = now_iso()
    with db.tx() as c:
        for o in observations:
            old = c.execute(
                "SELECT id, value FROM observations WHERE hotel_id=? AND feature=? AND scope=? AND source=? AND superseded=0",
                (hotel_id, o.feature, o.scope, o.source),
            ).fetchall()
            for r in old:
                ov = db.loads(r["value"], {}).get("v")
                if ov != o.value.get("v"):
                    changes.append({"feature": o.feature, "scope": o.scope, "source": o.source, "old": ov, "new": o.value.get("v")})
            if old:
                c.execute(
                    "UPDATE observations SET superseded=1 WHERE hotel_id=? AND feature=? AND scope=? AND source=? AND superseded=0",
                    (hotel_id, o.feature, o.scope, o.source),
                )
            c.execute(
                "INSERT INTO observations(hotel_id, feature, scope, value, source, confidence, observed_at, collected_at, evidence, method, simulated) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (hotel_id, o.feature, o.scope, db.dumps(o.value), o.source, o.confidence, o.observed_at, ts,
                 db.dumps(o.evidence), o.method, int(o.simulated)),
            )
        for ch in changes:
            c.execute("INSERT INTO events(hotel_id, ts, kind, payload) VALUES(?,?,?,?)", (hotel_id, ts, "changed", db.dumps(ch)))
    return changes
