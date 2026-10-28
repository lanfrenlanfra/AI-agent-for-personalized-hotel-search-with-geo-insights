"""Лягушка: сценарий «описание (+ фото) → топ-3 отеля».

1. Фото-референс (если есть) модель переводит в слова: что видно и что из этого важно для проживания.
2. Запрос и описание фото разбираются в критерии (LLM, а без него правила) и ищутся по базе отелей.
3. Каждое значение уже проверено при сборе: координаты → карта OSM, свежесть, источники. Здесь мы только
   показываем итог: чем отель подходит, что неясно и что стоит уточнить у отельера.
4. Короткий «голос лягушки» пишет модель по фактам (с запасным текстом по шаблону, если модели нет).
"""
from __future__ import annotations

import re

from . import llm, search

IMG_PROMPT = ("Ты помогаешь выбрать отель на Байкале по фотографии-референсу. Опиши по-русски в 1–2 коротких предложениях только то, "
              "что видно и важно для проживания: вид из окна или вокруг (озеро, лёд, лес, горы, закат), время года, тишина или многолюдность, "
              "тип жилья и интерьер (дерево, баня, камин, панорамные окна). Не выдумывай того, чего не видно.")


def describe_image(data_url: str) -> str | None:
    if not data_url or not data_url.startswith("data:image/") or len(data_url) > 6_000_000:
        return None
    txt = llm.chat([{"role": "user", "content": [{"type": "text", "text": IMG_PROMPT}, {"type": "image_url", "image_url": {"url": data_url}}]}],
                   temperature=0.2, timeout=120)
    return re.sub(r"\s+", " ", txt).strip()[:400] if txt else None


def understand(text: str, image: str | None) -> dict:
    """Склеивает текст и описание картинки в один запрос и разбирает его."""
    note = None
    if image:
        note = describe_image(image)
    full = text.strip()
    if note:
        full = (full + ". " if full else "") + "Хочу как на фото: " + note
    parsed = search.parse_query(full)
    return {"text": text.strip(), "image_note": note, "image_given": bool(image), "image_read": bool(note), "parsed": parsed}


def _short(i: dict) -> str:
    t = (i.get("text") or "").strip().rstrip(".")
    return (t[:1].lower() + t[1:]) if t else ""


def card(r: dict) -> dict:
    """Лаконичный итог по отелю: плюсы, минусы, что уточнить, чем проверено."""
    items = [i for i in r["items"] if i["feature"] != "text"]
    pros = [i for i in items if i["verdict"] == "ok"]
    cons = [i for i in items if i["verdict"] in ("no", "partial")]
    ask = [i for i in items if i["verdict"] in ("unknown", "maybe", "disputed", "stale")]
    checked = [i for i in items if i["verdict"] != "unknown"]
    ages = [i["age_days"] for i in checked if i.get("age_days") is not None]
    reasons = {"unknown": "данных пока нет", "maybe": "уверенность средняя", "disputed": "источники расходятся", "stale": "данные устарели"}
    return {
        "id": r["id"], "name": r["name"], "photo": r["photo"], "price": r["price_from"], "rating": r["rating"], "reviews": r["reviews"],
        "stars": r["stars"], "address": r["address"], "lat": r["lat"], "lon": r["lon"], "url": r["url"], "score": r["score"], "label": r["label"],
        "pros": [{"title": i["title"], "text": _short(i), "geo": bool(i.get("computed"))} for i in pros[:4]],
        "cons": [{"title": i["title"], "text": _short(i)} for i in cons[:3]],
        "ask": [{"title": i["title"], "why": reasons.get(i["verdict"], ""), "question": i["title"]} for i in ask[:3]],
        "proof": {"geo": sum(1 for i in checked if i.get("computed")), "checked": len(checked), "asked": len(items),
                  "freshest_days": min(ages) if ages else None, "oldest_days": max(ages) if ages else None,
                  "stale": sum(1 for i in items if i["verdict"] == "stale"), "disputed": sum(1 for i in items if i["verdict"] == "disputed")},
    }


