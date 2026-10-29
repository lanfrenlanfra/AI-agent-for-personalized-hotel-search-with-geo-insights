#!/usr/bin/env python3
"""Скачать тайлы OpenStreetMap (здания, дороги, вода, заведения) вокруг собранных отелей → data/raw/osm/.

Нужны для геохарактеристик (закат, вид, шум, пешие маршруты). Overpass публичный — поэтому с паузами и зеркалами.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ingest, net, osmtiles  # noqa: E402
from app.collectors.geo import SCENE_RADIUS  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-age-days", type=int, default=None, help="перекачать тайлы старше N дней")
    a = ap.parse_args()
    need: dict[tuple[int, int], str] = {}
    for p in ingest.list_raw():
        rec = ingest.load_record(p)
        if rec.get("lat") is None:
            continue
        for t in osmtiles.tiles_for(rec["lat"], rec["lon"], SCENE_RADIUS):
            need.setdefault(t, rec["name"])
    print(f"нужно тайлов: {len(need)}")
    done = 0
    for i, ((ix, iy), who) in enumerate(sorted(need.items()), 1):
        lat0 = osmtiles.tile_bbox(ix, iy)
        try:
            n = net.ensure_tiles((lat0[0] + lat0[2]) / 2, (lat0[1] + lat0[3]) / 2, 1.0, a.max_age_days)
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(need)}] тайл {ix},{iy} ({who}): {e}", file=sys.stderr)
            continue
        done += n
        print(f"[{i}/{len(need)}] тайл {ix},{iy}: {'скачан' if n else 'есть'}", flush=True)
    print(f"скачано: {done}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
