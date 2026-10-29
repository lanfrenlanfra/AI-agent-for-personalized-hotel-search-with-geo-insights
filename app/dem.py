"""Рельеф: плитки Terrarium (открытый набор Terrain Tiles on AWS) → высоты в метрах над уровнем моря.

Формат плитки: PNG 256×256, высота = R·256 + G + B/256 − 32768 (метры).
Источник данных плиток: комбинация SRTM, GMTED и других открытых моделей рельефа
(https://registry.opendata.aws/terrain-tiles/); на высоких широтах и в горах точность
порядка десятков метров по высоте — этого хватает для угла горизонта, но не для
отдельных построек, поэтому здания по-прежнему берутся из OpenStreetMap.

Слои разной детальности (ближний — подробный, дальний — грубый) нужны, чтобы видеть
и холм за соседним кварталом, и хребет на другом берегу озера, не качая сотни плиток.
Плитки кэшируются как int16 в data/raw/dem/z{Z}/{x}_{y}.npz и лежат в репозитории рядом
с тайлами OSM, поэтому демо работает без интернета.
"""
from __future__ import annotations

import io
import logging
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .config import settings

log = logging.getLogger("lupa.dem")

TILE = 256
URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
ATTRIBUTION = "Terrain Tiles on AWS (Mapzen/Tilezen: SRTM, GMTED и др.)"
# (zoom, радиус в метрах): ближний слой ≈12 м/пиксель, средний ≈47 м, дальний ≈190 м на 52° с. ш.
DEFAULT_LAYERS: tuple[tuple[int, float], ...] = ((13, 1800.0), (11, 9000.0), (9, 75000.0))
MAX_TILES_PER_LAYER = 64


class DemUnavailable(RuntimeError):
    """Рельеф недоступен: нет плиток в кэше и нет сети (или сеть отказала)."""


# ───────────────────────── геометрия веб-меркатора ─────────────────────────

def world_px(lat: float, lon: float, z: int) -> tuple[float, float]:
    """Глобальные пиксельные координаты точки на уровне z."""
    n = TILE * (2 ** z)
    lat = max(-85.0511, min(85.0511, lat))
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def world_px_arr(lat: np.ndarray, lon: np.ndarray, z: int) -> tuple[np.ndarray, np.ndarray]:
    n = TILE * (2 ** z)
    lat = np.clip(lat, -85.0511, 85.0511)
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2.0 * n
    return x, y


def metres_per_px(lat: float, z: int) -> float:
    return 156543.03392804097 * math.cos(math.radians(lat)) / (2 ** z)


def tiles_for(lat: float, lon: float, radius_m: float, z: int) -> list[tuple[int, int]]:
    """Плитки уровня z, покрывающие квадрат со стороной 2·radius_m вокруг точки."""
    r_px = radius_m / metres_per_px(lat, z)
    cx, cy = world_px(lat, lon, z)
    n = 2 ** z
    x0, x1 = int(math.floor((cx - r_px) / TILE)), int(math.floor((cx + r_px) / TILE))
    y0, y1 = int(math.floor((cy - r_px) / TILE)), int(math.floor((cy + r_px) / TILE))
    return [(x % n, y) for x in range(x0, x1 + 1) for y in range(max(0, y0), min(n - 1, y1) + 1)]


# ───────────────────────── кодирование и кэш плиток ─────────────────────────

def decode_terrarium(png: bytes) -> np.ndarray:
    """PNG Terrarium → float32 высоты (метры), форма (256, 256)."""
    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGB")
    a = np.asarray(img, dtype=np.float32)
    if a.shape[:2] != (TILE, TILE):
        raise ValueError(f"плитка {a.shape[1]}×{a.shape[0]}, ожидалась {TILE}×{TILE}")
    return a[..., 0] * 256.0 + a[..., 1] + a[..., 2] / 256.0 - 32768.0


def encode_terrarium(elev: np.ndarray) -> bytes:
    """Обратное преобразование — нужно тестам и офлайн-демо (синтетический рельеф)."""
    from PIL import Image

    v = np.clip(elev + 32768.0, 0, 65535.99)
    r = np.floor(v / 256.0)
    g = np.floor(v - r * 256.0)
    b = np.floor((v - np.floor(v)) * 256.0)
    img = Image.fromarray(np.stack([r, g, b], axis=-1).astype(np.uint8), "RGB")
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def cache_dir() -> Path:
    return settings.data_dir / "raw" / "dem"


def tile_path(z: int, x: int, y: int) -> Path:
    return cache_dir() / f"z{z}" / f"{x}_{y}.npz"