def top(res: list[dict], n: int = 3, wish: str = "") -> list[dict]:
    good = [r for r in res if r["label"] != "Не подходит"]
    pool = good if len(good) >= n else good + [r for r in res if r["label"] == "Не подходит"]
    # сначала те, о ком что-то известно: пустая «карточка каталога» не должна обгонять проверенный отель
    pool.sort(key=lambda r: (-(r["score"] + (6 if r["known"] else 0)), -(r["rating"] or 0)))
    out = [card(r) for r in pool[:n]]
    for c in out:
        c["say"] = template_say(c, wish)       # мгновенный текст; живой голос модели подменяет его, когда готов
    return out


_SIMPLIFY = [
    (r"(?i)закат виден из окон на ([\w-]+) с (\w+) этажа", r"окна на \1, с \2 этажа"),
    (r"(?i)закат виден из окон на ([\w-]+)", r"окна на \1"),
    (r"(?i)тише всего окна на ([\w-]+)", r"самые тихие окна на \1"),
    (r"(?i)гости подтверждают", "по отзывам гостей"),
    (r"(?i)гости опровергают", "по отзывам гостей нет"),
    (r"(?i)видна вода", "вода видна из окон"),
    (r"\s+за ночь$", " за ночь"),
]


def _bit(p: dict, n: int = 80) -> str:
    """Первая мысль из технического описания факта: без скобок, цифр-оценок и списка месяцев."""
    t = re.split(r"[;:(]", (p.get("text") or ""), 1)[0].strip().rstrip(".,— ")
    for rx, rep in _SIMPLIFY:
        t = re.sub(rx, rep, t)
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"


def _phrase(p: dict) -> str:
    t, title = _bit(p), p["title"].lower()
    if not p["geo"] or not t:
        return title
    return t if title[:4] in t.lower() else f"{title}: {t}"


def template_say(c: dict, wish: str) -> str:
    """Запасной голос: короткий текст по шаблону из тех же фактов."""
    if not (c["pros"] or c["cons"] or c["ask"]):
        return "Ква! Про этот отель у меня пока мало подробностей, поэтому ставлю его в запас."
    parts = ["Ква!"]
    pros = [p for p in c["pros"] if p["geo"]] + [p for p in c["pros"] if not p["geo"]]
    if pros:
        txt = "; ".join(_phrase(p) for p in pros[:2])
        parts.append(txt[:1].upper() + txt[1:] + ".")
    if c["cons"]:
        parts.append("Но " + "; ".join(_phrase({**p, "geo": True}) for p in c["cons"][:2]) + ".")
    if c["ask"]:
        parts.append("Не знаю наверняка: " + ", ".join(a["title"].lower() for a in c["ask"][:3]) + ". Лучше спросить у отеля.")
    return " ".join(parts)


def say(c: dict, wish: str) -> dict:
    facts = {"отель": c["name"], "совпадение_процентов": c["score"],
             "подходит": [f'{p["title"]}: {p["text"]}' for p in c["pros"]], "не_подходит": [f'{p["title"]}: {p["text"]}' for p in c["cons"]],
             "нужно_уточнить": [f'{a["title"]} ({a["why"]})' for a in c["ask"]], "цена_от": c["price"], "оценка": c["rating"]}
    out = llm.chat([
        {"role": "system", "content": "Ты — лягушка, дружелюбный агент по отелям Байкала. Напиши гостю 2–3 коротких живых предложения по-русски: насколько отель подходит под его желание, "
                                      "что в нём лучшее и что нужно уточнить. Используй ТОЛЬКО факты из данных, ничего не выдумывай, не пересказывай цифры подряд. "
                                      "Можно начать с «Ква!» (не больше одного раза), без списков, без эмодзи."},
        {"role": "user", "content": f"Желание гостя: {wish or 'не указано'}\nДанные: {facts}"}], temperature=0.6, timeout=60) if llm.usable() else None
    if out:
        return {"text": out.strip(), "by": "llm"}
    return {"text": template_say(c, wish), "by": "template"}


# ───────────────────────── сравнение по желаниям гостя ─────────────────────────

VERDICT_KIND = {"ok": "yes", "partial": "part", "maybe": "maybe", "no": "no", "disputed": "maybe", "stale": "maybe", "unknown": "unk"}


