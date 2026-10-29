"""Геосборщик: всё, что можно вычислить по координатам и OpenStreetMap.

Для каждого отеля собирается сцена из тайлов OSM вокруг (радиус 1,4 км) и считаются:
закат, вид на достопримечательности и воду, шум по фасадам, пешие маршруты,
ночные места, дети, парковка, зарядки, стройка, возраст здания, белые ночи.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import yaml

from .. import dem, osmextra, osmtiles
from ..config import settings
from ..geo import noise, sun, surroundings, view, walk
from ..geo.proj import Frame
from ..geo.scene import Building, Scene, add_forests, apply_terrain, scene_from_tiles
from ..geo.terrain import TerrainView
from .base import SIDE_KEYS, Obs, today

SCENE_RADIUS = 1400.0
LANDMARKS_FILE = Path(__file__).parent.parent / "features" / "landmarks.yaml"
DEFAULT_CENTER = {"russia/st._petersburg": (59.9398, 30.3146)}


def load_landmarks() -> list[dict]:
    return yaml.safe_load(LANDMARKS_FILE.read_text(encoding="utf-8")).get("landmarks", [])


def overlay_file(hotel_id: str) -> Path:
    return settings.runtime_dir / "overlay" / f"{hotel_id}.json"


def load_overlay(hotel_id: str | None) -> list[dict]:
    """Дополнительные здания «из будущего» (демо актуализации): {bearing, dist, w, d, height}."""
    if not hotel_id:
        return []
    f = overlay_file(hotel_id)
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    except (OSError, ValueError):
        return []


def build_scene(lat: float, lon: float, hotel_id: str | None = None, allow_net: bool = False) -> tuple[Scene | None, str | None, int]:
    """Сцена вокруг отеля: здания, дороги, вода (OSM), лес и остановки (доп. тайлы), рельеф (плитки Terrarium).

    allow_net — докачивать недостающие плитки рельефа. По умолчанию False: сцена строится из того, что лежит на диске,
    и без интернета не зависает.
    """
    tiles, missing = osmtiles.load_tiles(lat, lon, SCENE_RADIUS)
    if not tiles:
        return None, None, len(missing)
    frame = Frame(lat, lon)
    sc = scene_from_tiles(tiles, frame, radius=SCENE_RADIUS)
    extra_tiles, extra_missing = osmextra.load_tiles(lat, lon, SCENE_RADIUS)
    n_forest = add_forests(sc, extra_tiles, SCENE_RADIUS, settings.forest_height_m)
    for t in extra_tiles:
        for st in t.get("bus_stops", []):
            x, y = frame.xy(st["lat"], st["lon"])
            if abs(x) <= SCENE_RADIUS and abs(y) <= SCENE_RADIUS:
                sc.pois.append({"k": "bus_stop", "x": x, "y": y, "name": st.get("n"), "oh": None, "tags": {}})
    terrain, tinfo = dem.get(lat, lon, allow_net=allow_net)
    tinfo = dict(tinfo or {})
    tinfo.update({"used": terrain is not None, "forest_polygons": n_forest, "extra_tiles_missing": len(extra_missing)})
    sc.terrain_info = tinfo
    if terrain is not None:
        apply_terrain(sc, TerrainView(terrain, lat, lon))
    extra = load_overlay(hotel_id)
    for i, o in enumerate(extra):
        b = math.radians(o["bearing"])
        cx, cy = o["dist"] * math.sin(b), o["dist"] * math.cos(b)
        w, d = o.get("w", 40.0) / 2, o.get("d", 25.0) / 2
        ring = [(cx - w, cy - d), (cx + w, cy - d), (cx + w, cy + d), (cx - w, cy + d)]
        sc.buildings.append(Building(f"overlay{i}", ring, float(o["height"]), True, levels=None))
    if extra:
        sc.build_index()
    date_ = max((t.get("fetched_at", "")[:10] for t in tiles), default=today()) or today()
    return sc, date_, len(missing)


def _o(feature: str, res: dict, conf: float, day: str, method: str, source: str = "osm_geo", scope: str = "hotel", evidence: list | None = None) -> Obs:
    value = {"v": res["v"], "summary": res["summary"], "detail": res.get("detail", {})}
    return Obs(feature, value, source, conf, day, method, evidence or [], scope)


NO_TERRAIN_FACTOR = 0.85   # без модели высот закат/вид посчитаны «по плоской местности» — доверяем меньше


def _sun_event_obs(feature: str, word: str, res: dict, conf: float, day: str, method: str, floors: int | None, loss_by_month: dict[int, int] | None, loss_word: str) -> list[Obs]:
    """Наблюдения для заката или восхода: общее по отелю и по сторонам здания."""
    best = res["detail"].get("best_side")
    if loss_by_month and best:
        losses = [loss_by_month.get(m, 0) for m in best["months"]]
        mean = sum(losses) / len(losses) if losses else 0
        if mean >= 10:
            res = dict(res, summary=res["summary"] + f"; рельеф {loss_word} примерно на {round(mean)} мин")
    out = [_o(feature, res, conf, day, method)]
    for sd in res["detail"]["sides"]:
        vis = {m: fl for m, fl in sd["by_month"].items() if fl is not None and (floors is None or fl <= floors)}
        key = SIDE_KEYS.get(sd["side"])
        if not key:
            continue
        if vis:
            fl0 = min(vis.values())
            txt = f'Окна на {sd["side"]}: {word} виден ' + ("с первого этажа" if fl0 == 1 else f"с {fl0}-го этажа") + f' ({view.month_span(sorted(vis))})'
        else:
            txt = f'Окна на {sd["side"]}: {word} не виден'
        out.append(Obs(feature, {"v": bool(vis), "summary": txt, "detail": {"min_floor": min(vis.values()) if vis else None, "months": sorted(vis), "side": sd["side"]}},
                       "osm_geo", conf * 0.95, day, "То же по конкретной стороне здания", [], f"room:{key}"))
    return out


def collect(rec: dict, scene: Scene | None = None, osm_day: str | None = None, allow_net: bool = False, info_out: dict | None = None) -> list[Obs]:
    lat, lon = rec["lat"], rec["lon"]
    obs: list[Obs] = []
    # астрономия работает без OSM
    wn = sun.white_night_period(lat)
    obs.append(Obs("white_nights", {"v": bool(wn), "summary": (f"Белые ночи с {wn['start']} по {wn['end']} (солнце не глубже 9° под горизонтом)" if wn else "Белых ночей здесь нет"),
                                     "detail": wn or {}}, "astronomy", 0.9, today(), "Положение солнца в полночь по широте"))
    if scene is None:
        scene, osm_day, _ = build_scene(lat, lon, rec.get("id"), allow_net=allow_net)
    if scene is None:
        if info_out is not None:
            info_out["osm"] = False
        return obs
    if info_out is not None:
        info_out.update({"osm": True, "terrain": scene.terrain_info, "osm_day": osm_day})
    day = osm_day or today()
    floors = (rec.get("facts") or {}).get("floors") or None
    own = scene.building_at(0.0, 0.0, 30.0)
    own_found = own is not None
    tv = scene.terrain
    # доля зданий с явной высотой рядом — насколько можно доверять 3D-модели
    near = [b for b in scene.buildings if math.hypot((b.bbox[0] + b.bbox[2]) / 2, (b.bbox[1] + b.bbox[3]) / 2) < 600]
    cov_global = (sum(1 for b in near if b.explicit) / len(near)) if near else 0.0
    q_model = (0.45 + 0.4 * cov_global) * (1.0 if own_found else 0.65)
    tfactor = 1.0 if tv is not None else NO_TERRAIN_FACTOR
    layers = "здания OSM" + (" + лес OSM" if scene.forests else "") + (" + рельеф (плитки Terrarium)" if tv is not None else "")

    ss = view.site_sun_analysis(scene, lat)
    sun_months = {m["month"]: m for m in ss["detail"]["months"]} if ss else None
    sunset = view.sunset_analysis(scene, lat, floors)
    sconf = min(0.85, q_model * (0.5 + 0.5 * sunset["detail"]["height_coverage"])) * tfactor
    obs += _sun_event_obs("sunset_view", "закат", sunset, sconf, day,
                          f"{layers}; положение солнца на 15-е число каждого месяца; окно видит ±55° от фасада",
                          floors, {m: v["sunset_loss_min"] for m, v in sun_months.items()} if sun_months else None, "укорачивает вечернее солнце")
    sunrise = view.sunrise_analysis(scene, lat, floors)
    rconf = min(0.85, q_model * (0.5 + 0.5 * sunrise["detail"]["height_coverage"])) * tfactor
    obs += _sun_event_obs("sunrise_view", "восход", sunrise, rconf, day,
                          f"{layers}; положение солнца на 15-е число каждого месяца; окно видит ±55° от фасада",
                          floors, {m: v["sunrise_delay_min"] for m, v in sun_months.items()} if sun_months else None, "отодвигает восход")
    if ss:
        obs.append(Obs("sun_hours_winter", {"v": ss["v"], "summary": ss["summary"], "detail": ss["detail"]}, "dem_geo", 0.7, day,
                       "Положение солнца каждые 2 минуты в 15-е число месяца против профиля горизонта по модели высот (кривизна Земли, рефракция 0,13); здания не учитываются"))
    mv = view.mountain_view_analysis(scene, floors, osmextra.peaks_near(lat, lon))
    if mv:
        obs.append(_o("mountain_view", mv, min(0.7, q_model * 0.9) * (0.9 if mv["detail"].get("named_peaks") else 1.0), day,
                      f"Угол горизонта по модели высот против застройки и леса по секторам ±55° от каждого фасада; {layers}", source="dem_geo"))

    lm = view.landmark_analysis(scene, geo_landmarks(), floors)
    obs.append(_o("landmark_view", lm, min(0.8, q_model * (0.8 if lm.get("far") else 1.0)) * (1.0 if tv is not None else 0.95), day,
                  "Прямая видимость окно → вершина достопримечательности по зданиям на линии взгляда" + (" и по рельефу" if tv is not None else "")))
    wv = view.water_view_analysis(scene, floors)
    obs.append(_o("water_view", wv, min(0.75, q_model * 0.9), day, "Прямая видимость окно → береговая линия в радиусе 350 м" + (" с учётом рельефа" if tv is not None else "")))

    facades, _ = view.pick_facades(scene, own)
    nz = noise.noise_analysis(scene, facades)
    nconf = 0.55 * (1.0 if own_found else 0.75)
    obs.append(Obs("quiet", {"v": nz["median"], "summary": nz["summary"], "detail": {k: nz[k] for k in ("median", "best", "worst", "facades")}},
                   "osm_geo", nconf, day, "Затухание −20·lg(d) от дорог по классу, трамвая, железной дороги, баров и строек; экранирование зданием 12 дБ"))
    for fc in nz["facades"]:
        key = SIDE_KEYS.get(fc["side"])
        if key:
            obs.append(Obs("quiet", {"v": fc["score"], "summary": f'Окна на {fc["side"]}: {fc["score"]}/100' + (f', шумит {fc["top"][0]["label"]}' if fc["top"] else ""),
                                      "detail": {"db": fc["db"], "top": fc["top"]}}, "osm_geo", nconf, day, "Та же модель шума для конкретной стороны", [], f"room:{key}"))
    obs.append(Obs("quiet", {"v": nz["best"]["score"], "summary": f'Самые тихие окна — на {nz["best"]["side"]} ({nz["best"]["score"]}/100); как правило, это двор',
                              "detail": {"side": nz["best"]["side"], "az": nz["best"]["az"]}}, "osm_geo", nconf * 0.85, day,
                   "Лучшая из сторон здания (вероятно, двор)", [], "room:yard"))
    if nz["worst"]["score"] <= nz["best"]["score"] - 8:
        obs.append(Obs("quiet", {"v": nz["worst"]["score"], "summary": f'Самые шумные окна — на {nz["worst"]["side"]} ({nz["worst"]["score"]}/100); как правило, это улица',
                                  "detail": {"side": nz["worst"]["side"], "az": nz["worst"]["az"], "top": nz["worst"]["top"]}}, "osm_geo", nconf * 0.85, day,
                       "Худшая из сторон здания (вероятно, улица)", [], "room:street"))

    ns = surroundings.night_services(scene)
    obs.append(_o("night_services", ns, 0.7 if ns["v"] else 0.45, day, "POI с opening_hours = 24/7 в радиусе 500 м (теги заполнены не везде)"))
    fam = surroundings.family(scene, nz["median"])
    obs.append(_o("family_friendly", fam, 0.5, day, "Эвристика: площадки, пешеходные улицы, тишина"))
    pz = surroundings.pedestrian_zone(scene)
    if not pz.get("unknown"):
        obs.append(_o("pedestrian_zone", pz, 0.6, day, "Классы дорог в 45 м от входа"))
    obs.append(_o("ev_charging_near", surroundings.ev_charging(scene), 0.6, day, "Зарядные станции OSM в 500 м"))
    pk = surroundings.parking_osm(scene)
    if pk["v"]:
        obs.append(_o("parking", pk, 0.35, day, "Парковки OSM в 200 м (не обязательно принадлежат отелю)"))
    obs.append(_o("construction_nearby", surroundings.construction(scene), 0.55, day, "Объекты construction в OSM в 300 м (отметки появляются с задержкой)"))
    h = surroundings.historic(scene)
    if h:
        obs.append(_o("historic_building", h, 0.65, day, "Теги start_date/heritage здания в OSM"))

    # пешие маршруты
    g = walk.build_graph(scene)
    metro = [(p["x"], p["y"]) for p in scene.pois if p["k"] == "subway"]
    r = walk.walk_to(scene, g, metro) if metro else None
    if r:
        obs.append(_walk_obs("walk_to_metro", r, "до метро", day))
    else:
        sub = (rec.get("nearest_subways") or [])
        if sub and sub[0].get("distance"):
            m = sub[0]["distance"] * 1.3
            obs.append(Obs("walk_to_metro", {"v": max(1, round(m / 80)), "summary": f'≈{max(1, round(m / 80))} мин пешком до метро «{sub[0]["name"]}» (по прямой из карточки ×1,3)',
                                              "detail": {"meters": round(m), "method": "card_straight_x1.3", "station": sub[0]["name"]}},
                           "ostrovok_card", 0.45, (rec.get("fetched_at") or today())[:10], "Расстояние до метро из карточки, поправка на извилистость 1,3"))
    stops = [(p["x"], p["y"]) for p in scene.pois if p["k"] == "bus_stop"]
    if stops:
        rbus = walk.walk_to(scene, g, stops)
        if rbus:
            obs.append(_walk_obs("walk_to_bus", rbus, "до остановки автобуса", day, 0.6 if rbus["method"] == "graph" else 0.4))
    cx, cy = scene.frame.xy(*center_for(rec))
    rc = walk.walk_to(scene, g, [(cx, cy)])
    if rc:
        conf = 0.7 if rc["method"] == "graph" else 0.5
        obs.append(_walk_obs("walk_to_center", rc, "до центра", day, conf))
    water_pts = []
    for w in scene.water:
        water_pts.extend(view._densify(w["pts"], 40.0))
    water_pts = [(x, y) for x, y in water_pts if math.hypot(x, y) <= SCENE_RADIUS]
    rw = walk.walk_to(scene, g, water_pts) if water_pts else None
    if rw:
        obs.append(_walk_obs("walk_to_water", rw, "до воды", day))
    beach_pts = []
    for b in scene.beaches:
        beach_pts.extend(view._densify(b["pts"], 40.0))
    for w in scene.water:
        if w["k"] == "coastline":
            beach_pts.extend(view._densify(w["pts"], 60.0))
    beach_pts = [(x, y) for x, y in beach_pts if math.hypot(x, y) <= SCENE_RADIUS]
    rb = walk.walk_to(scene, g, beach_pts) if beach_pts else None
    if rb:
        obs.append(_walk_obs("walk_to_beach", rb, "до пляжа", day))
    else:
        nb = [b for b in (rec.get("nearest_beaches") or []) if isinstance(b, dict)]
        if nb:
            dist = nb[0].get("distance") or nb[0].get("distance_m")
            if dist:
                m = dist * 1.3
                obs.append(Obs("walk_to_beach", {"v": max(1, round(m / 80)), "summary": f"≈{max(1, round(m / 80))} мин пешком до пляжа (по данным Островка ×1,3)",
                                                  "detail": {"meters": round(m), "method": "card_straight_x1.3"}},
                               "ostrovok_card", 0.45, (rec.get("fetched_at") or today())[:10], "Расстояние до пляжа из карточки Островка"))
    return obs


def _walk_obs(feature: str, r: dict, label: str, day: str, conf: float | None = None) -> Obs:
    graph = r["method"] == "graph"
    txt = f'{r["minutes"]} мин пешком {label} ({r["meters"]} м'
    txt += ", по улицам OSM" if graph else ", по прямой ×1,3 — улицы за пределами карты"
    txt += ")"
    if graph and r.get("crossings"):
        txt += f"; по пути {r['crossings']} раз перейти оживлённую улицу"
    elif graph and r.get("crossings") == 0:
        txt += "; без перехода магистралей"
    if graph and r.get("terrain") and r.get("ascent_m", 0) >= 8:
        txt += f"; набор высоты {r['ascent_m']} м"
    c = conf if conf is not None else (0.75 if graph else 0.5)
    return Obs(feature, {"v": r["minutes"], "summary": txt[0].upper() + txt[1:], "detail": r}, "osm_geo", c, day,
               "Кратчайший пеший путь по графу улиц OSM (80 м/мин, ступени и магистрали со штрафом)" if graph else "Прямое расстояние × 1,3")


_LM: list[dict] | None = None


def geo_landmarks() -> list[dict]:
    global _LM
    if _LM is None:
        _LM = load_landmarks()
    return _LM


def center_for(rec: dict) -> tuple[float, float]:
    c = rec.get("region_center")
    if isinstance(c, dict) and c.get("lat") is not None:
        return (c["lat"], c.get("lng", c.get("lon")))
    return DEFAULT_CENTER.get(rec.get("region") or "", (rec["lat"], rec["lon"]))
