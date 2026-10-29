from datetime import date

from app.geo import noise, sun, view, walk, surroundings
from app.geo.proj import angdiff, compass_ru
from app.geo.scene import footprint_facades
from tests.geohelp import hotel, make_scene, rect, LAT


def test_angdiff_wraps():
    assert angdiff(350, 10) == -20
    assert angdiff(10, 350) == 20
    assert abs(angdiff(180, 0)) == 180


def test_sunset_azimuth_matches_astronomy():
    # лето: закат на северо-западе (~322° на 60° с.ш.), зима — на юго-западе (~217°)
    assert abs(sun.sun_azimuth_at_altitude(59.93, date(2026, 6, 21), 0.0) - 322.5) < 1.5
    assert abs(sun.sun_azimuth_at_altitude(59.93, date(2026, 12, 21), 0.0) - 217.5) < 1.5
    assert abs(sun.sun_azimuth_at_altitude(59.93, date(2026, 3, 21), 0.0) - 270.0) < 2.5


def test_white_nights_spb_but_not_moscow_south():
    p = sun.white_night_period(59.93)
    assert p and p["months"] == [5, 6, 7]
    assert sun.white_night_period(40.0) is None


def test_footprint_facades_have_outward_normals():
    h = hotel()
    fs = {round(f["az"]) for f in footprint_facades(h)}
    assert fs == {0, 90, 180, 270}


def test_sunset_visible_from_ground_floor_in_open_field():
    sc = make_scene([hotel()])
    r = view.sunset_analysis(sc, LAT)
    assert r["v"] is True
    assert r["detail"]["best_side"]["min_floor"] == 1
    assert "круглый год" in r["summary"] or "—" in r["summary"]


def test_wall_in_the_west_pushes_sunset_to_upper_floors():
    wall = rect("wall", -35, 0, 20, 200, 30.0, 9)       # 30 м, вплотную к западному фасаду (≈13 м до стены)
    sc = make_scene([hotel(), wall])
    r = view.sunset_analysis(sc, LAT)
    west = next(s for s in r["detail"]["sides"] if s["side"] == "запад")
    floors = {fl for fl in west["by_month"].values() if fl is not None}
    assert floors == {10}, west


def test_sunset_not_visible_when_walled_in_on_every_side():
    ring = [rect(f"w{i}", dx, dy, w, d, 60.0, 19) for i, (dx, dy, w, d) in enumerate([(-30, 0, 10, 200), (30, 0, 10, 200), (0, 30, 200, 10), (0, -30, 200, 10)])]
    sc = make_scene([hotel(levels=6, height=20)] + ring)
    r = view.sunset_analysis(sc, LAT, floors_known=6)
    assert r["v"] is False


def test_landmark_blocked_by_tall_building_until_floor_15():
    target = {"key": "tower", "name": "Башня", "lat": None, "lon": None, "height": 100.0}
    sc = make_scene([hotel(levels=20, height=60), rect("blocker", 300, 0, 40, 400, 60.0, 19)])
    lat, lon = sc.frame.ll(1000, 0)
    target["lat"], target["lon"] = lat, lon
    r = view.landmark_analysis(sc, [target], floors_known=20)
    assert r["v"] is True
    # окно у восточного фасада: до блокера 268,5 м (грань на x=280, окно на x=11,5), до цели 988,5 м.
    # Нужно (60-h)/268,5 <= (100-h)/988,5 + 0,002  =>  h >= 44,35 м  =>  этаж 15 (h = 0,8 + 3,2*(f-1) + 1,7)
    assert r["detail"]["landmarks"][0]["min_floor"] == 15
    assert r["detail"]["landmarks"][0]["side"] == "восток"


def test_landmark_unobstructed_visible_from_first_floor():
    sc = make_scene([hotel()])
    lat, lon = sc.frame.ll(0, 900)
    r = view.landmark_analysis(sc, [{"key": "t", "name": "Т", "lat": lat, "lon": lon, "height": 80.0}])
    assert r["detail"]["landmarks"][0]["min_floor"] == 1