def save_tile(z: int, x: int, y: int, elev: np.ndarray) -> Path:
    p = tile_path(z, x, y)
    p.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(p, e=np.round(elev).astype(np.int16))
    return p


def load_tile(z: int, x: int, y: int) -> np.ndarray | None:
    p = tile_path(z, x, y)
    if not p.exists():
        return None
    try:
        with np.load(p) as f:
            return f["e"].astype(np.float32)
    except Exception:  # noqa: BLE001 — битый файл = нет плитки, перекачается
        log.warning("битая плитка рельефа %s", p)
        return None


def fetch_tile(z: int, x: int, y: int, timeout: float = 12.0) -> np.ndarray:
    """Скачать плитку с AWS (без ключа). Бросает DemUnavailable при любой сетевой ошибке."""
    import requests

    url = URL.format(z=z, x=x, y=y)
    last: Exception | None = None
    for attempt in range(2):
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": "agent-kva-hackathon/1.0"})
            if r.status_code == 404:
                raise DemUnavailable(f"нет плитки {z}/{x}/{y}")
            r.raise_for_status()
            return decode_terrarium(r.content)
        except DemUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
    raise DemUnavailable(f"не удалось скачать плитку рельефа {z}/{x}/{y}: {last}")


# ───────────────────────── слой и рельеф ─────────────────────────

@dataclass
class Layer:
    z: int
    x0: int          # левая плитка мозаики
    y0: int          # верхняя плитка
    arr: np.ndarray  # float32, NaN там, где плитки нет

    @property
    def h(self) -> int:
        return self.arr.shape[0]

    @property
    def w(self) -> int:
        return self.arr.shape[1]

    def sample(self, px: np.ndarray, py: np.ndarray) -> np.ndarray:
        """Билинейная выборка по глобальным пиксельным координатам. NaN — вне слоя или без данных."""
        u = px - 0.5 - self.x0 * TILE
        v = py - 0.5 - self.y0 * TILE
        i0 = np.floor(u).astype(np.int64)
        j0 = np.floor(v).astype(np.int64)
        fu, fv = u - i0, v - j0
        ok = (i0 >= 0) & (j0 >= 0) & (i0 < self.w - 1) & (j0 < self.h - 1)
        i0c = np.clip(i0, 0, self.w - 2)
        j0c = np.clip(j0, 0, self.h - 2)
        a = self.arr
        z00, z10 = a[j0c, i0c], a[j0c, i0c + 1]
        z01, z11 = a[j0c + 1, i0c], a[j0c + 1, i0c + 1]
        out = (z00 * (1 - fu) * (1 - fv) + z10 * fu * (1 - fv) + z01 * (1 - fu) * fv + z11 * fu * fv)
        return np.where(ok, out, np.nan)


class Terrain:
    """Набор слоёв от подробного к грубому. Точка берётся из самого подробного слоя, где есть данные."""

    def __init__(self, layers: list[Layer], info: dict | None = None):
        self.layers = sorted(layers, key=lambda L: -L.z)
        self.info = info or {}

    def elevation(self, lat, lon) -> np.ndarray:
        lat = np.atleast_1d(np.asarray(lat, dtype=np.float64))
        lon = np.atleast_1d(np.asarray(lon, dtype=np.float64))
        lat, lon = np.broadcast_arrays(lat, lon)
        out = np.full(lat.shape, np.nan, dtype=np.float64)
        for L in self.layers:
            miss = np.isnan(out)
            if not miss.any():
                break
            px, py = world_px_arr(lat[miss], lon[miss], L.z)
            out[miss] = L.sample(px, py)
        return out

    def elevation_at(self, lat: float, lon: float) -> float | None:
        v = float(self.elevation(lat, lon)[0])
        return None if math.isnan(v) else v

    @property
    def finest_zoom(self) -> int:
        return self.layers[0].z if self.layers else 0

    @staticmethod
    def synthetic(lat0: float, lon0: float, func: Callable[[np.ndarray, np.ndarray], np.ndarray],
                  layers: tuple[tuple[int, float], ...] = ((13, 1800.0), (11, 9000.0), (9, 75000.0))) -> "Terrain":
        """Рельеф из функции высоты h(x, y) в метрах от точки (x — восток, y — север). Для тестов и демо."""
        built: list[Layer] = []
        kx = 111_195.0 * math.cos(math.radians(lat0))
        ky = 111_195.0
        for z, radius in layers:
            tl = tiles_for(lat0, lon0, radius, z)
            xs = sorted({t[0] for t in tl})
            ys = sorted({t[1] for t in tl})
            W, H = len(xs) * TILE, len(ys) * TILE
            px = (np.arange(W) + 0.5 + xs[0] * TILE)[None, :].repeat(H, 0)
            py = (np.arange(H) + 0.5 + ys[0] * TILE)[:, None].repeat(W, 1)
            n = TILE * (2 ** z)
            lon = px / n * 360.0 - 180.0
            lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * py / n))))
            arr = func((lon - lon0) * kx, (lat - lat0) * ky).astype(np.float32)
            built.append(Layer(z, xs[0], ys[0], arr))
        return Terrain(built, {"synthetic": True})


