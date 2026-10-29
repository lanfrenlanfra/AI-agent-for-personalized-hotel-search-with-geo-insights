"""Анализ рельефа вокруг отеля: угол горизонта, солнечные часы, видимость вершин и целей.

Алгоритм горизонта — тот же, что в r.horizon (GRASS GIS): из точки наблюдателя по каждому
азимуту идём по лучу и берём наибольший угол подъёма над рельефом, с поправкой на кривизну
Земли и атмосферную рефракцию (коэффициент 0,13): далёкие объекты кажутся чуть ниже.
Здания и лес сюда не входят — они считаются в сцене OSM; итоговое препятствие в модели видимости —
максимум из угла застройки и угла рельефа.
"""
from __future__ import annotations

import math
from datetime import date

import numpy as np

from ..dem import Terrain
from . import sun
from .proj import Frame, R_EARTH

REFRACTION_K = 0.13
MAX_RANGE_M = 80_000.0
DEG_M = 111_195.0
_DIST = np.concatenate([np.arange(60.0, 3000.0, 40.0), np.arange(3000.0, 15000.0, 100.0), np.arange(15000.0, MAX_RANGE_M + 1, 250.0)])
_DROP = (1.0 - REFRACTION_K) * _DIST ** 2 / (2.0 * R_EARTH)    # видимое «опускание» далёких объектов, м
NO_HORIZON = -90.0


