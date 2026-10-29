"""Википедия и Викиданные: достопримечательности рядом и история самого здания.

Ничего не угадываем. Статья считается «зданием отеля», только если у неё есть координаты в 200 м от отеля И название
пересекается с названием отеля по значимым словам. Если такой статьи нет, мы не пишем «истории нет» — мы пишем «не нашли»
статусом пробы, и характеристика остаётся без значения.
"""
from __future__ import annotations

import math
import re
from urllib.parse import quote

from .. import extcache
from ..collectors.base import Obs, today
from . import Result, evidence, status_from

API = "https://ru.wikipedia.org/w/api.php"
WD = "https://www.wikidata.org/w/api.php"
RADIUS_M = 1500
OWN_RADIUS_M = 200
GENERIC = {"отель", "гостиница", "hotel", "хостел", "hostel", "гостевой", "дом", "база", "отдыха", "апартаменты", "апарт", "apartments", "мини",
           "санаторий", "курорт", "resort", "усадьба", "парк", "комплекс", "the", "and", "для", "при"}


def haversine(la1, lo1, la2, lo2) -> float:
    p = math.pi / 180
    a = math.sin((la2 - la1) * p / 2) ** 2 + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    return 12742000 * math.asin(math.sqrt(a))


def tokens(s: str) -> set[str]:
    s = (s or "").lower().replace("ё", "е")
    return {t for t in re.findall(r"[a-zа-я0-9]{3,}", s) if t not in GENERIC}


def reduce_geo(raw: dict) -> list[dict]:
    pages = (raw.get("query") or {}).get("pages") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    out = []
    for p in pages:
        c = (p.get("coordinates") or [{}])[0]
        if c.get("lat") is None:
            continue
        out.append({"t": p.get("title"), "lat": c["lat"], "lon": c["lon"], "q": (p.get("pageprops") or {}).get("wikibase_item"), "x": (p.get("extract") or "")[:240]})
    return out


def reduce_wd(raw: dict) -> dict:
    ent = next(iter((raw.get("entities") or {}).values()), {})
    claims = ent.get("claims") or {}
    year = None
    for c in claims.get("P571", []):
        t = (((c.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}).get("time")
        if t:
            m = re.match(r"[+-]?(\d{1,4})", t)
            year = int(m.group(1)) if m else None
            break
    return {"inception": year, "heritage": bool(claims.get("P1435")), "label": ((ent.get("labels") or {}).get("ru") or {}).get("value")}


def page_url(title: str) -> str:
    return "https://ru.wikipedia.org/wiki/" + quote(title.replace(" ", "_"))


class Wiki:
    name = "wiki"
    title = "Википедия и Викиданные"
    features = ("sights_nearby", "building_history")

    def run(self, rec: dict, online: bool) -> Result:
        lat, lon = rec["lat"], rec["lon"]
        params = {"action": "query", "format": "json", "formatversion": 2, "generator": "geosearch", "ggscoord": f"{round(lat, 4)}|{round(lon, 4)}",
                  "ggsradius": RADIUS_M, "ggslimit": 20, "prop": "coordinates|pageprops|extracts", "colimit": 20, "ppprop": "wikibase_item",
                  "exintro": 1, "explaintext": 1, "exchars": 240, "exlimit": 20}
        pages, meta = extcache.fetch_json("wikipedia", API, params, online=online, max_age_days=180, reduce=reduce_geo)
        if pages is None:
            return Result(self.name, status_from([meta], False), "нет данных: " + (meta.get("error") or "ответа нет и в кэше пусто"), sources=[meta])
        day = (meta.get("fetched_at") or today())[:10]
        mine = tokens(rec.get("name", ""))
        own, others = None, []
        for p in pages:
            p["dist"] = round(haversine(lat, lon, p["lat"], p["lon"]))
            pt = tokens(p["t"])
            if p["dist"] <= OWN_RADIUS_M and pt and mine and len(pt & mine) / len(pt) >= 0.5:
                if own is None or p["dist"] < own["dist"]:
                    own = p
        for p in sorted(pages, key=lambda x: x["dist"]):
            if p is not own:
                others.append(p)
        obs: list[Obs] = []
        listing = [{"title": p["t"], "dist_m": p["dist"], "url": page_url(p["t"]), "extract": p["x"]} for p in others[:8]]
        n = len(others)
        if n:
            near = ", ".join(f"«{p['title']}» ({p['dist_m']} м)" for p in listing[:3])
            txt = f"В радиусе {RADIUS_M / 1000:g} км статей Википедии с координатами: {n}; ближайшие — {near}".replace(".", ",")
            conf = 0.65
        else:
            txt = f"В радиусе {RADIUS_M / 1000:g} км статей Википедии с координатами не нашли (в малых посёлках их мало — это не значит, что смотреть нечего)".replace(".", ",")
            conf = 0.3
        obs.append(Obs("sights_nearby", {"v": n, "summary": txt, "detail": {"radius_m": RADIUS_M, "items": listing}}, "wikipedia", conf, day,
                       f"Википедия (ru), geosearch в {RADIUS_M} м от отеля; в списке только статьи с координатами",
                       [evidence(meta, f"{n} статей в {RADIUS_M} м", "Википедия, geosearch")] + [
                           {"quote": f"«{p['title']}», {p['dist_m']} м", "date": day, "url": p["url"], "title": p["title"]} for p in listing[:3]]))
        srcs = [meta]
        if own:
            wd = None
            if own.get("q"):
                wdp = {"action": "wbgetentities", "ids": own["q"], "props": "claims|labels", "languages": "ru", "format": "json"}
                wd, wmeta = extcache.fetch_json("wikipedia", WD, wdp, online=online, max_age_days=365, reduce=reduce_wd)
                srcs.append(wmeta)
            bits = []
            if wd and wd.get("inception"):
                bits.append(f"построено в {wd['inception']} г.")
            if wd and wd.get("heritage"):
                bits.append("есть запись об охранном статусе (Викиданные, P1435)")
            tail = ("; " + "; ".join(bits)) if bits else ""
            txt = f"У здания есть статья в Википедии «{own['t']}» ({own['dist']} м от точки отеля){tail}"
            ev = [{"quote": f"«{own['t']}»: {own['x']}" if own["x"] else f"«{own['t']}»", "date": day, "url": page_url(own["t"]), "title": own["t"]}]
            if own.get("q"):
                ev.append({"quote": f"Викиданные {own['q']}", "date": day, "url": f"https://www.wikidata.org/wiki/{own['q']}", "title": "Викиданные"})
            obs.append(Obs("building_history", {"v": own["t"], "summary": txt, "detail": {"title": own["t"], "qid": own.get("q"), "dist_m": own["dist"], **(wd or {})}},
                           "wikipedia", 0.7 if wd and wd.get("inception") else 0.6, day,
                           f"Статья найдена в {OWN_RADIUS_M} м от отеля и совпала с названием отеля по значимым словам; год — из Викиданных (P571)", ev))
        st = status_from(srcs, True)
        msg = f"статей рядом: {n}" + ("; найдена статья о самом здании" if own else "; статьи о самом здании не нашли")
        if meta["status"] == "stale_cache":
            msg += f"; сохранённый ответ ({meta['age_days']} дн.)"
        return Result(self.name, st, msg, obs, srcs)


PROBE = Wiki()
