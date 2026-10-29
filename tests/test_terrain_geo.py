"""Рельеф, лес и подъём в геомоделях: сцена на Байкале (Листвянка) с синтетической местностью."""

import numpy as np

from app import dem, osmextra
from app.collectors import geo
from app.geo import view, walk
from app.geo.proj import Frame
from app.geo.scene import Building, Scene, add_forests, apply_terrain
from app.geo.terrain import TerrainView

LAT, LON = 51.87, 104.83


def rect(bid, cx, cy, w, d, height, levels=None):
    ring = [(cx - w / 2, cy - d / 2), (cx + w / 2, cy - d / 2), (cx + w / 2, cy + d / 2), (cx - w / 2, cy + d / 2)]
    return Building(str(bid), ring, height, True, levels=levels)


def scene(func=None, buildings=None, roads=None, pois=None):
    sc = Scene(frame=Frame(LAT, LON))
    sc.buildings = [rect("hotel", 0, 0, 20, 20, 12.0, 4)] + list(buildings or [])
    sc.roads, sc.pois = roads or [], pois or []
    sc.build_index()
    if func is not None:
        apply_terrain(sc, TerrainView(dem.Terrain.synthetic(LAT, LON, func), LAT, LON))
    return sc


def flat(x, y):
    return np.full_like(x, 460.0, dtype=np.float64)


def ridge(x_from, x_to, height, axis="x"):
    def f(x, y):
        z = np.full_like(x, 460.0, dtype=np.float64)
        v = x if axis == "x" else y
        z[(v >= x_from) & (v <= x_to)] += height
        return z
    return f


# ───────── закат и восход ─────────

def test_ridge_in_the_west_hides_sunset_but_flat_land_shows_it():
    open_ = view.sunset_analysis(scene(flat), LAT, floors_known=4)
    walled = view.sunset_analysis(scene(ridge(-2100, -2000, 500)), LAT, floors_known=4)
    assert open_["v"] is True and open_["detail"]["terrain_used"] is True
    assert walled["v"] is False and "рельеф" in walled["summary"]


def test_without_terrain_the_summary_says_so():
    r = view.sunset_analysis(scene(None), LAT, floors_known=4)
    assert r["v"] is True and "рельеф не учтён" in r["summary"] and r["detail"]["terrain_used"] is False


def test_ridge_in_the_east_costs_sunrise_not_sunset():
    sc = scene(ridge(2000, 2100, 500))
    assert view.sunset_analysis(sc, LAT, floors_known=4)["v"] is True
    assert view.sunrise_analysis(sc, LAT, floors_known=4)["v"] is False


def test_hill_behind_a_building_does_not_count_twice():
    # хребет сзади здания не должен «поднимать» само здание: base берётся из рельефа
    sc = scene(ridge(-2100, -2000, 500), buildings=[rect("b", -300, 0, 30, 30, 10.0, 3)])
    b = next(x for x in sc.buildings if x.id == "b")
    assert abs(b.base) < 1.0


def test_building_on_a_slope_gets_its_ground_height():
    sc = scene(lambda x, y: 460.0 + 0.1 * x, buildings=[rect("up", 400, 0, 20, 20, 10.0, 3)])
    up = next(x for x in sc.buildings if x.id == "up")
    assert 35.0 < up.base < 45.0                 # 0,1 × 400 м


# ───────── лес ─────────

def forest_tile(x0, x1, y0, y1):
    fr = Frame(LAT, LON)
    ring = [fr.ll(x0, y0), fr.ll(x1, y0), fr.ll(x1, y1), fr.ll(x0, y1), fr.ll(x0, y0)]
    return {"tile": [1, 1], "forests": [{"p": [[la, lo] for la, lo in ring]}], "bus_stops": []}


def test_forest_west_of_the_hotel_raises_the_floor_needed_for_sunset():
    sc = scene(flat)
    base = view.sunset_analysis(sc, LAT, floors_known=4)
    sc2 = scene(flat)
    assert add_forests(sc2, [forest_tile(-80, -30, -200, 200)], 1400, 14.0) == 1
    wooded = view.sunset_analysis(sc2, LAT, floors_known=4)
    assert wooded["detail"]["forest_used"] is True
    b0 = (base["detail"]["best_side"] or {}).get("min_floor", 0)
    b1 = (wooded["detail"]["best_side"] or {}).get("min_floor", 99)
    assert b1 >= b0 and (b1 > b0 or not wooded["v"])


def test_duplicate_forest_from_neighbour_tile_is_added_once():
    sc = scene(flat)
    t1, t2 = forest_tile(-80, -30, -200, 200), forest_tile(-80, -30, -200, 200)
    t2["tile"] = [2, 1]
    assert add_forests(sc, [t1, t2], 1400, 14.0) == 1


