"""Окружение отеля по OSM: ночные места, дети, парковки, стройки, история."""
from __future__ import annotations

import math
import re

from .scene import Scene

NIGHT_KINDS = {"pharmacy": "аптека", "convenience": "магазин", "supermarket": "магазин", "cafe": "кафе",
               "restaurant": "ресторан", "fast_food": "фастфуд", "bar": "бар"}


def is_24h(oh: str | None) -> bool:
    if not oh:
        return False
    s = oh.strip().lower().replace(" ", "")
    if s in ("24/7", "24h", "mo-su00:00-24:00", "mo-su00:00-23:59"):
        return True
    return bool(re.fullmatch(r"(mo-su)?00:00-24:00", s))


def night_services(scene: Scene, radius: float = 500.0) -> dict:
    found = []
    for p in scene.pois:
        if p["k"] in NIGHT_KINDS and is_24h(p.get("oh")):
            d = math.hypot(p["x"], p["y"])
            if d <= radius:
                found.append({"kind": NIGHT_KINDS[p["k"]], "name": p.get("name"), "dist": round(d)})
    found.sort(key=lambda r: r["dist"])
    pharm = [f for f in found if f["kind"] == "аптека"]
    shops = [f for f in found if f["kind"] == "магазин"]
    bits = []
    if pharm:
        bits.append(f'круглосуточная аптека в {pharm[0]["dist"]} м')
    if shops:
        bits.append(f'круглосуточный магазин в {shops[0]["dist"]} м')
    if not found:
        summary = "Круглосуточных аптек, магазинов и кафе в 500 м не найдено"
    else:
        summary = "Ночью работают: " + (", ".join(bits) if bits else f"{len(found)} мест рядом")
    return {"v": len(found), "summary": summary, "detail": {"places": found[:8], "radius_m": radius}}


def family(scene: Scene, quiet_score: int | None) -> dict:
    pg = [p for p in scene.pois if p["k"] == "playground" and math.hypot(p["x"], p["y"]) <= 400]
    ped = [r for r in scene.roads if r["hw"] == "pedestrian" and any(math.hypot(x, y) <= 300 for x, y in r["pts"])]
    clinics = [p for p in scene.pois if p["k"] in ("hospital", "clinic") and "детск" in (p.get("name") or "").lower()]
    score = 20.0
    score += 40.0 * min(len(pg) / 2.0, 1.0)
    score += 15.0 if ped else 0.0
    if quiet_score is not None:
        score += 0.25 * quiet_score
    score = int(round(min(100.0, score)))
    bits = []
    bits.append(f"детских площадок в 400 м: {len(pg)}" if pg else "детских площадок в 400 м нет")
    if ped:
        bits.append("рядом пешеходные улицы")
    if clinics:
        c = min(clinics, key=lambda p: math.hypot(p["x"], p["y"]))
        bits.append(f'детская клиника «{c.get("name")}» в {round(math.hypot(c["x"], c["y"]))} м')
    return {"v": score, "summary": "; ".join(bits).capitalize(), "detail": {"playgrounds": len(pg), "pedestrian_streets": len(ped), "children_clinic": bool(clinics)}}


def pedestrian_zone(scene: Scene) -> dict:
    near = []
    for r in scene.roads:
        d = min((math.hypot(x, y) for x, y in r["pts"]), default=1e9)
        if d <= 45:
            near.append((d, r["hw"]))
    if not near:
        return {"v": False, "summary": "Данных о дорогах у входа нет", "detail": {}, "unknown": True}
    vehicle = [d for d, hw in near if hw in ("residential", "service", "unclassified", "tertiary", "secondary", "primary", "trunk")]
    ped = [d for d, hw in near if hw in ("pedestrian", "footway", "living_street")]
    z = bool(ped) and not vehicle
    return {"v": z, "summary": "Вход в пешеходной зоне — подъехать на машине нельзя" if z else "К входу можно подъехать на машине",
            "detail": {"vehicle_roads": len(vehicle), "pedestrian": len(ped)}}


def ev_charging(scene: Scene, radius: float = 500.0) -> dict:
    ch = sorted(round(math.hypot(p["x"], p["y"])) for p in scene.pois if p["k"] == "charging_station" and math.hypot(p["x"], p["y"]) <= radius)
    return {"v": bool(ch), "summary": f"Зарядная станция в {ch[0]} м" if ch else "Зарядок для электромобилей в 500 м нет", "detail": {"count": len(ch)}}


def parking_osm(scene: Scene, radius: float = 200.0) -> dict:
    lots = sorted(round(math.hypot(p["x"], p["y"])) for p in scene.pois
                  if p["k"] == "parking" and (p.get("tags") or {}).get("access") not in ("private", "customers") and math.hypot(p["x"], p["y"]) <= radius)
    return {"v": bool(lots), "summary": f"Парковка в {lots[0]} м" if lots else "Отдельных парковок в 200 м не найдено", "detail": {"count": len(lots)}}


def construction(scene: Scene, radius: float = 300.0) -> dict:
    items = sorted(round(math.hypot(c["x"], c["y"])) for c in scene.construction if math.hypot(c["x"], c["y"]) <= radius)
    return {"v": bool(items), "summary": f"В {items[0]} м отмечена стройка (OpenStreetMap)" if items else "Строек в 300 м не отмечено",
            "detail": {"count": len(items), "nearest_m": items[0] if items else None}}


def historic(scene: Scene) -> dict | None:
    own = scene.building_at(0.0, 0.0, 30.0)
    if own is None:
        return None
    year = None
    if own.start_date:
        m = re.search(r"(1[0-9]{3}|20[0-9]{2})", str(own.start_date))
        year = int(m.group(1)) if m else None
    if own.heritage or (year is not None and year <= 1926):
        txt = f"Здание {year} года" if year else "Здание отмечено как объект наследия"
        if own.heritage and year:
            txt += ", объект наследия"
        return {"v": True, "summary": txt, "detail": {"year": year, "heritage": own.heritage}}
    if year is not None:
        return {"v": False, "summary": f"Здание {year} года", "detail": {"year": year, "heritage": False}}
    return None
