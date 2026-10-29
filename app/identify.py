"""Узнать конкретный отель: по названию в тексте или по его фотографии.

Название. Сравниваем «значимые» слова запроса со словами названия (без «отель», «гостевой дом», «Байкал»…)
с учётом падежей и опечаток: «в Маяке» → «Отель Маяк». Ищем и среди изученных отелей, и по всему каталогу.

Фото. Для каждой фотографии изученных отелей храним перцептивные хеши (dHash 16×16, 256 бит) центральной части
кадра в трёх масштабах. Он не меняется от сжатия, уменьшения и разной обрезки CDN (640×400, 828×560, 240×240),
поэтому снимок с сайта, скриншот или пересохранённый файл узнаются; похожий, но другой кадр — нет.
Индекс считается в фоне один раз (качаем маленькие превью с CDN Островка) и лежит в runtime/photo_index.json.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from functools import lru_cache

from . import db, ostrovok
from .config import settings

log = logging.getLogger("kva.identify")

# ───────────────────────── по названию ─────────────────────────

GENERIC = set("""
отель отеля отелю отелем отели гостиница гостиницы гостиницу гостевой гостевого гостевом гостевые дом дома доме домик домика домики
мини хостел хостела апартаменты апартамент квартира квартиры частный частного коттедж коттеджи коттеджа база базы отдыха усадьба
кемпинг глэмпинг турбаза турбазы шале вилла комплекс туркомплекс сюит sea resort spa спа парк эко
байкал байкала байкале байкалу байкальский байкальская байкальское листвянка листвянке хужир хужире ольхон ольхоне
на в у с и по под от до за из к о об the and hotel guest house apartments apartment hostel cottage villa baikal baykal lake
хочу найди найти покажи показать есть ищу нужен нужна нужно хочется где какой какая такой такая называется название названием
""".split())


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s-]", " ", str(s).lower().replace("ё", "е"))).strip()


# Основы слов, которые бывают в названиях, но ничего не различают: «Байкал51», «Байкалия», «база отдыха».
GENERIC_STEMS = ("байкал", "baikal", "baykal", "отел", "гостиниц", "гостев", "апартамент", "квартир", "коттедж", "хостел", "кемпинг",
                 "глэмпинг", "глемпинг", "турбаз", "отдых", "озер", "усадьб", "резиденц", "residen", "apart", "hotel", "guest")
# Обычные слова пожеланий: если всё название — одно такое слово («Уютный дом», «У озера», «Семейная»),
# по одному совпадению отель не закрепляем — только если гость явно назвал его («отель «Уютный»», или запрос из одного названия).
COMMON_STEMS = ("уютн", "тих", "семейн", "берег", "лес", "сосн", "горн", "гор", "солнеч", "зимн", "летн", "панорам", "красив", "нов",
                "стар", "центр", "люкс", "комфорт", "эконом", "бюджет", "дешев", "хорош", "лучш", "бан", "саун", "камин", "вид", "закат",
                "рассвет", "двор", "сад", "парк", "мор", "пляж", "залив", "бухт", "остров", "скал", "тепл", "чист", "уют", "прост", "дружн",
                "родн", "добр", "светл", "white", "comfort", "relax", "family", "cozy", "lux")


def _generic(t: str) -> bool:
    if t in GENERIC or t.isdigit() or t.startswith(GENERIC_STEMS):
        return True
    from .places import find_places                # «Снежные Гуси в Байкальске»: посёлок в названии ничего не различает
    return bool(find_places(t)[0])


def _tokens(s: str) -> list[str]:
    out = []
    for t in re.split(r"[\s-]+", _norm(s)):
        t = re.sub(r"\d+$", "", t)                   # «байкал51», «маяк38»
        if len(t) >= 3 and not _generic(t):
            out.append(t)
    return out


def common_word(t: str) -> bool:
    return t.startswith(COMMON_STEMS)


FILLER = set("хочу хотим хотел хотела найди найти покажи показать есть ищу ищем нужен нужна нужно где какой какая такой такая "
             "называется название названием под про это этот эту что как там тот".split())


@lru_cache(maxsize=8192)
def _name_tokens(name: str) -> tuple[str, ...]:
    return tuple(_tokens(name))


def raw_words(s: str) -> list[str]:
    return [w for w in re.split(r"[\s-]+", _norm(s)) if len(w) >= 3]


def _same(a: str, b: str) -> bool:
    """Одно слово в разных падежах или с опечаткой: «маяк»/«маяке», «лиса»/«лисы», «багульник»/«багульнек»."""
    if a == b:
        return True
    p = len(os.path.commonprefix([a, b]))
    if p >= max(3, max(len(a), len(b)) - 2):
        return True
    return min(len(a), len(b)) >= 5 and SequenceMatcher(None, a, b).ratio() >= 0.85


def _name_hit(query_tokens: list[str], name: str) -> float:
    """0 — не то; 1 — все значимые слова названия есть в запросе."""
    nt = _name_tokens(name)
    if not nt or not query_tokens:
        return 0.0
    got = sum(any(_same(q, n) for q in query_tokens) for n in nt)
    return got / len(nt) if got == len(nt) else 0.0


def by_name(text: str, limit: int = 3) -> dict:
    """{'hotels': [id изученных], 'catalog': [master_id только из каталога], 'names': {id: название}, 'used': [слова запроса]}."""
    qt = _tokens(text)
    out = {"hotels": [], "catalog": [], "names": {}, "used": []}
    if not qt:
        return out
    hits = []
    seen_master = set()
    for r in db.rows("SELECT id, master_id, name FROM hotels"):
        s = _name_hit(qt, r["name"])
        if s:
            hits.append((len(_name_tokens(r["name"])), "h", r["id"], r["name"]))
            seen_master.add(str(r["master_id"]))
    for r in db.rows("SELECT master_id, name, hotel_id FROM catalog"):
        if str(r["master_id"]) in seen_master:
            continue
        if _name_hit(qt, r["name"]):
            hits.append((len(_name_tokens(r["name"])), "h" if r["hotel_id"] else "c", r["hotel_id"] or str(r["master_id"]), r["name"]))
    if hits:
        best = max(h[0] for h in hits)              # «уютная лиса»: «Уютная лиса» (2 слова) точнее, чем «Уютный дом» (1)
        hits = [h for h in hits if h[0] == best]
    q = f" {_norm(text)} "
    # название целиком в запросе («база отдыха маяк 38») — первым; потом изученные
    hits.sort(key=lambda h: (f" {_norm(h[3])} " not in q, h[1] != "h"))
    for _, where, key, name in hits[:limit]:
        bucket = out["hotels"] if where == "h" else out["catalog"]
        if key not in bucket:
            bucket.append(key)
            out["names"][key] = name
    if hits:
        names = " ".join(h[3] for h in hits[:limit])
        out["used"] = [q for q in qt if any(_same(q, n) for n in _tokens(names))]
    return out


# ───────────────────────── по фото ─────────────────────────

HASH_N = 16
INDEX_FRACS = (0.3, 0.45, 0.6)                # доля короткой стороны превью 240×240
USER_FRACS = tuple(round(0.25 + 0.05 * i, 2) for i in range(15))   # снимок гостя: 0.25…0.95 — у CDN и скриншотов разная обрезка
MATCH_BITS = 48                               # из 256; у разных фотографий обычно 110–140
MARGIN_BITS = 16                              # насколько лучший отель должен быть ближе следующего
# Подобрано на реальных фото с имитацией обрезок CDN (640×400, 828×560, 1200×616), скриншота с рамкой,
# обрезки 10% и пересжатия JPEG: узнаются ≈99%, ложных совпадений 0.
_lock = threading.Lock()
_index: dict[str, dict[str, list[int]]] | None = None
_building = False


def _pil():
    from PIL import Image, ImageOps                # Pillow нужен только здесь
    return Image, ImageOps


def _dhash(img, frac: float) -> int:
    Image, _ = _pil()
    w, h = img.size
    n = HASH_N
    s = max(n + 1, int(min(w, h) * frac))
    box = ((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s)
    px = list(img.crop(box).convert("L").resize((n + 1, n), Image.LANCZOS).getdata())
    bits = 0
    for y in range(n):
        for x in range(n):
            bits = (bits << 1) | (px[y * (n + 1) + x] > px[y * (n + 1) + x + 1])
    return bits


def _open(data: bytes):
    Image, ImageOps = _pil()
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def image_hashes(data: bytes, fracs=INDEX_FRACS) -> list[int]:
    img = _open(data)
    return [_dhash(img, f) for f in fracs]


def _index_path():
    return settings.runtime_dir / "photo_index.json"


def _load() -> dict:
    global _index
    if _index is None:
        try:
            _index = json.loads(_index_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _index = {}
    return _index


def _save() -> None:
    p = _index_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(_index, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        log.warning("не удалось сохранить индекс фото: %s", e)


def hotel_photos(r) -> list[str]:
    photos = db.loads(r["photos"], {}) or {}
    rel = list(photos.get("images") or [])
    for g in photos.get("room_groups") or []:
        rel += g.get("images") or []
    seen, out = set(), []
    for x in rel:
        if isinstance(x, str) and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _download(rel: str) -> bytes:
    import requests
    url = ostrovok.photo_url(rel, "thumb") if rel.startswith("/t/") else rel
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 kva-hackathon/1.0"})
    r.raise_for_status()
    return r.content


fetch = _download                                # тесты подменяют загрузку


def missing() -> list[tuple[str, str]]:
    idx = _load()
    todo = []
    for r in db.rows("SELECT id, photos FROM hotels"):
        have = idx.get(r["id"], {})
        todo += [(r["id"], rel) for rel in hotel_photos(r) if rel not in have]
    return todo


def build_index(workers: int = 6) -> int:
    """Досчитать хеши недостающих фото. Возвращает, сколько добавлено."""
    todo = missing()
    if not todo:
        return 0

    def one(item):
        hid, rel = item
        try:
            return hid, rel, image_hashes(fetch(rel))
        except Exception as e:  # noqa: BLE001 — битое фото или сеть: пропускаем, попробуем в следующий раз
            log.debug("фото %s: %s", rel, e)
            return hid, rel, None

    added = 0
    with ThreadPoolExecutor(workers) as ex:
        for i, (hid, rel, hs) in enumerate(ex.map(one, todo), 1):
            if hs is None:
                continue
            with _lock:
                _load().setdefault(hid, {})[rel] = hs
            added += 1
            if i % 100 == 0:
                with _lock:
                    _save()
    with _lock:
        _save()
    log.info("индекс фото: добавлено %s из %s", added, len(todo))
    return added


def start_indexing() -> None:
    """Фоном досчитать индекс (при старте и когда появились новые изученные отели)."""
    global _building
    with _lock:
        if _building:
            return
        _building = True

    def run():
        global _building
        try:
            build_index()
        except Exception:  # noqa: BLE001
            log.exception("индекс фото")
        finally:
            _building = False

    threading.Thread(target=run, name="photo-index", daemon=True).start()


def status() -> dict:
    idx = _load()
    return {"photos": sum(len(v) for v in idx.values()), "hotels": len(idx), "building": _building}


def decode_data_url(data_url: str) -> bytes | None:
    m = re.match(r"data:image/[\w.+-]+;base64,(.+)$", data_url or "", re.S)
    if not m:
        return None
    try:
        return base64.b64decode(m.group(1))
    except ValueError:
        return None


def by_photo(data_url: str) -> dict | None:
    """{'hotels': [id], 'bits': расстояние, 'photo': путь лучшего фото} — чья это фотография, или None.
    Обычно один отель; два-три — если одно и то же фото выложено в нескольких объявлениях."""
    raw = decode_data_url(data_url)
    if not raw:
        return None
    try:
        user = image_hashes(raw, USER_FRACS)
    except Exception:  # noqa: BLE001 — не картинка или Pillow не установлен
        return None
    best: dict[str, tuple[int, str]] = {}
    with _lock:
        items = [(hid, rel, hs) for hid, photos in _load().items() for rel, hs in photos.items()]
    for hid, rel, hs in items:
        d = min(bin(u ^ h).count("1") for u in user for h in hs)
        if hid not in best or d < best[hid][0]:
            best[hid] = (d, rel)
    if not best:
        return None
    ranked = sorted(best.items(), key=lambda kv: kv[1][0])
    hid, (d, rel) = ranked[0]
    if d > MATCH_BITS:
        return None
    tied = [h for h, (dd, _) in ranked if dd <= MATCH_BITS and dd - d < MARGIN_BITS]
    if len(tied) > 3:                             # подходит ко многим — это не «то самое фото», а что-то однородное
        return None
    return {"hotels": tied, "bits": d, "photo": rel}
