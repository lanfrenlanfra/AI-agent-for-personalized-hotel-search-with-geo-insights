"""Поиск на естественном языке и объяснения «почему подходит / не подходит».

Запрос → намерения (характеристика + что именно хотят + важность) → оценка каждого отеля
по *итоговым фактам* с учётом их достоверности и возраста: неподтверждённое значение даёт
меньше очков, чем подтверждённое, а отсутствие данных не равно «нет» — отель получает
нейтральную ожидаемую оценку и отметку «спросим у отельера».
"""
from __future__ import annotations

import re
from datetime import date

from . import db, llm, ostrovok, places, registry
from .collectors.base import SCOPE_TITLES, today
from .collectors.geo import geo_landmarks

AMBIGUOUS = {"дом", "стол", "двор", "вход", "24", "кот", "сон", "спать", "вид на", "view", "жарк", "холодно", "тепл", "удобн кроват"}
EXTRA = {
    "landmark_view": ["вид на город", "виды на город", "вид на крыши", "вид на центр"],
    "family_friendly": ["с ребенком", "с малышом", "с детьми", "детей"],
    "pets_allowed": ["с питомцем", "с собакой", "с кошкой", "животными"],
    "quiet": ["тихий", "тихая", "тихое", "не шумно", "без шума", "для сна", "хорошо выспаться"],
    "walk_to_beach": ["недалеко от моря", "у моря", "рядом с морем", "на берегу"],
    "walk_to_center": ["недалеко от центра", "в центре", "рядом с центром", "в самом центре"],
    "walk_to_metro": ["недалеко от метро", "рядом с метро", "у метро"],
    "water_view": ["вид на нев", "вид на рек", "вид на канал", "вид на воду", "вид на залив", "вид на мор", "на неву", "на реку", "на воду"],
    "sunset_view": ["вид на закат", "закат", "закатом", "закату"],
}
THRESHOLDS = {"walk_to_metro": 10, "walk_to_center": 15, "walk_to_water": 8, "walk_to_beach": 15}
NEAR_WORDS = re.compile(r"\b(рядом|у|близко|в\s+двух\s+шагах|в\s+шаговой)\b")
MONTHS = [("январ", 1), ("феврал", 2), ("март", 3), ("апрел", 4), ("ма[йея]", 5), ("июн", 6), ("июл", 7), ("август", 8),
          ("сентябр", 9), ("октябр", 10), ("ноябр", 11), ("декабр", 12)]
SEASONS = {"зимой": [12, 1, 2], "весной": [3, 4, 5], "летом": [6, 7, 8], "осенью": [9, 10, 11]}
MONTH_TITLES = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]

