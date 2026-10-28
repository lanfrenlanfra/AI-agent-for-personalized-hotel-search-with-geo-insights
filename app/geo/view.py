"""Что видно из окон: закат, достопримечательности, вода.

Модель: окно на фасаде здания отеля, высота зависит от этажа; перед ним —
здания из OSM как призмы с плоской крышей. Солнце считается видимым, если ни одно
здание на пути не поднимается выше SUN_ALT_DEG над горизонтом (≈10–15 минут до
захода). Достопримечательность видима, если линия взгляда проходит выше всех зданий.
"""
from __future__ import annotations

import math
from datetime import date

from .proj import angdiff, compass_ru
from .scene import Scene, footprint_facades, synthetic_facades
from .sun import MONTHS_RU, month_reps, sun_azimuth_at_altitude

EYE = 1.7
FLOOR_H = 3.2
GROUND_OFF = 0.8
MAX_FLOOR_SCAN = 16
SUN_ALT_DEG = 2.0
WINDOW_FOV = 55.0      # окно видит ±55° от нормали фасада
SUN_RANGE = 1500.0


def window_h(floor: int) -> float:
    return GROUND_OFF + (floor - 1) * FLOOR_H + EYE


def pick_facades(scene: Scene, own) -> tuple[list[dict], bool]:
    """Один представитель на каждую из 8 сторон света. Второй результат — «реальный контур»."""
    if own is None:
        fs = synthetic_facades()
        real = False
    else:
        fs = footprint_facades(own)
        real = bool(fs)
        if not fs:
            fs = synthetic_facades()
    best: dict[int, dict] = {}
    for f in fs:
        sector = int(((f["az"] + 22.5) % 360) // 45)
        if sector not in best or f["len"] > best[sector]["len"]:
            best[sector] = f
    return list(best.values()), real


def month_span(months: list[int]) -> str:
    if not months:
        return ""
    ms = sorted(set(months))
    if len(ms) == 12:
        return "круглый год"
    # разбить на непрерывные отрезки с учётом перехода через декабрь
    groups: list[list[int]] = []
    for m in ms:
        if groups and m == groups[-1][-1] + 1:
            groups[-1].append(m)
        else:
            groups.append([m])
    if len(groups) > 1 and groups[0][0] == 1 and groups[-1][-1] == 12:
        groups[0] = groups[-1] + groups[0]
        groups.pop()
    parts = []
    for g in groups:
        if len(g) == 1:
            parts.append(MONTHS_RU[g[0] - 1])
        else:
            parts.append(f"{MONTHS_RU[g[0] - 1]} — {MONTHS_RU[g[-1] - 1]}")
    return ", ".join(parts)


def floor_word(n: int) -> str:
    return f"{n}-го этажа" if n else "первого этажа"


def coverage_of(profile: list[tuple[float, float, bool]], near: float = 700.0) -> float:
    pts = [e for d, h, e in profile if d <= near]
    if not pts:
        return 1.0  # впереди пусто — высоты не нужны
    return sum(1 for e in pts if e) / len(pts)


def sunset_analysis(scene: Scene, lat: float, floors_known: int | None = None, year: int = 2026) -> dict:
    own = scene.building_at(0.0, 0.0, 30.0)
    facades, real = pick_facades(scene, own)
    exclude = {own.id} if own else set()
    reps = month_reps(year)
    floors_scan = MAX_FLOOR_SCAN
    n_floors = floors_known or (own.levels if own and own.levels else None)

    sides: list[dict] = []
    covs: list[float] = []
    for f in facades:
        by_month: dict[int, int | None] = {}
        for d in reps:
            az = sun_azimuth_at_altitude(lat, d, SUN_ALT_DEG, setting=True)
            if az is None or abs(angdiff(az, f["az"])) > WINDOW_FOV:
                by_month[d.month] = None
                continue
            prof = scene.obstruction_profile(f["wx"], f["wy"], az, SUN_RANGE, exclude)
            covs.append(coverage_of(prof))
            min_floor = None
            for fl in range(1, floors_scan + 1):
                h = window_h(fl)
                worst = max((math.degrees(math.atan2(H - h, dist)) for dist, H, _ in prof), default=-90.0)
                if worst < SUN_ALT_DEG:
                    min_floor = fl
                    break
            by_month[d.month] = min_floor
        sides.append({"side": compass_ru(f["az"]), "az": round(f["az"]), "by_month": by_month, "len": round(f["len"])})

    # итог
    best = None
    for s in sides:
        vis = {m: fl for m, fl in s["by_month"].items() if fl is not None and (n_floors is None or fl <= n_floors)}
        if vis:
            cand = {"side": s["side"], "az": s["az"], "min_floor": min(vis.values()), "months": sorted(vis)}
            if best is None or (len(cand["months"]), -cand["min_floor"]) > (len(best["months"]), -best["min_floor"]):
                best = cand
    coverage = sum(covs) / len(covs) if covs else 1.0
    detail = {
        "sides": sides,
        "best_side": best,
        "floors_known": n_floors,
        "own_building_found": real,
        "height_coverage": round(coverage, 2),
        "sun_alt_deg": SUN_ALT_DEG,
        "window_fov": WINDOW_FOV,
    }
    if best:
        floor_txt = "с первого этажа" if best["min_floor"] == 1 else f"начиная с {best['min_floor']}-го этажа"
        summary = f"Закат виден из окон на {best['side']} {floor_txt}: {month_span(best['months'])}"
    else:
        summary = "Закат из окон не виден: перед фасадами высокая застройка" if real else "Закат из окон не виден (по ближайшей застройке)"
    return {"v": bool(best), "summary": summary, "detail": detail}


def _min_floor_to_target(scene: Scene, f: dict, tx: float, ty: float, th: float, exclude: set[str], tail: float = 45.0):
    """Минимальный этаж, с которого видна точка (tx,ty,th) из окна фасада f."""
    dx, dy = tx - f["wx"], ty - f["wy"]
    D = math.hypot(dx, dy)
    if D < 20:
        return None, D
    az = math.degrees(math.atan2(dx, dy)) % 360
    if abs(angdiff(az, f["az"])) > WINDOW_FOV + 5:
        return None, D
    prof = scene.obstruction_profile(f["wx"], f["wy"], az, max(D - tail, 5.0), exclude)
    for fl in range(1, MAX_FLOOR_SCAN + 1):
        h = window_h(fl)
        slope = (th - h) / D
        ok = True
        for d, H, _ in prof:
            if (H - h) / max(d, 1.0) > slope + 0.002:
                ok = False
                break
        if ok:
            return fl, D
    return None, D


def landmark_analysis(scene: Scene, landmarks: list[dict], floors_known: int | None = None, max_dist: float = 6000.0) -> dict:
    own = scene.building_at(0.0, 0.0, 30.0)
    facades, real = pick_facades(scene, own)
    exclude = {own.id} if own else set()
    n_floors = floors_known or (own.levels if own and own.levels else None)
    found = []
    for lm in landmarks:
        tx, ty = scene.frame.xy(lm["lat"], lm["lon"])
        D0 = math.hypot(tx, ty)
        if D0 > min(max_dist, lm.get("max_m", max_dist)):
            continue
        best = None
        for f in facades:
            fl, D = _min_floor_to_target(scene, f, tx, ty, lm["height"], exclude)
            if fl is not None and (best is None or fl < best["min_floor"]):
                best = {"min_floor": fl, "side": compass_ru(f["az"]), "az": round(f["az"])}
        if best and (n_floors is None or best["min_floor"] <= n_floors):
            found.append({"key": lm["key"], "name": lm["name"], "distance_m": round(D0), **best})
    found.sort(key=lambda r: (r["min_floor"], r["distance_m"]))
    far = any(r["distance_m"] > 1200 for r in found)
    detail = {"landmarks": found, "checked": len(landmarks), "far_obstacles_unchecked": True, "own_building_found": real}
    if found:
        parts = [f'{r["name"]} (с {r["min_floor"]}-го этажа, окна на {r["side"]}, {r["distance_m"]} м)' for r in found[:3]]
        summary = "Видны: " + "; ".join(parts)
    else:
        summary = "Известные достопримечательности из окон не просматриваются"
    return {"v": bool(found), "summary": summary, "detail": detail, "far": far}


def _densify(pts: list[tuple[float, float]], step: float = 30.0) -> list[tuple[float, float]]:
    out = []
    for i in range(len(pts) - 1):
        ax, ay = pts[i]
        bx, by = pts[i + 1]
        L = math.hypot(bx - ax, by - ay)
        n = max(1, int(L / step))
        for k in range(n):
            t = k / n
            out.append((ax + (bx - ax) * t, ay + (by - ay) * t))
    if pts:
        out.append(pts[-1])
    return out


def water_view_analysis(scene: Scene, floors_known: int | None = None, reach: float = 350.0) -> dict:
    own = scene.building_at(0.0, 0.0, 30.0)
    facades, real = pick_facades(scene, own)
    exclude = {own.id} if own else set()
    n_floors = floors_known or (own.levels if own and own.levels else None)
    samples: list[tuple[float, float, str]] = []
    for w in scene.water:
        big = w["k"] in ("lake", "coastline")      # озеро и залив видны издалека, реку и канал — только вблизи
        lim = max(reach, 3500.0) if big else reach
        for x, y in _densify(w["pts"], 100.0 if big else 30.0):
            if math.hypot(x, y) <= lim:
                samples.append((x, y, w["k"]))
    if len(samples) > 300:
        samples.sort(key=lambda p: math.hypot(p[0], p[1]))
        samples = samples[:300]
    best = None
    for f in facades:
        for sx, sy, kind in samples:
            if abs(angdiff(math.degrees(math.atan2(sx - f["wx"], sy - f["wy"])) % 360, f["az"])) > 60:
                continue
            fl, D = _min_floor_to_target(scene, f, sx, sy, 0.0, exclude, tail=12.0)
            if fl is not None and (best is None or fl < best["min_floor"] or (fl == best["min_floor"] and D < best["distance_m"])):
                best = {"min_floor": fl, "side": compass_ru(f["az"]), "az": round(f["az"]), "distance_m": round(D), "kind": kind}
    ok = bool(best) and (n_floors is None or best["min_floor"] <= n_floors)
    kinds = {"river": "реку", "canal": "канал", "water": "воду", "lake": "озеро", "coastline": "залив"}
    if ok:
        fl_txt = "с первого этажа" if best["min_floor"] == 1 else f'с {best["min_floor"]}-го этажа'
        summary = f'Видна вода ({kinds.get(best["kind"], "вода")}, {best["distance_m"]} м) из окон на {best["side"]} {fl_txt}'
    else:
        summary = "Водоём из окон не просматривается" if samples else "Рядом нет рек, каналов, озера и залива"
    return {"v": ok, "summary": summary, "detail": {"best": best, "water_points": len(samples), "own_building_found": real}}