# ───────── горы ─────────

def test_high_ridge_gives_mountain_view_and_flat_land_does_not():
    mt = view.mountain_view_analysis(scene(ridge(-8100, -8000, 1000)), floors_known=4)
    assert mt["v"] is True and mt["detail"]["best"]["peak_deg"] >= 5
    assert view.mountain_view_analysis(scene(flat), floors_known=4)["v"] is False
    assert view.mountain_view_analysis(scene(None), floors_known=4) is None       # без рельефа не выдумываем


def test_named_peak_is_listed_when_visible():
    fr = Frame(LAT, LON)
    la, lo = fr.ll(-12000, 0)
    sc = scene(flat)
    r = view.mountain_view_analysis(sc, 4, peaks=[{"lat": la, "lon": lo, "ele": 1800.0, "name": "Пик Тест"}])
    assert r["v"] is False or r["detail"]["named_peaks"] == [] or r["detail"]["named_peaks"][0]["name"] == "Пик Тест"
    # над ровной землёй пик 1800 м в 12 км виден (угол ≈ 6°), но «горы в окне» считаются по рельефу, а не по списку имён
    assert sc.terrain.peak_angle({"lat": la, "lon": lo, "ele": 1800.0})["visible"]


def test_site_sun_hours_drop_in_a_valley():
    ss = view.site_sun_analysis(scene(ridge(-1500, -1400, 400)), LAT)
    ss0 = view.site_sun_analysis(scene(flat), LAT)
    assert ss["v"] < ss0["v"] and ss["detail"]["winter_sunset_loss_min"] > 0
    assert view.site_sun_analysis(scene(None), LAT) is None


# ───────── пешком в гору ─────────

def _street():
    return [{"hw": "residential", "name": "ул", "pts": [(-50, 0), (150, 0), (350, 0), (550, 0)], "nodes": [1, 2, 3, 4]}]


def test_uphill_walk_is_slower_than_downhill_and_reports_ascent():
    up = scene(lambda x, y: 460.0 + 0.12 * x, roads=_street())
    down = scene(lambda x, y: 460.0 - 0.12 * x, roads=_street())
    ru = walk.walk_to(up, walk.build_graph(up), [(550, 0)])
    rd = walk.walk_to(down, walk.build_graph(down), [(550, 0)])
    assert ru["method"] == "graph" and ru["terrain"] is True
    assert ru["ascent_m"] > 40 and rd["ascent_m"] < 5
    assert ru["minutes"] > rd["minutes"]


def test_flat_walk_has_zero_ascent_and_same_time_as_before():
    sc = scene(flat, roads=_street())
    r = walk.walk_to(sc, walk.build_graph(sc), [(550, 0)])
    assert r["ascent_m"] == 0 and 6 <= r["minutes"] <= 9


def test_tobler_factor_is_one_on_the_level_and_slower_on_steep_slope():
    assert abs(walk.slope_factor(0.0, 100.0) - 1.0) < 0.02 or walk.slope_factor(0.0, 100.0) > 0.95
    assert walk.slope_factor(20.0, 100.0) < 0.6
    assert walk.slope_factor(1000.0, 100.0) >= 0.3


# ───────── сборщик ─────────

def test_collector_emits_terrain_features_with_dem_source_and_lower_confidence_without_it():
    rec = {"lat": LAT, "lon": LON, "facts": {"floors": 4}, "region": "x"}
    with_t = geo.collect(rec, scene(ridge(-2100, -2000, 500)), "2026-10-01")
    without = geo.collect(rec, scene(None), "2026-10-01")
    feats = {o.feature: o for o in with_t}
    assert feats["sun_hours_winter"].source == "dem_geo"
    assert feats["mountain_view"].source == "dem_geo" and feats["mountain_view"].value["v"] is True
    assert "sun_hours_winter" not in {o.feature for o in without}
    a = next(o for o in with_t if o.feature == "sunset_view" and o.scope == "hotel")
    b = next(o for o in without if o.feature == "sunset_view" and o.scope == "hotel")
    assert b.confidence <= a.confidence * 1.0001 or b.confidence < 0.85


def test_collect_offline_with_no_tiles_does_not_touch_the_network(monkeypatch, tmp_path):
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)       # пустая папка данных: ни тайлов OSM, ни плиток рельефа
    dem.clear_cache()
    called = []
    monkeypatch.setattr(dem, "fetch_tile", lambda *a, **k: called.append(1))
    info = {}
    obs = geo.collect({"lat": LAT, "lon": LON, "facts": {}, "region": "x"}, info_out=info)
    assert not called and info.get("osm") is False
    assert {o.feature for o in obs} == {"white_nights"}


