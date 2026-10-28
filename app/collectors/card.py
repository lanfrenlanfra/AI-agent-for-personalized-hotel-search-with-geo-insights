"""Сборщик по карточке Островка: удобства, правила, оценки.

Данные карточки вносит сам отель, поэтому надёжность умеренная (0,55): они часто
устаревают и расходятся с отзывами — это ловит reconcile.
"""
from __future__ import annotations

import re

from .base import Obs, today

# (характеристика, значение, шаблон по названию удобства). Отрицательные правила проверяются первыми.
AMENITY_RULES: list[tuple[str, bool, str]] = [
    ("elevator", False, r"отсутстви[ея]\s+лифт|без\s+лифт"),
    ("elevator", True, r"^\s*лифт|пассажирск\w+\s+лифт"),
    ("air_conditioning", True, r"кондиционер"),
    ("parking", False, r"парковка\s+отсутств|нет\s+парковк"),
    ("parking", True, r"парковк|гараж"),
    ("pets_allowed", False, r"животн\w*\s+не\s+допуска|не\s+допуска\w+\s+.*животн|животн\w*\s+запрещ"),
    ("pets_allowed", True, r"домашн\w+\s+животн\w*\s+(разреш|допуска\w+\b)|можно\s+с\s+животн|pet"),
    ("kitchen", True, r"кухня|кухонн|микроволнов"),
    ("bathtub", True, r"\bванн"),
    ("balcony", True, r"балкон|террас|лоджи"),
    ("workspace", True, r"письменн|рабоч\w+\s+(стол|место|зона)"),
    ("staff_english", True, r"на\s+английском|english"),
    ("luggage_storage", True, r"хранени\w+\s+багаж|камер\w+\s+хранени|багажн"),
]

ROOM_KEYS = [
    ("air_conditioning", r"condition|air_?con|ac$"),
    ("balcony", r"balcon|terrace"),
    ("bathtub", r"bathtub|bath$|jacuzzi"),
    ("kitchen", r"kitchen|microwave"),
    ("workspace", r"^desk$|workspace"),
    ("blackout_curtains", r"blackout"),
    ("soundproof_windows", r"soundproof|sound_?insul"),
]

RATING_MAP = {"cleanness": "cleanliness", "wifi": "wifi_quality", "meal": "breakfast_quality"}
RECEPTION_RE = re.compile(
    r"стойк\w*\s+регистрации[^.\d]{0,30}?(круглосуточно|(\d{1,2})[:.](\d{2})\s*[-–—]\s*(\d{1,2})[:.](\d{2}))", re.I
)


def _date(rec: dict) -> str:
    return (rec.get("fetched_at") or today())[:10]


def collect(rec: dict) -> list[Obs]:
    obs: list[Obs] = []
    day = _date(rec)
    pos: dict[str, list[str]] = {}
    neg: dict[str, list[str]] = {}
    for g in (rec.get("amenities") or {}).values():
        for a in g.get("items", []):
            name = (a.get("name") or "").strip()
            low = name.lower().replace("ё", "е")
            matched_neg = set()
            for feat, val, rx in AMENITY_RULES:
                if re.search(rx, low):
                    (pos if val else neg).setdefault(feat, []).append(name)
                    if not val:
                        matched_neg.add(feat)
                    break
    # правила: если по фиче есть и «нет», и «есть» из разных пунктов, «нет» важнее
    for feat, names in neg.items():
        obs.append(Obs(feat, {"v": False, "summary": f"В карточке: «{names[0]}»", "detail": {"amenities": names}},
                       "ostrovok_card", 0.6, day, "Список удобств в карточке отеля", [{"quote": names[0], "kind": "card"}]))
    for feat, names in pos.items():
        if feat in neg:
            continue
        obs.append(Obs(feat, {"v": True, "summary": f"В карточке: «{names[0]}»", "detail": {"amenities": names}},
                       "ostrovok_card", 0.6, day, "Список удобств в карточке отеля", [{"quote": names[0], "kind": "card"}]))

    # удобства в номерах (ключи вида desk, hairdryer, heating…)
    room_feats: dict[str, list[str]] = {}
    for g in rec.get("room_groups") or []:
        for key in g.get("amenities") or []:
            k = str(key).lower()
            for feat, rx in ROOM_KEYS:
                if re.search(rx, k):
                    room_feats.setdefault(feat, []).append(g.get("name") or "номер")
    seen = {o.feature for o in obs}
    for feat, rooms in room_feats.items():
        if feat in seen:
            continue
        rooms = list(dict.fromkeys(rooms))
        obs.append(Obs(feat, {"v": True, "summary": f"Указано в номерах: {', '.join(rooms[:3])}", "detail": {"rooms": rooms}},
                       "ostrovok_card", 0.55, day, "Удобства типов номеров в карточке", [{"quote": ", ".join(rooms[:3]), "kind": "card"}]))

    pol = rec.get("policies") or {}
    if pol.get("pets") and not any(o.feature == "pets_allowed" for o in obs):
        obs.append(Obs("pets_allowed", {"v": True, "summary": "В правилах отеля описаны условия для животных", "detail": {"policy": pol["pets"]}},
                       "ostrovok_card", 0.6, day, "Правила отеля (животные)"))
    if pol.get("parking") and not any(o.feature == "parking" for o in obs):
        obs.append(Obs("parking", {"v": True, "summary": "В правилах отеля описана парковка", "detail": {"policy": pol["parking"]}},
                       "ostrovok_card", 0.6, day, "Правила отеля (парковка)"))

    info = pol.get("extra_info") or ""
    m = RECEPTION_RE.search(info) if isinstance(info, str) else None
    if m:
        if m.group(1).lower().startswith("круглосуточно"):
            v, txt = True, "по правилам отеля стойка работает круглосуточно"
        else:
            h1, m1, h2, m2 = m.group(2), m.group(3), m.group(4), m.group(5)
            v = (int(h1) == 0 and int(h2) in (0, 24))
            txt = f"по правилам отеля стойка работает {int(h1):02d}:{m1}–{int(h2):02d}:{m2}"
        obs.append(Obs("reception_24h", {"v": v, "summary": txt[0].upper() + txt[1:], "detail": {"text": m.group(0)}},
                       "ostrovok_card", 0.6, day, "Правила размещения отеля", [{"quote": m.group(0)[:160], "kind": "card"}]))

    n = rec.get("num_reviews") or 0
    for k, feat in RATING_MAP.items():
        val = (rec.get("detailed_ratings") or {}).get(k)
        if isinstance(val, (int, float)) and val > 0:
            score = int(round(val * 10))
            obs.append(Obs(feat, {"v": score, "summary": f"Оценка гостей Островка: {val:.1f} из 10 (по {n} отзывам)", "detail": {"raw": round(val, 2), "reviews": n}},
                           "ostrovok_ratings", 0.6 if n >= 50 else 0.45, day, "Средние оценки по категориям на странице отеля"))

    y = (rec.get("facts") or {}).get("year_built") or 0
    if y and 1700 < y <= 2030:
        obs.append(Obs("historic_building", {"v": y <= 1926, "summary": f"Год постройки по карточке: {y}", "detail": {"year": y}},
                       "ostrovok_card", 0.55, day, "Год постройки в карточке отеля"))
    return obs
