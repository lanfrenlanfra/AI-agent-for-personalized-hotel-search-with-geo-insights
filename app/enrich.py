"""Дозаполнение «по требованию»: глубокая проверка тех отелей, которые нужны под запрос.

Каталог региона (app/catalog.py) знает все отели, но дёшево. Когда пользователь пишет запрос, мы:
1. сразу отвечаем по тем отелям, что уже проверены глубоко;
2. по каталогу выбираем лучших ещё не проверенных кандидатов (грубая оценка по метро, пляжу, цене, удобствам);
3. ставим их в очередь — фоновый воркер по одному, вежливо, скачивает карточку и отзывы, тайлы OSM, считает
   закат, шум и вид и сводит факты; результат кэшируется в БД, поэтому следующий похожий запрос уже мгновенный;
4. интерфейс показывает «проверено X из Y подходящих» и сам обновляет выдачу, пока очередь не опустеет.

Без интернета воркер честно помечает отель как «не удалось» с причиной; поиск по уже собранному продолжает работать.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from . import catalog, db, ingest, pipeline
from .config import settings

log = logging.getLogger("luppa.enrich")

RETRY_AFTER_MIN = 15
_wake = threading.Event()
_started = False
_lock = threading.Lock()
_recent: list[float] = []     # времена последних глубоких проверок, для часового лимита


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def enabled() -> bool:
    return bool(settings.enrich_on_demand) and not getattr(settings, "demo_mode", False) and catalog.stats()["total"] > 0


def queue(master_ids: list[str]) -> list[str]:
    """Поставить в очередь; уже проверенные и уже стоящие в очереди пропускаются, недавние неудачи — тоже."""
    put = []
    retry_before = (datetime.now(timezone.utc) - timedelta(minutes=RETRY_AFTER_MIN)).isoformat(timespec="seconds")
    with db.tx() as c:
        for mid in master_ids:
            r = c.execute("SELECT enrich_status, enrich_updated FROM catalog WHERE master_id=?", (mid,)).fetchone()
            if not r or r["enrich_status"] in ("done", "queued", "running"):
                continue
            if r["enrich_status"] == "failed" and (r["enrich_updated"] or "") > retry_before:
                continue
            c.execute("UPDATE catalog SET enrich_status='queued', enrich_error=NULL, enrich_updated=? WHERE master_id=?", (_now(), mid))
            put.append(mid)
    if put:
        _wake.set()
    return put


def _hour_budget_left() -> int:
    cutoff = time.time() - 3600
    _recent[:] = [t for t in _recent if t > cutoff]
    return max(0, settings.enrich_max_per_hour - len(_recent))


def process(master_id: str) -> str:
    """Глубокая проверка одного отеля. Возвращает id отеля в нашей базе. Сеть нужна для карточки, отзывов и тайлов."""
    from . import net
    r = db.row("SELECT * FROM catalog WHERE master_id=?", (master_id,))
    if not r:
        raise KeyError(master_id)
    rec = net.fetch_hotel(r["region"] or settings.region_slug, catalog.summary(r))
    net.save_record(rec)
    hid = ingest.upsert_hotel(rec)
    try:
        net.ensure_tiles(rec["lat"], rec["lon"])
    except Exception as e:  # noqa: BLE001 — без карты остаются отзывы и карточка; геофакты появятся при следующей проверке
        log.warning("тайлы OSM для %s: %s", master_id, e)
    pipeline.process_hotel(hid)
    return hid


def run_one(master_id: str) -> bool:
    db.execute("UPDATE catalog SET enrich_status='running', enrich_updated=? WHERE master_id=?", (_now(), master_id))
    _recent.append(time.time())
    try:
        hid = process(master_id)
    except Exception as e:  # noqa: BLE001
        log.warning("не удалось проверить %s: %s", master_id, e)
        db.execute("UPDATE catalog SET enrich_status='failed', enrich_error=?, enrich_updated=? WHERE master_id=?", (str(e)[:300], _now(), master_id))
        return False
    db.execute("UPDATE catalog SET enrich_status='done', hotel_id=?, enrich_error=NULL, enrich_updated=? WHERE master_id=?", (hid, _now(), master_id))
    from . import identify
    identify.start_indexing()                     # запомнить фото нового отеля, чтобы узнавать его по снимку
    return True


def process_queue(limit: int | None = None, delay: float = 0.0) -> dict:
    """Обработать очередь (синхронно). Воркер вызывает это в фоне; тесты — напрямую."""
    done = failed = 0
    while limit is None or done + failed < limit:
        if _hour_budget_left() <= 0:
            break
        r = db.row("SELECT master_id FROM catalog WHERE enrich_status='queued' ORDER BY enrich_updated LIMIT 1")
        if not r:
            break
        if run_one(r["master_id"]):
            done += 1
        else:
            failed += 1
        if delay:
            time.sleep(delay)
    return {"done": done, "failed": failed}


def _worker() -> None:
    while True:
        _wake.wait(timeout=30)
        _wake.clear()
        try:
            process_queue(delay=settings.collect_delay)
        except Exception:  # noqa: BLE001
            log.exception("воркер дозаполнения")


def start_worker() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    # прерванные перезапуском задачи возвращаем в очередь
    db.execute("UPDATE catalog SET enrich_status='queued' WHERE enrich_status='running'")
    threading.Thread(target=_worker, name="enrich", daemon=True).start()
    if db.row("SELECT 1 AS x FROM catalog WHERE enrich_status='queued' LIMIT 1"):
        _wake.set()


# ───────────────────────── связка с поиском ─────────────────────────

def _card(c: dict) -> dict:
    return {"master_id": c["master_id"], "name": c["name"], "stars": c["stars"], "rating": c["rating"], "reviews": c["reviews"], "price": c["price"],
            "address": c["address"], "lat": c["lat"], "lon": c["lon"], "status": c["enrich_status"] or "candidate", "error": c.get("enrich_error"),
            "coarse": c["coarse"], "notes": c["notes"], "subway": c.get("subway_name")}


def augment(parsed: dict, result: dict, queue_new: bool = True) -> dict:
    """Дополнить ответ поиска: покрытие каталога, кандидаты и постановка лучших в очередь."""
    cov = {"enabled": False, **catalog.stats(), "queued_now": 0, "candidates": []}
    if not cov["total"]:
        result["coverage"] = cov
        return result
    cov["enabled"] = enabled()
    cands = catalog.candidates(parsed, k=max(settings.enrich_batch * 2, 12))
    pin = [str(m) for m in parsed.get("pinned_catalog") or []]       # гость назвал отель, которого мы ещё не изучали — он первый в очереди
    if pin:
        rows = {str(r["master_id"]): r for r in db.rows(f"SELECT * FROM catalog WHERE master_id IN ({','.join('?' * len(pin))})", pin)}
        first = [dict(catalog._row(rows[m]), coarse=1.0, notes=["вы назвали этот отель"]) for m in pin if m in rows]
        cands = first + [c for c in cands if str(c["master_id"]) not in rows]
    if cov["enabled"]:
        if queue_new:
            cov["queued_now"] = len(queue([c["master_id"] for c in cands[:settings.enrich_batch]]))
            start_worker()
            cov.update(catalog.stats())
        cands = [dict(c, **(db.row("SELECT enrich_status, enrich_error FROM catalog WHERE master_id=?", (c["master_id"],)) or {})) for c in cands]
    cov["candidates"] = [_card(c) for c in cands if c.get("enrich_status") != "done"][:settings.enrich_batch]
    if pin and not parsed.get("intents") and not parsed.get("filters"):
        cov["candidates"] = [c for c in cov["candidates"] if str(c["master_id"]) in pin]      # спросили один отель — только его
    result["coverage"] = cov
    return result