def _cell(i: dict) -> dict:
    """Одна клетка таблицы: значок и очень короткая фраза «по-лягушачьи»."""
    v = i["verdict"]
    t = _bit(i, 44)
    if v == "unknown":
        t = "пока не знаю"
    elif v == "disputed":
        t = "источники спорят"
    elif v == "stale":
        t = "данные устарели, лучше уточнить"
    elif v == "maybe":
        t = t or "скорее да, но не уверена"
    elif v == "no":
        t = t or "нет"
    elif v == "ok" and not t:
        t = "да"
    if i["feature"] == "price":
        t = re.sub(r"\s*\(.*", "", i["text"]).replace("От", "от", 1)
    return {"kind": VERDICT_KIND.get(v, "unk"), "text": t[:1].upper() + t[1:] if t else "", "geo": bool(i.get("computed")), "expected": i.get("expected", 0)}


def compare_view(res: list[dict], ids: list[str]) -> dict:
    by = {r["id"]: r for r in res}
    picked = [by[i] for i in ids if i in by][:4]
    if not picked:
        return {"hotels": [], "rows": []}
    base = [i for i in picked[0]["items"] if i["feature"] != "text"]
    rows = []
    for idx, it in enumerate(base):
        cells = []
        for r in picked:
            items = [x for x in r["items"] if x["feature"] != "text"]
            cells.append(_cell(items[idx]) if idx < len(items) else {"kind": "unk", "text": "пока не знаю", "geo": False, "expected": 0})
        good = [c["expected"] for c in cells if c["kind"] in ("yes", "part", "maybe")]
        best = None
        if len(good) >= 2 and max(good) > min(good) + 0.04:
            best = max(range(len(cells)), key=lambda k: cells[k]["expected"] if cells[k]["kind"] in ("yes", "part", "maybe") else -1)
        rows.append({"title": it["title"], "feature": it["feature"], "cells": [{k: v for k, v in c.items() if k != "expected"} for c in cells], "best": best})
    cards = [card(r) for r in picked]
    return {"hotels": [{k: c[k] for k in ("id", "name", "photo", "price", "rating", "reviews", "stars", "address", "url", "score", "label")} for c in cards],
            "rows": rows, "verdict": compare_verdict(cards), "_cards": cards}


def compare_verdict(cards: list[dict]) -> str:
    if not cards:
        return ""
    order = sorted(cards, key=lambda c: -c["score"])
    w = order[0]
    pros = ", ".join(p["title"].lower() for p in w["pros"][:3])
    txt = f"Ква! Больше всего под ваши пожелания подходит «{w['name']}»" + (f": {pros}." if pros else ".")
    common = set.intersection(*[{a["title"] for a in c["ask"]} for c in cards]) if cards else set()
    if common:
        txt += f" Ни про один отель пока не знаю: {', '.join(t.lower() for t in sorted(common))}. Лучше спросить у отелей."
    for o in order[1:3]:
        if o["cons"]:
            txt += f" У «{o['name']}» не сходится: {o['cons'][0]['title'].lower()}."
        else:
            own = [a["title"] for a in o["ask"] if a["title"] not in common]
            if own:
                txt += f" Про «{o['name']}» не знаю наверняка: {own[0].lower()}."
    own_w = [a["title"] for a in w["ask"] if a["title"] not in common]
    if own_w:
        txt += f" У победителя лучше уточнить: {', '.join(t.lower() for t in own_w[:2])}."
    return txt


def say_compare(cards: list[dict], wish: str, base: str) -> dict:
    facts = [{"отель": c["name"], "совпадение": c["score"], "подходит": [p["title"] for p in c["pros"]], "не_подходит": [p["title"] for p in c["cons"]],
              "уточнить": [a["title"] for a in c["ask"]], "цена_от": c["price"]} for c in cards]
    out = llm.chat([
        {"role": "system", "content": "Ты — лягушка, дружелюбный агент по отелям Байкала. Сравни отели для гостя в 2–3 коротких предложениях по-русски: "
                                      "кто лучше подходит под его желания и чем остальные уступают или что в них нужно уточнить. Только факты из данных, ничего не выдумывай, "
                                      "без списков и эмодзи, можно начать с «Ква!»."},
        {"role": "user", "content": f"Желание гостя: {wish or 'не указано'}\nОтели: {facts}"}], temperature=0.5, timeout=60) if llm.usable() else None
    return {"text": out.strip(), "by": "llm"} if out else {"text": base, "by": "template"}