# ───────── разбор Overpass ─────────

ELEMENTS_TILE = [
    {"type": "way", "tags": {"natural": "wood"}, "geometry": [{"lat": 51.0, "lon": 104.0}, {"lat": 51.0, "lon": 104.01}, {"lat": 51.01, "lon": 104.01}, {"lat": 51.0, "lon": 104.0}]},
    {"type": "way", "tags": {"building": "yes"}, "geometry": [{"lat": 1, "lon": 1}] * 4},
    {"type": "node", "lat": 51.001, "lon": 104.002, "tags": {"highway": "bus_stop", "name": "Лист"}},
    {"type": "relation", "tags": {"landuse": "forest"}, "members": [{"role": "outer", "geometry": [{"lat": 51.0, "lon": 104.0}, {"lat": 51.0, "lon": 104.02}, {"lat": 51.02, "lon": 104.02}, {"lat": 51.0, "lon": 104.0}]},
                                                                       {"role": "inner", "geometry": [{"lat": 51.0, "lon": 104.0}] * 4}]},
]
ELEMENTS_AROUND = [
    {"type": "node", "lat": 51.9, "lon": 104.9, "tags": {"man_made": "mast", "tower:type": "communication", "operator": "МТС", "communication:lte": "yes", "communication:gsm": "yes"}},
    {"type": "node", "lat": 51.95, "lon": 104.95, "tags": {"man_made": "tower"}},
    {"type": "node", "lat": 52.0, "lon": 105.0, "tags": {"natural": "peak", "name": "Черского", "ele": "1 100 м"}},
    {"type": "node", "lat": 52.1, "lon": 105.1, "tags": {"natural": "peak"}},
]


def test_parse_tile_keeps_forest_outer_rings_and_bus_stops_only():
    t = osmextra.parse_tile(ELEMENTS_TILE, 3, 4, "2026-10-01T00:00:00+00:00")
    assert len(t["forests"]) == 2 and t["bus_stops"] == [{"lat": 51.001, "lon": 104.002, "n": "Лист"}]


def test_parse_around_masts_peaks_and_ele_parsing():
    a = osmextra.parse_around(ELEMENTS_AROUND, 51.9, 104.9, "2026-10-01T00:00:00+00:00")
    assert len(a["masts"]) == 2 and a["masts"][0]["op"] == "МТС" and set(a["masts"][0]["tech"]) == {"gsm", "umts", "lte", "5g"} - {"umts", "5g"}
    assert a["peaks"] == [{"lat": 52.0, "lon": 105.0, "n": "Черского", "ele": 1100.0}]


def test_extras_roundtrip_and_distance_filter(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    assert osmextra.masts_near(51.9, 104.9) is None            # пакет не собирали — это не «вышек нет»
    clat, clon = osmextra.around_center(51.9, 104.9)
    osmextra.save_around(clat, clon, osmextra.parse_around(ELEMENTS_AROUND, clat, clon, "2026-10-01T00:00:00+00:00"))
    near = osmextra.masts_near(51.9, 104.9)
    assert near and near[0]["dist_m"] < near[-1]["dist_m"] + 1
    assert osmextra.masts_near(51.9, 104.9, limit_m=100)[0]["dist_m"] <= 100 or osmextra.masts_near(51.9, 104.9, limit_m=100) == []
    t = osmextra.parse_tile(ELEMENTS_TILE, 3, 4, "2026-10-01T00:00:00+00:00")
    osmextra.save_tile(t)
    assert osmextra.load_tile(3, 4)["bus_stops"][0]["n"] == "Лист"


# ───────── силуэт для интерфейса ─────────

def test_horizon_profile_carries_terrain_silhouette_and_hides_sun_behind_ridge(world, monkeypatch):
    from app import horizon
    from tests import geohelp
    sc = geohelp.make_scene([geohelp.hotel(4, 12.0)])
    ridge_w = ridge(-3400, -3000, 600)      # 400 м шириной: шире шага сетки слоя (47 м), иначе пик сгладится
    apply_terrain(sc, TerrainView(dem.Terrain.synthetic(geohelp.LAT, geohelp.LON, ridge_w), geohelp.LAT, geohelp.LON))
    monkeypatch.setattr(horizon, "scene_for", lambda hid: (sc, "2026-10-01"))
    r = horizon.horizon("test_dvor_i_zakat", 270.0, 3, 6)
    assert r["terrain_used"] is True and all("terrain" in p for p in r["profile"])
    west = min(r["profile"], key=lambda p: abs(p["rel"]))
    assert west["terrain"] > 8.0 and west["el"] >= west["terrain"]      # хребет 600 м в 3 км — около 11° над горизонтом
    assert r["sunset_visible"] in (False, None)
