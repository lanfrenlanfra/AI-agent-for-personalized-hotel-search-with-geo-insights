from app.collectors import geo
from tests.geohelp import LAT, LON, hotel, make_scene, rect


def test_side_scoped_observations():
    # справа (на востоке) глухая высокая стена, на западе открыто; улица на севере
    sc = make_scene([hotel(8, 26.0), rect("wall", 60, 0, 10, 200, 60.0, 20)],
                    roads=[{"hw": "primary", "name": "Невский", "pts": [(-300, 40), (300, 40)], "nodes": []}])
    obs = geo.collect({"lat": LAT, "lon": LON, "facts": {"floors": 8}, "region": "x"}, sc, "2026-10-01")
    by = {(o.feature, o.scope) for o in obs}
    assert ("sunset_view", "hotel") in by
    assert ("sunset_view", "room:west") in by
    assert ("quiet", "room:yard") in by
    assert ("quiet", "room:street") in by
    west = next(o for o in obs if o.feature == "sunset_view" and o.scope == "room:west")
    assert west.value["v"] is True
    yard = next(o for o in obs if o.scope == "room:yard")
    street = next(o for o in obs if o.scope == "room:street")
    assert yard.value["v"] > street.value["v"]
