"""Синтетический демо-мир для тестов и «холодного» запуска без интернета.

ЭТО НЕ ДАННЫЕ ОСТРОВКА. Отели, отзывы и карта придуманы и помечены synthetic=true; интерфейс показывает
предупреждение. Реальные данные собирают scripts/collect_ostrovok.py и scripts/collect_osm.py
(`docker compose run --rm collect`), после чего демо-набор больше не используется.

Мир устроен так, чтобы показывать возможности сервиса: у одного отеля закат виден только с верхних этажей,
у другого окна во двор тихие, а на улицу — шумные, у третьего карточка устарела и противоречит свежим отзывам.
"""
from __future__ import annotations

import gzip
import json
import zlib
from datetime import date, timedelta
from pathlib import Path

from . import osmtiles
from .geo.proj import Frame

LAT0, LON0 = 59.9300, 30.3300
STEP = 50


def _nid(x: float, y: float) -> int:
    return int(round(x / 10)) * 100003 + int(round(y / 10)) + 9_000_000_000


def _line(frame: Frame, pts: list[tuple[float, float]]):
    return [[round(frame.ll(x, y)[0], 5), round(frame.ll(x, y)[1], 5)] for x, y in pts]


def _dense(a: tuple[float, float], b: tuple[float, float]) -> list[tuple[float, float]]:
    n = max(1, int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) // STEP))
    return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1)]