# Тип размещения: слово в запросе → группа → коды типа Островка (поле kind в каталоге и карточке).
# Порядок важен: «гостевой дом» проверяется раньше «дома».
KINDS: dict[str, dict] = {
    "guesthouse": {"title": "Гостевой дом", "codes": {"GUESTHOUSE", "BNB"},
                   "rx": r"гостев\w*\s+дом\w*|гестхаус\w*|гест-хаус\w*|guest\s*-?house\w*|b\s*&\s*b|bnb"},
    "apartment": {"title": "Квартира / апартаменты", "codes": {"APARTMENT", "APART_HOTEL"},
                  "rx": r"квартир\w*|апартамент\w*|апарт-отел\w*|апарт\b|посуточн\w*|однушк\w*|двушк\w*|студи[яюи]\b|apartments?\b|apart-?hotel\w*|flat\b|studio\b"},
    "house": {"title": "Дом / коттедж", "codes": {"COTTAGE", "VILLA_AND_BUNGALOWS"},
              "rx": r"коттедж\w*|домик\w*|(?:целый|целого|отдельн\w*|частн\w*|деревянн\w*)\s+дом\w*|дом\w*\s+целиком|вилл[аыуе]\b|бунгало|шале|таунхаус\w*|cottages?\b|villas?\b|chalet\b|bungalows?\b|(?:whole|private)\s+house\b"},
    "hostel": {"title": "Хостел", "codes": {"HOSTEL"}, "rx": r"хостел\w*|hostels?\b"},
    "camping": {"title": "Кемпинг / глэмпинг", "codes": {"CAMPING", "GLAMPING"}, "rx": r"кемпинг\w*|гл[эе]мпинг\w*|палатк\w*|camping\b|glamping\b"},   # «база отдыха» на Островке бывает любого типа — не фильтруем
    "sanatorium": {"title": "Санаторий", "codes": {"SANATORIUM", "RESORT"}, "rx": r"санатори\w*|профилактори\w*|sanatorium\b"},
}
KIND_NAMES = {"HOTEL": "отель", "MINI_HOTEL": "мини-отель", "BOUTIQUE": "бутик-отель", "GUESTHOUSE": "гостевой дом", "BNB": "гостевой дом",
              "APARTMENT": "квартира", "APART_HOTEL": "апарт-отель", "COTTAGE": "коттедж", "VILLA_AND_BUNGALOWS": "вилла или бунгало",
              "HOSTEL": "хостел", "CAMPING": "кемпинг", "GLAMPING": "глэмпинг", "SANATORIUM": "санаторий", "RESORT": "курорт"}


def kind_codes(kinds: list[str] | None) -> set[str]:
    return set().union(*(KINDS[k]["codes"] for k in (kinds or []) if k in KINDS)) if kinds else set()


def kind_title(kinds: list[str] | None) -> str:
    return " или ".join(KINDS[k]["title"].lower() for k in (kinds or []) if k in KINDS)


def find_kinds(text: str) -> tuple[list[str], list[tuple[int, int]]]:
    """Какой тип жилья просит гость: ([группы], [занятые куски текста]). «Не хостел» и «без палаток» не считаются."""
    kinds: list[str] = []
    spans: list[tuple[int, int]] = []
    for key, spec in KINDS.items():
        for m in re.finditer(r"(?<![\w-])(?:" + spec["rx"] + r")", text):
            if any(not (m.end() <= a or m.start() >= b) for a, b in spans):
                continue
            spans.append((m.start(), m.end()))
            if re.search(r"(?:\bне|\bбез)\s+$", text[max(0, m.start() - 6):m.start()]):
                continue
            if key not in kinds:
                kinds.append(key)
    return kinds, spans


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).lower().replace("ё", "е")).strip()


# ───────────────────────── разбор запроса ─────────────────────────

def _stem_rx(stem: str) -> re.Pattern:
    st = norm(stem)
    if len(st) < 4 and " " not in st:
        return re.compile(r"(?<![\w-])" + re.escape(st) + r"(?![\w-])")
    return re.compile(r"(?<![\w-])" + re.escape(st))


def _intent(feature: str, spec: dict, span: tuple[int, int]) -> dict:
    return {"feature": feature, "title": spec["title"], "kind": spec["kind"], "want": True, "scope": None, "threshold": None, "months": None,
            "floor": None, "landmark": None, "weight": 1.0, "span": span}


