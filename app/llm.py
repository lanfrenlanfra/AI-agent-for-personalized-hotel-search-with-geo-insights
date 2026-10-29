"""Тонкий клиент любого OpenAI-совместимого API (необязательный).

Без ключа система целиком работает на правилах. LLM используется только там, где
правила бессильны: разбор запроса и ответа отельера, имитация отельера, шаблоны для новой
характеристики. Любой сбой → None, и вызывающий код возвращается к правилам.
"""
from __future__ import annotations

import hashlib
import json
import os
import logging
import re
import threading
import time

import requests

from .config import settings

_cache: dict[str, object] = {}

# Запись и повтор ответов модели: data/llm_cache.json лежит в репозитории (записан при разработке с живой моделью),
# runtime/llm_cache.json пополняется во время работы. Так готовые примеры из интерфейса работают одинаково
# и без ключа; на новых запросах без ключа система возвращается к правилам.
_replay: dict[str, str] = {}
_loaded = False
_lock = threading.Lock()


def _paths():
    return settings.data_dir / "llm_cache.json", settings.runtime_dir / "llm_cache.json"


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    for p in _paths():
        try:
            _replay.update(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass


def _ckey(messages: list[dict]) -> str:
    return hashlib.sha1(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _remember(key: str, text: str) -> None:
    with _lock:
        _replay[key] = text
        _, rt = _paths()
        try:
            rt.parent.mkdir(parents=True, exist_ok=True)
            tmp = rt.with_suffix(".tmp")
            runtime_only = {}
            try:
                runtime_only = json.loads(rt.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            runtime_only[key] = text
            tmp.write_text(json.dumps(runtime_only, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, rt)
        except OSError:
            pass


def available() -> bool:
    """Есть ключ — можно ходить к модели."""
    return settings.llm_enabled


def usable() -> bool:
    """Есть ключ или записанные ответы модели для повтора."""
    _load()
    return settings.llm_enabled or bool(_replay)


def chat(messages: list[dict], temperature: float = 0.2, timeout: int = 90) -> str | None:
    _load()
    key = _ckey(messages)
    if key in _replay:
        return _replay[key]
    if not available():
        return None
    try:
        r = requests.post(
            f"{settings.llm_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json", **({"OpenAI-Project": settings.llm_project} if settings.llm_project else {})},
            json={"model": settings.llm_model, "messages": messages, "temperature": temperature},
            timeout=timeout,
        )
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"]["content"] or ""
        # «думающие» модели (qwen3, glm, deepseek) присылают рассуждения в <think>…</think>
        txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()
        if txt:
            _remember(key, txt)
        return txt or None
    except Exception as e:  # noqa: BLE001 — любой сбой сети/формата не должен ронять сервис
        logging.getLogger("kva.llm").warning("ошибка запроса: %s", e)
        return None


_state: tuple[float, str] = (0.0, "off")
_probing = False


def _probe() -> str:
    try:
        r = requests.get(f"{settings.llm_base_url}/models", headers={"Authorization": f"Bearer {settings.llm_api_key}"}, timeout=2)
        if r.status_code == 200:
            ids = [m.get("id", "") for m in (r.json().get("data") or [])]
            m = settings.llm_model
            return "ready" if (not ids or m in ids or f"{m}:latest" in ids) else "loading"
        return "ready"          # у части провайдеров списка моделей нет — считаем, что работает
    except Exception:  # noqa: BLE001
        return "loading"


def _refresh() -> None:
    global _state, _probing
    try:
        _state = (time.time(), _probe())
    finally:
        _probing = False


def state() -> str:
    """off — ключа нет; loading — сервис модели ещё не отвечает или модель скачивается; ready — можно спрашивать.
    Никогда не блокирует: проверка идёт в фоне, а ответ берётся из последней проверки (раз в 15 секунд)."""
    global _probing
    if not available():
        return "off"
    ts, cur = _state
    if time.time() - ts >= 15 and not _probing:
        _probing = True
        threading.Thread(target=_refresh, daemon=True).start()
    return cur if ts else "loading"


def chat_json(system: str, user: str, temperature: float = 0.0, timeout: int = 90) -> object | None:
    key = system[:60] + "|" + user
    if key in _cache:
        return _cache[key]
    txt = chat([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature, timeout)
    if not txt:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)```", txt, re.S)
    body = m.group(1) if m else txt
    try:
        val = json.loads(body.strip())
    except json.JSONDecodeError:
        m2 = re.search(r"(\{.*\}|\[.*\])", body, re.S)
        if not m2:
            return None
        try:
            val = json.loads(m2.group(1))
        except json.JSONDecodeError:
            return None
    if len(_cache) > 500:
        _cache.clear()
    _cache[key] = val
    return val
