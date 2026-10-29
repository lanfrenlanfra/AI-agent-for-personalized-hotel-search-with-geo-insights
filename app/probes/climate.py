"""Климат зимой: температура января и солнечное сияние в декабре–феврале по реанализу ERA5-Land (Open-Meteo Historical Weather).

Это климатическая норма за 2015–2024, а не прогноз и не измерение на метеостанции. Сетка ≈9 км: у Байкала, на склонах и
в долинах реальная температура отличается на несколько градусов. Если высота ячейки сетки заметно отличается от высоты отеля
по модели рельефа, уверенность снижается и это написано в данных.
"""
from __future__ import annotations


from .. import extcache
from ..collectors.base import Obs, today
from . import Result, evidence, status_from

URL = "https://archive-api.open-meteo.com/v1/archive"
Y0, Y1 = 2015, 2024
UNIT_NOTE = "реанализ ERA5-Land, сетка около 9 км"


def reduce(raw: dict) -> dict:
    d = raw.get("daily") or {}
    times = d.get("time") or []
    temp = d.get("temperature_2m_mean") or []
    sun = d.get("sunshine_duration") or []
    jan, sun_w = [], []
    for i, t in enumerate(times):
        m = int(t[5:7])
        if m == 1 and i < len(temp) and temp[i] is not None:
            jan.append(float(temp[i]))
        if m in (12, 1, 2) and i < len(sun) and sun[i] is not None:
            sun_w.append(float(sun[i]) / 3600.0)
    return {"jan_temp": round(sum(jan) / len(jan), 2) if jan else None, "n_jan": len(jan),
            "winter_sun_h": round(sum(sun_w) / len(sun_w), 2) if sun_w else None, "n_sun": len(sun_w),
            "grid_elevation": raw.get("elevation"), "years": [Y0, Y1]}


def _ground(lat: float, lon: float) -> float | None:
    try:
        from .. import dem
        t, _ = dem.get(lat, lon, allow_net=False)
        return t.elevation_at(lat, lon) if t is not None else None
    except Exception:  # noqa: BLE001
        return None


class Climate:
    name = "climate"
    title = "Климат зимой (Open-Meteo)"
    features = ("winter_temp", "winter_sunshine")

    def run(self, rec: dict, online: bool) -> Result:
        lat, lon = round(rec["lat"], 2), round(rec["lon"], 2)
        params = {"latitude": lat, "longitude": lon, "start_date": f"{Y0}-01-01", "end_date": f"{Y1}-12-31",
                  "daily": "temperature_2m_mean,sunshine_duration", "timezone": "UTC"}
        data, meta = extcache.fetch_json("open_meteo", URL, params, online=online, max_age_days=365 * 3, reduce=reduce)
        if data is None:
            return Result(self.name, status_from([meta], False), "нет данных: " + (meta.get("error") or "ответа нет и в кэше пусто"), sources=[meta])
        obs: list[Obs] = []
        day = (meta.get("fetched_at") or today())[:10]
        dz = None
        ge = data.get("grid_elevation")
        gz = _ground(rec["lat"], rec["lon"])
        if ge is not None and gz is not None:
            dz = round(float(gz) - float(ge))
        elev_note = f"; высота ячейки сетки {round(ge)} м" if ge is not None else ""
        penalty = 1.0
        if dz is not None and abs(dz) > 150:
            penalty = 0.75
            elev_note += f", отель по модели высот на {dz:+d} м от неё — температура может отличаться"
        if data.get("jan_temp") is not None and data.get("n_jan", 0) >= 200:
            t = data["jan_temp"]
            txt = f"В январе в среднем {t:.1f} °C ({Y0}–{Y1}, {UNIT_NOTE}{elev_note})".replace(".", ",")
            obs.append(Obs("winter_temp", {"v": t, "summary": txt, "detail": {"years": [Y0, Y1], "grid_elevation": ge, "hotel_minus_grid_m": dz, "days": data["n_jan"]}},
                           "open_meteo", 0.72 * penalty, day, f"Среднесуточная температура января за {Y0}–{Y1}, Open-Meteo Historical Weather ({UNIT_NOTE})",
                           [evidence(meta, f"{data['n_jan']} январских дней, среднее {t:.1f} °C", "Open-Meteo Historical Weather")]))
        if data.get("winter_sun_h") is not None and data.get("n_sun", 0) >= 200:
            h = data["winter_sun_h"]
            txt = f"Зимой (декабрь — февраль) в среднем {h:.1f} ч солнца в день с учётом облаков ({Y0}–{Y1}, {UNIT_NOTE})".replace(".", ",")
            obs.append(Obs("winter_sunshine", {"v": h, "summary": txt, "detail": {"years": [Y0, Y1], "days": data["n_sun"]}},
                           "open_meteo", 0.55 * penalty, day, f"sunshine_duration за декабрь–февраль {Y0}–{Y1}, Open-Meteo ({UNIT_NOTE})",
                           [evidence(meta, f"{data['n_sun']} зимних дней, среднее {h:.1f} ч", "Open-Meteo Historical Weather")]))
        st = status_from([meta], bool(obs))
        msg = f"получено {len(obs)} знач." if obs else "ответ пришёл, но данных по зиме в нём нет"
        if meta["status"] == "stale_cache":
            msg += f"; сеть недоступна, использован сохранённый ответ ({meta['age_days']} дн.)"
        return Result(self.name, st, msg, obs, [meta])


PROBE = Climate()
