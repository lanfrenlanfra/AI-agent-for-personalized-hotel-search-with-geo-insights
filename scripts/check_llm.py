"""Проверка подключения к LLM: docker compose exec app python scripts/check_llm.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import llm  # noqa: E402
from app.config import settings  # noqa: E402

if not llm.available():
    print("LLM выключен: в .env нет LLM_API_KEY. Сервис работает на правилах.")
    sys.exit(0)
print(f"LLM: {settings.llm_base_url}  модель: {settings.llm_model}")
out = llm.chat([{"role": "user", "content": "Ответь одним словом: «готово»."}], timeout=120)
if out:
    print("Ответ модели:", out[:200])
    res = llm.chat_json('Верни JSON {"quiet": true|false, "sunset": true|false}', "тихий номер с видом на закат")
    print("Разбор запроса:", res)
    sys.exit(0)
print("Нет ответа от модели. Причина в строке «[llm] ошибка запроса» выше.")
sys.exit(1)
