"""Узнать конкретный отель: по названию и по фотографии (сеть подменена)."""
import base64
import io
import json
import random

import pytest
from PIL import Image, ImageDraw, ImageFilter
from starlette.testclient import TestClient

from app import db, frog, identify, main


@pytest.fixture(scope="module")
def client(world):
    main.BOOT = False
    with TestClient(main.app) as c:
        yield c


def _picture(seed: int, w: int = 1200, h: int = 800) -> Image.Image:
    """«Фотография»: случайные пятна и полосы, размытые, как реальный кадр без мелкого шума."""
    rnd = random.Random(seed)
    im = Image.new("RGB", (w, h), tuple(rnd.randrange(256) for _ in range(3)))
    d = ImageDraw.Draw(im)
    for _ in range(260):
        x, y = rnd.randrange(w), rnd.randrange(h)
        r = rnd.randrange(8, 140)
        col = tuple(rnd.randrange(256) for _ in range(3))
        (d.ellipse if rnd.random() < 0.5 else d.rectangle)((x - r, y - r // 2, x + r, y + r), fill=col)
    return im.filter(ImageFilter.GaussianBlur(2))


def _jpeg(im: Image.Image, q: int = 82) -> bytes:
    b = io.BytesIO()
    im.save(b, "JPEG", quality=q)
    return b.getvalue()


def _cover(im, W, H):
    w, h = im.size
    k = max(W / w, H / h)
    im = im.resize((round(w * k), round(h * k)))
    w, h = im.size
    return im.crop(((w - W) // 2, (h - H) // 2, (w - W) // 2 + W, (h - H) // 2 + H))


def _data_url(im) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(_jpeg(im)).decode()


@pytest.fixture(scope="module")
def photos(world):
    """У каждого синтетического отеля по три «фото»; CDN отдаёт квадратные превью 240×240."""
    pics, seed = {}, 100
    for r in db.rows("SELECT id, photos FROM hotels"):
        rels = []
        for k in range(3):
            rel = f"/t/{{size}}/test/{r['id']}_{k}.jpg"
            pics[rel] = _picture(seed)
            seed += 1
            rels.append(rel)
        db.execute("UPDATE hotels SET photos=? WHERE id=?", (json.dumps({"images": rels}), r["id"]))
    old = identify.fetch
    identify.fetch = lambda rel: _jpeg(_cover(pics[rel], 240, 240), 80)
    identify._index = {}
    assert identify.build_index(workers=2) == len(pics)
    yield pics
    identify.fetch = old


def test_name_lookup_handles_cases_and_typos(world):
    h = db.row("SELECT id, name FROM hotels LIMIT 1")
    word = identify._tokens(h["name"])
    assert word, h["name"]
    assert h["id"] in identify.by_name(h["name"])["hotels"]
    assert h["id"] in identify.by_name("хочу в " + " ".join(word))["hotels"]
    assert identify._same("маяк", "маяке") and identify._same("лиса", "лисы") and identify._same("багульник", "багульнек")
    assert not identify._same("уют", "уютный") and not identify._same("закат", "залив")
    assert not identify.by_name("тихий номер у озера")["hotels"]


def test_ordinary_words_do_not_pin_a_hotel(world):
    db.execute("INSERT INTO catalog(master_id, name, kind) VALUES('9100001', 'Отель Закат', 'HOTEL')")
    try:
        assert frog.recognize("тихий номер с видом на закат", None)["how"] is None      # это пожелание, а не название
        assert frog.recognize("отель «Закат»", None)["catalog"] == ["9100001"]          # а так — название
        assert frog.recognize("отель Закат", None)["catalog"] == ["9100001"]
        assert frog.recognize("Закат", None)["how"] is None                           # одно обычное слово — скорее пожелание
        assert frog.recognize("хочу на берег Байкала", None)["how"] is None
    finally:
        db.execute("DELETE FROM catalog WHERE master_id='9100001'")


def test_ask_by_name_puts_the_hotel_first(client, world):
    h = db.row("SELECT id, name FROM hotels LIMIT 1")
    r = client.post("/api/frog/ask", json={"text": h["name"]}).json()
    assert r["mode"] == "search" and r["top"][0]["id"] == h["id"] and r["top"][0]["pinned"]
    assert len(r["top"]) == 1, "только название — случайные «похожие» не нужны"
    assert h["name"] in r["ack"] and r["understood"]["found"]["how"] == "name"
    r = client.post("/api/frog/ask", json={"text": h["name"] + ", тихий номер"}).json()
    assert r["top"][0]["id"] == h["id"] and len(r["top"]) > 1                     # с пожеланиями — он первый, рядом другие


def test_photo_is_recognized_after_cdn_crops_and_screenshots(world, photos):
    for rel, pic in list(photos.items())[:6]:
        hid = rel.split("/")[-1].rsplit("_", 1)[0]
        for variant in (pic, _cover(pic, 640, 400), _cover(pic, 828, 560), _cover(pic, 1200, 616),
                        _cover(pic, 828, 560).crop((-30, -20, 858, 580)), pic.crop((60, 40, 1140, 760)).resize((768, 512))):
            m = identify.by_photo(_data_url(variant))
            assert m and m["hotels"] == [hid], (rel, variant.size, m)
    assert identify.by_photo(_data_url(_picture(9999))) is None                   # чужое фото не узнаём
    assert identify.by_photo("data:image/jpeg;base64,bm90IGFuIGltYWdl") is None    # не картинка


def test_ask_with_hotel_photo(client, world, photos, monkeypatch):
    from app import llm
    monkeypatch.setattr(llm, "chat", lambda *a, **k: (_ for _ in ()).throw(AssertionError("узнали по фото — модель не нужна")))
    rel, pic = next(iter(photos.items()))
    hid = rel.split("/")[-1].rsplit("_", 1)[0]
    r = client.post("/api/frog/ask", json={"text": "", "image": _data_url(_cover(pic, 828, 560))}).json()
    assert r["mode"] == "search" and r["top"][0]["id"] == hid and r["top"][0]["pinned"]
    assert r["understood"]["found"]["how"] == "photo" and r["ack"].startswith("Ква! Узнала фото")