def parse_query_rules(q: str) -> dict:
    text = norm(q)
    feats = registry.all_features()
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    found: dict[str, dict] = {}
    # 0) тип жилья и посёлок — раньше характеристик, чтобы «Малое Море» не стало «рядом с морем»
    pre_filters: dict = {}
    kinds, kspans = find_kinds(text)
    if kinds:
        pre_filters["kind"] = kinds
    where, pspans = places.find_places(text)
    if where:
        pre_filters["place"] = where
        pre_filters["place_title"] = places.title(where)          # для плашки «Я поняла так» и фраз лягушки
        pre_filters["place_where"] = places.title(where, where=True)
    taken += kspans + pspans
    # 1) достопримечательности по названию
    for lm in geo_landmarks():
        for syn in [lm["name"]] + lm.get("synonyms", []):
            m = _stem_rx(syn).search(text)
            if m and free(m.start(), m.end()):
                if "landmark_view" in feats:
                    it = found.setdefault("landmark_view", _intent("landmark_view", feats["landmark_view"], (m.start(), m.end())))
                    it["landmark"] = lm["key"]
                    it["title"] = f'Вид на «{lm["name"]}»'
                    taken.append((m.start(), m.end()))
                break
    # 2) двор / улица
    m = re.search(r"\bво?\s+двор\w*", text)
    if m and free(m.start(), m.end()) and "quiet" in feats:
        it = found.setdefault("quiet", _intent("quiet", feats["quiet"], (m.start(), m.end())))
        it["scope"] = "room:yard"
        taken.append((m.start(), m.end()))
    # 3) синонимы характеристик: длинные раньше
    cands = []
    for key, spec in feats.items():
        syns = [s for s in spec.get("synonyms", []) if norm(s) not in AMBIGUOUS] + EXTRA.get(key, []) + [spec["title"]]
        for s in syns:
            n = norm(s)
            if n in AMBIGUOUS or not n:
                continue
            for m in _stem_rx(n).finditer(text):
                cands.append((len(n), m.start(), m.end(), key))
    cands.sort(key=lambda t: (-t[0], t[1]))
    for _, a, b, key in cands:
        if free(a, b):
            if key not in found:
                found[key] = _intent(key, feats[key], (a, b))
            taken.append((a, b))
    # 4) уточнения
    for it in found.values():
        spec = feats[it["feature"]]
        a, b = it["span"]
        left = text[max(0, a - 12):a]
        if re.search(r"(без|нет|не)\s+$", left) and spec["kind"] == "bool" and it["feature"] not in ("quiet",):
            it["want"] = False
        if spec["kind"] == "bool" and spec.get("good") == "low":
            it["want"] = False
        if spec["kind"] == "number":
            near = NEAR_WORDS.search(text[max(0, a - 14):a + 2])
            it["threshold"] = THRESHOLDS.get(it["feature"], 15) * (0.7 if near else 1.0)
    for rxs in (r"\d{1,3}\s*(?:мин|минут)\w*", r"\d{1,2}\s*(?:-?(?:го|й|ом))?\s*этаж\w*", r"(?:до|дешевле|не\s+дороже|бюджет)\s*\d[\d\s]{2,8}\s*(?:₽|руб\w*|р\b)?",
                r"\d\s*(?:\+\s*)?(?:звезд\w*|\*)", r"(?:не\s+выше|выше|ниже)\s+\d+"):
        for m in re.finditer(rxs, text):
            if free(m.start(), m.end()):
                taken.append((m.start(), m.end()))
    # числа с «мин» → порог ближайшей пешей характеристики
    for m in re.finditer(r"(\d{1,3})\s*(?:мин|минут)", text):
        walks = [it for it in found.values() if it["kind"] == "number"]
        if walks:
            nearest = min(walks, key=lambda i: abs(i["span"][0] - m.start()))
            nearest["threshold"] = float(m.group(1))
    # месяцы и сезоны
    months: list[int] = []
    for stem, num in MONTHS:
        if re.search(r"(?<![\w])(?:в|на|с)?\s*" + stem + r"\w*", text):
            months.append(num)
    for word, ms in SEASONS.items():
        if word in text:
            months += ms
    if months:
        for key in ("sunset_view", "white_nights"):
            if key in found:
                found[key]["months"] = sorted(set(months))
    # этаж
    mf = re.search(r"(\d{1,2})\s*(?:-?(?:го|й|ом))?\s*этаж", text)
    if mf:
        for key in ("sunset_view", "landmark_view", "water_view"):
            if key in found:
                found[key]["floor"] = int(mf.group(1))
    # фильтры
    filters: dict = {}
    mp = re.search(r"(?:до|дешевле|не\s+дороже|бюджет)\s*(\d[\d\s]{2,8})\s*(?:₽|руб|р\b|т\.?\s*р)?", text)
    if mp:
        val = int(re.sub(r"\s", "", mp.group(1)))
        if val >= 500:
            filters["price_max"] = val
    ms_ = re.search(r"(\d)\s*(?:\+\s*)?(?:звезд|\*)", text)
    if ms_:
        filters["stars_min"] = int(ms_.group(1))
    filters.update(pre_filters)
    for it in found.values():
        spec = feats[it["feature"]]
        it["weight"] = round(0.6 + 0.8 * min(1.0, spec.get("importance", 0.3) + 0.2), 2)
    rest = text
    for a, b in sorted(taken, reverse=True):
        rest = rest[:a] + " " + rest[b:]
    words = [w for w in re.findall(r"[a-zа-я0-9-]{4,}", rest) if w not in STOP]
    intents = sorted(found.values(), key=lambda i: i["span"][0])
    for it in intents:
        it.pop("span", None)
    return {"query": q, "intents": intents, "filters": filters, "text_terms": words, "parsed_by": "rules"}


