import pytest
from starlette.testclient import TestClient

from app import main


@pytest.fixture(scope="module")
def client(world):
    # мир уже загружен фикстурой world; приложение без on_startup, чтобы не перезаписывать данные
    main.BOOT = False
    with TestClient(main.app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_meta_and_list(client):
    assert client.get("/api/meta").json()["hotels"] == 3
    hs = client.get("/api/hotels").json()
    assert len(hs) == 3 and all(h["synthetic"] for h in hs)


def test_detail_facts(client):
    d = client.get("/api/hotels/test_dvor_i_zakat").json()
    assert d["groups"] and d["facts"] and d["reviews_list"]
    f = client.get("/api/hotels/test_dvor_i_zakat/facts/reception_24h").json()
    assert f["fact"]["conflict"] and len(f["observations"]) >= 2
    assert all(o["evidence"] is not None for o in f["observations"])


def test_search_endpoint(client):
    r = client.get("/api/search", params={"q": "тихий номер во двор с видом на закат"}).json()
    assert r["parsed"]["intents"] and r["results"][0]["why"]


def test_compare_endpoint(client):
    r = client.get("/api/compare", params={"ids": "test_dvor_i_zakat,test_tishina"}).json()
    assert r["rows"] and len(r["names"]) == 2


def test_horizon(client):
    h = client.get("/api/hotels/test_dvor_i_zakat/horizon", params={"az": 270, "floor": 8, "month": 6}).json()
    assert h["available"] and h["profile"] and h["sun_path"]
    low = client.get("/api/hotels/test_dvor_i_zakat/horizon", params={"az": 270, "floor": 1, "month": 6}).json()
    assert max(p["el"] for p in low["profile"]) >= max(p["el"] for p in h["profile"])


def test_hotelier_flow(client):
    assert client.post("/api/hotelier/confirm", json={"hotel_id": "test_tishina", "feature": "elevator", "value": True}).status_code == 401
    tok = client.post("/api/hotelier/login", json={"login": "hotelier", "password": "hotelier123"}).json()["token"]
    r = client.post("/api/hotelier/confirm", headers={"x-token": tok}, json={"hotel_id": "test_tishina", "feature": "elevator", "value": False})
    assert r.status_code == 200
    f = client.get("/api/hotels/test_tishina/facts/elevator").json()["fact"]
    assert f["value"] is False and f["status"] == "confirmed"


def test_simulated_dialog(client):
    qs = client.get("/api/questions", params={"hotel_id": "test_dvor_i_zakat"}).json()
    assert qs
    r = client.post(f"/api/questions/{qs[0]['id']}/simulate").json()
    assert r["answer_text"] and "fields" in r


def test_add_feature_by_text(client):
    s = client.post("/api/features/suggest", json={"description": "Номера с видом на двор-колодец"}).json()
    assert s["title"] and s["synonyms"]
    r = client.post("/api/features", json={"description": "Рядом есть детская площадка во дворе", "title": "Детская площадка", "synonyms": ["площадк"]})
    assert r.status_code == 201
    assert any(f["key"] == r.json()["feature"]["key"] for f in client.get("/api/features").json())
    key = r.json()["feature"]["key"]
    assert client.delete(f"/api/features/{key}").status_code == 200


def test_freshness_demo_building(client):
    before = client.get("/api/hotels/test_dvor_i_zakat/facts/sunset_view", params={"scope": "room:west"}).json()["fact"]["value"]
    r = client.post("/api/hotels/test_dvor_i_zakat/demo/building", json={"bearing": 270, "dist": 25, "height": 90}).json()
    assert r["changes"], "появление высокого здания должно менять факты"
    after = client.get("/api/hotels/test_dvor_i_zakat/facts/sunset_view", params={"scope": "room:west"}).json()["fact"]["value"]
    assert before is True and after is False
    client.delete("/api/hotels/test_dvor_i_zakat/demo/building")
    again = client.get("/api/hotels/test_dvor_i_zakat/facts/sunset_view", params={"scope": "room:west"}).json()["fact"]["value"]
    assert again is True


def test_metrics_and_freshness(client):
    m = client.get("/api/metrics").json()
    assert m["hotels"] == 3 and m["facts"] > 20 and m["evaluation"]["labeled"] == 0
    assert "stale" in client.get("/api/freshness").json()
    client.post("/api/eval/labels", json={"hotel_id": "test_tishina", "feature": "elevator", "value": False})
    assert client.get("/api/metrics").json()["evaluation"]["labeled"] == 1


def test_search_reports_coverage_and_jobs(client):
    j = client.post("/api/search", json={"q": "тихий номер"}).json()
    assert j["coverage"]["total"] == 0 and j["coverage"]["candidates"] == []     # каталога региона нет — только собранное
    p = client.post("/api/search", json={"q": "тихий номер", "passive": True}).json()
    assert p["results"]
    jobs = client.get("/api/jobs").json()
    assert jobs["jobs"] == [] and jobs["total"] == 0


def test_frog_top3_and_say(client):
    r = client.post("/api/frog/ask", json={"text": "тихий номер во двор с видом на закат"}).json()
    assert r["understood"]["intents"] and 1 <= len(r["top"]) <= 3
    t = r["top"][0]
    assert {"pros", "cons", "ask", "proof", "score"} <= set(t)
    # опрос прогресса по уже разобранному запросу
    r2 = client.post("/api/frog/ask", json={"text": "x", "parsed": r["parsed"], "passive": True}).json()
    assert r2["top"] and "understood" not in r2
    s = client.post("/api/frog/say", json={"hotel_id": t["id"], "text": "тихий номер", "parsed": r["parsed"]}).json()
    assert s["text"] and s["by"] in ("llm", "template")
    assert client.post("/api/frog/ask", json={"text": ""}).status_code == 400


def test_frog_reads_image(client, monkeypatch):
    from app import llm
    monkeypatch.setattr(llm, "chat", lambda messages, *a, **k: "Деревянный дом у воды, на озере лёд, вечер.")
    img = "data:image/jpeg;base64,/9j/4AAQ"
    r = client.post("/api/frog/ask", json={"text": "тихо", "image": img}).json()
    u = r["understood"]
    assert u["image_given"] and u["image_read"] and "лёд" in u["image_note"]


def test_frog_image_not_read(client, monkeypatch):
    from app import llm
    monkeypatch.setattr(llm, "chat", lambda *a, **k: None)
    r = client.post("/api/frog/ask", json={"text": "тихо", "image": "data:image/jpeg;base64,/9j/4AAQ"}).json()
    assert r["understood"]["image_given"] and not r["understood"]["image_read"] and r["top"]


def test_frog_compare_only_requested(client):
    r = client.post("/api/frog/ask", json={"text": "тихий номер с видом на закат"}).json()
    ids = [t["id"] for t in r["top"]][:3]
    c = client.post("/api/frog/compare", json={"ids": ids, "text": "тихий номер с видом на закат", "parsed": r["parsed"]}).json()
    asked = {i["title"] for i in r["parsed"]["intents"]}
    assert {row["title"] for row in c["rows"]} == asked          # ровно то, что просил гость
    assert len(c["hotels"]) == len(ids) and c["verdict"].startswith("Ква!")
    assert all(len(row["cells"]) == len(ids) and all(x["kind"] in ("yes", "part", "maybe", "no", "unk") for x in row["cells"]) for row in c["rows"])
    s = client.post("/api/frog/compare/say", json={"ids": ids, "text": "x", "parsed": r["parsed"]}).json()
    assert s["text"] and s["by"] in ("llm", "template")
    assert client.post("/api/frog/compare", json={"ids": ids[:1], "parsed": r["parsed"]}).status_code == 400


def _ask(client, text="", image=None):
    return client.post("/api/frog/ask", json={"text": text, "image": image}).json()


def test_frog_smalltalk_and_offtopic(client):
    for t, kind in [("привет", "greet"), ("Привет!", "greet"), ("кто ты?", "about"), ("спасибо, пока", "thanks"), ("хочу в Сочи", "region"),
                    ("реши мне уравнение", "off"), ("хочу отель", "vague")]:
        r = _ask(client, t)
        assert r["mode"] == "chat" and r["kind"] == kind and r["reply"].startswith("Ква") and r["hints"], (t, r)
    assert _ask(client, "тихий номер с видом на закат")["mode"] == "search"


def test_frog_irrelevant_image(client, monkeypatch):
    from app import llm
    img = "data:image/jpeg;base64,/9j/4AAQ"
    monkeypatch.setattr(llm, "chat", lambda *a, **k: '{"relevant": false, "what": "рыжий кот на диване", "wishes": ""}')
    r = _ask(client, "", img)                                   # одно фото и оно не про отели
    assert r["mode"] == "chat" and r["kind"] == "image_off" and "рыжий кот" in r["reply"]
    r = _ask(client, "тихий номер с видом на закат", img)       # текст нормальный: ищем по нему, фото не учитываем
    assert r["mode"] == "search" and r["understood"]["image_off"] and r["top"]
    monkeypatch.setattr(llm, "chat", lambda *a, **k: '{"relevant": true, "what": "дом у озера", "wishes": "Деревянный дом у озера, зимой, тихо."}')
    r = _ask(client, "", img)                                   # фото про отдых, слов нет: ищем по тому, что на нём
    assert r["mode"] == "search" and r["understood"]["image_note"]
    monkeypatch.setattr(llm, "chat", lambda *a, **k: None)
    r = _ask(client, "", img)                                   # модель не видит картинки
    assert r["mode"] == "chat" and r["kind"] == "image_unread"


def test_frog_chat_is_instant_without_model(client, monkeypatch):
    from app import llm
    def boom(*a, **k):
        raise AssertionError("модель не должна вызываться для болтовни")
    monkeypatch.setattr(llm, "chat", boom)
    monkeypatch.setattr(llm, "chat_json", boom)
    for t in ("привет", "реши мне уравнение", "хочу отель"):
        r = _ask(client, t)
        assert r["mode"] == "chat" and r["by"] == "template"
