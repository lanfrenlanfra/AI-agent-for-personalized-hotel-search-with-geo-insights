"""Мобильная связь: вышки в радиусе 10 км.

Источник по умолчанию — OpenStreetMap (пакет «вокруг точки», собирается при онлайн-проверке или скриптом collect_extra.py).
Если задан OPENCELLID_KEY и есть сеть, добавляем краудсорсинговую базу OpenCellID.

Честно: по вышкам в OSM нельзя обещать «связь есть» — нет мощности, направленности и рельефа. Поэтому уверенность низкая, а
формулировка «вышки рядом отмечены», а не «связь хорошая». Отсутствие вышек в OSM — тоже не «связи нет».
"""
from __future__ import annotations

from .. import extcache, osmextra
from ..collectors.base import Obs, today
from ..config import settings
from . import Result, evidence, status_from

RADIUS_M = 10_000
OCID = "https://opencellid.org/cell/getInArea"


def reduce_ocid(raw: dict) -> dict:
    cells = raw.get("cells") or []
    radio: dict[str, int] = {}
    for c in cells:
        radio[str(c.get("radio") or "?")] = radio.get(str(c.get("radio") or "?"), 0) + 1
    return {"count": len(cells), "radio": radio}


class Masts:
    name = "masts"
    title = "Вышки связи (OSM, OpenCellID)"
    features = ("mobile_coverage",)

    def run(self, rec: dict, online: bool) -> Result:
        lat, lon = rec["lat"], rec["lon"]
        near = osmextra.masts_near(lat, lon, RADIUS_M)
        srcs: list[dict] = []
        ocid = None
        if settings.opencellid_key:
            dlat, dlon = 0.05, 0.08
            params = {"key": settings.opencellid_key, "BBOX": f"{lat - dlat:.4f},{lon - dlon:.4f},{lat + dlat:.4f},{lon + dlon:.4f}", "format": "json", "limit": 50}
            ocid, m = extcache.fetch_json("opencellid", OCID, params, online=online, max_age_days=180, reduce=reduce_ocid, cache_key=f"ocid:{lat:.2f},{lon:.2f}")
            srcs.append(m)
        if near is None and not (ocid and ocid.get("count")):
            return Result(self.name, "offline",
                          "данных о вышках ещё не собирали: нужен онлайн-сбор (scripts/collect_extra.py) или запрос при проверке отеля", sources=srcs)
        day = today()
        near = near or []
        n = len(near)
        detail: dict = {"radius_m": RADIUS_M, "osm_masts": near[:10]}
        if ocid:
            detail["opencellid"] = ocid
        if n:
            first = near[0]
            who = ", ".join(sorted({x["op"] for x in near if x.get("op")})) or "оператор не указан"
            techs = sorted({t for x in near for t in x.get("tech", [])})
            txt = f"В OpenStreetMap отмечено вышек в {RADIUS_M // 1000} км: {n}; ближайшая в {first['dist_m'] / 1000:.1f} км ({who}" + (f"; {', '.join(techs).upper()}" if techs else "") + ")"
            conf = 0.4
        else:
            txt = f"В OpenStreetMap вышек в {RADIUS_M // 1000} км не отмечено (в OSM они нанесены неполно: это не значит, что связи нет)"
            conf = 0.2
        txt = txt.replace(".", ",")
        if ocid and ocid.get("count"):
            conf = min(0.6, conf + 0.2)
            txt += f"; в OpenCellID рядом {ocid['count']} базовых станций"
        ev = [{"quote": f"{x['dist_m']} м: {x.get('op') or 'без оператора'}", "date": day, "url": None, "title": "OpenStreetMap, узел-вышка"} for x in near[:3]]
        for m in srcs:
            ev.append(evidence(m, f"OpenCellID: {ocid['count']} станций" if ocid else "OpenCellID не ответил", "OpenCellID"))
        o = Obs("mobile_coverage", {"v": n, "summary": txt, "detail": detail}, "osm_masts", conf, day,
                f"Вышки OSM (man_made=mast/tower, tower:type=communication) в {RADIUS_M // 1000} км" + ("; плюс OpenCellID" if ocid else ""), ev)
        st = status_from(srcs, True) if srcs else "ok"
        return Result(self.name, st, f"вышек OSM рядом: {n}" + (f", OpenCellID: {ocid['count']}" if ocid else ""), [o], srcs)


PROBE = Masts()
