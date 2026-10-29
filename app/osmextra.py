"""Дополнительные данные OpenStreetMap, которых нет в основных тайлах (app/osmtiles.py).

Основные тайлы уже собраны и лежат в репозитории, поэтому новое хранится отдельно и не ломает их:

* тайлы той же сетки (лес как препятствие для вида, автобусные остановки) → data/raw/osm_extra/tiles/;
* «пакеты вокруг точки» (вышки сотовой связи в 25 км, названные вершины в 70 км) → data/raw/osm_extra/around/,
  ячейка 0,05° (≈3,5×5,5 км) общая для соседних отелей.

Ограничения (честно): лес — это многоугольники landuse=forest/natural=wood без «дырок» (поляны и посёлки внутри леса
в OSM часто не вырезаны), высота деревьев берётся типовая (FOREST_HEIGHT_M); вышки в OSM отмечены неполно.
"""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

from .config import settings
from .osmtiles import _pts, tile_bbox, tiles_for

MAST_RADIUS_M = 25_000
PEAK_RADIUS_M = 70_000
AROUND_CELL = 0.05


def _root() -> Path:
    return settings.data_dir / "raw" / "osm_extra"


def tile_path(ix: int, iy: int) -> Path:
    return _root() / "tiles" / f"{ix}_{iy}.json.gz"


def around_key(lat: float, lon: float) -> str:
    return f"{round(lat / AROUND_CELL)}_{round(lon / AROUND_CELL)}"


def around_path(lat: float, lon: float) -> Path:
    return _root() / "around" / f"{around_key(lat, lon)}.json.gz"


def around_center(lat: float, lon: float) -> tuple[float, float]:
    return round(lat / AROUND_CELL) * AROUND_CELL, round(lon / AROUND_CELL) * AROUND_CELL


# ───────────────────────── запросы ─────────────────────────

def tile_query(ix: int, iy: int) -> str:
    s, w, n, e = tile_bbox(ix, iy)
    bb = f"({s},{w},{n},{e})"
    return f"""[out:json][timeout:120];
(
  way["natural"="wood"]{bb};
  way["landuse"="forest"]{bb};
  node["highway"="bus_stop"]{bb};
  node["public_transport"="platform"]["bus"="yes"]{bb};
);
out geom qt;
(
  relation["natural"="wood"]{bb};
  relation["landuse"="forest"]{bb};
);
out geom{bb} qt;"""


def around_query(lat: float, lon: float) -> str:
    return f"""[out:json][timeout:120];
(
  node["man_made"~"^(mast|tower)$"]["tower:type"="communication"](around:{MAST_RADIUS_M},{lat},{lon});
  node["communication:mobile_phone"="yes"](around:{MAST_RADIUS_M},{lat},{lon});
  node["man_made"="mast"]["communication:mobile_phone"](around:{MAST_RADIUS_M},{lat},{lon});
);
out tags qt;
(
  node["natural"="peak"]["name"](around:{PEAK_RADIUS_M},{lat},{lon});
);
out tags qt;"""


# ───────────────────────── разбор ответов Overpass ─────────────────────────

_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def _num(v) -> float | None:
    """Число из значения тега: «1100», «1100 m», «1 100 м», «1100,5». Тысячные пробелы склеиваем, из «a;b» берём первое."""
    s = re.sub(r"(?<=\d)[ \u00a0](?=\d{3}(?!\d))", "", str(v).split(";")[0])
    m = _NUM.search(s)
    return float(m.group().replace(",", ".")) if m else None


def parse_tile(elements: list[dict], ix: int, iy: int, fetched_at: str) -> dict:
    t = {"schema": 1, "tile": [ix, iy], "fetched_at": fetched_at, "forests": [], "bus_stops": []}
    for el in elements:
        tags = el.get("tags") or {}
        typ = el.get("type")
        if typ == "node" and (tags.get("highway") == "bus_stop" or tags.get("public_transport") == "platform"):
            if el.get("lat") is not None:
                s = {"lat": round(el["lat"], 5), "lon": round(el["lon"], 5)}
                if tags.get("name"):
                    s["n"] = tags["name"]
                t["bus_stops"].append(s)
            continue
        is_forest = tags.get("natural") == "wood" or tags.get("landuse") == "forest"
        if not is_forest:
            continue
        if typ == "way":
            geom = el.get("geometry") or []
            if len(geom) >= 4:
                t["forests"].append({"p": _pts(geom)})
        elif typ == "relation":
            for m in el.get("members", []):
                g = m.get("geometry") or []
                if m.get("role") == "outer" and len(g) >= 4:
                    t["forests"].append({"p": _pts(g)})
    return t


def parse_around(elements: list[dict], lat: float, lon: float, fetched_at: str) -> dict:
    out = {"schema": 1, "center": [lat, lon], "fetched_at": fetched_at, "masts": [], "peaks": []}
    for el in elements:
        tags = el.get("tags") or {}
        if el.get("type") != "node" or el.get("lat") is None:
            continue
        p = {"lat": round(el["lat"], 5), "lon": round(el["lon"], 5)}
        if tags.get("natural") == "peak":
            if not tags.get("name"):
                continue
            p["n"] = tags["name"]
            ele = _num(tags.get("ele"))
            if ele is not None:
                p["ele"] = ele
            out["peaks"].append(p)
            continue
        techs = [t for t in ("gsm", "umts", "lte", "5g") if any(tags.get(k) == "yes" for k in (f"communication:{t}", f"communication:mobile_phone:{t}", f"mobile_phone:{t}"))]
        op = tags.get("operator") or tags.get("communication:mobile_phone:operator") or tags.get("network")
        if op:
            p["op"] = op
        if techs:
            p["tech"] = techs
        out["masts"].append(p)
    return out


# ───────────────────────── хранение ─────────────────────────

def _save(p: Path, obj: dict) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    return p


def _load(p: Path) -> dict | None:
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_tile(t: dict) -> Path:
    return _save(tile_path(*t["tile"]), t)


def load_tile(ix: int, iy: int) -> dict | None:
    return _load(tile_path(ix, iy))


def load_tiles(lat: float, lon: float, radius_m: float = 1400.0) -> tuple[list[dict], list[tuple[int, int]]]:
    got, missing = [], []
    for ix, iy in tiles_for(lat, lon, radius_m):
        t = load_tile(ix, iy)
        (got if t else missing).append(t if t else (ix, iy))
    return got, missing


def save_around(lat: float, lon: float, obj: dict) -> Path:
    return _save(around_path(lat, lon), obj)


def load_around(lat: float, lon: float) -> dict | None:
    return _load(around_path(lat, lon))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from .geo.proj import haversine
    return haversine(lat1, lon1, lat2, lon2)


def masts_near(lat: float, lon: float, limit_m: float = MAST_RADIUS_M) -> list[dict] | None:
    """Вышки вокруг отеля (по возрастанию расстояния) или None, если пакет для ячейки не собирали."""
    pack = load_around(*around_center(lat, lon))
    if pack is None:
        return None
    out = []
    for m in pack.get("masts", []):
        d = haversine_m(lat, lon, m["lat"], m["lon"])
        if d <= limit_m:
            out.append({**m, "dist_m": round(d)})
    out.sort(key=lambda m: m["dist_m"])
    return out


def peaks_near(lat: float, lon: float, limit_m: float = PEAK_RADIUS_M) -> list[dict]:
    pack = load_around(*around_center(lat, lon))
    if not pack:
        return []
    return [p for p in pack.get("peaks", []) if haversine_m(lat, lon, p["lat"], p["lon"]) <= limit_m]
