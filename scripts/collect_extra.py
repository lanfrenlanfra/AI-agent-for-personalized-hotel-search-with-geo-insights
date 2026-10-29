#!/usr/bin/env python3
"""Собрать дополнительные данные для «скрытых» характеристик → data/raw/ (нужен интернет).

  dem     плитки рельефа Terrarium (AWS, без ключа) → data/raw/dem/
  osm     лес и остановки (тайлы), вышки связи и названные вершины (пакет вокруг точки) → data/raw/osm_extra/
  probes  ответы Open-Meteo, Википедии, Викисклада (и OpenCellID, если задан OPENCELLID_KEY) → data/raw/ext/

Результат можно закоммитить: с ним проект работает без интернета (docker compose up). Повторный запуск берёт из кэша то, что
уже есть. Ошибки по отдельным отелям не останавливают сбор.

  python scripts/collect_extra.py                 # всё
  python scripts/collect_extra.py --only dem      # только рельеф
  python scripts/collect_extra.py --limit 5       # пять отелей — для пробы
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import dem, ingest, net  # noqa: E402
from app.collectors.geo import SCENE_RADIUS  # noqa: E402
from app.config import settings  # noqa: E402
from app.probes import all_probes  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["dem", "osm", "probes"], action="append", help="что собирать (можно несколько раз); по умолчанию всё")
    ap.add_argument("--limit", type=int, default=0, help="только первые N отелей")
    ap.add_argument("--delay", type=float, default=1.0, help="пауза между отелями, с (вежливость к открытым API)")
    ap.add_argument("--max-age-days", type=int, default=None, help="перекачать пакеты OSM старше N дней")
    a = ap.parse_args()
    what = set(a.only or ["dem", "osm", "probes"])
    settings.dem_fetch = True
    recs = []
    for p in ingest.list_raw():
        r = ingest.load_record(p)
        if r.get("lat") is not None:
            recs.append(r)
    if a.limit:
        recs = recs[: a.limit]
    print(f"отелей: {len(recs)}; собираем: {', '.join(sorted(what))}")
    problems = 0
    for i, rec in enumerate(recs, 1):
        lat, lon, name = rec["lat"], rec["lon"], rec.get("name", "?")
        line = [f"[{i}/{len(recs)}] {name}"]
        if what & {"dem", "osm"}:
            try:
                if "osm" in what:
                    ex = net.ensure_extras(lat, lon, SCENE_RADIUS, max_age_days=a.max_age_days)
                    line.append(f"OSM+: тайлов {ex['tiles']}, пакетов {ex['around']}")
                    for e in ex["errors"]:
                        problems += 1
                        line.append("! " + e)
                    info = ex.get("dem") or {}
                else:
                    dem.clear_cache()
                    _, info = dem.get(lat, lon, allow_net=True)
                if "dem" in what:
                    layers = (info or {}).get("layers") or []
                    if layers:
                        line.append(f"рельеф: ок (слоёв {len(layers)}, плиток из кэша {info.get('tiles_cached')}, скачано {info.get('tiles_fetched')}, не хватает {info.get('tiles_missing')})")
                        if info.get("tiles_missing"):
                            line.append(f"! часть плиток не получена: {info.get('error') or 'нет в данных'}")
                    else:
                        problems += 1
                        line.append(f"рельеф: НЕТ ({(info or {}).get('error') or 'плитки не скачались'})")
            except Exception as e:  # noqa: BLE001
                problems += 1
                line.append(f"! {type(e).__name__}: {e}")
        if "probes" in what:
            st = []
            for p in all_probes():
                try:
                    res = p.run(rec, True)
                    st.append(f"{p.name}={res.status}")
                    if res.status in ("error",):
                        problems += 1
                except Exception as e:  # noqa: BLE001
                    problems += 1
                    st.append(f"{p.name}=ошибка({type(e).__name__})")
            line.append("источники: " + " ".join(st))
        print("; ".join(line), flush=True)
        time.sleep(a.delay)
    print(f"готово; проблем: {problems}. Дальше: перезапустите приложение (или POST /api/freshness/refresh) — факты пересчитаются по новым данным.")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
