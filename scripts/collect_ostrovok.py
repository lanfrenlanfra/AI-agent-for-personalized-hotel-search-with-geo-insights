#!/usr/bin/env python3
"""Сбор отелей региона с ostrovok.ru → data/raw/ostrovok/<master_id>.json.

Запускать там, где есть интернет:  docker compose run --rm collect   (или  python scripts/collect_ostrovok.py)
Берутся публичные страницы каталога и отелей и публичный JSON отзывов — то, что видит любой посетитель сайта.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import net, ostrovok  # noqa: E402
from app.config import settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--region", default=settings.region_slug)
    ap.add_argument("--limit", type=int, default=settings.collect_limit)
    ap.add_argument("--min-reviews", type=int, default=15, help="пропускать отели с меньшим числом отзывов (в них нечего проверять)")
    ap.add_argument("--from-html-dir", help="офлайн: разобрать сохранённые страницы *.html вместо сети")
    ap.add_argument("--refresh", action="store_true", help="перекачать уже собранные")
    a = ap.parse_args()

    out = settings.data_dir / "raw" / "ostrovok"
    out.mkdir(parents=True, exist_ok=True)

    if a.from_html_dir:
        n = 0
        for p in sorted(Path(a.from_html_dir).glob("*.html")):
            page = ostrovok.parse_next_data(p.read_text(encoding="utf-8"))
            rec = ostrovok.build_record(page, p.stem, a.region, f"file://{p.name}")
            print("сохранено:", net.save_record(rec))
            n += 1
        print(f"разобрано страниц: {n}")
        return 0

    got, page = 0, 1
    seen: set[str] = set()
    while got < a.limit:
        try:
            items = net.listing(a.region, page)
        except Exception as e:  # noqa: BLE001
            print(f"страница каталога {page}: {e}", file=sys.stderr)
            break
        if not items:
            break
        for it in items:
            if got >= a.limit:
                break
            if it["master_id"] in seen or it["num_reviews"] < a.min_reviews:
                continue
            seen.add(it["master_id"])
            if (out / f'{it["master_id"]}.json').exists() and not a.refresh:
                print("уже есть:", it["name"])
                got += 1
                continue
            try:
                rec = net.fetch_hotel(a.region, it)
            except Exception as e:  # noqa: BLE001
                print(f'пропуск {it["name"]}: {e}', file=sys.stderr)
                continue
            net.save_record(rec)
            got += 1
            print(f'[{got}/{a.limit}] {rec["name"]} — отзывов: {len(rec["reviews"])}, фото: {len(rec["images"])}', flush=True)
        page += 1
    print(f"готово: {got} отелей в {out}")
    return 0 if got else 1


if __name__ == "__main__":
    raise SystemExit(main())