STOP = set("видом вид окна окно выше летом зимой весной осенью быстрый семейный хочу нужен нужна нужно ищу отель отеля отели гостиница номер номера хорошо чтобы можно очень который которая какой недалеко рядом быть было есть чтоб подходит подойдет для при без этаж этажа этажей мин минут рублей".split())


def parse_query_llm(q: str) -> dict | None:
    if not llm.usable():
        return None
    feats = registry.all_features()
    catalog = "\n".join(f'- {k}: {v["title"]} ({v["kind"]}{", меньше=лучше" if v.get("good") == "low" else ""})' for k, v in feats.items())
    lms = ", ".join(f'{l["key"]}={l["name"]}' for l in geo_landmarks())
    sysmsg = ("Ты разбираешь запрос гостя к поиску отелей. Верни JSON {\"intents\":[{\"feature\":ключ из списка,\"want\":true|false,"
              "\"scope\":null|\"room:yard\"|\"room:street\",\"threshold\":число минут или null,\"months\":[номера месяцев] или null,"
              "\"floor\":число или null,\"landmark\":ключ достопримечательности или null,\"weight\":0.5..2}],"
              "\"filters\":{\"price_max\":число или отсутствует,\"stars_min\":число или отсутствует}}. "
              "Только характеристики из списка, ничего не выдумывай.\nХарактеристики:\n" + catalog + "\nДостопримечательности: " + lms)
    res = llm.chat_json(sysmsg, q, timeout=30)   # не заставляем ждать: не успела модель — разбор по правилам
    if not isinstance(res, dict) or not isinstance(res.get("intents"), list):
        return None
    out = []
    for it in res["intents"]:
        key = it.get("feature")
        if key not in feats:
            continue
        spec = feats[key]
        base = _intent(key, spec, (0, 0))
        base.update({"want": bool(it.get("want", True)), "scope": it.get("scope") or None, "threshold": it.get("threshold"),
                     "months": it.get("months"), "floor": it.get("floor"), "landmark": it.get("landmark"), "weight": float(it.get("weight") or 1.0)})
        if spec["kind"] == "number" and base["threshold"] is None:
            base["threshold"] = THRESHOLDS.get(key, 15)
        if base["landmark"]:
            nm = next((l["name"] for l in geo_landmarks() if l["key"] == base["landmark"]), None)
            if nm:
                base["title"] = f"Вид на «{nm}»"
        base.pop("span", None)
        out.append(base)
    if not out:
        return None
    return {"query": q, "intents": out, "filters": res.get("filters") or {}, "text_terms": [], "parsed_by": "llm"}


