"""Пешие маршруты по графу улиц OpenStreetMap (а не по прямой).

Скорость 80 м/мин; ступени и крупные магистрали — со штрафом; считаем, сколько раз
по пути нужно пересечь оживлённую улицу. Перепад высот в Петербурге ≤ нескольких
метров, поэтому рельеф не учитываем; для холмистых регионов сюда подключается DEM.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from .scene import Scene

SPEED_M_MIN = 80.0
WALKABLE = {
    "pedestrian", "footway", "path", "steps", "living_street", "residential", "service", "unclassified",
    "tertiary", "secondary", "primary", "trunk", "track", "cycleway",
}
PENALTY = {"steps": 1.25, "primary": 1.1, "trunk": 1.4, "secondary": 1.05, "track": 1.2}
MAJOR = {"primary", "trunk", "secondary"}


@dataclass
class Graph:
    coords: dict[int, tuple[float, float]]
    adj: dict[int, list[tuple[int, float, int, str]]]   # node → [(to, cost_m, way_id, hw)]
    node_major: set[int]                                   # узлы, лежащие на магистралях

    def nearest(self, x: float, y: float, limit: float = 150.0) -> int | None:
        best, bd = None, limit
        for n, (nx, ny) in self.coords.items():
            d = math.hypot(nx - x, ny - y)
            if d < bd:
                best, bd = n, d
        return best


def build_graph(scene: Scene) -> Graph:
    coords: dict[int, tuple[float, float]] = {}
    adj: dict[int, list[tuple[int, float, int, str]]] = {}
    major: set[int] = set()
    for wi, r in enumerate(scene.roads):
        hw = r["hw"]
        if hw not in WALKABLE or len(r["pts"]) < 2:
            continue
        nodes = r.get("nodes") or []
        pts = r["pts"]
        if len(nodes) != len(pts):
            continue
        pen = PENALTY.get(hw, 1.0)
        for i, n in enumerate(nodes):
            coords[n] = pts[i]
            if hw in MAJOR | {"trunk"}:
                major.add(n)
        for i in range(len(nodes) - 1):
            a, b = nodes[i], nodes[i + 1]
            d = math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) * pen
            adj.setdefault(a, []).append((b, d, wi, hw))
            adj.setdefault(b, []).append((a, d, wi, hw))
    return Graph(coords, adj, major)


def shortest(g: Graph, src: int, targets: set[int]):
    """Дейкстра от src до ближайшей из targets. Возвращает (cost_m, путь по рёбрам) или None."""
    dist = {src: 0.0}
    prev: dict[int, tuple[int, int, str]] = {}
    pq = [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, 1e18):
            continue
        if u in targets:
            edges = []
            cur = u
            while cur != src:
                p, wid, hw = prev[cur]
                edges.append((p, cur, wid, hw))
                cur = p
            edges.reverse()
            return d, edges
        for v, c, wid, hw in g.adj.get(u, ()):
            nd = d + c
            if nd < dist.get(v, 1e18):
                dist[v] = nd
                prev[v] = (u, wid, hw)
                heapq.heappush(pq, (nd, v))
    return None


def crossings(g: Graph, edges: list[tuple[int, int, int, str]]) -> int:
    """Сколько раз путь пересекает магистраль (узел на магистрали, а идём по другим дорогам)."""
    n = 0
    for e1, e2 in zip(edges, edges[1:]):
        node = e1[1]
        if node in g.node_major and e1[3] not in MAJOR and e2[3] not in MAJOR:
            n += 1
    return n


def walk_to(scene: Scene, g: Graph, targets_xy: list[tuple[float, float]], snap: float = 150.0) -> dict | None:
    """Пеший маршрут от отеля (0,0) до ближайшей из целей. Если граф не достаёт — по прямой ×1,3 (помечено)."""
    if not targets_xy:
        return None
    src = g.nearest(0.0, 0.0, 120.0)
    tnodes: dict[int, tuple[float, float]] = {}
    for tx, ty in targets_xy:
        n = g.nearest(tx, ty, snap)
        if n is not None:
            tnodes[n] = (tx, ty)
    if src is not None and tnodes:
        res = shortest(g, src, set(tnodes))
        if res:
            cost, edges = res
            tx, ty = tnodes[edges[-1][1]] if edges else (0, 0)
            snap_extra = math.hypot(*g.coords[src]) + math.hypot(g.coords[edges[-1][1]][0] - tx, g.coords[edges[-1][1]][1] - ty) if edges else 0.0
            meters = cost + snap_extra
            return {
                "meters": round(meters),
                "minutes": max(1, round(meters / SPEED_M_MIN)),
                "crossings": crossings(g, edges),
                "method": "graph",
                "target": [round(tx), round(ty)],
            }
    straight = min(math.hypot(tx, ty) for tx, ty in targets_xy)
    meters = straight * 1.3
    return {"meters": round(meters), "minutes": max(1, round(meters / SPEED_M_MIN)), "crossings": None, "method": "straight_x1.3", "target": None}
