#!/usr/bin/env python3
"""Сбор данных региона с ostrovok.ru: индекс каталога → data/raw/catalog/<регион>.json и стартовая выборка отелей → data/raw/ostrovok/<master_id>.json.

Остальные отели региона сервис дозаполняет сам, по запросам пользователей (app/enrich.py).

Запускать там, где есть интернет:  docker compose run --rm collect   (или  python scripts/collect_ostrovok.py)
Берутся публичные страницы каталога и отелей и публичный JSON отзывов — то, что видит любой посетитель сайта.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import catalog, net, ostrovok  # noqa: E402
from app.config import settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--region", default=settings.region_slug)
    ap.add_argument("--limit", type=int, default=settings.collect_limit)
    ap.add_argument("--min-reviews", type=int, default=15, help="для стартовой выборки: пропускать отели с меньшим числом отзывов (в них нечего проверять)")
    ap.add_argument("--catalog-pages", type=int, default=settings.collect_catalog_pages, help="сколько страниц каталога обойти для индекса региона (по 20 отелей)")
    ap.add_argument("--with-apartments", action="store_true", help="включать апартаменты в стартовую выборку")
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

    # 1) дешёвый индекс региона: страницы каталога (по 20 отелей) — цена, звёзды, метро, пляж, удобства
    rows, total = catalog.crawl(a.region, a.catalog_pages, min_reviews=0, progress=lambda m: print(m, flush=True))
    if rows:
        print("индекс каталога:", catalog.save_file(rows, a.region, total), f"({len(rows)} из {total or '?'})")
    else:
        print("каталог не получен — идём по страницам по порядку", file=sys.stderr)
    # 2) стартовая глубокая выборка: разные районы, цены и типы, а не «первые N». Остальное доберётся по запросам пользователя.
    pool = [r for r in rows if a.with_apartments or not r["apart"]]
    chosen = catalog.stratified_sample(pool, a.limit, a.min_reviews)
    got = 0
    for it in chosen:
        if (out / f'{it["master_id"]}.json').exists() and not a.refresh:
            got += 1
            continue
        try:
            rec = net.fetch_hotel(a.region, catalog.summary(it))
        except Exception as e:  # noqa: BLE001
            print(f'пропуск {it["name"]}: {e}', file=sys.stderr)
            continue
        net.save_record(rec)
        got += 1
        print(f'[{got}/{len(chosen)}] {rec["name"]} — отзывов: {len(rec["reviews"])}, фото: {len(rec["images"])}', flush=True)
    print(f"готово: {got} отелей в {out}")
    return 0 if got else 1


if __name__ == "__main__":
    raise SystemExit(main())
