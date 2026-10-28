"""Посёлки и места Байкала в запросе гостя: «квартира в Листвянке», «на Ольхоне», «Малое Море».

Объект подходит, если посёлок указан у него в каталоге Островка (поле place). У трети объектов посёлка нет,
тогда решаем по координатам: центр посёлка и радиус посчитаны по самим объектам каталога (медиана и 90-й
перцентиль расстояний). Ольхон и Малое Море — группы посёлков; для острова есть ещё проверка «остров, а не берег напротив».
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache

# key: (название, «где» для фраз, регулярка по нормализованному тексту (ё→е, нижний регистр), коды place, центр (lat, lon), радиус, км)
_P = [
    ("olkhon", "Ольхон", "на Ольхоне", r"ольхон\w*", {"Khuzhir", "Kharantsy", "Khalgai"}, None, 0),
    ("maloe_more", "Малое Море", "на Малом Море", r"мал\w*\s+мор[еяю]\w*",
     {"Sakhyurta", "Kurkut", "Shida", "Sarma", "Shara-Togot", "Kurma", "Haday"}, (53.05, 106.86), 14),
    ("north", "Северный Байкал", "на севере Байкала", r"север\w*\s+байкал\w*|север\w*\s+побережь\w*",
     {"Severobaykalsk", "Nizhneangarsk", "Baykalskoye"}, (55.63, 109.34), 25),
    ("khuzhir", "Хужир", "в Хужире", r"хужир\w*", {"Khuzhir"}, (53.1936, 107.3436), 2.0),
    ("listvyanka", "Листвянка", "в Листвянке", r"листвянк\w*", {"Listvyanka"}, (51.8604, 104.8620), 4.0),
    ("baikalsk", "Байкальск", "в Байкальске", r"байкальск(?:е|а|у|ом)?(?![\w-])", {"Baikalsk"}, (51.5215, 104.1365), 2.0),
    ("severobaykalsk", "Северобайкальск", "в Северобайкальске", r"северобайкальск\w*", {"Severobaykalsk"}, (55.6360, 109.3384), 4.5),
    ("nizhneangarsk", "Нижнеангарск", "в Нижнеангарске", r"нижнеангарск\w*", {"Nizhneangarsk"}, (55.7926, 109.5863), 3.0),
    ("goloustnoe", "Большое Голоустное", "в Большом Голоустном", r"(?:больш\w*\s+)?голоустн\w*", {"Bolshoye Goloustnoye"}, (52.0321, 105.4083), 3.0),
    ("goryachinsk", "Горячинск", "в Горячинске", r"горячинск\w*", {"Goryachinsk"}, (52.9847, 108.3024), 1.7),
    ("sludyanka", "Слюдянка", "в Слюдянке", r"слюдянк\w*", {"Sludyanka", "Sukhoy Ruchey"}, (51.6605, 103.7168), 3.4),
    ("ust_barguzin", "Усть-Баргузин", "в Усть-Баргузине", r"усть[-\s]?баргузин\w*", {"Ust-Barguzin"}, (53.4142, 109.0196), 3.2),
    ("kurkut", "Куркут", "в Куркуте", r"куркут\w*", {"Kurkut"}, (53.0289, 106.8370), 2.3),
    ("sakhyurta", "Сахюрта (МРС)", "в Сахюрте", r"сахюрт\w*|мрс(?![\w-])", {"Sakhyurta"}, (53.0203, 106.8801), 1.7),
    ("maksimikha", "Максимиха", "в Максимихе", r"максимих\w*", {"Maksimikha"}, (53.2657, 108.7485), 2.3),
    ("utulik", "Утулик", "в Утулике", r"утулик\w*", {"Utulik"}, (51.5448, 104.0665), 2.7),
    ("kultuk", "Култук", "в Култуке", r"култук\w*", {"Kultuk"}, (51.7300, 103.7149), 3.7),
    ("vidrino", "Выдрино", "в Выдрино", r"выдрин\w*", {"Vidrino", "Rechka Vydrino", "Mamay"}, (51.4449, 104.6479), 6.0),
    ("turka", "Турка", "в Турке", r"турк(?:а|е|у|ой)(?![\w-])", {"Turka"}, (52.9438, 108.2251), 6.0),
    ("sarma", "Сарма", "в Сарме", r"сарм(?:а|е|у|ой)(?![\w-])", {"Sarma"}, (53.0829, 106.7994), 1.8),
    ("enkheluk", "Энхэлук", "в Энхэлуке", r"энх[эе]лук\w*", {"Novyy Enkheluk"}, (52.4853, 106.9680), 2.5),
    ("tankhoy", "Танхой", "в Танхое", r"танхо[йяею]\w*", {"Tankhoy"}, (51.5591, 105.1264), 3.0),
    ("solzan", "Солзан", "в Солзане", r"солзан\w*", {"Solzan"}, (51.4988, 104.2159), 1.5),
    ("kharantsy", "Харанцы", "в Харанцах", r"харанц\w*", {"Kharantsy"}, (53.2247, 107.4131), 1.8),
    ("gremyachinsk", "Гремячинск", "в Гремячинске", r"гремячинск\w*", {"Gremyachinsk"}, (52.7927, 107.9767), 1.5),
    ("buguldeyka", "Бугульдейка", "в Бугульдейке", r"бугульдейк\w*", {"Bugul'deyka"}, (52.5398, 106.0613), 1.5),
    ("bolshie_koty", "Большие Коты", "в Больших Котах", r"больш\w*\s+кот(?:ы|ах|ов|ам)(?![\w-])", {"Bolshiye Koty"}, (51.9048, 105.0772), 1.5),
    ("bolshaya_rechka", "Большая Речка", "в Большой Речке", r"больш\w*\s+речк\w*", {"Bolshaya Rechka"}, (51.9541, 104.7542), 1.5),
    ("port_baikal", "Порт Байкал", "в Порту Байкал", r"порт\w*\s+байкал\w*", {"Baykal"}, (51.8755, 104.7984), 1.6),
    ("kurma", "Курма", "в Курме", r"курм(?:а|е|у|ой)(?![\w-])", {"Kurma"}, (53.1798, 106.9663), 1.5),
]
PLACES: dict[str, dict] = {k: {"title": t, "where": w, "rx": rx, "codes": c, "center": ctr, "r_km": r} for k, t, w, rx, c, ctr, r in _P}

# Латиница: как посёлок записан в каталоге Островка (Listvyanka, Khuzhir…) плюс привычные варианты.
# Для групп и неоднозначных кодов («Baykal» — это и порт, и озеро) латиницу задаём явно.
_LATIN_EXTRA = {
    "olkhon": ["olkhon", "olhon", "olchon"], "maloe_more": [r"mal[oa]y?e\s+more", r"small\s+sea"], "north": [r"north(?:ern)?\s+baika?l"],
    "listvyanka": ["listvianka", "listvjanka"], "khuzhir": ["huzhir", "khuzir"], "baikalsk": ["baykalsk"], "severobaykalsk": ["severobaikalsk"],
    "port_baikal": [r"port\s+baika?l", r"port\s+baykal"], "sakhyurta": ["sahyurta", "mrs"], "goloustnoe": [r"(?:bolsh\w*\s+)?goloustn\w*"],
    "ust_barguzin": [r"ust[-\s]?barguzin"],
}
_LATIN_SKIP = {"port_baikal", "olkhon", "maloe_more", "north"}          # их коды — общие слова или чужие посёлки


def _latin(key: str, p: dict) -> list[str]:
    out = list(_LATIN_EXTRA.get(key, []))
    if key not in _LATIN_SKIP:
        out += [re.escape(c.lower()).replace("\\ ", r"[\s-]+").replace("\\-", r"[\s-]?") for c in p["codes"]]
    return out


for _k, _p in PLACES.items():
    _lat = _latin(_k, _p)
    _p["rx_all"] = _p["rx"] + ("|(?:" + "|".join(_lat) + r")(?![\w-])" if _lat else "")


def _km(a: float, b: float, c: float, d: float) -> float:
    return 111.2 * math.hypot(a - c, (b - d) * math.cos(math.radians((a + c) / 2)))


def _on_olkhon(lat: float, lon: float) -> bool:
    """Остров, а не материковый берег Малого Моря: восточнее линии пролива (Сахюрта, Курма — материк)."""
    return 52.98 <= lat <= 53.45 and 106.95 + (lat - 53.0) * 0.5 < lon < 107.9


def find_places(text: str) -> tuple[list[str], list[tuple[int, int]]]:
    """([ключи мест], [занятые куски текста]). Регистр не важен: «ЛИСТВЯНКА», «Listvyanka», «в хужире»."""
    text = text.lower().replace("ё", "е")
    keys: list[str] = []
    spans: list[tuple[int, int]] = []
    for key, p in PLACES.items():
        for m in re.finditer(r"(?<![\w-])(?:" + p["rx_all"] + ")", text, re.I):
            if any(not (m.end() <= a or m.start() >= b) for a, b in spans):
                continue
            spans.append((m.start(), m.end()))
            if re.search(r"(?:\bне|\bкроме)\s+(?:в|на)?\s*$", text[max(0, m.start() - 10):m.start()]):
                continue
            if key not in keys:
                keys.append(key)
    return keys, spans


def title(keys: list[str] | None, where: bool = False) -> str:
    return " или ".join(PLACES[k]["where" if where else "title"] for k in (keys or []) if k in PLACES)


@lru_cache(maxsize=4)
def _place_by_master(path: str, mtime: float) -> dict[str, str]:      # mtime в ключе: пересобрали каталог — перечитаем
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except (OSError, ValueError):
        return {}
    return {str(r["master_id"]): r["place"] for r in data.get("hotels", []) if r.get("place")}


def place_of(master_id: str | None) -> str | None:
    """Посёлок объекта по каталогу Островка (если указан)."""
    if not master_id:
        return None
    from .catalog import catalog_file
    p = catalog_file()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    return _place_by_master(str(p), mtime).get(str(master_id))


def in_place(key: str, code: str | None, lat: float | None, lon: float | None) -> bool | None:
    """Объект в этом месте? None — не знаем (нет ни посёлка, ни координат)."""
    p = PLACES[key]
    if code:
        if code in p["codes"]:
            return True
        if key != "olkhon" or lat is None or lon is None:
            return False                        # посёлок указан и он другой
    if lat is None or lon is None:
        return None
    if key == "olkhon":
        return _on_olkhon(lat, lon)
    return _km(lat, lon, *p["center"]) <= p["r_km"]


def matches(keys: list[str], code: str | None, lat: float | None, lon: float | None) -> bool | None:
    got = [in_place(k, code, lat, lon) for k in keys if k in PLACES]
    if any(g is True for g in got):
        return True
    if got and all(g is False for g in got):
        return False
    return None
