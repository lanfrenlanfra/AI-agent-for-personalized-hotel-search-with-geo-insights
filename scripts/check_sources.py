#!/usr/bin/env python3
"""Проверка живых источников: отвечают ли они так, как ждёт код. Нужен интернет; ничего не пишет в data/.

Зачем: адреса и поля внешних API (Terrarium, Overpass, Open-Meteo, Википедия, Викисклад) в разработке были проверены только
по документации и записанным ответам. Этот скрипт за минуту показывает, что из этого реально работает у вас, и печатает,
что именно сломалось. Код выхода 0 — всё прошло.

  python scripts/check_sources.py
  python scripts/check_sources.py --lat 53.0 --lon 108.0     # другая точка (по умолчанию Листвянка)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import dem, extcache, osmextra  # noqa: E402
from app.probes import air, climate, commons, wiki  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str):
    def deco(fn):
        def run(*a, **k):
            try:
                ok, msg = fn(*a, **k)
            except Exception as e:  # noqa: BLE001
                ok, msg = False, f"{type(e).__name__}: {str(e)[:200]}"
            RESULTS.append((name, ok, msg))
            print(("  ок   " if ok else "  СБОЙ ") + f"{name}: {msg}", flush=True)
        return run
    return deco


@check("Terrarium (плитка рельефа, AWS)")
def c_terrarium(lat, lon):
    x, y = dem.world_px(lat, lon, 11)
    arr = dem.fetch_tile(11, int(x // 256), int(y // 256))
    z = float(arr[int(y % 256), int(x % 256)])
    return arr.shape == (256, 256) and -500 < z < 6000, f"плитка 256×256, высота в точке {z:.0f} м (Байкал ≈ 456 м над уровнем моря)"


@check("Terrarium: глубины озера (ожидаем отрицательные значения или ≈456)")
def c_bathy(lat, lon):
    # центр южной котловины Байкала: если модель включает батиметрию, будут большие минусы — код это учитывает (медиана вокруг отеля, обрезка)
    x, y = dem.world_px(52.9, 107.6, 9)
    arr = dem.fetch_tile(9, int(x // 256), int(y // 256))
    z = float(arr[int(y % 256), int(x % 256)])
    return True, f"в центре озера модель даёт {z:.0f} м ({'батиметрия есть' if z < 400 else 'озеро как плоская поверхность'}); оба случая обработаны"


@check("Overpass (OpenStreetMap): три запроса от лёгкого к тяжёлому, по каждому зеркалу")
def c_overpass(lat, lon):
    import time

    import requests
    from app import net
    tests = [("пинг", "[out:json][timeout:20];node(1);out;"),
             ("вышки 25 км", f'[out:json][timeout:30];node["man_made"~"^(mast|tower)$"]["tower:type"="communication"](around:{osmextra.MAST_RADIUS_M},{lat},{lon});out tags qt;'),
             ("вершины 70 км", f'[out:json][timeout:40];node["natural"="peak"]["name"](around:{osmextra.PEAK_RADIUS_M},{lat},{lon});out tags qt;')]
    ok_mirrors = 0
    for url in net.OVERPASS:
        host = url.split("/")[2]
        line = []
        alive = True
        for name, q in tests:
            if not alive:
                break
            t0 = time.time()
            try:
                r = requests.post(url, data={"data": q}, timeout=45, headers=extcache.HEADERS)
                dt = time.time() - t0
                if r.status_code != 200:
                    line.append(f"{name}: HTTP {r.status_code} за {dt:.0f} с")
                    alive = False
                else:
                    n = len(r.json().get("elements", []))
                    line.append(f"{name}: ок, {n} объектов за {dt:.0f} с")
            except Exception as e:  # noqa: BLE001
                line.append(f"{name}: {type(e).__name__} за {time.time() - t0:.0f} с")
                alive = False
        full = alive
        ok_mirrors += full
        print(f"       · {host}: " + "; ".join(line), flush=True)
    return ok_mirrors > 0, f"зеркал, прошедших все три запроса: {ok_mirrors} из {len(net.OVERPASS)}"


@check("Open-Meteo Historical Weather (температура, солнечные часы)")
def c_meteo(lat, lon):
    raw = extcache.http_json(climate.URL, {"latitude": round(lat, 2), "longitude": round(lon, 2), "start_date": "2022-12-01", "end_date": "2023-02-28",
                                           "daily": "temperature_2m_mean,sunshine_duration", "timezone": "UTC"}, 20)
    d = raw.get("daily") or {}
    t, s = d.get("temperature_2m_mean") or [], d.get("sunshine_duration") or []
    nt, ns = sum(v is not None for v in t), sum(v is not None for v in s)
    return nt > 60 and ns > 60, f"температур {nt}, значений sunshine_duration {ns} из {len(t)} дней; высота ячейки {raw.get('elevation')} м"


@check("Open-Meteo Air Quality (PM2.5, окно дат)")
def c_air(lat, lon):
    from datetime import date, timedelta
    end = date.today() - timedelta(days=2)
    raw = extcache.http_json(air.URL, {"latitude": round(lat, 2), "longitude": round(lon, 2), "hourly": "pm2_5", "start_date": (end - timedelta(days=30)).isoformat(),
                                       "end_date": end.isoformat(), "timezone": "UTC"}, 20)
    v = [x for x in (raw.get("hourly") or {}).get("pm2_5", []) if x is not None]
    return len(v) > 300, f"получено часовых значений PM2.5: {len(v)}"


@check("Википедия (geosearch + extracts + pageprops)")
def c_wiki(lat, lon):
    params = {"action": "query", "format": "json", "formatversion": 2, "generator": "geosearch", "ggscoord": f"{round(lat, 4)}|{round(lon, 4)}", "ggsradius": 5000,
              "ggslimit": 10, "prop": "coordinates|pageprops|extracts", "colimit": 10, "ppprop": "wikibase_item", "exintro": 1, "explaintext": 1, "exchars": 100, "exlimit": 10}
    pages = wiki.reduce_geo(extcache.http_json(wiki.API, params, 20))
    return len(pages) > 0, f"статей с координатами в 5 км: {len(pages)}" + (f"; например «{pages[0]['t']}»" if pages else "")


@check("Викиданные (wbgetentities)")
def c_wd(lat, lon):
    raw = extcache.http_json(wiki.WD, {"action": "wbgetentities", "ids": "Q5", "props": "claims|labels", "languages": "ru", "format": "json"}, 20)
    return "Q5" in (raw.get("entities") or {}), "структура ответа распознана"


@check("Викисклад (geosearch по файлам)")
def c_commons(lat, lon):
    params = {"action": "query", "format": "json", "formatversion": 2, "generator": "geosearch", "ggscoord": f"{round(lat, 4)}|{round(lon, 4)}", "ggsradius": 1000,
              "ggsnamespace": 6, "ggslimit": 10, "prop": "imageinfo", "iiprop": "url|timestamp|extmetadata", "iiurlwidth": 320}
    ph = commons.reduce(extcache.http_json(commons.API, params, 20))
    return len(ph) > 0, f"файлов с геометкой в 1 км: {len(ph)}" + (f"; лицензия первого: {ph[0]['lic'] or 'не указана'}" if ph else "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float, default=51.867)
    ap.add_argument("--lon", type=float, default=104.83)
    a = ap.parse_args()
    print(f"точка: {a.lat}, {a.lon}")
    print("(Overpass иногда отвечает медленно: до 45 секунд на запрос)", flush=True)
    for f in (c_terrarium, c_bathy, c_overpass, c_meteo, c_air, c_wiki, c_wd, c_commons):
        f(a.lat, a.lon)
    bad = [r for r in RESULTS if not r[1]]
    print(f"\nитог: {len(RESULTS) - len(bad)} из {len(RESULTS)} прошли")
    if bad:
        print("Сбойные источники код обходит статусом «источник не ответил», но значения по ним не появятся. Пришлите вывод этого скрипта — подправим адрес или поле.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
