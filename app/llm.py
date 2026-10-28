"""Тонкий клиент любого OpenAI-совместимого API (необязательный).

Без ключа система целиком работает на правилах. LLM используется только там, где
правила бессильны: разбор запроса и ответа отельера, имитация отельера, шаблоны для новой
характеристики. Любой сбой → None, и вызывающий код возвращается к правилам.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading

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
        print(f"[llm] ошибка запроса: {e}", flush=True)
        return None


def chat_json(system: str, user: str, temperature: float = 0.0) -> object | None:
    key = system[:60] + "|" + user
    if key in _cache:
        return _cache[key]
    txt = chat([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature)
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
