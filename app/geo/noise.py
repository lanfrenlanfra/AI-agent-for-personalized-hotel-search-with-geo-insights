"""Упрощённая модель шума для каждой стороны здания.

Источники: дороги по классу, трамвай, железная дорога, бары/пабы/ночные клубы,
стройки. Затухание в свободном поле −20·lg(d); стороны, отвёрнутые от источника,
получают экранирование зданием. Это эвристика, а не картографическая карта шума —
поэтому уверенность по умолчанию умеренная, а результат объясняет, что именно шумит.
Для Европы рядом можно подключить открытые карты шума (END), здесь их нет.
"""
from __future__ import annotations

import math

from .proj import angdiff, bearing_xy, compass_ru, point_seg_dist
from .scene import Scene

# уровень, дБ(A), на расстоянии 10 м
ROAD_L10 = {
    "motorway": 80, "trunk": 77, "primary": 72, "secondary": 67, "tertiary": 61,
    "unclassified": 55, "residential": 52, "living_street": 46, "service": 46,
}
RAIL_L10 = {"tram": 70, "rail": 80, "light_rail": 72, "subway": 60}
POI_L = {"nightclub": 74, "bar": 66, "pub": 65}
SHIELD_DB = 12.0
NAMES = {
    "motorway": "магистраль", "trunk": "магистраль", "primary": "крупная улица", "secondary": "оживлённая улица",
    "tertiary": "улица", "residential": "жилая улица", "unclassified": "улица", "service": "проезд", "living_street": "двор",
    "tram": "трамвай", "rail": "железная дорога", "light_rail": "лёгкое метро", "subway": "метро наземное",
    "nightclub": "ночной клуб", "bar": "бар", "pub": "паб",
}


def _db_sum(levels: list[float]) -> float:
    if not levels:
        return 30.0
    return 10 * math.log10(sum(10 ** (l / 10) for l in levels))


def score_from_db(db: float) -> int:
    return int(round(max(0.0, min(100.0, 100.0 * (72.0 - db) / (72.0 - 40.0)))))


def _nearest_on_poly(px, py, pts):
    best = (1e18, 0.0, 0.0)
    for i in range(len(pts) - 1):
        d = point_seg_dist(px, py, *pts[i], *pts[i + 1])
        if d[0] < best[0]:
            best = d
    return best


def facade_noise(scene: Scene, f: dict, reach: float = 250.0) -> dict:
    px, py = f["wx"], f["wy"]
    contrib: list[tuple[float, str, str, float]] = []  # (dB, kind, name, dist)

    def add(L10: float, d: float, cx: float, cy: float, kind: str, name: str | None, night_bonus: float = 0.0):
        d = max(d, 8.0)
        L = L10 - 20.0 * math.log10(d / 10.0) + night_bonus
        brg = bearing_xy(cx - px, cy - py)
        if abs(angdiff(brg, f["az"])) > 100.0:
            L -= SHIELD_DB
        if L > 25.0:
            contrib.append((L, kind, name or "", d))

    for r in scene.roads:
        base = ROAD_L10.get(r["hw"])
        if base is None or len(r["pts"]) < 2:
            continue
        d, cx, cy = _nearest_on_poly(px, py, r["pts"])
        if d <= reach:
            add(base, d, cx, cy, r["hw"], r["name"])
    for r in scene.rails:
        base = RAIL_L10.get(r["k"])
        if base is None or len(r["pts"]) < 2:
            continue
        d, cx, cy = _nearest_on_poly(px, py, r["pts"])
        if d <= reach * 1.6:
            add(base, d, cx, cy, r["k"], None)
    for p in scene.pois:
        base = POI_L.get(p["k"])
        if base is None:
            continue
        d = math.hypot(p["x"] - px, p["y"] - py)
        if d <= 120:
            add(base, d, p["x"], p["y"], p["k"], p["name"], night_bonus=2.0)
    for c in scene.construction:
        d = math.hypot(c["x"] - px, c["y"] - py)
        if d <= 200:
            add(75, d, c["x"], c["y"], "construction", None)

    db = _db_sum([c[0] for c in contrib])
    contrib.sort(reverse=True)
    top = [{"kind": k, "label": NAMES.get(k, k), "name": n, "dist": round(d), "db": round(L)} for L, k, n, d in contrib[:3]]
    return {"side": compass_ru(f["az"]), "az": round(f["az"]), "db": round(db, 1), "score": score_from_db(db), "top": top}


def noise_analysis(scene: Scene, facades: list[dict]) -> dict:
    per = [facade_noise(scene, f) for f in facades]
    per.sort(key=lambda r: -r["score"])
    scores = sorted(r["score"] for r in per)
    median = scores[len(scores) // 2] if scores else 50
    best, worst = per[0], per[-1]
    sources = []
    for r in per:
        for t in r["top"]:
            sources.append(t)
    main = sorted(sources, key=lambda t: -t["db"])[:1]
    if main:
        m = main[0]
        who = f'{m["label"]} {m["name"]}'.strip() if m["name"] else m["label"]
        loud = f"Главный шум — {who} в {m['dist']} м."
    else:
        loud = "Заметных источников шума рядом нет."
    quiet_txt = f'Тише всего окна на {best["side"]} ({best["score"]}/100)'
    if worst["score"] < best["score"] - 8:
        quiet_txt += f', громче на {worst["side"]} ({worst["score"]}/100)'
    return {
        "median": median,
        "best": best,
        "worst": worst,
        "facades": per,
        "summary": f"{quiet_txt}. {loud}",
    }
