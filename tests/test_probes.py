"""Внешние источники на заранее записанных ответах: сеть не нужна, честные статусы проверяются."""
import pytest

from app import db, extcache, osmextra, pipeline, reconcile
from app.config import settings
from app.probes import air, climate, commons, masts, wiki
from app.probes import run_all, runs

REC = {"lat": 51.8651, "lon": 104.8321, "name": "Отель Байкальская Волна", "id": "h"}


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "opencellid_key", "")
    extcache.reset_breakers()
    calls = []
    monkeypatch.setattr(extcache, "http_json", lambda url, params, timeout, headers=None: (calls.append(url), FAKE(url, params))[1])
    return calls


def days(y0=2015, y1=2024):
    from datetime import date, timedelta
    d, out = date(y0, 1, 1), []
    while d <= date(y1, 12, 31):
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def FAKE(url, params):
    if "archive-api.open-meteo" in url:
        t = days()
        return {"elevation": 470.0, "daily": {"time": t, "temperature_2m_mean": [-18.0 if s[5:7] == "01" else 5.0 for s in t],
                                              "sunshine_duration": [3 * 3600.0 if s[5:7] in ("12", "01", "02") else 9 * 3600.0 for s in t]}}
    if "air-quality-api" in url:
        from datetime import date, timedelta
        d, tt = date.today() - timedelta(days=400), []
        for i in range(24 * 380):
            tt.append((d + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00"))
        return {"hourly": {"time": tt, "pm2_5": [12.0 if t[5:7] in ("12", "01", "02") else 4.0 for t in tt]}}
    if "ru.wikipedia.org" in url:
        return {"query": {"pages": [
            {"pageid": 1, "title": "Байкальская Волна (гостиница)", "coordinates": [{"lat": 51.8652, "lon": 104.8322}], "pageprops": {"wikibase_item": "Q1"}, "extract": "Гостиница в Листвянке."},
            {"pageid": 2, "title": "Музей Байкала", "coordinates": [{"lat": 51.8660, "lon": 104.8300}], "pageprops": {"wikibase_item": "Q2"}, "extract": "Музей."},
            {"pageid": 3, "title": "Шаман-камень", "coordinates": [{"lat": 51.8570, "lon": 104.8390}], "extract": "Скала."}]}}
    if "wikidata.org" in url:
        return {"entities": {"Q1": {"labels": {"ru": {"value": "Байкальская Волна"}}, "claims": {
            "P571": [{"mainsnak": {"datavalue": {"value": {"time": "+1958-00-00T00:00:00Z"}}}}], "P1435": [{}]}}}}
    if "commons.wikimedia.org" in url:
        return {"query": {"pages": [{"title": f"File:Улица {i}.jpg", "imageinfo": [{"descriptionurl": f"https://commons.wikimedia.org/wiki/File:{i}", "thumburl": "https://upload.x/t.jpg", "timestamp": "2019-07-01T10:00:00Z",
                                                                                    "extmetadata": {"Artist": {"value": "<a>Автор</a>"}, "LicenseShortName": {"value": "CC BY-SA 4.0"}}}]} for i in range(4)]}}
    raise AssertionError("неожиданный запрос " + url)


def by_feature(res):
    return {o.feature: o for o in res.observations}


# ───────── кэш и статусы ─────────

def test_cache_roundtrip_offline_and_stale(sandbox, monkeypatch):
    r1 = climate.PROBE.run(REC, online=True)
    assert r1.status == "ok" and len(sandbox) == 1
    r2 = climate.PROBE.run(REC, online=False)                       # без сети — из кэша, сеть не трогали
    assert r2.status == "cached" and len(sandbox) == 1 and r2.sources[0]["status"] == "cached"
    monkeypatch.setattr(extcache, "age_days", lambda e: 4000)       # кэш очень старый, сети нет → показываем с возрастом
    monkeypatch.setattr(extcache, "http_json", lambda *a, **k: (_ for _ in ()).throw(OSError("нет сети")))
    r3 = climate.PROBE.run(REC, online=True)
    assert r3.status == "cached" and r3.sources[0]["status"] == "stale_cache" and r3.sources[0]["age_days"] == 4000
    assert "сохранённый ответ" in r3.message


def test_offline_without_cache_gives_no_observation_not_a_guess(sandbox):
    r = climate.PROBE.run(REC, online=False)
    assert r.status == "offline" and r.observations == [] and sandbox == []


def test_unreachable_host_is_an_error_and_breaker_stops_retries(monkeypatch):
    n = []
    monkeypatch.setattr(extcache, "http_json", lambda *a, **k: (n.append(1), (_ for _ in ()).throw(OSError("timeout")))[1])
    assert climate.PROBE.run(REC, True).status == "error"
    climate.PROBE.run(REC, True)
    climate.PROBE.run(REC, True)
    assert len(n) == extcache.BREAKER_FAILS                          # дальше хост считается недоступным и мы не ждём таймаутов


def test_api_key_is_never_stored_in_cache_url(tmp_path, monkeypatch):
    monkeypatch.setattr(extcache, "http_json", lambda *a, **k: {"cells": []})
    _, meta = extcache.fetch_json("opencellid", "https://opencellid.org/x", {"key": "SECRET", "a": 1}, online=True, max_age_days=1)
    assert "SECRET" not in meta["url"]
    assert all("SECRET" not in p.read_text(encoding="utf-8") for p in (tmp_path / "raw" / "ext").rglob("*.json"))


# ───────── климат и воздух ─────────

def test_climate_values_and_evidence():
    r = climate.PROBE.run(REC, True)
    o = by_feature(r)
    assert o["winter_temp"].value["v"] == -18.0 and o["winter_temp"].source == "open_meteo"
    assert abs(o["winter_sunshine"].value["v"] - 3.0) < 0.01
    assert o["winter_temp"].confidence <= 0.72 and o["winter_temp"].evidence[0]["url"].startswith("https://archive-api.open-meteo.com")
    assert "9 км" in o["winter_temp"].method


def test_air_quality_averages_and_names_the_model_limit():
    r = air.PROBE.run(REC, True)
    o = r.observations[0]
    assert o.feature == "air_quality" and 4.0 < o.value["v"] < 12.0
    assert o.value["detail"]["winter_mean"] == 12.0 and "не датчик" in o.value["summary"] and o.confidence <= 0.5


def test_air_cache_survives_date_change(monkeypatch):
    air.PROBE.run(REC, True)
    import datetime
    class D(datetime.date):
        @classmethod
        def today(cls):
            return datetime.date(2030, 1, 1)
    monkeypatch.setattr(air, "date", D)
    assert air.PROBE.run(REC, False).status == "cached"              # ключ кэша не зависит от «сегодняшней» даты запроса


# ───────── Википедия, Викисклад ─────────

def test_wiki_finds_own_building_and_lists_sights():
    r = wiki.PROBE.run(REC, True)
    o = by_feature(r)
    assert o["building_history"].value["v"] == "Байкальская Волна (гостиница)"
    assert "1958" in o["building_history"].value["summary"] and "охранном" in o["building_history"].value["summary"]
    assert o["sights_nearby"].value["v"] == 2                        # сама гостиница в «достопримечательности» не входит
    assert any(e.get("url", "").startswith("https://ru.wikipedia.org/wiki/") for e in o["building_history"].evidence)


def test_wiki_does_not_claim_history_for_an_unrelated_name():
    r = wiki.PROBE.run({**REC, "name": "Хостел Уют"}, True)
    assert "building_history" not in by_feature(r) and "sights_nearby" in by_feature(r)
    assert "не нашли" in r.message


def test_wiki_name_must_be_close_in_space_too():
    far = dict(REC, lat=51.90, lon=104.9)
    assert "building_history" not in by_feature(wiki.PROBE.run(far, True))


def test_commons_photos_have_author_license_and_links():
    r = commons.PROBE.run(REC, True)
    o = r.observations[0]
    it = o.value["detail"]["items"][0]
    assert o.value["v"] == 4 and it["author"] == "Автор" and it["license"] == "CC BY-SA 4.0" and it["url"].startswith("https://commons.wikimedia.org/")


# ───────── вышки ─────────

def test_masts_no_pack_is_offline_not_zero():
    r = masts.PROBE.run(REC, False)
    assert r.status == "offline" and r.observations == []


def test_masts_zero_towers_stays_low_confidence_and_does_not_promise_no_signal():
    clat, clon = osmextra.around_center(REC["lat"], REC["lon"])
    osmextra.save_around(clat, clon, {"masts": [], "peaks": [], "center": [clat, clon], "fetched_at": "2026-10-01T00:00:00+00:00"})
    o = masts.PROBE.run(REC, False).observations[0]
    assert o.value["v"] == 0 and o.confidence <= 0.25 and "не значит" in o.value["summary"]


def test_masts_counted_with_operator():
    clat, clon = osmextra.around_center(REC["lat"], REC["lon"])
    osmextra.save_around(clat, clon, {"masts": [{"lat": REC["lat"] + 0.02, "lon": REC["lon"], "op": "МТС", "tech": ["lte"]}], "peaks": [], "center": [clat, clon], "fetched_at": "2026-10-01T00:00:00+00:00"})
    o = masts.PROBE.run(REC, False).observations[0]
    assert o.value["v"] == 1 and "МТС" in o.value["summary"] and o.confidence <= 0.4


# ───────── интеграция: пробы → наблюдения → факты ─────────

def test_run_all_saves_observations_and_status_journal(world):
    hid = "test_dvor_i_zakat"
    rec = pipeline.hotel_record(hid)
    rep = run_all(hid, rec, online=True)
    assert {r["probe"] for r in rep} == {"climate", "air", "wiki", "commons", "masts"}
    assert next(r for r in rep if r["probe"] == "masts")["status"] == "offline"
    reconcile.reconcile_hotel(hid)
    f = {r["feature"]: r for r in db.rows("SELECT * FROM facts WHERE hotel_id=?", (hid,))}
    assert "winter_temp" in f and f["winter_temp"]["status"] == "likely", "реанализ — модель: «подтверждено» одна запись дать не может"
    assert f["street_photos"]["status"] in ("weak", "likely")
    assert any(r["probe"] == "masts" and r["status"] == "offline" for r in runs(hid))


# ───────── Overpass: мёртвые зеркала не ждём снова ─────────

def test_overpass_skips_a_mirror_that_failed_and_raises_fast_when_all_dead(monkeypatch):
    from app import net
    net._overpass_dead.clear()
    calls = []

    class S:
        def post(self, url, data=None, timeout=None):
            calls.append(url)
            raise OSError("timeout")

    monkeypatch.setattr(net, "session", lambda: S())
    with pytest.raises(RuntimeError):
        net._overpass("q")
    first = len(calls)
    assert first == len(net.OVERPASS)
    with pytest.raises(RuntimeError):
        net._overpass("q")
    assert len(calls) == first + 1                 # все зеркала помечены мёртвыми: пробуем одно, а не ждём все снова
    net._overpass_dead.clear()
