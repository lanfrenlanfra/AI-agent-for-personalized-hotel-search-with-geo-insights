"""Вид из окна: силуэт застройки на горизонте и путь солнца — для визуализации в карточке отеля."""
from __future__ import annotations

import math
from datetime import date

from . import db
from .collectors import geo as geo_collector
from .geo import sun, view
from .geo.proj import angdiff, compass_ru

_cache: dict[tuple, object] = {}


def scene_for(hotel_id: str):
    h = db.row("SELECT lat, lon FROM hotels WHERE id=?", (hotel_id,))
    if not h:
        return None
    f = geo_collector.overlay_file(hotel_id)
    key = (hotel_id, f.stat().st_mtime_ns if f.exists() else 0, geo_collector.SCENE_RADIUS)
    if key not in _cache:
        if len(_cache) > 8:
            _cache.clear()
        sc, day, _ = geo_collector.build_scene(h["lat"], h["lon"], hotel_id)
        _cache[key] = (sc, day)
    return _cache[key]


def map_buildings(hotel_id: str, radius: float = 230.0) -> dict:
    got = scene_for(hotel_id)
    if not got or not got[0]:
        return {"available": False, "buildings": []}
    sc, day = got
    own = sc.building_at(0.0, 0.0, 30.0)
    out = []
    for b in sc.buildings:
        cx = (b.bbox[0] + b.bbox[2]) / 2
        cy = (b.bbox[1] + b.bbox[3]) / 2
        if math.hypot(cx, cy) > radius:
            continue
        out.append({"p": [[round(v, 6) for v in sc.frame.ll(x, y)] for x, y in b.ring], "h": round(b.height, 1), "explicit": b.explicit,
                    "own": own is b, "overlay": str(b.id).startswith("overlay")})
    return {"available": True, "osm_date": day, "buildings": out}


def horizon(hotel_id: str, side_az: float, floor: int, month: int, span: float = 62.0) -> dict:
    got = scene_for(hotel_id)
    h = db.row("SELECT lat, lon FROM hotels WHERE id=?", (hotel_id,))
    if not got or not got[0] or not h:
        return {"available": False}
    sc, day = got
    own = sc.building_at(0.0, 0.0, 30.0)
    facades, real = view.pick_facades(sc, own)
    f = min(facades, key=lambda x: abs(angdiff(x["az"], side_az)))
    exclude = {own.id} if own else set()
    wh = view.window_h(floor)
    prof = []
    az0 = f["az"] - span
    tv = sc.terrain
    for i in range(int(2 * span / 2) + 1):
        az = (az0 + 2 * i) % 360
        obs = sc.obstruction_profile(f["wx"], f["wy"], az, 1500.0, exclude)
        el_b = max((math.degrees(math.atan2(H - wh, d)) for d, H, _ in obs), default=0.0)
        row = {"az": round(az, 1), "rel": round(angdiff(az, f["az"]), 1)}
        if tv is not None:
            el_t = tv.horizon_angle(az, wh)
            row["terrain"] = round(max(el_t, -2.0), 2)       # силуэт одного рельефа — для отдельной заливки на рисунке
            el_b = max(el_b, el_t)
        row["el"] = round(max(el_b, -2.0), 2)
        prof.append(row)
    d = date(2026, month, 15)
    path = []
    for alt in range(0, 26, 2):
        a = sun.sun_azimuth_at_altitude(h["lat"], d, float(alt), setting=True)
        if a is not None:
            path.append({"alt": alt, "az": round(a, 1), "rel": round(angdiff(a, f["az"]), 1)})
    sunset = sun.sun_azimuth_at_altitude(h["lat"], d, view.SUN_ALT_DEG, setting=True)
    visible = None
    if sunset is not None and abs(angdiff(sunset, f["az"])) <= view.WINDOW_FOV:
        obs = sc.obstruction_profile(f["wx"], f["wy"], sunset, view.SUN_RANGE, exclude)
        worst = max((math.degrees(math.atan2(H - wh, dd)) for dd, H, _ in obs), default=-90.0)
        if tv is not None:
            worst = max(worst, tv.horizon_angle(sunset, wh))
        visible = worst < view.SUN_ALT_DEG
    return {"available": True, "side": compass_ru(f["az"]), "facade_az": round(f["az"]), "floor": floor, "month": month, "fov": view.WINDOW_FOV,
            "profile": prof, "sun_path": path, "sunset_az": round(sunset, 1) if sunset is not None else None,
            "sunset_rel": round(angdiff(sunset, f["az"]), 1) if sunset is not None else None, "sunset_visible": visible,
            "real_footprint": real, "osm_date": day, "terrain_used": tv is not None, "terrain": sc.terrain_info}
