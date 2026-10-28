"""Лягушка: сценарий «описание (+ фото) → топ-3 отеля».

1. Фото-референс (если есть) модель переводит в слова: что видно и что из этого важно для проживания.
2. Запрос и описание фото разбираются в критерии (LLM, а без него правила) и ищутся по базе отелей.
3. Каждое значение уже проверено при сборе: координаты → карта OSM, свежесть, источники. Здесь мы только
   показываем итог: чем отель подходит, что неясно и что стоит уточнить у отельера.
4. Короткий «голос лягушки» пишет модель по фактам (с запасным текстом по шаблону, если модели нет).
"""
from __future__ import annotations

import json
import re

from . import llm, search

IMG_PROMPT = ("Посмотри на фото: человек выбирает отель на Байкале и приложил его как референс. Верни ТОЛЬКО JSON вида "
              '{"relevant": true|false, "what": "что на фото, 3-8 слов", "wishes": "..."}. '
              "relevant=true, если это отель, номер, жильё, интерьер, вид из окна, озеро, лес, горы, берег или другое место отдыха; "
              "false — для еды, документов, скриншотов, животных, людей без обстановки, техники и всего прочего, что не про жильё и отдых. "
              "wishes — при relevant=true 1–2 коротких русских предложения о том, что видно и важно для проживания: вид из окна, озеро, лёд, лес, закат, "
              "тишина или людно, тип дома, дерево, баня, камин, панорамные окна, сезон; иначе пустая строка. Не выдумывай того, чего не видно.")


def describe_image(data_url: str) -> dict | None:
    """{'relevant': bool, 'what': str, 'wishes': str} или None, если фото не удалось прочитать."""
    if not data_url or not data_url.startswith("data:image/") or len(data_url) > 6_000_000:
        return None
    raw = llm.chat([{"role": "user", "content": [{"type": "text", "text": IMG_PROMPT}, {"type": "image_url", "image_url": {"url": data_url}}]}],
                   temperature=0.2, timeout=120)
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        d = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        d = None
    if isinstance(d, dict) and "relevant" in d:
        return {"relevant": bool(d.get("relevant")), "what": re.sub(r"\s+", " ", str(d.get("what") or "")).strip()[:80],
                "wishes": re.sub(r"\s+", " ", str(d.get("wishes") or "")).strip()[:400]}
    t = re.sub(r"\s+", " ", raw).strip()[:400]            # модель ответила обычным текстом: считаем описанием
    return {"relevant": True, "what": "", "wishes": t}


HINTS = ["тихий номер с видом на закат над Байкалом, зимой", "у берега, с баней и тёплым номером, до 7000 ₽", "Ольхон, вид на скалу Шаманка, можно с собакой"]
_GREET = re.compile(r"^\W*(привет|приветик|здравствуй|здравствуйте|добрый\s+(день|вечер|утро)|доброе\s+утро|хай|хэй|ку|здарова|салют|hello|hi|hey|ква)\W*$", re.I)
_THANKS = re.compile(r"спасиб|благодар|пока\b|до свидания|всего доброго|thanks", re.I)
_ABOUT = re.compile(r"кто ты|ты кто|что ты умеешь|что умеешь|как тебя зовут|ты умеешь|чем занимаешься|как дела|как ты|помоги|помощь|что ты такое|расскажи о себе", re.I)
_REGION = re.compile(r"\b(сочи|москв\w*|питер\w*|санкт|петербург\w*|крым\w*|ялт\w*|казан\w*|турци\w*|египт\w*|дубай|таиланд\w*|пхукет|бали|париж\w*|алтай\w*|карели\w*|камчатк\w*|сибир\w*|красноярск\w*|новосибирск\w*|геленджик\w*|анап\w*|кавказ\w*|эльбрус\w*)", re.I)
_HOTELISH = re.compile(r"отел|номер|жильё|жилье|гостиниц|хостел|апартамент|квартир|коттедж|кемпинг|глэмпинг|санатори|дом\b|домик|баз[аы] отдыха|ночь|ночев|останов|пожить|отдох|отдых|поездк|путешеств|байкал|озер|хочу|нужн|ищу|подбер|найд", re.I)


def _pick(opts: list[str], key: str) -> str:
    return opts[sum(ord(c) for c in key) % len(opts)]