class TerrainView:
    def __init__(self, terrain: Terrain, lat0: float, lon0: float):
        self.terrain = terrain
        self.lat0, self.lon0 = lat0, lon0
        self.frame = Frame(lat0, lon0)
        self.ground0 = self._ground_at_hotel()
        self._prof: dict[int, np.ndarray] = {}

    # ---------- высоты ----------
    def _ground_at_hotel(self) -> float:
        """Медиана по девяти точкам в 120 м: одна «битая» ячейка (например, береговая) не сдвинет высоту отеля."""
        pts = [(0.0, 0.0)] + [(120.0 * math.sin(math.radians(a)), 120.0 * math.cos(math.radians(a))) for a in range(0, 360, 45)]
        lat = np.array([self.lat0 + y / DEG_M for _, y in pts])
        lon = np.array([self.lon0 + x / (DEG_M * math.cos(math.radians(self.lat0))) for x, _ in pts])
        z = self.terrain.elevation(lat, lon)
        z = z[~np.isnan(z)]
        return float(np.median(z)) if z.size else float(self.terrain.elevation_at(self.lat0, self.lon0) or 0.0)

    def ground_rel_xy(self, x: float, y: float) -> float | None:
        """Высота земли в точке (x, y) сцены относительно земли у отеля, м."""
        lat, lon = self.frame.ll(x, y)
        z = self.terrain.elevation_at(lat, lon)
        return None if z is None else z - self.ground0

    # ---------- профили по азимутам ----------
    @staticmethod
    def _key(az: float) -> int:
        return int(round((az % 360.0) * 4.0)) % 1440     # шаг 0,25°

    def _profiles(self, keys: list[int]) -> None:
        todo = [k for k in dict.fromkeys(keys) if k not in self._prof]
        if not todo:
            return
        az = np.radians(np.array(todo, dtype=float) / 4.0)[:, None]
        lat = self.lat0 + _DIST[None, :] * np.cos(az) / DEG_M
        lon = self.lon0 + _DIST[None, :] * np.sin(az) / (DEG_M * math.cos(math.radians(self.lat0)))
        z = self.terrain.elevation(lat, lon)
        for i, k in enumerate(todo):
            self._prof[k] = z[i]

    def horizon_angles(self, azs, eye_h: float = 1.7) -> np.ndarray:
        """Угол горизонта по рельефу (градусы над математическим горизонтом) для каждого азимута; eye_h — высота глаз над землёй у отеля."""
        azs = np.atleast_1d(np.asarray(azs, dtype=float))
        keys = [self._key(a) for a in azs]
        self._profiles(keys)
        eye = self.ground0 + eye_h
        out = np.empty(len(azs))
        for i, k in enumerate(keys):
            z = self._prof[k]
            ang = np.degrees(np.arctan2(z - eye - _DROP, _DIST))
            ang = ang[~np.isnan(ang)]
            out[i] = float(ang.max()) if ang.size else NO_HORIZON
        return out

    def horizon_angle(self, az: float, eye_h: float = 1.7) -> float:
        return float(self.horizon_angles([az], eye_h)[0])

    def skyline(self, azs, eye_h: float = 1.7) -> list[dict]:
        """Силуэт рельефа для рисования: [{az, el}], el не ниже −2°."""
        ang = self.horizon_angles(azs, eye_h)
        return [{"az": round(float(a) % 360.0, 1), "el": round(max(float(e), -2.0), 2)} for a, e in zip(azs, ang)]

    def distance_to_skyline(self, az: float, eye_h: float = 1.7) -> float | None:
        """Расстояние до точки, которая задаёт горизонт по этому азимуту, м."""
        k = self._key(az)
        self._profiles([k])
        z = self._prof[k]
        ang = np.degrees(np.arctan2(z - (self.ground0 + eye_h) - _DROP, _DIST))
        if np.all(np.isnan(ang)):
            return None
        return float(_DIST[int(np.nanargmax(ang))])

    # ---------- солнце ----------
    def sun_summary(self, lat: float, d: date, eye_h: float = 1.7, step_deg: float = 0.5) -> dict:
        """Прямой солнечный свет в день d по рельефу: часы, задержка восхода и потеря света на закате (мин)."""
        H = np.arange(-180.0, 180.0 + 1e-9, step_deg)
        alt, az = sun.sun_position(lat, d, H)
        day = alt > sun.SUN_RISE_SET_ALT
        if not day.any():
            return {"flat_hours": 0.0, "hours": 0.0, "sunrise_delay_min": 0, "sunset_loss_min": 0, "polar_night": True}
        h_t = np.full(alt.shape, NO_HORIZON)
        idx = np.where(alt > -5.0)[0]
        h_t[idx] = self.horizon_angles(az[idx], eye_h)
        # диск виден, когда центр поднялся выше рельефа на ту же «поправку на диск и рефракцию», что и над ровным горизонтом
        vis = alt > sun.SUN_RISE_SET_ALT + np.maximum(h_t, 0.0)
        per_step_h = step_deg / 15.0
        flat_hours = float(day.sum() * per_step_h)
        hours = float(vis.sum() * per_step_h)
        d_idx, v_idx = np.where(day)[0], np.where(vis)[0]
        if v_idx.size:
            rise_delay = float((H[v_idx[0]] - H[d_idx[0]]) * 4.0)      # 1° часового угла = 4 минуты
            set_loss = float((H[d_idx[-1]] - H[v_idx[-1]]) * 4.0)
        else:
            rise_delay = set_loss = flat_hours * 30.0
        return {"flat_hours": round(flat_hours, 2), "hours": round(hours, 2), "sunrise_delay_min": int(round(max(0.0, rise_delay))),
                "sunset_loss_min": int(round(max(0.0, set_loss))), "polar_night": False}

    # ---------- вершины и цели ----------
    def bearing_dist(self, lat: float, lon: float) -> tuple[float, float]:
        dy = (lat - self.lat0) * DEG_M
        dx = (lon - self.lon0) * DEG_M * math.cos(math.radians(self.lat0))
        return math.degrees(math.atan2(dx, dy)) % 360.0, math.hypot(dx, dy)

    def peak_angle(self, peak: dict, eye_h: float = 1.7) -> dict | None:
        """Видна ли вершина по рельефу: угол вершины против угла рельефа на пути к ней."""
        az, dist = self.bearing_dist(peak["lat"], peak["lon"])
        if dist < 400.0 or dist > MAX_RANGE_M:
            return None
        ele = peak.get("ele")
        if ele is None:
            z = self.terrain.elevation_at(peak["lat"], peak["lon"])
            if z is None:
                return None
            ele = z
        eye = self.ground0 + eye_h
        drop = (1.0 - REFRACTION_K) * dist ** 2 / (2.0 * R_EARTH)
        ang_peak = math.degrees(math.atan2(ele - eye - drop, dist))
        k = self._key(az)
        self._profiles([k])
        z = self._prof[k]
        before = _DIST < max(dist - 800.0, 0.0)           # сам массив вершины не считаем препятствием
        ang = np.degrees(np.arctan2(z - eye - _DROP, _DIST))
        ang = ang[before & ~np.isnan(ang)]
        block = float(ang.max()) if ang.size else NO_HORIZON
        return {"az": az, "distance_m": dist, "ele": ele, "angle": ang_peak, "terrain_block": block, "visible": ang_peak >= block + 0.15}

    def line_clear(self, x0: float, y0: float, h0: float, x1: float, y1: float, top1: float, margin: float = 1.0) -> bool:
        """Прямая видимость от окна (высота h0 над землёй отеля) до верхней точки цели (top1 м над землёй отеля) поверх рельефа."""
        D = math.hypot(x1 - x0, y1 - y0)
        if D < 80.0:
            return True
        n = max(4, int(D / 30.0))
        t = np.linspace(0.0, 1.0, n + 1)[1:-1]
        xs, ys = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        lat = self.lat0 + ys / DEG_M
        lon = self.lon0 + xs / (DEG_M * math.cos(math.radians(self.lat0)))
        z = self.terrain.elevation(lat, lon) - self.ground0
        line = h0 + (top1 - h0) * t
        drop = (1.0 - REFRACTION_K) * (D * t) * (D * (1 - t)) / (2.0 * R_EARTH)   # кривизна для хорды
        z = z + drop
        ok = np.isnan(z) | (z + margin <= line)
        return bool(ok.all())
