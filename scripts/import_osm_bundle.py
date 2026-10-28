#!/usr/bin/env python3
"""Превратить выгрузку тайлов OSM из браузера (api.openstreetmap.org → JSON) в data/raw/osm/tiles/*.json.gz.

Файл: {"tiles": {"<ix>_<iy>": [элементы в формате Overpass (way с geometry, node, relation с members[].geometry)]}}.
Нужен, когда у вас нет прямого доступа к Overpass; обычный путь — scripts/collect_osm.py.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import osmtiles  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("использование: python scripts/import_osm_bundle.py osm-tiles-1.json [osm-tiles-2.json ...]")
        return 2
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n = 0
    for f in sys.argv[1:]:
        data = json.loads(Path(f).read_text(encoding="utf-8"))
        for key, els in data["tiles"].items():
            ix, iy = (int(x) for x in key.split("_"))
            t = osmtiles.parse_overpass(els, ix, iy, data.get("fetched_at") or now)
            osmtiles.save_tile(t)
            n += 1
    print(f"тайлов сохранено: {n}")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
