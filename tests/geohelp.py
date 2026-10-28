"""Помощники для геотестов: сцены из прямоугольников."""
from app.geo.proj import Frame
from app.geo.scene import Building, Scene

LAT, LON = 59.93, 30.33


def rect(bid, cx, cy, w, d, height, levels=None, explicit=True):
    ring = [(cx - w / 2, cy - d / 2), (cx + w / 2, cy - d / 2), (cx + w / 2, cy + d / 2), (cx - w / 2, cy + d / 2)]
    return Building(str(bid), ring, height, explicit, levels=levels)


def make_scene(buildings, roads=None, pois=None, rails=None, water=None, construction=None):
    sc = Scene(frame=Frame(LAT, LON))
    sc.buildings = list(buildings)
    sc.roads = roads or []
    sc.pois = pois or []
    sc.rails = rails or []
    sc.water = water or []
    sc.construction = construction or []
    sc.build_index()
    return sc


def hotel(levels=8, height=26.0):
    return rect("hotel", 0, 0, 20, 20, height, levels)
