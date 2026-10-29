"""Викисклад: фотографии с геометками рядом с отелем.

Это снимки случайных авторов разных лет. Мы не оцениваем по ним «красиво/некрасиво» и ничего не распознаём на фото,
а только показываем, что они есть, с автором, датой и лицензией, чтобы человек посмотрел сам.
"""
from __future__ import annotations

import re

from .. import extcache
from ..collectors.base import Obs, today
from . import Result, evidence, status_from

API = "https://commons.wikimedia.org/w/api.php"
RADIUS_M = 150
MAX_LIST = 6


def _strip(html: str | None) -> str:
    return re.sub(r"<[^>]+>", "", html or "").strip()[:80]


def reduce(raw: dict) -> list[dict]:
    pages = (raw.get("query") or {}).get("pages") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    out = []
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        md = ii.get("extmetadata") or {}
        out.append({"t": p.get("title"), "page": ii.get("descriptionurl"), "thumb": ii.get("thumburl"), "by": _strip((md.get("Artist") or {}).get("value")),
                    "lic": _strip((md.get("LicenseShortName") or {}).get("value")), "date": (ii.get("timestamp") or "")[:10]})
    return out


class Commons:
    name = "commons"
    title = "Фотографии с геометками (Викисклад)"
    features = ("street_photos",)

    def run(self, rec: dict, online: bool) -> Result:
        lat, lon = rec["lat"], rec["lon"]
        params = {"action": "query", "format": "json", "formatversion": 2, "generator": "geosearch", "ggscoord": f"{round(lat, 4)}|{round(lon, 4)}",
                  "ggsradius": RADIUS_M, "ggsnamespace": 6, "ggslimit": 30, "prop": "imageinfo", "iiprop": "url|timestamp|extmetadata", "iiurlwidth": 320}
        photos, meta = extcache.fetch_json("commons", API, params, online=online, max_age_days=180, reduce=reduce)
        if photos is None:
            return Result(self.name, status_from([meta], False), "нет данных: " + (meta.get("error") or "ответа нет и в кэше пусто"), sources=[meta])
        n = len(photos)
        day = (meta.get("fetched_at") or today())[:10]
        items = [{"title": p["t"], "url": p["page"], "thumb": p["thumb"], "author": p["by"], "license": p["lic"], "date": p["date"]} for p in photos[:MAX_LIST]]
        txt = f"В радиусе {RADIUS_M} м на Викискладе {n} фото с геометкой" if n else f"В радиусе {RADIUS_M} м на Викискладе фото с геометкой нет"
        conf = 0.5 if n else 0.25
        ev = [evidence(meta, f"{n} фото в {RADIUS_M} м", "Викисклад, geosearch")] + [
            {"quote": f"{i['title']} — {i['author'] or 'автор не указан'}, {i['license'] or 'лицензия не указана'}", "date": i["date"], "url": i["url"], "title": i["title"]} for i in items[:3] if i["url"]]
        o = Obs("street_photos", {"v": n, "summary": txt, "detail": {"radius_m": RADIUS_M, "items": items}}, "commons", conf, day,
                f"Wikimedia Commons, geosearch в {RADIUS_M} м (файлы с координатами)", ev)
        msg = f"фото рядом: {n}" + (f"; сохранённый ответ ({meta['age_days']} дн.)" if meta["status"] == "stale_cache" else "")
        return Result(self.name, status_from([meta], True), msg, [o], [meta])


PROBE = Commons()
