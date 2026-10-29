"""Проверка подключения к LLM: docker compose exec app python scripts/check_llm.py

Проверяет текст, разбор запроса в JSON и картинку (лягушка ведь смотрит фото гостей).
"""
import base64
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import llm  # noqa: E402
from app.config import settings  # noqa: E402

if not llm.available():
    print("LLM выключен: нет ключа (LLM_API_KEY или GIGACHAT_AUTH_KEY в .env). Сервис работает на правилах.")
    sys.exit(0)

if settings.llm_provider == "gigachat":
    from app import gigachat  # noqa: E402
    ca = gigachat.verify()
    print(f"LLM: GigaChat, модель {settings.gigachat_model}, для фото {settings.gigachat_vision_model}, scope {settings.gigachat_scope}")
    print(f"Сертификат Минцифры: {ca if ca is not True else 'не найден (' + settings.gigachat_ca_bundle + ') — запросы, скорее всего, упадут на проверке сертификата'}")
    if not gigachat.token():
        print("Токен не получен. Причина в строке «[llm] GigaChat» выше.")
        sys.exit(1)
    print("Токен получен.")
    try:
        r = gigachat._request("GET", "/models", timeout=15)
        ids = [m.get("id") for m in r.json().get("data") or []]
        print("Доступные модели:", ", ".join(ids) or "(список пуст)", f"— адрес {gigachat._base}")
        for m in (settings.gigachat_model, settings.gigachat_vision_model):
            if ids and m not in ids:
                print(f"  ! модели {m} нет в списке — поправьте GIGACHAT_MODEL / GIGACHAT_VISION_MODEL")
    except Exception as e:  # noqa: BLE001
        print("Список моделей не получен:", gigachat._explain(e))
else:
    print(f"LLM: {settings.llm_base_url}  модель: {settings.llm_model}")

out = llm.chat([{"role": "user", "content": "Ответь одним словом: «готово»."}], timeout=120)
if not out:
    print("Нет ответа от модели. Причина в строке «[llm] ошибка запроса» выше.")
    sys.exit(1)
print("Ответ модели:", out[:200])
print("Разбор запроса:", llm.chat_json('Верни JSON {"quiet": true|false, "sunset": true|false}', "тихий номер с видом на закат"))

try:                                                   # картинка: синее «озеро» под голубым небом
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (320, 200), (150, 200, 240))
    ImageDraw.Draw(im).rectangle((0, 120, 320, 200), fill=(30, 80, 160))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=85)
    url = "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()
    pic = llm.chat([{"role": "user", "content": [{"type": "text", "text": "Опиши картинку в 5 словах."}, {"type": "image_url", "image_url": {"url": url}}]}], timeout=120)
    print("Фото:", pic[:200] if pic else "модель картинку не разобрала — фото гостей будут искаться только по тексту")
except ImportError:
    print("Фото: Pillow не установлен, проверка картинки пропущена")
sys.exit(0)
