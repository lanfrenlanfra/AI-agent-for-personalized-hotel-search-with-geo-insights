"""Актуализация: данные устаревают, поэтому их регулярно перепроверяют.

* статусы пересчитываются по возрасту относительно срока жизни характеристики (ttl_days);
* устаревшее и спорное превращается в вопросы отельеру;
* «обновить» заново прогоняет сборщики: офлайн — по сохранённым данным, онлайн — с перекачкой карточки,
  отзывов и тайлов OSM; любое изменение значения записывается как событие и видно в интерфейсе;
* демо: «рядом выросло здание» — наложение на сцену, после которого видно, как факт меняется.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, timedelta

from . import db, hotelier, pipeline, reconcile, registry
from .collectors import geo as geo_collector
from .collectors.base import SCOPE_TITLES, SOURCES, now_iso, today
from .config import settings

log = logging.getLogger("lupa.freshness")


def scan(horizon_days: int = 30) -> dict:
    """Что устарело, что скоро устареет, что спорно."""
    feats = registry.all_features()
    names = {r["id"]: r["name"] for r in db.rows("SELECT id, name FROM hotels")}
    ref = date.fromisoformat(today())
    stale, soon, disputed = [], [], []
    counts: dict[str, int] = {}
    for r in db.rows("SELECT * FROM facts"):
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        spec = feats.get(r["feature"])
        if not spec:
            continue
        row = {"hotel_id": r["hotel_id"], "hotel": names.get(r["hotel_id"]), "feature": r["feature"], "title": spec["title"],
               "scope": r["scope"], "scope_title": SCOPE_TITLES.get(r["scope"], r["scope"]), "observed_at": r["observed_at"], "expires_at": r["expires_at"]}
        if r["status"] == "stale":
            stale.append(row)
        elif r["status"] == "disputed":
            disputed.append({**row, "explanation": r["explanation"]})
        elif r["expires_at"] and date.fromisoformat(r["expires_at"][:10]) <= ref + timedelta(days=horizon_days):
            soon.append(row)
    return {"as_of": today(), "counts": counts, "stale": stale[:200], "expiring_soon": soon[:200], "disputed": disputed[:200],
            "stale_total": len(stale), "expiring_total": len(soon), "disputed_total": len(disputed)}


def events(limit: int = 50, hotel_id: str | None = None) -> list[dict]:
    feats = registry.all_features()
    names = {r["id"]: r["name"] for r in db.rows("SELECT id, name FROM hotels")}
    sql = "SELECT * FROM events" + (" WHERE hotel_id=?" if hotel_id else "") + " ORDER BY id DESC LIMIT ?"
    out = []
    for r in db.rows(sql, (hotel_id, limit) if hotel_id else (limit,)):
        p = db.loads(r["payload"], {}) or {}
        title = feats.get(p.get("feature", ""), {}).get("title", p.get("feature"))
        out.append({"id": r["id"], "ts": r["ts"], "kind": r["kind"], "hotel_id": r["hotel_id"], "hotel": names.get(r["hotel_id"]), "feature": p.get("feature"),
                    "title": title, "scope": p.get("scope"), "old": p.get("old"), "new": p.get("new"), "source": p.get("source"), "text": p.get("text") or _event_text(r["kind"], title, p)})
    return out


def _event_text(kind: str, title: str | None, p: dict) -> str:
    if kind == "changed":
        def fmt(v):
            return "да" if v is True else "нет" if v is False else str(v)
        return f'«{title}»: было {fmt(p.get("old"))}, стало {fmt(p.get("new"))} (источник: {SOURCES.get(p.get("source"), {}).get("title", p.get("source"))})'
    return kind


def log_event(hotel_id: str | None, kind: str, payload: dict) -> None:
    db.execute("INSERT INTO events(hotel_id, ts, kind, payload) VALUES(?,?,?,?)", (hotel_id, now_iso(), kind, db.dumps(payload)))


def recompute_statuses() -> int:
    """Пересчитать итоги (возраст меняет вес и статус) и обновить вопросы — дёшево, без сети."""
    n = reconcile.reconcile_all()
    for r in db.rows("SELECT id FROM hotels"):
        hotelier.generate_questions(r["id"])
    return n


def refresh_hotel(hotel_id: str, online: bool = False) -> dict:
    h = db.row("SELECT * FROM hotels WHERE id=?", (hotel_id,))
    if not h:
        raise KeyError(hotel_id)
    started = db.row("SELECT COALESCE(MAX(id),0) AS m FROM events")["m"]
    note = "офлайн: пересчёт по сохранённым данным"
    if online:
        try:
            from . import ingest, net
            region = settings.region_slug
            rec = net.fetch_hotel(region, {"master_id": h["master_id"], "slug": h["slug"] or hotel_id, "serp": None})
            net.save_record(rec)
            ingest.upsert_hotel(rec)
            net.ensure_tiles(h["lat"], h["lon"], geo_collector.SCENE_RADIUS, max_age_days=60)
            net.ensure_extras(h["lat"], h["lon"], geo_collector.SCENE_RADIUS, max_age_days=180)
            note = "онлайн: карточка, отзывы и карта перекачаны"
        except Exception as e:  # noqa: BLE001
            note = f"онлайн-обновление не удалось ({e}); пересчитано по сохранённым данным"
            log.warning("%s: %s", hotel_id, note)
    pipeline.process_hotel(hotel_id, online=online)
    changed = [e for e in events(200, hotel_id) if e["id"] > started]
    log_event(hotel_id, "refreshed", {"text": f"Перепроверка: {note}; изменений значений: {len(changed)}", "online": online})
    return {"hotel_id": hotel_id, "note": note, "changes": changed}


def refresh_stale(limit: int = 10, online: bool = False) -> dict:
    sc = scan(0)
    ids = list(dict.fromkeys([x["hotel_id"] for x in sc["stale"]] + [x["hotel_id"] for x in sc["expiring_soon"]]))[:limit]
    res = [refresh_hotel(i, online) for i in ids]
    return {"refreshed": len(res), "changes": sum(len(r["changes"]) for r in res)}


# ───────────────────────── демо: «рядом построили дом» ─────────────────────────

def add_building(hotel_id: str, bearing: float = 270.0, dist: float = 45.0, height: float = 40.0, w: float = 60.0, d: float = 30.0) -> dict:
    f = geo_collector.overlay_file(hotel_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    cur = geo_collector.load_overlay(hotel_id)
    cur.append({"bearing": bearing, "dist": dist, "height": height, "w": w, "d": d})
    f.write_text(json.dumps(cur), encoding="utf-8")
    log_event(hotel_id, "world_changed", {"text": f"В OSM появилось здание {int(height)} м в {int(dist)} м по азимуту {int(bearing)}° (демо)"})
    return refresh_hotel(hotel_id)


def reset_buildings(hotel_id: str) -> dict:
    f = geo_collector.overlay_file(hotel_id)
    if f.exists():
        f.unlink()
    return refresh_hotel(hotel_id)


# ───────────────────────── фоновая перепроверка ─────────────────────────
_started = False


def start_background() -> None:
    global _started
    if _started or not settings.refresh_enabled:
        return
    _started = True

    def loop():
        while True:
            time.sleep(max(1, settings.refresh_interval_min) * 60)
            try:
                recompute_statuses()
                r = refresh_stale(limit=5, online=settings.refresh_online)
                log.info("фоновая перепроверка: %s", r)
            except Exception:  # noqa: BLE001
                log.exception("фоновая перепроверка упала")

    threading.Thread(target=loop, name="lupa-refresh", daemon=True).start()