def test_noise_busy_road_hits_the_facing_side():
    road = {"hw": "primary", "name": "Невский пр.", "pts": [(-200, -26), (200, -26)], "nodes": []}
    sc = make_scene([hotel()], roads=[road])
    facades, _ = view.pick_facades(sc, sc.building_at(0, 0))
    r = noise.noise_analysis(sc, facades)
    south = next(f for f in r["facades"] if f["side"] == "юг")
    north = next(f for f in r["facades"] if f["side"] == "север")
    assert south["score"] < 30
    assert north["score"] > south["score"] + 20
    assert r["best"]["side"] in ("север", "северо-восток", "северо-запад")
    assert "Невский" in r["summary"]


def test_noise_quiet_courtyard_scores_high():
    sc = make_scene([hotel()], roads=[{"hw": "residential", "name": "x", "pts": [(-300, 200), (300, 200)], "nodes": []}])
    facades, _ = view.pick_facades(sc, sc.building_at(0, 0))
    assert noise.noise_analysis(sc, facades)["median"] > 80


def test_bar_nearby_adds_noise():
    sc0 = make_scene([hotel()])
    sc1 = make_scene([hotel()], pois=[{"k": "nightclub", "x": 0, "y": -40, "name": "Клуб", "oh": None, "tags": {}}])
    f0, _ = view.pick_facades(sc0, sc0.building_at(0, 0))
    f1, _ = view.pick_facades(sc1, sc1.building_at(0, 0))
    assert noise.noise_analysis(sc1, f1)["worst"]["score"] < noise.noise_analysis(sc0, f0)["worst"]["score"]


def _grid_roads():
    # улица вдоль x на y=0 из узлов 1..6 (каждые 100 м), перпендикулярная магистраль на x=250 (узел 4 — перекрёсток)
    pts = [(-50, 0), (50, 0), (150, 0), (250, 0), (350, 0), (450, 0)]
    street = {"hw": "residential", "name": "ул", "pts": pts, "nodes": [1, 2, 3, 4, 5, 6]}
    main = {"hw": "primary", "name": "пр", "pts": [(250, -200), (250, 0), (250, 200)], "nodes": [10, 4, 11]}
    # продолжение улицы пересекает магистраль: узлы 3 и 5 принадлежат улице; пешеход проходит через узел 4
    return [street, main]


def test_walk_graph_counts_crossing_of_busy_road():
    roads = _grid_roads()
    sc = make_scene([hotel()], roads=roads)
    g = walk.build_graph(sc)
    res = walk.walk_to(sc, g, [(430, 0)])
    assert res["method"] == "graph"
    assert 5 <= res["minutes"] <= 7
    assert res["crossings"] == 1


def test_walk_falls_back_to_straight_line_when_no_graph():
    sc = make_scene([hotel()])
    g = walk.build_graph(sc)
    res = walk.walk_to(sc, g, [(800, 0)])
    assert res["method"].startswith("straight")
    assert res["minutes"] == round(800 * 1.3 / 80)


def test_night_services_counts_only_24h():
    pois = [
        {"k": "pharmacy", "x": 100, "y": 0, "name": "Аптека", "oh": "24/7", "tags": {}},
        {"k": "pharmacy", "x": 90, "y": 0, "name": "Дневная", "oh": "Mo-Fr 09:00-20:00", "tags": {}},
        {"k": "supermarket", "x": 700, "y": 0, "name": "Далеко", "oh": "24/7", "tags": {}},
    ]
    r = surroundings.night_services(make_scene([hotel()], pois=pois))
    assert r["v"] == 1 and "аптека" in r["summary"]


def test_compass_names():
    assert compass_ru(270) == "запад"
    assert compass_ru(315) == "северо-запад"
    assert compass_ru(359) == "север"
