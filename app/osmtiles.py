"""Тайлы OpenStreetMap: сетка, запрос Overpass, разбор ответа, хранение.

Сырой тайл — компактный JSON (gzip), общий для всех отелей рядом: так репозиторий
остаётся небольшим, а сбор не повторяет запросы.
"""
from __future__ import annotations

import gzip
import json
import math
from pathlib import Path

from .config import settings

DLAT = 0.01
DLON = 0.02
ROAD_CLASSES = "motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service|pedestrian|footway|path|steps|cycleway|track"
POI_AMENITY = {"bar", "pub", "nightclub", "pharmacy", "cafe", "restaurant", "fast_food", "hospital", "clinic", "charging_station", "parking"}
POI_SHOP = {"convenience", "supermarket"}


def tile_of(lat: float, lon: float) -> tuple[int, int]:
    return (math.floor(lon / DLON), math.floor(lat / DLAT))


def tile_bbox(ix: int, iy: int) -> tuple[float, float, float, float]:
    """south, west, north, east"""
    return (iy * DLAT, ix * DLON, (iy + 1) * DLAT, (ix + 1) * DLON)


def tiles_for(lat: float, lon: float, radius_m: float = 1400.0) -> list[tuple[int, int]]:
    dlat = radius_m / 111_195.0
    dlon = radius_m / (111_195.0 * math.cos(math.radians(lat)))
    x0, y0 = tile_of(lat - dlat, lon - dlon)
    x1, y1 = tile_of(lat + dlat, lon + dlon)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def tiles_dir() -> Path:
    return settings.data_dir / "raw" / "osm" / "tiles"


def tile_path(ix: int, iy: int) -> Path:
    return tiles_dir() / f"{ix}_{iy}.json.gz"


def save_tile(tile: dict) -> Path:
    p = tile_path(*tile["tile"])
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump(tile, f, ensure_ascii=False, separators=(",", ":"))
    return p


def load_tile(ix: int, iy: int) -> dict | None:
    p = tile_path(ix, iy)
    if not p.exists():
        return None
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return json.load(f)


def load_tiles(lat: float, lon: float, radius_m: float = 1400.0) -> tuple[list[dict], list[tuple[int, int]]]:
    got, missing = [], []
    for ix, iy in tiles_for(lat, lon, radius_m):
        t = load_tile(ix, iy)
        (got if t else missing).append(t if t else (ix, iy))
    return got, missing


def overpass_query(ix: int, iy: int) -> str:
    s, w, n, e = tile_bbox(ix, iy)
    bb = f"({s},{w},{n},{e})"
    return f"""[out:json][timeout:180];
(
  way["building"]{bb};
  way["highway"~"^({ROAD_CLASSES})$"]{bb};
  way["railway"~"^(tram|rail|light_rail|subway)$"]["tunnel"!="yes"]{bb};
  way["natural"~"^(coastline|water|beach)$"]{bb};
  way["waterway"~"^(river|canal|riverbank)$"]{bb};
  way["leisure"="playground"]{bb};
  nwr["amenity"~"^({'|'.join(sorted(POI_AMENITY))})$"]{bb};
  nwr["shop"~"^({'|'.join(sorted(POI_SHOP))})$"]{bb};
  node["railway"="station"]["station"="subway"]{bb};
  node["railway"="subway_entrance"]{bb};
  way["landuse"="construction"]{bb};
  way["highway"="construction"]{bb};
);
out geom qt;
(
  relation["natural"="water"]{bb};
);
out geom{bb} qt;"""


def _centroid(geom: list[dict]) -> tuple[float, float] | None:
    if not geom:
        return None
    return (sum(p["lat"] for p in geom) / len(geom), sum(p["lon"] for p in geom) / len(geom))


def _pts(geom: list[dict]) -> list[list[float]]:
    return [[round(p["lat"], 5), round(p["lon"], 5)] for p in geom]


