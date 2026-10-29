"""Запись ответов LLM для работы без ключа.

Запускается ВНУТРИ работающего контейнера с настроенной моделью (LLM_* в .env):
    docker compose exec app python scripts/record_llm.py
Скрипт прогоняет через приложение примеры запросов из интерфейса, черновики новых характеристик и
имитацию ответов отельера у лучших отелей. Ответы модели складываются в runtime/llm_cache.json, оттуда их
копируют в репозиторий (data/llm_cache.json), и тот же разбор работает у тех, у кого ключа нет:
    docker compose cp app:/app/runtime/llm_cache.json data/llm_cache.json
"""
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import llm  # noqa: E402

BASE = "http://127.0.0.1:8080"
DRAFTS = ["в номере есть чайник и можно сделать чай ночью", "рядом есть каток или горнолыжный склон",
          "можно приехать с большой собакой, есть выгул"]
HOTELS_PER_QUERY = 3
QUESTIONS_PER_HOTEL = 3


def call(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def main() -> int:
    if not llm.available():
        print("Нужен LLM_API_KEY (для Ollama подойдёт любой) и модель в .env. Запись без модели невозможна.")
        return 1
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    examples = json.loads(re.search(r"const EXAMPLES = (\[.*?\]);", js, re.S).group(1))
    seen: set[str] = set()
    for q in examples:
        print("запрос:", q, flush=True)
        res = call("/api/search?" + urllib.parse.urlencode({"q": q, "passive": "1"}))
        print("   разобрано:", res.get("parsed", {}).get("parsed_by"), flush=True)
        for h in (res.get("results") or [])[:HOTELS_PER_QUERY]:
            hid = h.get("id") or h.get("hotel_id")
            if not hid or hid in seen:
                continue
            seen.add(hid)
            qs = call("/api/questions?" + urllib.parse.urlencode({"hotel_id": hid}))
            for item in qs[:QUESTIONS_PER_HOTEL]:
                r = call(f"/api/questions/{item['id']}/simulate", {})
                print("   ответ отельера:", (r.get("answer_text") or "")[:80], flush=True)
    for d in DRAFTS:
        r = call("/api/features/suggest", {"description": d})
        print("характеристика:", r.get("title"), "|", r.get("drafted_by"), flush=True)
    print("Готово. Скопируйте файл: docker compose cp app:/app/runtime/llm_cache.json data/llm_cache.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
