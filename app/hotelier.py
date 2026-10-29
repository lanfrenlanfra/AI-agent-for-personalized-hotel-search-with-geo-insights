"""Диалог с отельером: вопросы, разбор свободных ответов по полям, имитация, подтверждение значений.

Агент спрашивает только то, чего не узнал сам: нет данных, источники спорят, данные
устарели или вычисление ненадёжно. Ответ — свободный текст; он раскладывается по
характеристикам и областям («окна на запад», «номера во двор», «с 5-го этажа»).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from . import db, llm, registry
from .collectors import reviews as rv
from .collectors.base import SCOPE_TITLES, Obs, now_iso, save_observations, today
from .reconcile import reconcile_hotel

DIRECTIONS = [
    (r"юго-?запад", "room:southwest"), (r"северо-?запад", "room:northwest"), (r"юго-?восток", "room:southeast"), (r"северо-?восток", "room:northeast"),
    (r"запад|на\s+закат", "room:west"), (r"восток|на\s+рассвет", "room:east"), (r"\bсевер", "room:north"), (r"\bюг\b|\bюж", "room:south"),
    (r"во\s*двор|двор[аеу]?\b|внутренн\w+\s+двор", "room:yard"),
    (r"на\s+(улицу|проспект|набережн\w+|дорогу|магистраль)|улиц\w+\s+сторон|фасад\w*\s+на\s+улиц", "room:street"),
    (r"на\s+(неву|реку|канал|воду|залив|фонтанку|мойку)|с\s+видом\s+на\s+(неву|реку|канал|воду|залив)", "room:water"),
]
YES = re.compile(r"^\W*(да|есть|конечно|разумеется|так точно|имеется|безусловно|принимаем|разрешаем)\b")
NO = re.compile(r"^\W*(нет|к сожалению,?\s+нет|не\s+(принимаем|разрешаем|работает|круглосуточно|предусмотрен\w*)|отсутству\w+)\b")
FLOOR = re.compile(r"(?:с|со|начиная\s+с|от)\s*(\d{1,2})\s*(?:-?(?:го|ого|й))?\s*этаж")
FLOOR_RANGE = re.compile(r"(\d{1,2})\s*[-–]\s*(\d{1,2})\s*этаж")
MINUTES = re.compile(r"(\d{1,3})\s*(?:мин|минут)")
SENT = re.compile(r"(?<=[.!?;])\s+|\r?\n+|,\s*(?:а|но|зато)\s+")


# ───────────────────────── вопросы ─────────────────────────

def _fact_rows(hotel_id: str) -> list[dict]:
    return [dict(r) for r in db.rows("SELECT * FROM facts WHERE hotel_id=?", (hotel_id,))]


def question_text(spec: dict, reason: str, fact: dict | None) -> str:
    base = spec.get("question") or f"Как обстоит дело с характеристикой «{spec['title']}»?"
    if reason in ("disputed", "conflict") and fact and fact.get("conflict"):
        c = db.loads(fact["conflict"], {})
        return f'{c.get("text", "Источники расходятся")}. Как на самом деле? {base}'
    if reason == "stale" and fact:
        v = db.loads(fact["value"], {})
        return f'Данные могли устареть (последнее подтверждение — {fact["observed_at"]}): «{v.get("summary")}». Это по-прежнему так? {base}'
    if reason == "weak" and fact:
        v = db.loads(fact["value"], {})
        return f'Мы не уверены: «{v.get("summary")}». {base}'
    return base


def plan_questions(hotel_id: str, limit: int = 6) -> list[dict]:
    feats = registry.all_features()
    facts = {(f["feature"], f["scope"]): f for f in _fact_rows(hotel_id)}
    cands = []
    for key, spec in feats.items():
        imp = float(spec.get("importance", 0))
        if imp <= 0:
            continue
        f = facts.get((key, "hotel"))
        reason, score = None, 0.0
        if f is None:
            if imp >= 0.5 and not spec.get("computed"):
                reason, score = "unknown", imp * 0.8
        else:
            conflict = db.loads(f["conflict"], None) if f.get("conflict") else None
            if f["status"] == "disputed":
                reason, score = "disputed", imp * 1.6 + 0.4
            elif conflict and imp >= 0.3:
                reason, score = "conflict", imp * 1.3 + 0.2
            elif f["status"] == "stale" and imp >= 0.4:
                reason, score = "stale", imp * 1.0
            elif f["status"] == "weak" and imp >= 0.6:
                reason, score = "weak", imp * 0.9
        if reason:
            cands.append((score, key, reason, f))
    cands.sort(key=lambda t: -t[0])
    return [{"feature": k, "reason": r, "text": question_text(feats[k], r, f)} for _, k, r, f in cands[:limit]]


def generate_questions(hotel_id: str, limit: int = 6) -> list[int]:
    ids = []
    existing = {r["feature"] for r in db.rows("SELECT feature FROM questions WHERE hotel_id=? AND status='open'", (hotel_id,))}
    planned = plan_questions(hotel_id, limit)
    keep = {p["feature"] for p in planned}
    with db.tx() as c:
        # вопросы, ставшие неактуальными (значение подтверждено), закрываем
        for feat in existing - keep:
            c.execute("UPDATE questions SET status='dismissed' WHERE hotel_id=? AND feature=? AND status='open'", (hotel_id, feat))
        for p in planned:
            if p["feature"] in existing:
                c.execute("UPDATE questions SET text=?, reason=? WHERE hotel_id=? AND feature=? AND status='open'", (p["text"], p["reason"], hotel_id, p["feature"]))
                continue
            cur = c.execute("INSERT INTO questions(hotel_id, feature, text, reason, created_at) VALUES(?,?,?,?,?)", (hotel_id, p["feature"], p["text"], p["reason"], now_iso()))
            ids.append(cur.lastrowid)
    return ids


# ───────────────────────── разбор ответа ─────────────────────────

def _scope_of(sentence: str) -> str:
    s = sentence.lower().replace("ё", "е")
    for rx, scope in DIRECTIONS:
        if re.search(rx, s):
            return scope
    return "hotel"


def _numbers(sentence: str) -> dict:
    s = sentence.lower()
    d: dict[str, Any] = {}
    m = FLOOR_RANGE.search(s)
    if m:
        d["floor_from"], d["floor_to"] = int(m.group(1)), int(m.group(2))
    else:
        m = FLOOR.search(s)
        if m:
            d["floor_from"] = int(m.group(1))
    return d


def _score_for(spec: dict, pol: bool) -> int:
    return 85 if pol else 20


def parse_answer_rules(text: str, question_feature: str | None, simulated: bool = False) -> list[Obs]:
    feats = registry.all_features()
    src = "hotelier_sim" if simulated else "hotelier"
    conf = 0.55 if simulated else 0.9
    out: dict[tuple[str, str], Obs] = {}
    sentences = [s.strip() for s in SENT.split(text) if len(s.strip()) > 1]
    for sent in sentences:
        scope = _scope_of(sent)
        nums = _numbers(sent)
        pseudo = [{"id": "ans", "date": today(), "plus": sent, "minus": "", "text": ""}]
        for key, spec in feats.items():
            if spec["kind"] == "number":
                if key == question_feature:
                    m = MINUTES.search(sent.lower())
                    if m:
                        out[(key, scope)] = Obs(key, {"v": int(m.group(1)), "summary": f"По словам отельера: {sent[:140]}", "detail": {"scope": scope}},
                                                src, conf, today(), "Ответ отельера (свободный текст)", [{"quote": sent[:200], "kind": "hotelier"}], scope, simulated)
                continue
            ms = rv.extract_mentions(pseudo, spec)
            pol = None
            if ms:
                pol = ms[0]["polarity"]
            elif key == question_feature:
                low = sent.lower()
                if YES.search(low):
                    pol = True
                elif NO.search(low):
                    pol = False
                elif len(sentences) == 1:
                    pol = not rv.NEG_WORDS.search(low)
            if pol is None:
                continue
            value = {"v": pol if spec["kind"] == "bool" else _score_for(spec, pol),
                     "summary": f"По словам отельера: {sent[:160]}", "detail": {"scope": scope, **nums}}
            out[(key, scope)] = Obs(key, value, src, conf, today(), "Ответ отельера (свободный текст)", [{"quote": sent[:200], "kind": "hotelier"}], scope, simulated)
    return list(out.values())


def parse_answer_llm(text: str, question_feature: str | None, simulated: bool) -> list[Obs] | None:
    if not llm.usable():
        return None
    feats = registry.all_features()
    schema = {k: {"title": v["title"], "kind": v["kind"], "hint": v["description"][:100]} for k, v in feats.items()}
    system = ("Ты разбираешь ответ отельера на вопросы о гостинице и раскладываешь его по характеристикам. "
              "Верни ТОЛЬКО JSON-массив объектов {feature, value, scope, floor_from, quote}. "
              "feature — ключ из списка; value: true/false для bool, число 0-100 для score, число для number; "
              f"scope: hotel | {' | '.join(k for k in SCOPE_TITLES if k != 'hotel')}; floor_from — число или null; quote — дословная цитата ≤160 символов. "
              "Не выдумывай: включай только то, что явно сказано. Характеристики: " + str(schema))
    data = llm.chat_json(system, f"Основной вопрос касался: {question_feature}\nОтвет отельера: {text}")
    if not isinstance(data, list):
        return None
    src = "hotelier_sim" if simulated else "hotelier"
    conf = 0.55 if simulated else 0.9
    res = []
    for it in data:
        if not isinstance(it, dict) or it.get("feature") not in feats:
            continue
        spec = feats[it["feature"]]
        val = it.get("value")
        if spec["kind"] == "bool" and not isinstance(val, bool):
            continue
        if spec["kind"] in ("score", "number") and not isinstance(val, (int, float)):
            continue
        scope = it.get("scope") if it.get("scope") in SCOPE_TITLES else "hotel"
        detail = {"scope": scope}
        if isinstance(it.get("floor_from"), int):
            detail["floor_from"] = it["floor_from"]
        quote = str(it.get("quote") or text)[:200]
        res.append(Obs(it["feature"], {"v": val, "summary": f"По словам отельера: {quote[:160]}", "detail": detail}, src, conf, today(),
                       "Ответ отельера, разобран языковой моделью", [{"quote": quote, "kind": "hotelier"}], scope, simulated))
    return res or None


def parse_answer(text: str, question_feature: str | None, simulated: bool = False) -> tuple[list[Obs], str]:
    o = parse_answer_llm(text, question_feature, simulated)
    if o:
        return o, "llm"
    return parse_answer_rules(text, question_feature, simulated), "rules"


def answer_question(question_id: int, text: str, simulated: bool = False) -> dict:
    q = db.row("SELECT * FROM questions WHERE id=?", (question_id,))
    if not q:
        raise KeyError("вопрос не найден")
    obs, how = parse_answer(text, q["feature"], simulated)
    changes = save_observations(q["hotel_id"], obs)
    parsed = [{"feature": o.feature, "scope": o.scope, "scope_title": SCOPE_TITLES.get(o.scope, o.scope), "value": o.value["v"],
               "floor_from": (o.value.get("detail") or {}).get("floor_from"), "quote": o.evidence[0]["quote"] if o.evidence else ""} for o in obs]
    with db.tx() as c:
        c.execute("UPDATE questions SET status='answered', answer_text=?, answered_at=?, parsed=? WHERE id=?", (text, now_iso(), db.dumps({"how": how, "fields": parsed}), question_id))
    reconcile_hotel(q["hotel_id"])
    generate_questions(q["hotel_id"])
    return {"parsed_by": how, "fields": parsed, "changes": changes, "simulated": simulated}


def confirm_fact(hotel_id: str, feature: str, scope: str, value: Any, simulated: bool = False) -> dict:
    spec = registry.get(feature)
    if not spec:
        raise KeyError("неизвестная характеристика")
    src = "hotelier_sim" if simulated else "hotelier"
    if spec["kind"] == "bool":
        value = bool(value)
    elif spec["kind"] in ("score", "number"):
        value = int(round(float(value)))
    summary = {"bool": "Отельер подтвердил: " + ("да" if value is True else "нет"), "score": f"Отельер оценил: {value}/100", "number": f"Отельер указал: {value}"}.get(spec["kind"], "Отельер указал значение")
    o = Obs(feature, {"v": value, "summary": summary, "detail": {"scope": scope, "via": "cabinet"}}, src, 0.95 if not simulated else 0.55, today(),
            "Подтверждено или исправлено в кабинете отельера", [{"quote": summary, "kind": "hotelier"}], scope, simulated)
    changes = save_observations(hotel_id, [o])
    reconcile_hotel(hotel_id)
    generate_questions(hotel_id)
    return {"changes": changes}


# ───────────────────────── имитатор отельера ─────────────────────────

def _rand(hotel_id: str, feature: str) -> float:
    h = hashlib.sha1(f"{hotel_id}:{feature}".encode()).digest()
    return int.from_bytes(h[:4], "big") / 2**32


def _simulate_rules(hotel_id: str, feature: str) -> str:
    spec = registry.get(feature) or {"title": feature, "kind": "bool"}
    f = db.row("SELECT * FROM facts WHERE hotel_id=? AND feature=? AND scope='hotel'", (hotel_id, feature))
    val = db.loads(f["value"], {}) if f else {}
    v = val.get("v")
    r = _rand(hotel_id, feature)
    deviate = r < 0.15            # иногда отельер отвечает не как мы думали — чтобы проверять разбор расхождений
    detail = val.get("detail") or {}
    title = spec["title"].lower()
    kind = spec["kind"]
    if v is None:
        v = r > 0.45
    elif deviate and kind == "bool":
        v = not v
    if feature == "sunset_view":
        b = (detail.get("best_side") or {})
        side = b.get("side", "запад")
        fl = b.get("min_floor", 5)
        return (f"Закат отлично виден из номеров на {side} начиная с {fl}-го этажа." if v else "Закат из окон не виден — напротив высокие дома.")
    if feature == "landmark_view":
        lms = detail.get("landmarks") or []
        if v and lms:
            m = lms[0]
            return f'Из номеров на {m["side"]} с {m["min_floor"]}-го этажа видно: {m["name"]}.'
        return "Достопримечательностей из окон не видно." if not v else "Из верхних этажей виден центр города."
    if feature == "water_view":
        return "Да, из номеров на воду видно реку." if v else "Вид на воду есть только с крыши, из номеров не видно."
    if feature == "quiet":
        if kind == "score" and isinstance(v, (int, float)) and v >= 55 and not deviate:
            return "Номера во двор очень тихие, ночью почти ничего не слышно. На улицу чуть громче из-за машин."
        return "Тихими наши номера не назовёшь: рядом оживлённая улица, ночью слышен трафик. Во двор спокойнее."
    if feature == "elevator":
        return "Да, лифт есть и работает." if v else "Лифта нет, подъём пешком, багаж поможем донести."
    if feature == "reception_24h":
        return "Администратор на стойке круглосуточно, заселиться можно в любое время." if v else "Стойка работает до 22:00, ночное заселение по договорённости."
    if feature == "pets_allowed":
        return "Да, принимаем с небольшими животными до 10 кг, доплата 500 рублей за ночь." if v else "К сожалению, с животными размещение не предусмотрено."
    if feature == "parking":
        return "Да, во дворе есть парковка, лучше забронировать место заранее." if v else "Нет, своей парковки нет, можно оставить машину на платной парковке рядом."
    if feature == "soundproof_windows":
        return "Да, во всех номерах стеклопакеты с шумоизоляцией." if v else "Нет, окна обычные деревянные."
    if feature == "blackout_curtains":
        return "Да, в номерах плотные шторы блэкаут, комната полностью затемняется." if v else "Шторы обычные, в белые ночи светло."
    if feature == "construction_nearby":
        return "Да, напротив ведётся ремонт фасада, работы идут с 9 до 18 часов до конца года." if v else "Нет, рядом тихо, строек нет."
    if feature == "wifi_quality":
        return "Wi-Fi быстрый, около 100 Мбит/с, удалённо работать комфортно." if (isinstance(v, (int, float)) and v >= 60) or v is True else "Wi-Fi слабоват в дальних номерах."
    if kind == "bool":
        return f"Да, {title} у нас есть." if v else f"Нет, к сожалению, {title} у нас нет."
    if kind == "score":
        return f"На {title} гости обычно не жалуются." if (isinstance(v, (int, float)) and v >= 55) else f"По параметру «{title}» есть замечания гостей, мы работаем над этим."
    if kind == "number" and isinstance(v, (int, float)):
        return f"Идти примерно {int(v)} минут."
    return "Уточним у администратора и сообщим."


def simulate_answer(hotel_id: str, feature: str) -> str:
    if llm.usable():
        h = db.row("SELECT name FROM hotels WHERE id=?", (hotel_id,))
        spec = registry.get(feature) or {}
        f = db.row("SELECT value, status FROM facts WHERE hotel_id=? AND feature=? AND scope='hotel'", (hotel_id, feature))
        known = db.loads(f["value"], {}).get("summary") if f else "ничего не известно"
        txt = llm.chat([
            {"role": "system", "content": "Ты — администратор отеля. Отвечай на вопрос коротко (1–3 предложения), по-русски, как живой человек, без списков. "
                                           "Можешь уточнять детали (этажи, стороны, цены). Иногда слегка не соглашайся с тем, что о тебе известно."},
            {"role": "user", "content": f'Отель «{h["name"] if h else hotel_id}». Что известно системе: {known}. Вопрос: {spec.get("question") or spec.get("title")}'},
        ], temperature=0.7)
        if txt:
            return txt.strip()
    return _simulate_rules(hotel_id, feature)
