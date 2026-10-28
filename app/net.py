"""Сетевой слой: Островок и Overpass (OpenStreetMap).

Вежливый клиент: пауза между запросами, повторы с нарастающей задержкой, зеркала Overpass.
Если Островок отдаёт страницу антибота (нет __NEXT_DATA__) — понятная ошибка, а не молчаливый пропуск.
Всё, что требует сети, живёт здесь; остальное приложение работает офлайн по сохранённым данным.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import requests

from . import ostrovok, osmtiles
from .config import settings

log = logging.getLogger("lupa.net")
OSTROVOK = "https://ostrovok.ru"
OVERPASS = ["https://maps.mail.ru/osm/tools/overpass/api/interpreter", "https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.private.coffee/api/interpreter"]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 lupa-hackathon/1.0",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
}
_session: requests.Session | None = None
_last = 0.0


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(HEADERS)
    return _session


def get(url: str, params: dict | None = None, retries: int = 3, timeout: int = 40) -> requests.Response:
    global _last
    err: Exception | None = None
    for attempt in range(retries):
        wait = settings.collect_delay - (time.time() - _last)
        if wait > 0:
            time.sleep(wait)
        try:
            r = session().get(url, params=params, timeout=timeout)
            _last = time.time()
            if r.status_code in (429, 503):
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"не удалось получить {url}: {err}")


# ───────────────────────── Островок ─────────────────────────

def listing(region: str, page: int) -> list[dict]:
    url = f"{OSTROVOK}/hotel/{region}/"
    r = get(url, params={"page": page} if page > 1 else None)
    return [ostrovok.serp_summary(i) for i in ostrovok.serp_items(ostrovok.parse_next_data(r.text))]


def catalog_page(region: str, page: int) -> tuple[list[dict], int | None]:
    """Одна страница каталога → (дешёвые записи, всего отелей в регионе)."""
    r = get(f"{OSTROVOK}/hotel/{region}/", params={"page": page} if page > 1 else None)
    pp = ostrovok.parse_next_data(r.text)
    total = (pp.get("serpData") or {}).get("totalHotels")
    return [ostrovok.catalog_row(i) for i in ostrovok.serp_items(pp)], total


def hotel_url(region: str, master_id: str, slug: str) -> str:
    return f"{OSTROVOK}/hotel/{region}/mid{master_id}/{slug}/"


def fetch_reviews(master_id: str) -> dict | None:
    try:
        r = get(f"{OSTROVOK}/hotel/search/v1/site/ost_reviews", params={"master_id": master_id, "lang": "ru"})
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("отзывы %s: %s", master_id, e)
        return None


def fetch_hotel(region: str, summary: dict) -> dict:
    region = summary.get("region") or region        # у отеля свой населённый пункт: russia/listvyanka, russia/irkutsk_oblast_khuzhir…
    url = hotel_url(region, summary["master_id"], summary["slug"])
    page = ostrovok.parse_next_data(get(url).text)
    rev = fetch_reviews(summary["master_id"])
    return ostrovok.build_record(page, summary["slug"], region, url, summary.get("serp"), rev)


def save_record(rec: dict) -> str:
    d = settings.data_dir / "raw" / "ostrovok"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f'{rec["master_id"]}.json'
    p.write_text(json.dumps(rec, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return str(p)


# ───────────────────────── OpenStreetMap ─────────────────────────

def fetch_tile(ix: int, iy: int) -> dict:
    q = osmtiles.overpass_query(ix, iy)
    err: Exception | None = None
    for url in OVERPASS * 2:
        try:
            r = session().post(url, data={"data": q}, timeout=200)
            if r.status_code in (429, 504):
                time.sleep(8)
                continue
            r.raise_for_status()
            return osmtiles.parse_overpass(r.json().get("elements", []), ix, iy, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(3)
    raise RuntimeError(f"Overpass недоступен для тайла {ix},{iy}: {err}")


def ensure_tiles(lat: float, lon: float, radius_m: float = 1400.0, max_age_days: int | None = None) -> int:
    """Скачать недостающие (и устаревшие, если задан max_age_days) тайлы вокруг точки. Возвращает число скачанных."""
    n = 0
    for ix, iy in osmtiles.tiles_for(lat, lon, radius_m):
        t = osmtiles.load_tile(ix, iy)
        if t and max_age_days is not None:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(t["fetched_at"])).days
            except Exception:  # noqa: BLE001
                age = 10**6
            if age <= max_age_days:
                continue
        elif t:
            continue
        osmtiles.save_tile(fetch_tile(ix, iy))
        n += 1
    return n
