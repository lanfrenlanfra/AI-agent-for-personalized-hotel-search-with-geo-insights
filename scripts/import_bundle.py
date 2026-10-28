#!/usr/bin/env python3
"""Превратить lupa-bundle.json (из scripts/browser_export.js) в data/raw/ostrovok/*.json."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import net, ostrovok  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("использование: python scripts/import_bundle.py lupa-bundle.json")
        return 2
    bundle = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    n = 0
    for h in bundle.get("hotels", []):
        try:
            url = f'{net.OSTROVOK}/hotel/{h["region"]}/mid{h["page"]["hotel"]["masterId"]}/{h["slug"]}/'
            rec = ostrovok.build_record(h["page"], h["slug"], h["region"], url, h.get("serp"), h.get("api_reviews"))
            rec["fetched_at"] = (bundle.get("exported_at") or rec["fetched_at"])
            print("сохранено:", net.save_record(rec), "—", rec["name"], f'(отзывов: {len(rec["reviews"])})')
            n += 1
        except Exception as e:  # noqa: BLE001
            print("пропуск:", h.get("slug"), e, file=sys.stderr)
    print(f"готово: {n} отелей. Дальше: python scripts/collect_osm.py и docker compose up --build")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