def parse_overpass(elements: list[dict], ix: int, iy: int, fetched_at: str) -> dict:
    t: dict = {"schema": 1, "tile": [ix, iy], "bbox": list(tile_bbox(ix, iy)), "fetched_at": fetched_at,
               "buildings": [], "roads": [], "rails": [], "water": [], "beaches": [], "pois": [], "construction": []}

    def add_poi(kind: str, tags: dict, lat: float, lon: float):
        p = {"k": kind, "lat": round(lat, 5), "lon": round(lon, 5)}
        if tags.get("name"):
            p["n"] = tags["name"]
        if tags.get("opening_hours"):
            p["oh"] = tags["opening_hours"]
        extra = {k: tags[k] for k in ("access", "healthcare:speciality") if k in tags}
        if extra:
            p["t"] = extra
        t["pois"].append(p)

    for el in elements:
        tags = el.get("tags") or {}
        typ = el.get("type")
        geom = el.get("geometry") or []
        if typ == "way" and tags.get("building") and tags.get("building") != "construction":
            if len(geom) >= 4:
                b = {"id": el["id"], "p": _pts(geom)}
                for src, dst in (("height", "h"), ("building:levels", "lv"), ("name", "n"), ("start_date", "sd")):
                    if tags.get(src):
                        b[dst] = tags[src]
                if tags.get("heritage") or tags.get("historic") in ("building", "monument", "castle", "church"):
                    b["her"] = 1
                t["buildings"].append(b)
            continue
        if typ == "way" and (tags.get("building") == "construction" or tags.get("landuse") == "construction" or tags.get("highway") == "construction"):
            c = _centroid(geom)
            if c:
                t["construction"].append({"k": "highway" if tags.get("highway") else "building", "lat": round(c[0], 5), "lon": round(c[1], 5)})
            continue
        if typ == "way" and tags.get("highway"):
            if len(geom) >= 2 and len(el.get("nodes", [])) == len(geom):
                r = {"id": el["id"], "hw": tags["highway"], "p": _pts(geom), "nodes": el["nodes"]}
                if tags.get("name"):
                    r["n"] = tags["name"]
                if tags.get("oneway") == "yes":
                    r["ow"] = 1
                t["roads"].append(r)
            continue
        if typ == "way" and tags.get("railway") in ("tram", "rail", "light_rail", "subway"):
            if len(geom) >= 2:
                t["rails"].append({"k": tags["railway"], "p": _pts(geom)})
            continue
        if typ == "way" and tags.get("natural") == "beach":
            if len(geom) >= 2:
                t["beaches"].append({"p": _pts(geom)})
            continue
        if typ == "way" and (tags.get("natural") in ("coastline", "water") or tags.get("waterway") in ("river", "canal", "riverbank")):
            if len(geom) >= 2:
                kind = "coastline" if tags.get("natural") == "coastline" else tags.get("waterway") or ("lake" if tags.get("water") == "lake" else "water")
                kind = "river" if kind == "riverbank" else kind
                t["water"].append({"k": kind, "p": _pts(geom)})
            continue
        if typ == "relation" and tags.get("natural") == "water":
            kind = "lake" if tags.get("water") == "lake" or "Байкал" in (tags.get("name") or "") else "water"
            for m in el.get("members", []):
                g = m.get("geometry") or []
                if m.get("role") == "outer" and len(g) >= 2:
                    t["water"].append({"k": kind, "p": _pts(g)})
            continue
        # точечные объекты
        if typ == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            c = _centroid(geom) if geom else ((el.get("center") or {}).get("lat"), (el.get("center") or {}).get("lon"))
            lat, lon = (c if c else (None, None))
        if lat is None or lon is None:
            continue
        if tags.get("leisure") == "playground":
            add_poi("playground", tags, lat, lon)
        elif tags.get("amenity") in POI_AMENITY:
            add_poi(tags["amenity"], tags, lat, lon)
        elif tags.get("shop") in POI_SHOP:
            add_poi(tags["shop"], tags, lat, lon)
        elif tags.get("railway") in ("station", "subway_entrance"):
            add_poi("subway", tags, lat, lon)
    return t