def smalltalk(text: str, image: dict | None, image_given: bool) -> tuple[str, str] | None:
    """Не поиск ли это? Возвращает (вид, шаблонный ответ) или None, если надо искать."""
    t = text.strip()
    place = _REGION.search(t)
    if image_given and not t:
        if image is None:
            return "image_unread", "Ква! Фото я пока не разглядела, а без слов не пойму. Опишите, какой отдых на Байкале хотите, и подберу отели."
        if not image["relevant"]:
            what = f"«{image['what']}»" if image["what"] else "что-то интересное"
            return "image_off", (f"Ква! На фото {what}. Выглядит любопытно, но на отель или место для отдыха не очень похоже. "
                                 "Пришлите вид из окна, дом или интерьер, который нравится, или просто опишите мечту словами.")
        if not image["wishes"]:
            return "image_vague", "Ква! Фото красивое, но по нему одному я пока не поняла, что для вас важно. Добавьте пару слов: тишина, баня, берег, бюджет?"
        return None
    if _GREET.match(t):
        return "greet", _pick(["Ква! Привет-привет! Я лягушка-агент: подбираю отели на Байкале и проверяю их по карте. Расскажите, какой отдых хотите: тихий, с видом на озеро, с баней?",
                               "Ква-ква! Рада вас видеть. Давайте найдём жильё у самого Байкала. Что для вас главное: вид, тишина, баня или путь до берега?"], t)
    if _THANKS.search(t) and len(t) < 40:
        return "thanks", "Ква! Всегда пожалуйста. Соберётесь на Байкал, просто расскажите, какой отель нужен, а я подберу три варианта."
    if _ABOUT.search(t):
        return "about", ("Ква! Я лягушка-агент. Читаю карту, отзывы и данные Островка и подбираю три отеля на Байкале под ваши пожелания, можно даже по фото. "
                         "Расскажите, чего хочется: вид на закат, тишины, бани?")
    if place:
        return "region", (f"Ква! Я живу на берегу Байкала и знаю только его, про «{place.group(0)}» подсказать не смогу. "
                          "Зато найду отель у озера: что важно — вид, тишина, баня, бюджет?")
    return None


def vague_or_off(text: str) -> tuple[str, str]:
    if _HOTELISH.search(text):
        return "vague", ("Ква! Отелей на Байкале много, давайте сузим. Что для вас важно: вид на озеро, тишина, баня, путь до берега, бюджет? "
                         "Можно описать мечту своими словами или приложить фото.")
    return "off", _pick(["Ква-а… С этим я не помогу: умею только подбирать отели на Байкале. Но давайте попробуем: какой отдых вам по душе, тихий или шумный, с видом на озеро или с баней?",
                         "Ква! Это вне моего болота: я лягушка-агент по отелям Байкала. Расскажите, где хотите проснуться: с видом на лёд, рядом с берегом, в тишине?"], text)


LLM_KINDS = ("greet", "thanks", "about", "region", "vague", "off")


def _hist_messages(history: list | None, n: int = 6) -> list[dict]:
    out = []
    for h in (history or [])[-n:]:
        if isinstance(h, dict) and isinstance(h.get("text"), str) and h["text"].strip():
            out.append({"role": "user" if h.get("role") == "user" else "assistant", "content": h["text"].strip()[:400]})
    return out


def chat_reply(text: str, kind: str, base: str, live: bool = False, history: list | None = None) -> dict:
    """Ответ лягушки. По умолчанию мгновенный шаблон; живой ответ модели (live=True) запрашивается отдельно и подменяет шаблон на странице."""
    if live and llm.usable() and kind in LLM_KINDS:
        out = llm.chat([
            {"role": "system", "content": "Ты — лягушка Ква, весёлый и тёплый агент по отелям Байкала. Ответь гостю на его реплику в 1–3 коротких предложениях по-русски, с лёгким юмором, "
                                          "и мягко подведи к тому, чтобы он описал, какой отдых или отель на Байкале ищет (вид, тишина, баня, берег, бюджет, с кем едет). "
                                          "Если он спрашивает про другое место или тему, шутливо скажи, что умеешь только отели Байкала. Учитывай предыдущие реплики диалога. "
                                          "Ничего не выдумывай про конкретные отели, без списков и эмодзи, можно начать с «Ква!»."},
            *_hist_messages(history), {"role": "user", "content": text}], temperature=0.8, timeout=30)
        if out:
            return {"text": out.strip(), "by": "llm"}
    return {"text": base, "by": "template"}


_RESET = re.compile(r"заново|с нуля|новый поиск|начн[её]м сначала|начать сначала|забудь|совсем другое|другой отель", re.I)