# ───────────────────────── сборка рельефа вокруг отеля ─────────────────────────

_cache: "OrderedDict[tuple, tuple[Terrain | None, dict, float]]" = OrderedDict()
_lock = threading.Lock()
NEGATIVE_TTL_S = 600.0   # неудачную попытку (нет сети/плиток) не повторяем на каждом отеле


def layers_spec() -> tuple[tuple[int, float], ...]:
    return DEFAULT_LAYERS


def _mosaic(z: int, tiles: list[tuple[int, int]], got: dict[tuple[int, int], np.ndarray]) -> Layer | None:
    have = [t for t in tiles if t in got]
    if not have:
        return None
    xs = sorted({t[0] for t in tiles})
    ys = sorted({t[1] for t in tiles})
    arr = np.full((len(ys) * TILE, len(xs) * TILE), np.nan, dtype=np.float32)
    for (x, y), a in got.items():
        if x in xs and y in ys:
            arr[(y - ys[0]) * TILE:(y - ys[0] + 1) * TILE, (x - xs[0]) * TILE:(x - xs[0] + 1) * TILE] = a
    return Layer(z, xs[0], ys[0], arr)


def build(lat: float, lon: float, allow_net: bool = False, fetcher: Callable[[int, int, int], np.ndarray] | None = None,
          spec: tuple[tuple[int, float], ...] | None = None) -> tuple[Terrain | None, dict]:
    """Собрать рельеф вокруг точки. Сеть — только если allow_net. Возвращает (рельеф или None, сведения)."""
    spec = spec or layers_spec()
    fetcher = fetcher or fetch_tile
    layers: list[Layer] = []
    info = {"tiles_cached": 0, "tiles_fetched": 0, "tiles_missing": 0, "error": None, "layers": []}
    net_dead = False                    # после первой сетевой неудачи остальные плитки не пытаемся качать: без интернета ждать каждую нет смысла
    for z, radius in spec:
        tl = tiles_for(lat, lon, radius, z)
        if len(tl) > MAX_TILES_PER_LAYER:
            info["error"] = f"слишком много плиток на уровне {z}: {len(tl)}"
            continue
        got: dict[tuple[int, int], np.ndarray] = {}
        for x, y in tl:
            a = load_tile(z, x, y)
            if a is not None:
                info["tiles_cached"] += 1
            elif allow_net and not net_dead:
                try:
                    a = fetcher(z, x, y)
                    save_tile(z, x, y, a)
                    info["tiles_fetched"] += 1
                except DemUnavailable as e:
                    info["error"] = str(e)
                    net_dead = True
                    a = None
            if a is not None:
                got[(x, y)] = a
            else:
                info["tiles_missing"] += 1
        layer = _mosaic(z, tl, got)
        if layer is not None:
            layers.append(layer)
            info["layers"].append({"z": z, "radius_m": radius, "tiles": len(got), "of": len(tl)})
    if not layers:
        return None, info
    t = Terrain(layers, info)
    if t.elevation_at(lat, lon) is None:           # сам отель вне данных — рельеф бесполезен
        info["error"] = info["error"] or "в плитках нет высоты в точке отеля"
        return None, info
    return t, info


def get(lat: float, lon: float, allow_net: bool | None = None, fetcher=None) -> tuple[Terrain | None, dict]:
    """Рельеф вокруг отеля с кэшем в памяти. allow_net=None — по настройке DEM_FETCH."""
    if not settings.dem_enabled:
        return None, {"error": "рельеф отключён (DEM_ENABLED=false)", "disabled": True}
    net_ok = settings.dem_fetch if allow_net is None else allow_net
    key = (round(lat, 2), round(lon, 2), bool(net_ok))
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and (hit[0] is not None or not net_ok or now - hit[2] < NEGATIVE_TTL_S):
            _cache.move_to_end(key)
            return hit[0], hit[1]
    t, info = build(lat, lon, allow_net=net_ok, fetcher=fetcher)
    with _lock:
        _cache[key] = (t, info, now)
        while len(_cache) > 6:
            _cache.popitem(last=False)
    return t, info


def clear_cache() -> None:
    with _lock:
        _cache.clear()