def parse_query(q: str, use_llm: bool = True) -> dict:
    rules = parse_query_rules(q)
    ll = parse_query_llm(q) if use_llm and llm.state() != "loading" else None   # модель ещё грузится: не ждём её, разбираем правилами
    if ll:
        ll["text_terms"] = rules["text_terms"]
        for k in ("kind", "place", "place_title", "place_where"):        # тип жилья и посёлок — только по справочникам, не по фантазии модели
            ll["filters"].pop(k, None)
        for k, v in rules["filters"].items():
            ll["filters"].setdefault(k, v)
        return ll
    return rules


# ───────────────────────── оценка отеля ─────────────────────────

def _age_days(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return (date.fromisoformat(today()) - date.fromisoformat(s[:10])).days
    except ValueError:
        return None


def load_facts() -> dict[str, dict[str, list[dict]]]:
    out: dict[str, dict[str, list[dict]]] = {}
    for r in db.rows("SELECT * FROM facts"):
        d = dict(r)
        d["value"] = db.loads(d["value"], {})
        d["conflict"] = db.loads(d["conflict"], None) if d["conflict"] else None
        out.setdefault(d["hotel_id"], {}).setdefault(d["feature"], []).append(d)
    return out


def _match(spec: dict, it: dict, f: dict) -> float:
    """Насколько значение факта отвечает пожеланию, 0..1."""
    v = f["value"].get("v")
    det = f["value"].get("detail") or {}
    kind = spec["kind"]
    if kind == "bool":
        ok = bool(v)
        if it["feature"] == "landmark_view" and it.get("landmark") and ok:
            lms = det.get("landmarks")
            if lms is not None:
                hit = next((l for l in lms if l["key"] == it["landmark"]), None)
                ok = hit is not None
                if hit and it.get("floor") and hit["min_floor"] > it["floor"]:
                    return 0.35
        if it["feature"] == "sunset_view" and ok:
            if not it.get("months"):
                ms0 = det.get("months") or ((det.get("best_side") or {}).get("months")) or []
                if ms0 and len(ms0) < 6:
                    return 0.4 + 0.6 * len(ms0) / 6        # закат виден лишь часть года — частичное совпадение
            if it.get("months"):
                ms = det.get("months") or ((det.get("best_side") or {}).get("months"))
                if ms is not None and not set(ms) & set(it["months"]):
                    return 0.15
            if it.get("floor"):
                mf = det.get("min_floor") if det.get("min_floor") is not None else (det.get("best_side") or {}).get("min_floor")
                if mf and mf > it["floor"]:
                    return 0.35
        if it["feature"] == "white_nights" and ok and it.get("months"):
            if not set(det.get("months") or []) & set(it["months"]):
                return 0.1
        return 1.0 if ok == it["want"] else 0.0
    if kind == "score":
        s = float(v)
        m = max(0.0, min(1.0, (s - 30) / 45))
        return m if (spec.get("good", "high") == "high") == it["want"] else 1 - m
    if kind == "number":
        thr = float(it.get("threshold") or THRESHOLDS.get(it["feature"], 15))
        x = float(v)
        if spec.get("good") == "low":
            return 1.0 if x <= thr else max(0.0, 1.0 - (x - thr) / max(thr, 1.0))
        return min(1.0, x / thr)
    return 0.5


STATUS_CERT = {"confirmed": 1.0, "likely": 0.8, "weak": 0.5, "disputed": 0.45, "stale": 0.45}
PRIOR = 0.3


def evaluate_intent(spec: dict, it: dict, hotel_facts: dict[str, list[dict]]) -> dict:
    cands = hotel_facts.get(it["feature"], [])
    # выбираем только подходящую область; без указания — лучшую из всех
    if it.get("scope"):
        scoped = [f for f in cands if f["scope"] == it["scope"]]
        if scoped:
            cands = scoped
    best = None
    for f in cands:
        m = _match(spec, it, f)
        cert = max(0.15, f["confidence"]) * STATUS_CERT.get(f["status"], 0.6)
        key = (m * cert, m, cert)
        if best is None or key > best[0]:
            best = (key, f, m, cert)
    base = {"feature": it["feature"], "title": it["title"], "weight": it["weight"], "want": it["want"]}
    if best is None:
        return {**base, "verdict": "unknown", "match": None, "expected": PRIOR, "text": "Данных пока нет — спросим у отельера", "status": "unknown",
                "confidence": 0.0, "age_days": None, "scope": None, "scope_title": None}
    _, f, m, cert = best
    expected = cert * m + (1 - cert) * PRIOR
    if f["status"] == "disputed":
        verdict = "disputed"
    elif m >= 0.7:
        verdict = "ok" if f["status"] in ("confirmed", "likely") else "maybe"
    elif m >= 0.35:
        verdict = "partial"
    else:
        verdict = "no" if cert >= 0.4 else "unknown"
    hf = next((x for x in hotel_facts.get(it["feature"], []) if x["scope"] == "hotel"), None)
    if verdict == "ok" and ((f.get("conflict")) or (hf and hf is not f and hf.get("conflict"))):
        verdict = "maybe"            # источники расходятся, пусть и слабо — не обещаем
    if f["status"] == "stale":
        verdict = "stale" if verdict in ("ok", "maybe") else verdict
    return {**base, "verdict": verdict, "match": round(m, 2), "expected": round(expected, 3), "text": f["value"].get("summary") or "",
            "status": f["status"], "confidence": round(f["confidence"], 2), "age_days": _age_days(f["observed_at"]),
            "scope": f["scope"], "scope_title": SCOPE_TITLES.get(f["scope"], f["scope"]), "computed": bool(spec.get("computed")), "conflict": (f["conflict"] or {}).get("text") if f["conflict"] else None}


def score_hotel(hotel: dict, intents: list[dict], filters: dict, terms: list[str], facts: dict[str, list[dict]]) -> dict:
    feats = registry.all_features()
    items = [evaluate_intent(feats[it["feature"]], it, facts) for it in intents if it["feature"] in feats]
    # цена и звёзды — жёсткие данные карточки
    extra: list[dict] = []
    if filters.get("price_max") and hotel.get("price_from"):
        ok = hotel["price_from"] <= filters["price_max"]
        extra.append({"feature": "price", "title": f'Цена до {int(filters["price_max"])} ₽', "weight": 1.2, "verdict": "ok" if ok else "no", "match": 1.0 if ok else 0.0,
                      "expected": 1.0 if ok else 0.0, "text": f'От {int(hotel["price_from"])} ₽ за ночь (по данным каталога)', "status": "confirmed", "confidence": 0.6, "age_days": _age_days(hotel.get("collected_at")),
                      "scope": None, "scope_title": None})
    if filters.get("stars_min") and hotel.get("stars") is not None:
        ok = (hotel["stars"] or 0) >= filters["stars_min"]
        extra.append({"feature": "stars", "title": f'От {filters["stars_min"]}★', "weight": 0.8, "verdict": "ok" if ok else "no", "match": 1.0 if ok else 0.0,
                      "expected": 1.0 if ok else 0.0, "text": f'{hotel["stars"]}★', "status": "confirmed", "confidence": 0.7, "age_days": None, "scope": None, "scope_title": None})
    hard_miss = False
    if filters.get("kind") and hotel.get("kind"):
        code = str(hotel["kind"]).upper()
        ok = code in kind_codes(filters["kind"])
        hard_miss = not ok
        extra.append({"feature": "kind", "title": kind_title(filters["kind"]).capitalize(), "weight": 1.5, "verdict": "ok" if ok else "no", "match": 1.0 if ok else 0.0,
                      "expected": 1.0 if ok else 0.0, "text": f"Это {KIND_NAMES.get(code, code.lower())} (по данным Островка)", "status": "confirmed", "confidence": 0.9,
                      "age_days": None, "scope": None, "scope_title": None})
    if filters.get("place"):
        inside = places.matches(filters["place"], hotel.get("place"), hotel.get("lat"), hotel.get("lon"))
        if inside is not None:
            hard_miss = hard_miss or not inside
            extra.append({"feature": "place", "title": filters.get("place_title") or places.title(filters["place"]), "weight": 1.5,
                          "verdict": "ok" if inside else "no", "match": 1.0 if inside else 0.0, "expected": 1.0 if inside else 0.0,
                          "text": "По адресу и координатам Островка" if inside else (hotel.get("address") or "Другое место на Байкале"),
                          "status": "confirmed", "confidence": 0.9, "age_days": None, "scope": None, "scope_title": None})
    items += extra
    text_bonus = 0.0
    if terms:
        blob = norm(" ".join([hotel.get("name") or "", hotel.get("address") or "", (hotel.get("description") or "")[:1500]]))
        hits = [t for t in terms if t[:5] in blob]
        text_bonus = 0.12 * len(hits) / len(terms)
        if hits:
            items.append({"feature": "text", "title": "Слова из запроса в описании", "weight": 0.0, "verdict": "ok", "match": 1.0, "expected": 1.0,
                          "text": "Найдено: " + ", ".join(hits[:5]), "status": "confirmed", "confidence": 0.5, "age_days": None, "scope": None, "scope_title": None})
    wsum = sum(i["weight"] for i in items) or 1.0
    score = sum(i["weight"] * i["expected"] for i in items) / wsum
    score = min(1.0, score + text_bonus)
    if hard_miss:
        score *= 0.3          # гость просил квартиру или Листвянку — отель с баней в другом посёлке это не заменит
    pro = [i for i in items if i["verdict"] == "ok"]
    con = [i for i in items if i["verdict"] in ("no", "partial")]
    unk = [i for i in items if i["verdict"] in ("unknown", "maybe", "disputed", "stale")]
    if not intents and not filters:
        label = "—"
    elif hard_miss or (con and not pro):
        label = "Не подходит"
    elif con:
        label = "Частично"
    elif unk and not pro:
        label = "Мало данных"
    elif unk:
        label = "Подходит, есть вопросы"
    else:
        label = "Подходит"
    return {"score": round(score * 100), "label": label, "items": items, "why": explain(items)}


def explain(items: list[dict]) -> str:
    parts = []
    ok = [i for i in items if i["verdict"] == "ok" and i["feature"] != "text"]
    no = [i for i in items if i["verdict"] == "no"]
    part = [i for i in items if i["verdict"] == "partial"]
    unk = [i for i in items if i["verdict"] in ("unknown", "maybe", "disputed", "stale")]
    if ok:
        parts.append("Подходит: " + "; ".join(f'{i["title"].lower()} — {i["text"].rstrip(".").lower() if i["text"] else "да"}' for i in ok[:3]))
    if part:
        parts.append("Частично: " + "; ".join(f'{i["title"].lower()} — {i["text"].rstrip(".").lower()}' for i in part[:3]))
    if no:
        parts.append("Не подходит: " + "; ".join(f'{i["title"].lower()} — {i["text"].rstrip(".").lower()}' for i in no[:3]))
    if unk:
        parts.append("Неясно: " + "; ".join(i["title"].lower() + (" (спорные данные)" if i["verdict"] == "disputed" else " (данные устарели)" if i["verdict"] == "stale" else "") for i in unk[:3]))
    return ". ".join(parts)


def flatten_description(desc) -> str:
    if isinstance(desc, str):
        return desc
    parts: list[str] = []
    for blk in desc or []:
        if isinstance(blk, dict):
            if blk.get("title"):
                parts.append(str(blk["title"]))
            parts += [str(p) for p in blk.get("paragraphs", [])]
        else:
            parts.append(str(blk))
    return "\n".join(parts)


def hotel_row(r) -> dict:
    d = dict(r)
    photos = db.loads(d.get("photos"), {}) or {}
    card = db.loads(d.get("card"), {}) or {}
    first = (photos.get("images") or [None])[0]
    d["photo"] = ostrovok.photo_url(first, "card") if first and first.startswith("/t/") else first
    d["description"] = flatten_description(card.get("description"))
    d["kind"] = card.get("kind")
    return d


def search(q: str, limit: int = 30, parsed: dict | None = None) -> dict:
    parsed = parsed or parse_query(q)
    facts = load_facts()
    res = []
    kinds = {r["master_id"]: r["kind"] for r in db.rows("SELECT master_id, kind FROM catalog WHERE kind IS NOT NULL")} if (parsed.get("filters") or {}).get("kind") else {}
    for r in db.rows("SELECT * FROM hotels"):
        h = hotel_row(r)
        h["kind"] = h.get("kind") or kinds.get(h.get("master_id"))
        if (parsed.get("filters") or {}).get("place"):
            h["place"] = places.place_of(h.get("master_id"))
        sc = score_hotel(h, parsed["intents"], parsed["filters"], parsed["text_terms"], facts.get(h["id"], {}))
        known = [i for i in sc["items"] if i["status"] != "unknown" and i["feature"] != "text"]
        res.append({"id": h["id"], "master_id": h.get("master_id"), "name": h["name"], "stars": h["stars"], "rating": h["rating"], "reviews": h["reviews_count"], "price_from": h["price_from"],
                    "lat": h["lat"], "lon": h["lon"], "photo": h["photo"], "address": h["address"], "url": h["url"], **sc,
                    "known": len(known), "asked": len(sc["items"])})
    res.sort(key=lambda x: (-x["score"], -(x["rating"] or 0)))
    out = res[:limit]
    pins, pin_master = set(parsed.get("pinned") or []), {str(x) for x in parsed.get("pinned_catalog") or []}
    if pins or pin_master:                       # отель, узнанный по фото или названию, не должен потеряться за лимитом
        out += [r for r in res[limit:] if r["id"] in pins or str(r.get("master_id")) in pin_master]
    return {"parsed": {k: v for k, v in parsed.items() if k != "text_terms"}, "total": len(res), "results": out}


# ───────────────────────── сравнение ─────────────────────────

def compare(hotel_ids: list[str], features: list[str] | None = None) -> dict:
    feats = registry.all_features()
    facts = load_facts()
    keys = features or [k for k, s in feats.items() if any(k in facts.get(h, {}) for h in hotel_ids)]
    rows = []
    for k in keys:
        spec = feats.get(k)
        if not spec:
            continue
        cells = []
        for h in hotel_ids:
            fl = facts.get(h, {}).get(k, [])
            f = next((x for x in fl if x["scope"] == "hotel"), fl[0] if fl else None)
            if not f:
                cells.append(None)
                continue
            cells.append({"value": f["value"].get("v"), "summary": f["value"].get("summary"), "status": f["status"], "confidence": round(f["confidence"], 2),
                          "age_days": _age_days(f["observed_at"]), "scope": f["scope"]})
        vals = [c["value"] for c in cells if c]
        differs = len({str(v) for v in vals}) > 1 or any(c is None for c in cells) and len(vals) > 0
        best = None
        known = [(i, c) for i, c in enumerate(cells) if c and isinstance(c["value"], (int, float)) and not isinstance(c["value"], bool)]
        if len(known) >= 2 and spec["kind"] in ("score", "number"):
            pick = max if spec.get("good", "high") == "high" else min
            best = pick(known, key=lambda t: t[1]["value"])[0]
        elif spec["kind"] == "bool":
            yes = [i for i, c in enumerate(cells) if c and c["value"] is True]
            if yes and len(yes) < len([c for c in cells if c]):
                best = yes[0] if len(yes) == 1 else None
        rows.append({"feature": k, "title": spec["title"], "group": spec["group"], "kind": spec["kind"], "unit": spec.get("unit"), "cells": cells, "differs": differs, "best": best})
    rows.sort(key=lambda r: (not r["differs"], r["group"]))
    return {"hotels": hotel_ids, "rows": rows}
