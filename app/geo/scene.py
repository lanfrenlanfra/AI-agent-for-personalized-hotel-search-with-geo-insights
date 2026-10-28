"""3D-сцена из OpenStreetMap: здания с высотами, дороги, вода, POI.

Видимость считаем в 2,5D: каждое здание — многоугольник с плоской крышей.
Это простая, но проверяемая модель; её ограничения (нет рельефа внутри квартала,
часть зданий без высот) отражаются в уверенности результата.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .proj import Frame, bearing_xy, point_in_ring, ray_segment, ring_area

LEVEL_H = 3.1          # метров на этаж
DEFAULT_HEIGHT = 18.0  # если в OSM нет ни высоты, ни этажности


@dataclass
class Building:
    id: str
    ring: list[tuple[float, float]]
    height: float
    explicit: bool
    name: str | None = None
    start_date: str | None = None
    heritage: bool = False
    levels: int | None = None
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)

    def __post_init__(self):
        xs = [p[0] for p in self.ring]
        ys = [p[1] for p in self.ring]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))


@dataclass
class Scene:
    frame: Frame
    buildings: list[Building] = field(default_factory=list)
    roads: list[dict] = field(default_factory=list)      # {hw, name, pts[(x,y)], nodes[ids]}
    rails: list[dict] = field(default_factory=list)      # {k, pts}
    water: list[dict] = field(default_factory=list)      # {k, pts, closed}
    beaches: list[dict] = field(default_factory=list)    # {pts}
    pois: list[dict] = field(default_factory=list)       # {k, x, y, name, oh, tags}
    construction: list[dict] = field(default_factory=list)  # {k, x, y}
    cell: float = 80.0
    _grid: dict[tuple[int, int], list[int]] = field(default_factory=dict)

    # ---------- индекс ----------
    def build_index(self) -> None:
        self._grid = {}
        c = self.cell
        for i, b in enumerate(self.buildings):
            x0, y0, x1, y1 = b.bbox
            for gx in range(int(math.floor(x0 / c)), int(math.floor(x1 / c)) + 1):
                for gy in range(int(math.floor(y0 / c)), int(math.floor(y1 / c)) + 1):
                    self._grid.setdefault((gx, gy), []).append(i)

    def candidates_along(self, ox: float, oy: float, ex: float, ey: float) -> list[int]:
        """Индексы зданий в ячейках вдоль отрезка O→E."""
        c = self.cell
        L = math.hypot(ex - ox, ey - oy)
        steps = max(1, int(L / (c * 0.5)))
        seen: set[int] = set()
        cells: set[tuple[int, int]] = set()
        for s in range(steps + 1):
            t = s / steps
            x, y = ox + (ex - ox) * t, oy + (ey - oy) * t
            gx, gy = int(math.floor(x / c)), int(math.floor(y / c))
            for ddx in (-1, 0, 1):
                for ddy in (-1, 0, 1):
                    cells.add((gx + ddx, gy + ddy))
        for cell in cells:
            for i in self._grid.get(cell, ()):
                seen.add(i)
        return list(seen)

    # ---------- запросы ----------
    def building_at(self, x: float, y: float, max_dist: float = 25.0) -> Building | None:
        """Здание, содержащее точку, либо ближайшее в пределах max_dist."""
        best, best_d = None, 1e18
        for b in self.buildings:
            x0, y0, x1, y1 = b.bbox
            if x0 - max_dist <= x <= x1 + max_dist and y0 - max_dist <= y <= y1 + max_dist:
                if point_in_ring(x, y, b.ring):
                    return b
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                d = math.hypot(cx - x, cy - y)
                if d < best_d:
                    best, best_d = b, d
        if best is not None:
            from .proj import point_seg_dist
            dmin = min(
                point_seg_dist(x, y, *best.ring[i], *best.ring[(i + 1) % len(best.ring)])[0]
                for i in range(len(best.ring))
            )
            if dmin <= max_dist:
                return best
        return None

    def obstruction_profile(
        self, ox: float, oy: float, az_deg: float, max_dist: float, exclude: set[str] | None = None
    ) -> list[tuple[float, float, bool]]:
        """Все здания на луче из точки (ox,oy) по азимуту: [(расстояние до входа, высота, explicit)]."""
        dx, dy = math.sin(math.radians(az_deg)), math.cos(math.radians(az_deg))
        ex, ey = ox + dx * max_dist, oy + dy * max_dist
        out: list[tuple[float, float, bool]] = []
        for i in self.candidates_along(ox, oy, ex, ey):
            b = self.buildings[i]
            if exclude and b.id in exclude:
                continue
            ts: list[float] = []
            n = len(b.ring)
            for k in range(n):
                ax, ay = b.ring[k]
                bx, by = b.ring[(k + 1) % n]
                t = ray_segment(ox, oy, dx, dy, ax, ay, bx, by)
                if t is not None and t <= max_dist:
                    ts.append(t)
            if ts:
                d = min(ts)
                if d > 0.5:
                    out.append((d, b.height, b.explicit))
        out.sort()
        return out


# ---------------------------------------------------------------- построение из сырых данных

_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def parse_height(tags: dict) -> tuple[float, bool, int | None]:
    h = tags.get("h")
    lv = tags.get("lv")
    levels = None
    if lv not in (None, ""):
        m = _NUM.search(str(lv))
        if m:
            levels = int(float(m.group(0).replace(",", ".")))
    if h not in (None, ""):
        m = _NUM.search(str(h))
        if m:
            v = float(m.group(0).replace(",", "."))
            if 2.0 <= v <= 600.0:
                return v, True, levels
    if levels and 1 <= levels <= 120:
        return levels * LEVEL_H + 1.5, True, levels
    return DEFAULT_HEIGHT, False, levels


def scene_from_tiles(tiles: list[dict], frame: Frame, radius: float | None = None) -> Scene:
    """Собрать сцену из сырых тайлов (формат data/raw/osm/tiles)."""
    sc = Scene(frame=frame)
    seen_b: set[str] = set()
    seen_r: set[str] = set()

    def inside(x: float, y: float) -> bool:
        return radius is None or (abs(x) <= radius and abs(y) <= radius)

    for t in tiles:
        for b in t.get("buildings", []):
            bid = str(b["id"])
            if bid in seen_b or len(b.get("p", [])) < 3:
                continue
            ring = [frame.xy(la, lo) for la, lo in b["p"]]
            if ring[0] == ring[-1]:
                ring = ring[:-1]
            if len(ring) < 3:
                continue
            cx = sum(p[0] for p in ring) / len(ring)
            cy = sum(p[1] for p in ring) / len(ring)
            if not inside(cx, cy):
                continue
            seen_b.add(bid)
            h, explicit, levels = parse_height(b)
            sc.buildings.append(
                Building(bid, ring, h, explicit, b.get("n"), b.get("sd"), bool(b.get("her")), levels)
            )
        for r in t.get("roads", []):
            rid = str(r["id"])
            if rid in seen_r:
                continue
            seen_r.add(rid)
            pts = [frame.xy(la, lo) for la, lo in r["p"]]
            sc.roads.append({"hw": r["hw"], "name": r.get("n"), "pts": pts, "nodes": r.get("nodes", []), "ow": r.get("ow")})
        for r in t.get("rails", []):
            sc.rails.append({"k": r["k"], "pts": [frame.xy(la, lo) for la, lo in r["p"]]})
        for w in t.get("water", []):
            sc.water.append({"k": w["k"], "pts": [frame.xy(la, lo) for la, lo in w["p"]], "closed": bool(w.get("closed"))})
        for w in t.get("beaches", []):
            sc.beaches.append({"pts": [frame.xy(la, lo) for la, lo in w["p"]]})
        for p in t.get("pois", []):
            x, y = frame.xy(p["lat"], p["lon"])
            sc.pois.append({"k": p["k"], "x": x, "y": y, "name": p.get("n"), "oh": p.get("oh"), "tags": p.get("t") or {}})
        for c in t.get("construction", []):
            x, y = frame.xy(c["lat"], c["lon"])
            sc.construction.append({"k": c.get("k", "building"), "x": x, "y": y})
    sc.build_index()
    return sc


def bearing_between(x0: float, y0: float, x1: float, y1: float) -> float:
    return bearing_xy(x1 - x0, y1 - y0)


def footprint_facades(b: Building, min_len: float = 5.0) -> list[dict]:
    """Фасады здания: середина каждого ребра, внешняя нормаль (азимут), длина."""
    ring = b.ring
    area = ring_area(ring)
    ccw = area > 0
    facades = []
    n = len(ring)
    for i in range(n):
        ax, ay = ring[i]
        bx, by = ring[(i + 1) % n]
        L = math.hypot(bx - ax, by - ay)
        if L < min_len:
            continue
        ex, ey = (bx - ax) / L, (by - ay) / L
        # внешняя нормаль: для CCW-кольца это (ey, -ex)
        nx, ny = (ey, -ex) if ccw else (-ey, ex)
        mx, my = (ax + bx) / 2, (ay + by) / 2
        facades.append(
            {"x": mx, "y": my, "nx": nx, "ny": ny, "az": bearing_xy(nx, ny), "len": L, "wx": mx + nx * 1.5, "wy": my + ny * 1.5}
        )
    return facades


def synthetic_facades(x: float = 0.0, y: float = 0.0, half: float = 10.0) -> list[dict]:
    out = []
    for az in (0, 90, 180, 270):
        nx, ny = math.sin(math.radians(az)), math.cos(math.radians(az))
        out.append({"x": x + nx * half, "y": y + ny * half, "nx": nx, "ny": ny, "az": float(az), "len": 2 * half,
                    "wx": x + nx * (half + 1.5), "wy": y + ny * (half + 1.5)})
    return out
