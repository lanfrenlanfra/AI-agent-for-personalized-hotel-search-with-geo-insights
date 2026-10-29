"""Каталог региона, отбор кандидатов и дозаполнение по запросу (сеть подменена)."""
import json

import pytest

from app import catalog, db, enrich, net, ostrovok, registry, search
from app.config import settings

ITEM = {
    "masterId": 7001, "id": "test_hotel", "name": "Тест", "kind": "HOTEL", "isApartLike": False, "stars": 3, "rating": 8.6, "numReviews": 210,
    "datelessRate": {"amount": 5200, "currency": "RUB"}, "location": {"address": "Невский пр., 1", "latitude": 59.93, "longitude": 30.33},
    "searchRegionCenterDistance": 900, "nearestSubways": [{"distance": 300, "name": "Невский проспект"}, {"distance": 800, "name": "Гостиный двор"}],
    "nearestBeaches": None, "amenities": ["has_internet", "has_pets"], "regionCatalogSlug": "russia/st._petersburg",
}


def row(i, lat, lon, price, reviews=100, subway=500, kind="HOTEL", apart=False):
    return {"master_id": str(i), "slug": f"h{i}", "name": f"Отель {i}", "kind": kind, "apart": apart, "stars": 3, "rating": 8.5, "reviews": reviews,
            "price": price, "lat": lat, "lon": lon, "address": "", "center_m": 1000, "subway_m": subway, "subway_name": "М", "beach_m": None,
            "amenities": [], "region": "russia/st._petersburg"}


def test_catalog_row_from_real_shape():
    r = ostrovok.catalog_row(ITEM)
    assert r["master_id"] == "7001" and r["price"] == 5200 and r["subway_m"] == 300 and r["subway_name"] == "Невский проспект"
    assert r["beach_m"] is None and "has_pets" in r["amenities"] and r["apart"] is False


