"""GigaChat (Сбер) как модель для лягушки — вместо или вместе с локальной Ollama.

Чем отличается от OpenAI-совместимых API, и что здесь для этого сделано:
* авторизация: «ключ авторизации» меняется на токен доступа на 30 минут (POST /api/v2/oauth, scope),
  токен кешируется и обновляется заранее;
* картинки нельзя положить в сообщение как data URL: файл загружается в POST /files (purpose=general),
  а в сообщение идёт его id в поле attachments (одно изображение на сообщение);
* сертификаты: цепочка подписана корневым сертификатом Минцифры — путь к нему в GIGACHAT_CA_BUNDLE
  (Dockerfile скачивает его при сборке);
* у бесплатного тарифа — один запрос за раз, поэтому параллельность ограничена, а на 429 есть повтор.
Любой сбой → None, и вызывающий код возвращается к правилам, как и с другими моделями.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import threading
import time
import uuid

import requests

from .config import settings

log = logging.getLogger("lupa.gigachat")
OLD_BASE = "https://gigachat.devices.sberbank.ru/api/v1"     # прежний адрес: Сбер переводит всех на api.giga.chat

_token: tuple[str, float] = ("", 0.0)                         # (токен, когда истекает, unix-время)
_token_lock = threading.Lock()
_slots = threading.BoundedSemaphore(max(1, settings.gigachat_max_parallel))
_base: str | None = None                                      # адрес, который ответил (новый или прежний)


def verify():
    """Путь к сертификату Минцифры, если он есть; иначе стандартная проверка (и понятная ошибка в логе)."""
    p = settings.gigachat_ca_bundle
    return p if p and os.path.exists(p) else True


def _auth_header() -> str:
    key = settings.gigachat_auth_key.strip()
    return key if key.lower().startswith("basic ") else f"Basic {key}"


def token(force: bool = False) -> str | None:
    global _token
    with _token_lock:
        tok, exp = _token
        if tok and not force and time.time() < exp - 60:          # обновляем за минуту до конца
            return tok
        try:
            r = requests.post(settings.gigachat_auth_url, timeout=20, verify=verify(),
                              headers={"Authorization": _auth_header(), "RqUID": str(uuid.uuid4()), "Accept": "application/json",
                                       "Content-Type": "application/x-www-form-urlencoded"},
                              data={"scope": settings.gigachat_scope})
            r.raise_for_status()
            d = r.json()
            exp = float(d.get("expires_at") or 0)
            exp = exp / 1000 if exp > 1e11 else exp                # в документации пример в секундах, на практике бывают миллисекунды
            _token = (d["access_token"], exp or time.time() + 1700)
            return _token[0]
        except Exception as e:  # noqa: BLE001
            log.warning("GigaChat: не удалось получить токен: %s", _explain(e))
            print(f"[llm] GigaChat: не удалось получить токен: {_explain(e)}", flush=True)
            return None


def _explain(e: Exception) -> str:
    s = str(e)
    if "CERTIFICATE_VERIFY_FAILED" in s or "certificate verify failed" in s:
        return (s[:200] + " — нужен корневой сертификат Минцифры: пересоберите образ (Dockerfile скачивает его) "
                "или укажите путь в GIGACHAT_CA_BUNDLE")
    if isinstance(e, requests.HTTPError) and e.response is not None:
        body = (e.response.text or "")[:300]
        if e.response.status_code in (400, 401):
            return f"{e.response.status_code} {body} — проверьте GIGACHAT_AUTH_KEY и GIGACHAT_SCOPE (физлицам — GIGACHAT_API_PERS)"
        return f"{e.response.status_code} {body}"
    return s[:300]


def _bases() -> list[str]:
    first = _base or settings.gigachat_base_url
    return [first] + [b for b in (settings.gigachat_base_url, OLD_BASE) if b != first]


def _request(method: str, path: str, **kw) -> requests.Response:
    """Запрос к API с токеном: 401 — обновить токен и повторить, 429 — подождать и повторить,
    404 на основном адресе — попробовать прежний (gigachat.devices.sberbank.ru)."""
    global _base
    last: Exception | None = None
    for base in _bases():
        force = False
        for attempt in range(3):
            tok = token(force=force)
            if not tok:
                raise RuntimeError("нет токена GigaChat")
            try:
                r = requests.request(method, base + path, headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"},
                                     verify=verify(), **kw)
            except requests.ConnectionError as e:
                last = e
                break                                               # адрес не отвечает — пробуем прежний
            if r.status_code == 401:
                force, last = True, requests.HTTPError(response=r)
                continue
            if r.status_code == 429:
                last = requests.HTTPError(response=r)
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 404 and path in ("/models", "/chat/completions"):
                last = requests.HTTPError(response=r)
                break
            r.raise_for_status()
            _base = base
            return r
    raise last or RuntimeError("GigaChat недоступен")


def _upload_image(data_url: str) -> str | None:
    m = re.match(r"data:(image/[\w.+-]+);base64,(.+)$", data_url, re.S)
    if not m:
        return None
    mime, raw = m.group(1), base64.b64decode(m.group(2))
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(mime, "jpg")
    r = _request("POST", "/files", files={"file": (f"photo.{ext}", raw, mime)}, data={"purpose": "general"}, timeout=60)
    return r.json().get("id")


def _convert(messages: list[dict]) -> tuple[list[dict], bool]:
    """OpenAI-формат (content со списком частей и image_url) → GigaChat (текст + attachments). Второе — есть ли картинка."""
    out, has_image = [], False
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            out.append({"role": msg["role"], "content": content})
            continue
        texts, files = [], []
        for part in content or []:
            if part.get("type") == "text":
                texts.append(part.get("text") or "")
            elif part.get("type") == "image_url":
                fid = _upload_image((part.get("image_url") or {}).get("url") or "")
                if fid:
                    files.append(fid)
                    has_image = True
        m = {"role": msg["role"], "content": "\n".join(texts)}
        if files:
            m["attachments"] = files[:1]                           # GigaChat: одно изображение на сообщение
        out.append(m)
    return out, has_image


def chat(messages: list[dict], temperature: float = 0.2, timeout: int = 90) -> str | None:
    with _slots:
        try:
            msgs, has_image = _convert(messages)
            model = settings.gigachat_vision_model if has_image else settings.gigachat_model
            body = {"model": model, "messages": msgs, "temperature": max(temperature, 0.01)}   # GigaChat не любит ровно 0
            r = _request("POST", "/chat/completions", json=body, timeout=timeout)
            return (r.json()["choices"][0]["message"]["content"] or "").strip() or None
        except Exception as e:  # noqa: BLE001 — сеть, лимиты, формат: не роняем сервис
            print(f"[llm] GigaChat: ошибка запроса: {_explain(e)}", flush=True)
            return None


def probe() -> str:
    """ready — токен получен и модель есть в списке; loading — пока не ответил (сеть, сертификат, ключ)."""
    try:
        r = _request("GET", "/models", timeout=8)
        ids = [m.get("id", "") for m in (r.json().get("data") or [])]
        return "ready" if not ids or settings.gigachat_model in ids else "loading"
    except Exception as e:  # noqa: BLE001
        log.debug("GigaChat probe: %s", e)
        return "loading"