def build_osm(root: Path, stamp: str) -> int:
    fr = Frame(LAT0, LON0)
    tiles: dict[tuple[int, int], dict] = {}

    def tile_for(lat: float, lon: float) -> dict:
        ix, iy = osmtiles.tile_of(lat, lon)
        if (ix, iy) not in tiles:
            tiles[(ix, iy)] = {"schema": 1, "tile": [ix, iy], "bbox": list(osmtiles.tile_bbox(ix, iy)), "fetched_at": stamp + "T09:00:00+00:00",
                               "buildings": [], "roads": [], "rails": [], "water": [], "beaches": [], "pois": [], "construction": []}
        return tiles[(ix, iy)]

    bid = [1000]

    def building(cx, cy, w, d, h=None, lv=None, name=None):
        bid[0] += 1
        ring = [(cx - w / 2, cy - d / 2), (cx + w / 2, cy - d / 2), (cx + w / 2, cy + d / 2), (cx - w / 2, cy + d / 2), (cx - w / 2, cy - d / 2)]
        b = {"id": bid[0], "p": _line(fr, ring)}
        if h:
            b["h"] = str(h)
        if lv:
            b["lv"] = str(lv)
        if name:
            b["n"] = name
        lat, lon = fr.ll(cx, cy)
        tile_for(lat, lon)["buildings"].append(b)

    def road(hw, name, a, b, oneway=False):
        pts = _dense(a, b)
        r = {"id": zlib.crc32(f"{hw}{name}{a}{b}".encode()), "hw": hw, "p": _line(fr, pts), "nodes": [_nid(*p) for p in pts]}
        if name:
            r["n"] = name
        lat, lon = fr.ll(*pts[len(pts) // 2])
        tile_for(lat, lon)["roads"].append(r)

    def poi(kind, x, y, name=None, oh=None):
        lat, lon = fr.ll(x, y)
        p = {"k": kind, "lat": round(lat, 5), "lon": round(lon, 5)}
        if name:
            p["n"] = name
        if oh:
            p["oh"] = oh
        tile_for(lat, lon)["pois"].append(p)

    # улицы
    road("primary", "Тестовый проспект", (-450, 60), (450, 60))
    road("residential", "Тихая улица", (-450, -60), (450, -60))
    for x in (-250, -150, 0, 150, 250):
        road("residential", f"Переулок {x}", (x, -450), (x, 450))
    road("pedestrian", "Пешеходная улица", (-450, -250), (450, -250))
    # вода
    for k in range(-9, 10):
        a, b = (-200, k * 50), (-200, (k + 1) * 50)
        t = tile_for(*fr.ll(-200, k * 50 + 25))
        t["water"].append({"k": "canal", "p": _line(fr, [a, b])})
    # застройка: ряд у проспекта (y 20..50) и ряд у тихой улицы (y -50..-20)
    front_hotels, back_hotels = (0,), (120, -130)
    for x in range(-420, 430, 40):
        if -300 < x < -150:
            continue                                         # набережная канала: застройки нет, горизонт на западе открыт
        if all(abs(x - hx) > 30 for hx in front_hotels):
            building(x, 35, 36, 30, h=21)
        if all(abs(x - hx) > 30 for hx in back_hotels):
            building(x, -35, 36, 30, h=18 if x % 80 else 24)
    building(0, 35, 20, 30, h=30, lv=9, name="Тестовый отель «Двор и закат»")
    building(120, -35, 24, 30, h=24, lv=7, name="Тестовый отель «Тишина»")
    building(-130, -35, 24, 30, h=18, lv=5, name="Тестовый отель «Канал»")
    building(120, -105, 70, 28, h=45)                        # высокая стена за Тихой улицей
    for x in range(-420, 430, 60):
        building(x, 100, 40, 30, h=24)
        if abs(x - 120) > 50:
            building(x, -110, 40, 30, h=21)
    # заведения, метро, площадка, стройка
    poi("bar", 12, 75, "Бар «Ночной»", "Mo-Su 18:00-04:00")
    poi("pharmacy", 100, -75, "Аптека 24", "24/7")
    poi("supermarket", -60, -75, "Магазин", "24/7")
    poi("subway", 300, 80, "метро «Тестовое»")
    poi("playground", -135, -80)
    poi("charging_station", 80, 75)
    tile_for(*fr.ll(180, -100))["construction"].append({"k": "building", "lat": round(fr.ll(180, -100)[0], 5), "lon": round(fr.ll(180, -100)[1], 5)})
    d = root / "raw" / "osm" / "tiles"
    d.mkdir(parents=True, exist_ok=True)
    for (ix, iy), t in tiles.items():
        with gzip.open(d / f"{ix}_{iy}.json.gz", "wt", encoding="utf-8") as f:
            json.dump(t, f, ensure_ascii=False, separators=(",", ":"))
    return len(tiles)


def _reviews(prefix: str, spec: list[tuple[int, str, str, str, float]]) -> list[dict]:
    today = date.today()
    out = []
    for i, (days_ago, plus, minus, text, rating) in enumerate(spec):
        out.append({"id": f"{prefix}{i}", "date": (today - timedelta(days=days_ago)).isoformat(), "plus": plus, "minus": minus, "text": text,
                    "rating": rating, "room": "Стандарт", "traveller": "couple", "trip": "leisure", "adults": 2, "children": 0,
                    "detailed": {"cleanness": int(rating), "wifi": 8, "meal": 8}, "wifi_quality": "good", "images": []})
    return out


def build_hotels(root: Path) -> int:
    fr = Frame(LAT0, LON0)
    today = date.today()

    def pos(x, y):
        la, lo = fr.ll(x, y)
        return round(la, 5), round(lo, 5)

    def base(mid, slug, name, x, y, stars, price, rating, fetched_days, floors, year):
        la, lo = pos(x, y)
        return {"schema": 1, "synthetic": True, "master_id": mid, "slug": slug, "region": "synthetic", "url": f"synthetic://{slug}",
                "fetched_at": (today - timedelta(days=fetched_days)).isoformat() + "T10:00:00+00:00", "name": name, "city": "Санкт-Петербург (синтетический)",
                "address": "Тестовый проспект, 1", "lat": la, "lon": lo, "stars": stars, "rating": rating, "num_reviews": 14, "price_from": price,
                "kind": "Hotel", "facts": {"floors": floors, "rooms": 40, "year_built": year, "year_renovated": 0},
                "region_center": {"lat": 59.9398, "lng": 30.3146}, "nearest_subways": [{"name": "Тестовое", "distance": 300}], "nearest_beaches": [], "pois": [],
                "description": [{"title": "Об отеле", "paragraphs": [f"{name}: придуманный отель для демонстрации сервиса. Данные не принадлежат Островку."]}],
                "images": [], "room_groups": [], "review_images": [], "policies": {}, "detailed_ratings": {"cleanness": 9.1, "wifi": 8.4, "meal": 8.8}}

    def am(*names):
        return {"general": {"name": "Общее", "items": [{"id": i, "name": n, "free": True} for i, n in enumerate(names)]}}

    h1 = base("990001", "test_dvor_i_zakat", "Тестовый отель «Двор и закат»", 0, 35, 4, 7600, 9.2, 3, 9, 2012)
    h1["amenities"] = am("Лифт", "Кондиционер", "Круглосуточная стойка регистрации", "Хранение багажа")
    h1["policies"] = {"extra_info": "Часы работы стойки регистрации 10:00 - 22:00."}
    h1["reviews"] = _reviews("a", [
        (20, "Тихий номер во двор, отлично выспались", "", "Закат смотрели с 8 этажа — красота.", 10),
        (60, "Завтрак вкусный", "На проспект окна шумные, слышно трамвай и бар по ночам", "", 8),
        (110, "Чисто", "", "Стойка регистрации работает круглосуточно, заселились в 3 ночи без проблем.", 9),
        (200, "Лифт быстрый", "", "Окна во двор — очень тихо.", 9),
        (330, "Удобная кровать", "Ночью слышен бар напротив", "", 8),
        (500, "Вайфай отличный", "", "С верхних этажей виден закат над крышами.", 10),
        (700, "Чистые номера", "", "", 9),
    ])
    h2 = base("990002", "test_tishina", "Тестовый отель «Тишина»", 120, -35, 3, 4800, 8.7, 5, 7, 1980)
    h2["amenities"] = am("Лифт", "Парковка", "Хранение багажа")
    h2["reviews"] = _reviews("b", [
        (15, "Тихо, спокойно", "", "Окна выходят во двор, ночью тишина.", 9),
        (80, "Хороший завтрак", "Стена высокого дома напротив — света мало", "Закат из окна не видно, соседний дом закрывает.", 8),
        (160, "Чисто и тихо", "", "С детьми было удобно, рядом площадка.", 9),
        (260, "Тишина полная", "", "", 9),
        (420, "Wi-Fi медленный", "", "", 7),
    ])
    h3 = base("990003", "test_kanal", "Тестовый отель «Канал»", -130, -35, 3, 5200, 8.3, 430, 5, 1905)
    h3["amenities"] = am("Кондиционер", "Домашние животные размещаются", "Без лифта")
    h3["reviews"] = _reviews("c", [
        (25, "Вид на канал из окна, закат прекрасный", "Кондиционера нет, душно", "", 8),
        (70, "", "В номере жарко, кондиционер не работает", "", 6),
        (140, "Рядом набережная", "", "Нет лифта, чемоданы тащили на 4 этаж.", 7),
        (300, "Кошка проживала с нами без проблем", "", "", 9),
        (520, "Атмосферное здание", "", "", 9),
    ])
    d = root / "raw" / "ostrovok"
    d.mkdir(parents=True, exist_ok=True)
    for h in (h1, h2, h3):
        (d / f'{h["master_id"]}.json').write_text(json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
    return 3


def build(root: Path) -> dict:
    stamp = (date.today() - timedelta(days=4)).isoformat()
    return {"tiles": build_osm(root, stamp), "hotels": build_hotels(root)}