def test_stratified_sample_is_diverse_not_first_n():
    rows = [row(i, 59.90 + (i % 10) * 0.02, 30.2 + (i // 10) * 0.04, 2000 + i * 100, reviews=500 - i) for i in range(100)]
    rows += [row(1000 + i, 59.93, 30.33, 9000, reviews=900, kind="HOSTEL") for i in range(5)]
    got = catalog.stratified_sample(rows, 20, min_reviews=15)
    assert len(got) == 20 and len({g["master_id"] for g in got}) == 20
    cells = {(round(g["lat"], 2), round(g["lon"], 1)) for g in got}
    assert len(cells) >= 8, "выборка должна покрывать разные районы"
    prices = [g["price"] for g in got]
    assert min(prices) < 4000 and max(prices) > 8000
    assert not catalog.stratified_sample([row(1, 59.9, 30.3, 3000, reviews=3)], 5, min_reviews=15)


def test_coarse_score_prefers_close_metro_and_respects_price():
    parsed = {"intents": [{"feature": "walk_to_metro", "title": "До метро", "threshold": 10}], "filters": {"price_max": 6000}}
    near, far = row(1, 0, 0, 5000, subway=300), row(2, 0, 0, 5000, subway=1800)
    dear = row(3, 0, 0, 12000, subway=300)
    s = {k: catalog.coarse_score(parsed, v)[0] for k, v in (("near", near), ("far", far), ("dear", dear))}
    assert s["near"] > s["far"] and s["near"] > s["dear"]


@pytest.fixture()
def lab(world, tmp_path):
    """Отдельная чистая БД и данные: тесты каталога не должны менять общий «мир»."""
    saved = (settings.data_dir, settings.runtime_dir, settings.enrich_on_demand)
    src = sorted((world / "data" / "raw" / "ostrovok").glob("*.json"))[0]
    settings.data_dir = tmp_path / "data"
    settings.runtime_dir = tmp_path / "rt"
    settings.enrich_on_demand = True
    (settings.data_dir / "raw" / "ostrovok").mkdir(parents=True)
    db.reset_connection(str(tmp_path / "lab.db"))
    registry.invalidate()
    db.init_schema()
    registry.sync_builtin()
    yield json.loads(src.read_text(encoding="utf-8"))
    settings.data_dir, settings.runtime_dir, settings.enrich_on_demand = saved
    db.reset_connection(str(world / "t.db"))
    registry.invalidate()


def test_on_demand_enrichment(lab, monkeypatch):
    tmpl = lab
    rows = [row(9001, tmpl["lat"], tmpl["lon"], 4000, subway=250), row(9002, tmpl["lat"], tmpl["lon"], 4100, subway=2500)]
    catalog.save_file(rows, settings.region_slug, total=2)
    assert catalog.load() == 2 and catalog.stats()["total"] == 2 and enrich.enabled()

    calls = []

    def fake_fetch(region, summary):
        calls.append(summary["master_id"])
        if summary["master_id"] == "9002":
            raise RuntimeError("нет сети")
        rec = dict(tmpl, master_id=summary["master_id"], slug="live_" + summary["master_id"], name="Живой отель", url="https://ostrovok.ru/x")
        rec.pop("synthetic", None)
        return rec

    monkeypatch.setattr(net, "fetch_hotel", fake_fetch)
    monkeypatch.setattr(net, "ensure_tiles", lambda *a, **k: 0)
    monkeypatch.setattr(enrich, "start_worker", lambda: None)

    parsed = search.parse_query("тихий номер, метро в 5 минутах")
    res = {"results": []}
    enrich.augment(parsed, res)
    cov = res["coverage"]
    assert cov["queued_now"] == 2 and cov["total"] == 2 and cov["pending"] == 2
    assert [c["master_id"] for c in cov["candidates"]][0] == "9001", "ближе к метро — выше в очереди кандидатов"

    out = enrich.process_queue()
    assert out == {"done": 1, "failed": 1} and sorted(calls) == ["9001", "9002"]
    st = {r["master_id"]: (r["enrich_status"], r["enrich_error"]) for r in db.rows("SELECT * FROM catalog")}
    assert st["9001"][0] == "done" and st["9002"] == ("failed", "нет сети")

    r = search.search("тихий номер", 10)
    assert [h["id"] for h in r["results"]] == ["live_9001"], "после проверки отель появляется в выдаче"
    assert db.row("SELECT COUNT(*) AS n FROM observations WHERE hotel_id='live_9001'")["n"] > 0
    assert db.row("SELECT hotel_id FROM catalog WHERE master_id='9001'")["hotel_id"] == "live_9001"

    # недавний сбой не повторяется сразу, проверенный отель не ставится заново
    assert enrich.queue(["9001", "9002"]) == []
    cov2 = {"results": []}
    enrich.augment(parsed, cov2, queue_new=False)
    assert cov2["coverage"]["enriched"] == 1 and cov2["coverage"]["queued_now"] == 0


def test_hourly_cap(lab, monkeypatch):
    tmpl = lab
    catalog.save_file([row(i, tmpl["lat"], tmpl["lon"], 4000) for i in (1, 2, 3)], settings.region_slug, 3)
    catalog.load()
    monkeypatch.setattr(enrich, "start_worker", lambda: None)
    monkeypatch.setattr(enrich, "run_one", lambda mid: db.execute("UPDATE catalog SET enrich_status='done' WHERE master_id=?", (mid,)) and True)
    monkeypatch.setattr(settings, "enrich_max_per_hour", 0)
    enrich.queue(["1", "2"])
    assert enrich.process_queue() == {"done": 0, "failed": 0}


def test_kind_parsing_and_filter():
    p = search.parse_query_rules("квартира или коттедж с баней до 5000")
    assert p["filters"]["kind"] == ["apartment", "house"] and p["filters"]["price_max"] == 5000
    assert any(i["feature"] == "banya_sauna" for i in p["intents"]) and "квартира" not in p["text_terms"]
    assert search.parse_query_rules("гостевой дом у озера")["filters"]["kind"] == ["guesthouse"]   # не «дом/коттедж»
    assert "kind" not in search.parse_query_rules("не хостел, тихий номер")["filters"]
    assert "kind" not in search.parse_query_rules("чтобы было уютно, как дома")["filters"]
    parsed = {"intents": [], "filters": {"kind": ["apartment"]}}
    flat, _ = catalog.coarse_score(parsed, row(1, 51.8, 104.8, 4000, kind="APARTMENT", apart=True))
    hotel, _ = catalog.coarse_score(parsed, row(2, 51.8, 104.8, 4000, kind="HOTEL"))
    assert flat > hotel * 3, "в очередь на проверку должны попадать квартиры, а не отели"
    sc = search.score_hotel({"kind": "HOTEL"}, [], parsed["filters"], [], {})
    assert sc["label"] == "Не подходит" and any(i["feature"] == "kind" and i["verdict"] == "no" for i in sc["items"])
    assert search.score_hotel({"kind": "APARTMENT"}, [], parsed["filters"], [], {})["label"] == "Подходит"


def test_place_parsing_and_matching():
    from app import places
    p = search.parse_query_rules("домик на Малом Море с баней")
    assert p["filters"]["place"] == ["maloe_more"] and p["filters"]["kind"] == ["house"]
    assert not any(i["feature"] == "walk_to_beach" for i in p["intents"]), "«Малое Море» — место, а не «рядом с морем»"
    assert search.parse_query_rules("квартира в Листвянке")["filters"]["place_where"] == "в Листвянке"
    assert search.parse_query_rules("Северобайкальск")["filters"]["place"] == ["severobaykalsk"]
    assert "place" not in search.parse_query_rules("байкальский омуль и тихий номер")["filters"]    # прилагательное, не город
    assert "place" not in search.parse_query_rules("квартира не в Листвянке")["filters"]
    # посёлок из каталога главнее координат; без посёлка — по координатам
    assert places.in_place("listvyanka", "Listvyanka", None, None) is True
    assert places.in_place("listvyanka", "Baikalsk", 51.86, 104.86) is False
    assert places.in_place("listvyanka", None, 51.857, 104.860) is True
    assert places.in_place("listvyanka", None, 51.927, 104.781) is False          # Большая Речка, 9 км
    assert places.matches(["olkhon"], None, 53.19, 107.34) is True               # Хужир
    assert places.matches(["olkhon"], "Sakhyurta", 53.02, 106.87) is False        # паром, но материк
    assert places.matches(["listvyanka"], None, None, None) is None
    parsed = {"intents": [], "filters": search.parse_query_rules("в Листвянке")["filters"]}
    far, _ = catalog.coarse_score(parsed, row(1, 51.52, 104.13, 4000))           # Байкальск
    near, notes = catalog.coarse_score(parsed, row(2, 51.857, 104.860, 4000))
    assert near > far * 3 and any("Листвянка" in n for n in notes)
    sc = search.score_hotel({"lat": 51.52, "lon": 104.13}, [], parsed["filters"], [], {})
    assert sc["label"] == "Не подходит" and any(i["feature"] == "place" and i["verdict"] == "no" for i in sc["items"])


def test_places_and_kinds_ignore_case_and_script():
    for q in ("квартира в листвянке", "КВАРТИРА В ЛИСТВЯНКЕ", "Квартира В Листвянке", "apartments in Listvyanka", "FLAT LISTVYANKA"):
        f = search.parse_query_rules(q)["filters"]
        assert f.get("kind") == ["apartment"] and f.get("place") == ["listvyanka"], (q, f)
    assert search.parse_query_rules("ДОМИК НА ОЛЬХОНЕ")["filters"]["place"] == ["olkhon"]
    assert search.parse_query_rules("Cottage, Khuzhir")["filters"] == {**search.parse_query_rules("Cottage, Khuzhir")["filters"], "kind": ["house"], "place": ["khuzhir"]}
    assert search.parse_query_rules("Ust-Barguzin")["filters"]["place"] == ["ust_barguzin"]
    assert "place" not in search.parse_query_rules("lake baikal, quiet room")["filters"]       # «Baikal» — озеро, а не Порт Байкал
