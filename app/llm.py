"""Тонкий клиент любого OpenAI-совместимого API (необязательный).

Без ключа система целиком работает на правилах. LLM используется только там, где
правила бессильны: разбор запроса и ответа отельера, имитация отельера, шаблоны для новой
характеристики. Любой сбой → None, и вызывающий код возвращается к правилам.
"""
from __future__ import annotations

import json
import re

import requests

from .config import settings

_cache: dict[str, object] = {}


def available() -> bool:
    return settings.llm_enabled


def chat(messages: list[dict], temperature: float = 0.2, timeout: int = 90) -> str | None:
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
        return re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip() or None
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
