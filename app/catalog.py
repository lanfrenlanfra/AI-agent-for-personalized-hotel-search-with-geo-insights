"""Каталог региона: дешёвый индекс всех отелей и умный отбор.

Идея (как в любом поиске по большому инвентарю): сначала дёшево отобрать кандидатов по тому, что видно уже
в каталоге — цена, звёзды, оценка, расстояние до метро и пляжа, удобства-флаги, — и только потом дорого проверять
их глубоко (карточка, отзывы, карта, расчёт заката и шума). Каталог региона — это сотни и тысячи записей за десятки
запросов, а глубокая проверка — несколько запросов на отель.

* `crawl` — собрать страницы каталога в data/raw/catalog/<регион>.json (нужен интернет);
* `load` — загрузить индекс в БД;
* `stratified_sample` — выбор отелей для первичного глубокого сбора: разные районы, цены и типы, а не «первые N»;
* `coarse_score` / `candidates` — оценка кандидатов под запрос по дешёвым признакам.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from . import db
from .config import settings

CHEAP_FLAGS = {"pets_allowed": "has_pets", "parking": "has_parking", "family_friendly": "has_kids", "air_conditioning": "air-conditioning",
               "kitchen": "kitchen"}
WALK_M_PER_MIN = 80.0
DETOUR = 1.3


def catalog_file(region: str | None = None) -> Path:
    r = re.sub(r"[^a-z0-9._-]+", "_", (region or settings.region_slug).lower())
    return settings.data_dir / "raw" / "catalog" / f"{r}.json"


def save_file(rows: list[dict], region: str, total: int | None) -> Path:
    p = catalog_file(region)
    p.parent.mkdir(parents=True, exist_ok=True)
    from .collectors.base import now_iso
    p.write_text(json.dumps({"schema": 1, "region": region, "total_in_region": total, "fetched_at": now_iso(), "hotels": rows}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return p


def crawl(region: str, pages: int, start: int = 1, min_reviews: int = 0, progress=print) -> tuple[list[dict], int | None]:
    """Обойти страницы каталога региона. Сеть нужна только здесь."""
    from . import net
    rows: dict[str, dict] = {}
    total = None
    for page in range(start, start + pages):
        try:
            items, t = net.catalog_page(region, page)
        except Exception as e:  # noqa: BLE001
            progress(f"каталог, страница {page}: {e}")
            break
        total = t or total
        if not items:
            break
        for it in items:
            if it["reviews"] >= min_reviews and it["lat"] is not None:
                rows[it["master_id"]] = it
        if page % 10 == 0 or page == start:
            progress(f"каталог: страница {page}, отелей в индексе {len(rows)}")
    return list(rows.values()), total


def load(region: str | None = None) -> int:
    """Файл каталога → таблица catalog (статусы глубокой проверки сохраняются)."""
    p = catalog_file(region)
    if not p.exists():
        return 0
    data = json.loads(p.read_text(encoding="utf-8"))
    n = 0
    with db.tx() as c:
        for r in data.get("hotels", []):
            c.execute(
                "INSERT INTO catalog(master_id, slug, region, name, kind, apart, stars, rating, reviews, price, lat, lon, address, center_m, subway_m, subway_name, beach_m, amenities, fetched_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(master_id) DO UPDATE SET "
                "slug=excluded.slug, name=excluded.name, kind=excluded.kind, apart=excluded.apart, stars=excluded.stars, rating=excluded.rating, reviews=excluded.reviews, "
                "price=excluded.price, lat=excluded.lat, lon=excluded.lon, address=excluded.address, center_m=excluded.center_m, subway_m=excluded.subway_m, "
                "subway_name=excluded.subway_name, beach_m=excluded.beach_m, amenities=excluded.amenities, fetched_at=excluded.fetched_at",
                (r["master_id"], r["slug"], r.get("region") or data.get("region"), r["name"], r.get("kind"), int(bool(r.get("apart"))), r.get("stars"), r.get("rating"), r.get("reviews"),
                 r.get("price"), r["lat"], r["lon"], r.get("address"), r.get("center_m"), r.get("subway_m"), r.get("subway_name"), r.get("beach_m"),
                 db.dumps(r.get("amenities") or []), data.get("fetched_at")))
            n += 1
        # уже глубоко собранные отели помечаем
        c.execute("UPDATE catalog SET hotel_id=(SELECT h.id FROM hotels h WHERE h.master_id=catalog.master_id), enrich_status='done' "
                  "WHERE master_id IN (SELECT master_id FROM hotels) AND enrich_status!='done'")
    return n


def summary(r) -> dict:
    """Запись каталога → то, что нужно net.fetch_hotel (цена и звёзды из каталога попадают в карточку)."""
    return {"master_id": str(r["master_id"]), "slug": r["slug"], "name": r["name"], "region": r["region"],
            "serp": {"datelessRate": {"amount": r["price"]}, "stars": r["stars"], "searchRegionCenterDistance": r.get("center_m") if isinstance(r, dict) else r["center_m"]}}


# ───────────────────────── отбор для первичного сбора ─────────────────────────

def _kind_group(r: dict) -> str:
    k = (r.get("kind") or "").upper()
    if r.get("apart") or "APART" in k:
        return "apart"
    if "HOSTEL" in k:
        return "hostel"
    return "hotel"


def stratified_sample(rows: list[dict], n: int, min_reviews: int = 15, cell_deg: float = 0.012) -> list[dict]:
    """Разнообразная выборка: ячейки карты × ценовые терцили × тип размещения; внутри — отели с самым большим числом отзывов
    (по ним есть что проверять); ячейки обходятся по кругу, чтобы центр не вытеснил окраины."""
    pool = [r for r in rows if r.get("lat") is not None and (r.get("reviews") or 0) >= min_reviews]
    prices = sorted(r["price"] for r in pool if r.get("price"))
    t1, t2 = (prices[len(prices) // 3], prices[2 * len(prices) // 3]) if len(prices) >= 3 else (0, 0)
    buckets: dict[tuple, list[dict]] = {}
    for r in pool:
        price = r.get("price") or 0
        tier = 0 if price <= t1 else 1 if price <= t2 else 2
        key = (int(r["lat"] / cell_deg), int(r["lon"] / (cell_deg * 2)), tier, _kind_group(r))
        buckets.setdefault(key, []).append(r)
    for b in buckets.values():
        b.sort(key=lambda r: -(r.get("reviews") or 0))
    order = sorted(buckets.values(), key=lambda b: -(b[0].get("reviews") or 0))
    out: list[dict] = []
    depth = 0
    while len(out) < n and any(len(b) > depth for b in order):
        for b in order:
            if len(b) > depth and len(out) < n:
                out.append(b[depth])
        depth += 1
    return out


# ───────────────────────── грубая оценка под запрос ─────────────────────────

def _row(r) -> dict:
    d = dict(r)
    d["amenities"] = db.loads(d.get("amenities"), []) or []
    return d


def _walk_score(meters: float | None, thr_min: float) -> float | None:
    if meters is None:
        return None
    minutes = meters * DETOUR / WALK_M_PER_MIN
    return 1.0 if minutes <= thr_min else max(0.0, 1.0 - (minutes - thr_min) / max(thr_min, 1.0))


def coarse_score(parsed: dict, r: dict) -> tuple[float, list[str]]:
    """0..1 и пояснения: что видно по каталогу. Закат, тишину и вид по каталогу не узнать — они вносят нейтральные 0,5."""
    notes: list[str] = []
    parts: list[float] = []
    am = set(r.get("amenities") or [])
    for it in parsed["intents"]:
        f = it["feature"]
        if f in ("walk_to_metro", "walk_to_beach", "walk_to_center"):
            m = {"walk_to_metro": r.get("subway_m"), "walk_to_beach": r.get("beach_m"), "walk_to_center": r.get("center_m")}[f]
            thr = float(it.get("threshold") or {"walk_to_metro": 10, "walk_to_beach": 15, "walk_to_center": 15}[f])
            sc = _walk_score(m, thr)
            if sc is None:
                parts.append(0.35)
            else:
                parts.append(sc)
                notes.append(f"{it['title'].lower()}: ≈{round(m * DETOUR / WALK_M_PER_MIN)} мин по каталогу")
        elif f in CHEAP_FLAGS:
            has = CHEAP_FLAGS[f] in am
            parts.append(1.0 if has == bool(it.get("want", True)) else 0.1)
            notes.append(f"{it['title'].lower()}: {'есть' if has else 'нет'} в каталоге")
        elif f == "wifi_quality":
            parts.append(1.0 if "has_internet" in am else 0.3)
        else:
            parts.append(0.5)
    base = sum(parts) / len(parts) if parts else 0.5
    rating = r.get("rating") or 0
    q = 0.25 * max(0.0, min(1.0, (rating - 6.5) / 3.0)) + 0.2 * min(1.0, math.log10(1 + (r.get("reviews") or 0)) / 3.0)
    score = 0.55 * base + q
    flt = parsed.get("filters") or {}
    if flt.get("price_max") and r.get("price") and r["price"] > flt["price_max"]:
        score *= 0.25
        notes.append(f"дороже {int(flt['price_max'])} ₽ (от {int(r['price'])} ₽)")
    if flt.get("stars_min") and (r.get("stars") or 0) < flt["stars_min"]:
        score *= 0.4
    if flt.get("kind"):
        from .search import KIND_NAMES, kind_codes      # поздний импорт: каталог не должен тянуть геомодули поиска при загрузке
        code = (r.get("kind") or "").upper()
        if code in kind_codes(flt["kind"]):
            notes.append(f"тип: {KIND_NAMES.get(code, code.lower())}")
        else:
            score *= 0.1                                 # в очередь на проверку ставим жильё того типа, что просили
    if flt.get("place"):
        from . import places
        inside = places.matches(flt["place"], places.place_of(r.get("master_id")), r.get("lat"), r.get("lon"))
        if inside:
            notes.append(f"место: {flt.get('place_title') or places.title(flt['place'])}")
        elif inside is False:
            score *= 0.1                                 # и в том посёлке, что просили
    return round(score, 3), notes


def stats() -> dict:
    row = db.row("SELECT COUNT(*) AS n, SUM(enrich_status='done') AS d, SUM(enrich_status IN ('queued','running')) AS p, SUM(enrich_status='failed') AS f FROM catalog")
    return {"total": row["n"] or 0, "enriched": row["d"] or 0, "pending": row["p"] or 0, "failed": row["f"] or 0}


def candidates(parsed: dict, k: int = 12, only_unverified: bool = True) -> list[dict]:
    """Лучшие по дешёвым признакам отели каталога (по умолчанию — ещё не проверенные глубоко)."""
    sql = "SELECT * FROM catalog" + (" WHERE enrich_status!='done'" if only_unverified else "")
    scored = []
    for r in db.rows(sql):
        d = _row(r)
        s, notes = coarse_score(parsed, d)
        d["coarse"] = s
        d["notes"] = notes
        scored.append(d)
    scored.sort(key=lambda d: -d["coarse"])
    return scored[:k]
