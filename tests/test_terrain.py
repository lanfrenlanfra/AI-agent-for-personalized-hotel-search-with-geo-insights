import math
from datetime import date

import numpy as np

from app import dem
from app.geo import sun
from app.geo.terrain import TerrainView

LAT, LON = 51.87, 104.83          # Листвянка


def ridge_west(height=300.0, x_from=-3200.0, x_to=-3000.0, base=456.0):
    """Плоская местность и хребет шириной 200 м к западу от отеля."""
    def f(x, y):
        z = np.full_like(x, base, dtype=np.float64)
        z[(x >= x_from) & (x <= x_to)] += height
        return z
    return f


def view_of(func):
    t = dem.Terrain.synthetic(LAT, LON, func)
    return TerrainView(t, LAT, LON)


# ───────── кодирование плиток и геометрия ─────────

def test_terrarium_roundtrip_is_exact_within_a_centimetre():
    e = (np.random.default_rng(1).random((256, 256)) * 2400 - 100).astype(np.float32)
    got = dem.decode_terrarium(dem.encode_terrarium(e))
    assert np.abs(got - e).max() < 0.01


def test_tiles_cover_the_point_and_scale_with_radius():
    small = dem.tiles_for(LAT, LON, 500, 13)
    big = dem.tiles_for(LAT, LON, 5000, 13)
    x, y = dem.world_px(LAT, LON, 13)
    assert (int(x // 256), int(y // 256)) in small
    assert len(big) > len(small)


def test_metres_per_pixel_shrinks_with_latitude():
    assert dem.metres_per_px(60, 11) < dem.metres_per_px(0, 11)
    assert abs(dem.metres_per_px(0, 0) - 156543.03) < 1


def test_synthetic_terrain_returns_the_function_between_layers():
    t = dem.Terrain.synthetic(LAT, LON, lambda x, y: 456.0 + 0.1 * x)
    # 600 м к востоку: подробный слой, 20 км к востоку: грубый слой; линейный склон бильинейная выборка передаёт точно
    kx = 111_195.0 * math.cos(math.radians(LAT))
    for east in (0.0, 600.0, 20_000.0, -30_000.0):
        z = t.elevation_at(LAT, LON + east / kx)
        assert z is not None and abs(z - (456.0 + 0.1 * east)) < 1.0, east


def test_elevation_is_nan_outside_all_layers():
    t = dem.Terrain.synthetic(LAT, LON, lambda x, y: np.full_like(x, 500.0))
    assert t.elevation_at(LAT + 3.0, LON) is None


# ───────── горизонт ─────────

def test_horizon_angle_matches_ridge_geometry():
    v = view_of(ridge_west())
    ang = v.horizon_angle(270.0, eye_h=1.7)
    expect = math.degrees(math.atan2(300.0 - 1.7, 3000.0))        # ближний край хребта на 3000 м
    assert abs(ang - expect) < 0.4, (ang, expect)
    assert v.horizon_angle(90.0) < 0.0                               # на восток — ровно, угол ниже нуля
    assert abs((v.distance_to_skyline(270.0) or 0) - 3000.0) < 300.0


def test_higher_window_sees_over_the_ridge_slightly_lower():
    v = view_of(ridge_west())
    assert v.horizon_angle(270.0, 40.0) < v.horizon_angle(270.0, 1.7)


def test_earth_curvature_hides_a_distant_low_obstacle():
    # 20-метровый вал в 40 км: прямая линия видела бы его под 0,03°, но кривизна Земли (≈110 м) прячет его
    v = view_of(ridge_west(height=20.0, x_from=-40_300.0, x_to=-40_000.0))
    assert v.horizon_angle(270.0, 1.7) < 0.0


def test_ground0_is_robust_to_one_broken_cell():
    def f(x, y):
        z = np.full_like(x, 460.0, dtype=np.float64)
        z[(abs(x) < 30) & (abs(y) < 30)] = -900.0     # «бататметрия» в одной береговой ячейке
        return z
    assert abs(view_of(f).ground0 - 460.0) < 1.0


# ───────── солнце ─────────

def test_sun_position_matches_the_sunset_azimuth_formula():
    d = date(2026, 6, 21)
    # закат: высота −0,833°; азимут из прежней функции с тем же порогом
    H = np.arange(0, 181, 0.05)
    alt, az = sun.sun_position(LAT, d, H)
    i = int(np.argmin(np.abs(alt - 0.0)))
    assert abs(az[i] - sun.sun_azimuth_at_altitude(LAT, d, 0.0)) < 0.5
    alt0, az0 = sun.sun_position(LAT, d, 0.0)
    assert abs(float(az0) - 180.0) < 0.01 and float(alt0) > 55       # полдень: солнце на юге, высоко


def test_ridge_in_the_west_costs_sunset_light_but_not_sunrise():
    v = view_of(ridge_west())
    s = v.sun_summary(LAT, date(2026, 3, 21))
    assert s["sunrise_delay_min"] <= 4
    assert 25 <= s["sunset_loss_min"] <= 60, s
    assert s["hours"] < s["flat_hours"]
    assert abs(s["flat_hours"] - 12.1) < 0.4


def test_open_terrain_has_no_sun_loss():
    v = view_of(lambda x, y: np.full_like(x, 456.0))
    s = v.sun_summary(LAT, date(2026, 6, 21))
    assert s["sunrise_delay_min"] <= 3 and s["sunset_loss_min"] <= 3
    assert abs(s["hours"] - s["flat_hours"]) < 0.2


def test_winter_valley_loses_more_than_summer():
    v = view_of(ridge_west())
    win, summ = v.sun_summary(LAT, date(2026, 12, 21)), v.sun_summary(LAT, date(2026, 6, 21))
    assert win["flat_hours"] < summ["flat_hours"]
    # зимой солнце опускается круче к югу, угол 5° съедает меньше времени, чем весной — но потеря есть и там
    assert win["sunset_loss_min"] > 0


# ───────── вершины и цели ─────────

def peak(az_deg, dist_m, ele, name="Пик"):
    kx = 111_195.0 * math.cos(math.radians(LAT))
    return {"lat": LAT + dist_m * math.cos(math.radians(az_deg)) / 111_195.0, "lon": LON + dist_m * math.sin(math.radians(az_deg)) / kx, "ele": ele, "name": name}


def test_far_peak_over_flat_land_is_visible_and_angle_is_right():
    v = view_of(lambda x, y: np.full_like(x, 456.0))
    r = v.peak_angle(peak(270, 20_000, 1500.0))
    assert r["visible"]
    drop = (1 - 0.13) * 20_000 ** 2 / (2 * 6371008.8)
    assert abs(r["angle"] - math.degrees(math.atan2(1500 - 457.7 - drop, 20_000))) < 0.05


def test_taller_nearer_ridge_hides_the_peak():
    v = view_of(ridge_west(height=900.0, x_from=-10_300.0, x_to=-10_000.0))
    assert not v.peak_angle(peak(270, 20_000, 1500.0))["visible"]


def test_line_of_sight_blocked_by_hill_between():
    v = view_of(ridge_west(height=120.0, x_from=-1000.0, x_to=-900.0))
    assert v.line_clear(0, 0, 10.0, 500, 0, 40.0)                    # в другую сторону — свободно
    assert not v.line_clear(0, 0, 10.0, -2000, 0, 60.0)              # цель за хребтом, ниже его гребня
    assert v.line_clear(0, 0, 10.0, -2000, 0, 300.0)                  # высокая цель видна поверх