def merge_wish(prev: str, new: str, history: list | None = None) -> str:
    """Дополняет прежние пожелания новой репликой («забыла, хочу ещё с бассейном»). Модель, если жива, умеет и заменять передуманное."""
    prev, new = (prev or "").strip(), (new or "").strip()
    if not prev or _RESET.search(new):
        return _RESET.sub("", new).strip(" .,") or new
    if llm.state() == "ready":
        d = llm.chat_json(
            "Ты помогаешь агенту собирать пожелания гостя к отелю на Байкале. Даны прежние пожелания и новая реплика гостя. Верни ТОЛЬКО JSON "
            '{"query": "..."}: одно связное описание всех актуальных пожеланий по-русски (до 300 символов), без пересказа диалога. '
            "Новые пожелания добавляй к прежним; если гость передумал или поправил себя, новое заменяет старое; ничего не выдумывай.",
            json.dumps({"прежние_пожелания": prev, "новая_реплика": new}, ensure_ascii=False), timeout=20)
        q = d.get("query") if isinstance(d, dict) else None
        if isinstance(q, str) and 3 <= len(q.strip()) <= 400:
            return q.strip()
    return f"{prev}. {new}"


def understand(text: str, image: str | None, wish: str = "", history: list | None = None) -> dict:
    """Склеивает текст и описание картинки в один запрос и разбирает его. Если это не поиск, готовит ответ лягушки."""
    img = describe_image(image) if image else None
    res = {"text": text.strip(), "image_given": bool(image), "image_read": img is not None, "image_note": None, "image_off": False, "image_what": None, "chat": None}
    if img:
        res["image_what"] = img["what"] or None
        if img["relevant"]:
            res["image_note"] = img["wishes"] or img["what"] or None
        else:
            res["image_off"] = True
    full = text.strip()
    if img and img["relevant"] and img["wishes"]:
        full = (full + ". " if full else "") + "Хочу как на фото: " + img["wishes"]
    st = smalltalk(text, img, bool(image))
    if st:
        res["chat"] = {"kind": st[0], **chat_reply(text, st[0], st[1])}
        res["parsed"] = {"query": text, "intents": [], "filters": {}, "text_terms": [], "parsed_by": "rules"}
        return res
    new_rules = search.parse_query(full, use_llm=False)      # дёшево: правила, только по новой реплике
    has_new = bool(new_rules["intents"] or new_rules.get("filters") or (img and img["relevant"]))
    if not has_new:
        kind, base = vague_or_off(full) if not (img and img["relevant"]) else ("image_vague", "Ква! Фото красивое, но я не уловила, что на нём для вас главное. Добавьте пару слов: тишина, баня, берег, бюджет?")
        res["chat"] = {"kind": kind, **chat_reply(text or full, kind, base)}
        res["parsed"] = new_rules
        return res
    prev = (wish or "").strip()[:600]
    merged = merge_wish(prev, full, history) if prev else full
    parsed = search.parse_query(merged, use_llm=llm.state() == "ready")   # модель разбирает запрос, только если она живая
    if not parsed["intents"] and not parsed.get("filters"):
        if img and img["relevant"]:
            kind, base = "image_vague", "Ква! Фото красивое, но я не уловила, что на нём для вас главное. Добавьте пару слов: тишина, баня, берег, бюджет?"
        else:
            kind, base = vague_or_off(text or full)
        res["chat"] = {"kind": kind, **chat_reply(text or full, kind, base)}
    res["parsed"] = parsed
    res["wish"] = merged
    res["ack"] = None
    if prev:
        before = {i["title"] for i in search.parse_query(prev, use_llm=False)["intents"]}
        added = [i["title"].lower() for i in parsed["intents"] if i["title"] not in before]
        res["ack"] = (f"Ква! Добавила к прежнему: {', '.join(added)}. Обновила тройку." if added
                      else "Ква! Учла вашу поправку и пересобрала тройку.")
    return res


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


def _wrong_kind(r: dict) -> bool:
    """Не тот тип жилья или не тот посёлок, что просил гость."""
    return any(i["feature"] in ("kind", "place") and i["verdict"] == "no" for i in r["items"])


def top(res: list[dict], n: int = 3, wish: str = "") -> list[dict]:
    res = [r for r in res if not _wrong_kind(r)]   # просили квартиру в Листвянке — отель «для массовки» не подставляем, лучше честно сказать, что ещё ищем
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
