"""Локальная метрическая проекция и угловая арифметика."""
from __future__ import annotations

import math

R_EARTH = 6371008.8


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(math.sqrt(a))


class Frame:
    """Плоская система координат (метры) с началом в (lat0, lon0): x — восток, y — север."""

    def __init__(self, lat0: float, lon0: float):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = math.radians(1) * R_EARTH * math.cos(math.radians(lat0))
        self.ky = math.radians(1) * R_EARTH

    def xy(self, lat: float, lon: float) -> tuple[float, float]:
        return ((lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky)

    def ll(self, x: float, y: float) -> tuple[float, float]:
        return (self.lat0 + y / self.ky, self.lon0 + x / self.kx)


def bearing_xy(dx: float, dy: float) -> float:
    """Азимут вектора (dx на восток, dy на север), градусы по часовой от севера, 0..360."""
    return math.degrees(math.atan2(dx, dy)) % 360.0


def angdiff(a: float, b: float) -> float:
    """Знаковая разность углов a-b в (-180, 180]."""
    d = (a - b + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


def compass_ru(az: float) -> str:
    names = ["север", "северо-восток", "восток", "юго-восток", "юг", "юго-запад", "запад", "северо-запад"]
    return names[int(((az % 360) + 22.5) // 45) % 8]


def point_seg_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> tuple[float, float, float]:
    """Расстояние от точки до отрезка + ближайшая точка."""
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy), cx, cy


def point_in_ring(x: float, y: float, ring: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-18) + xi:
            inside = not inside
        j = i
    return inside


def ring_area(ring: list[tuple[float, float]]) -> float:
    """Знаковая площадь (>0 против часовой)."""
    s = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def ray_segment(ox: float, oy: float, dx: float, dy: float, ax: float, ay: float, bx: float, by: float) -> float | None:
    """Пересечение луча O+t*D (t>=0) с отрезком AB. Возвращает t или None."""
    ex, ey = bx - ax, by - ay
    den = dx * ey - dy * ex
    if abs(den) < 1e-12:
        return None
    t = ((ax - ox) * ey - (ay - oy) * ex) / den
    u = ((ax - ox) * dy - (ay - oy) * dx) / den
    if t >= 0.0 and 0.0 <= u <= 1.0:
        return t
    return None
