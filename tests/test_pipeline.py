from app import db, search


def facts(hid):
    out = {}
    for r in db.rows("SELECT * FROM facts WHERE hotel_id=?", (hid,)):
        out[(r["feature"], r["scope"])] = dict(r, value=db.loads(r["value"], {}), conflict=db.loads(r["conflict"], None) if r["conflict"] else None)
    return out


def test_loaded(world):
    assert db.row("SELECT COUNT(*) n FROM hotels")["n"] == 3


def test_geo_facts_exist(world):
    f = facts("test_dvor_i_zakat")
    for k in ("sunset_view", "quiet", "water_view", "walk_to_metro", "walk_to_center"):
        assert (k, "hotel") in f, k
    assert ("quiet", "room:yard") in f and ("quiet", "room:street") in f


def test_yard_quieter_than_street(world):
    f = facts("test_dvor_i_zakat")
    assert f[("quiet", "room:yard")]["value"]["v"] > f[("quiet", "room:street")]["value"]["v"]


def test_reception_conflict_detected(world):
    # карточка: стойка 10–22, отзыв: «круглосуточно» → расхождение должно быть видно
    f = facts("test_dvor_i_zakat")[("reception_24h", "hotel")]
    assert f["conflict"] is not None


def test_stale_card_flagged(world):
    # карточка третьего отеля «снята» 430 дней назад: кондиционер по карточке против отзывов «нет»
    f = facts("test_kanal")[("air_conditioning", "hotel")]
    assert f["status"] in ("disputed", "stale", "weak", "likely") and f["conflict"] is not None


def test_questions_generated(world):
    n = db.row("SELECT COUNT(*) n FROM questions WHERE status='open'")["n"]
    assert n > 0


def test_search_ranks(world):
    res = search.search("тихий номер с видом на закат")
    ids = [r["id"] for r in res["results"]]
    assert set(ids) == {"test_dvor_i_zakat", "test_tishina", "test_kanal"}
    top = res["results"][0]
    assert top["items"] and top["why"]
    sun = {r["id"]: next(i for i in r["items"] if i["feature"] == "sunset_view") for r in res["results"]}
    assert sun["test_tishina"]["verdict"] in ("no", "partial", "unknown", "disputed", "maybe")


def test_compare(world):
    c = search.compare(["test_dvor_i_zakat", "test_tishina"])
    assert c["rows"] and any(r["differs"] for r in c["rows"])
