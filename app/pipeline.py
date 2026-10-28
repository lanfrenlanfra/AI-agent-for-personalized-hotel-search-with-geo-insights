"""Конвейер: сырые данные → наблюдения всех сборщиков → итоговые факты → вопросы отельеру."""
from __future__ import annotations

import logging

from . import db, hotelier, ingest, reconcile, registry
from .collectors import card as card_collector
from .collectors import geo as geo_collector
from .collectors import reviews as review_collector
from .collectors.base import save_observations
from .config import settings

log = logging.getLogger("lupa.pipeline")


def hotel_record(hotel_id: str) -> dict | None:
    """Собрать запись отеля из БД в виде, понятном сборщикам."""
    h = db.row("SELECT * FROM hotels WHERE id=?", (hotel_id,))
    if not h:
        return None
    card = db.loads(h["card"], {}) or {}
    revs = []
    for r in db.rows("SELECT * FROM reviews WHERE hotel_id=? ORDER BY date DESC", (hotel_id,)):
        extra = db.loads(r["images"], {}) or {}
        revs.append({"id": r["id"], "date": r["date"], "rating": r["rating"], "plus": r["plus"], "minus": r["minus"], "text": r["text"],
                     "room": r["room"], "traveller": r["traveller_type"], "images": extra.get("images", []),
                     "detailed": extra.get("detailed", {}), "wifi_quality": extra.get("wifi_quality")})
    rec = dict(card)
    rec.update({"id": h["id"], "name": h["name"], "lat": h["lat"], "lon": h["lon"], "stars": h["stars"], "rating": h["rating"],
                "num_reviews": h["reviews_count"], "fetched_at": h["collected_at"], "reviews": revs, "region": settings.region_slug})
    return rec


def collect_text_sources(hotel_id: str, rec: dict) -> int:
    obs = card_collector.collect(rec) + review_collector.collect(rec["reviews"]) + review_collector.collect_ratings(rec["reviews"])
    save_observations(hotel_id, obs)
    return len(obs)


def collect_geo_source(hotel_id: str, rec: dict) -> int:
    obs = geo_collector.collect(rec)
    save_observations(hotel_id, obs)
    return len(obs)


def process_hotel(hotel_id: str, geo: bool = True, reviews_only: bool = False) -> dict:
    rec = hotel_record(hotel_id)
    if not rec:
        raise KeyError(hotel_id)
    n = collect_text_sources(hotel_id, rec)
    if geo and not reviews_only:
        try:
            n += collect_geo_source(hotel_id, rec)
        except Exception:  # noqa: BLE001 — сбой геомодели одного отеля не должен ронять остальное
            log.exception("геосборщик упал для %s", hotel_id)
    reconcile.reconcile_hotel(hotel_id)
    hotelier.generate_questions(hotel_id)
    return {"hotel": hotel_id, "observations": n}


def bootstrap(force: bool = False) -> dict:
    """Первый запуск: схема, реестр, загрузка данных, расчёт. Повторный — дёшево, если уже загружено."""
    db.init_schema()
    registry.sync_builtin()
    have = db.row("SELECT COUNT(*) AS n FROM hotels")["n"]
    if have and not force:
        return {"hotels": have, "loaded": False}
    ids = ingest.ingest_all()
    log.info("загружено отелей: %d", len(ids))
    for hid in ids:
        process_hotel(hid)
    db.execute("INSERT INTO meta(key, value) VALUES('seeded_at', datetime('now')) ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    return {"hotels": len(ids), "loaded": True}
