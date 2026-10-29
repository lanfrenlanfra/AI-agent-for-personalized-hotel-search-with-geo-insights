"""Качество воздуха: PM2.5 по атмосферной модели CAMS (Open-Meteo Air Quality) за последние 12 месяцев.

Это МОДЕЛЬ с шагом около 45 км, а не датчик у отеля. Она хорошо показывает фон региона и отопительный сезон, но не видит
дым печей и транспорт на отдельной улице. Для сравнения всегда даём ссылку на OpenAQ/IQAir в «как проверить».
"""
from __future__ import annotations

from datetime import date, timedelta

from .. import extcache
from ..collectors.base import Obs, today
from . import Result, evidence, status_from

URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
WHO_ANNUAL = 5.0       # руководство ВОЗ 2021 по среднегодовому PM2.5, мкг/м³


def reduce(raw: dict) -> dict:
    h = raw.get("hourly") or {}
    by: dict[str, list[float]] = {}
    for t, v in zip(h.get("time") or [], h.get("pm2_5") or []):
        if v is not None:
            by.setdefault(t[:7], []).append(float(v))
    return {"months": {m: round(sum(v) / len(v), 2) for m, v in sorted(by.items()) if len(v) >= 24 * 15}, "hours": sum(len(v) for v in by.values())}


class Air:
    name = "air"
    title = "Качество воздуха (Open-Meteo, CAMS)"
    features = ("air_quality",)

    def run(self, rec: dict, online: bool) -> Result:
        lat, lon = round(rec["lat"], 2), round(rec["lon"], 2)
        end = date.today() - timedelta(days=2)
        params = {"latitude": lat, "longitude": lon, "hourly": "pm2_5", "start_date": (end - timedelta(days=365)).isoformat(), "end_date": end.isoformat(), "timezone": "UTC"}
        data, meta = extcache.fetch_json("open_meteo_aq", URL, params, online=online, max_age_days=120, reduce=reduce, cache_key=f"air:{lat},{lon}")
        if data is None:
            return Result(self.name, status_from([meta], False), "нет данных: " + (meta.get("error") or "ответа нет и в кэше пусто"), sources=[meta])
        months = data.get("months") or {}
        if len(months) < 6:
            return Result(self.name, "no_data", f"в ответе слишком мало месяцев ({len(months)}) для оценки", sources=[meta])
        mean = sum(months.values()) / len(months)
        winter = [v for m, v in months.items() if m[5:7] in ("12", "01", "02")]
        wmean = sum(winter) / len(winter) if winter else None
        txt = f"PM2.5 в среднем {mean:.1f} мкг/м³ за год"
        if wmean is not None:
            txt += f", в декабре–феврале {wmean:.1f}"
        txt += " (модель CAMS, шаг около 45 км, не датчик у отеля)"
        txt = txt.replace(".", ",")
        detail = {"months": months, "winter_mean": round(wmean, 1) if wmean is not None else None, "who_annual_guideline": WHO_ANNUAL, "model_step_km": 45}
        o = Obs("air_quality", {"v": round(mean, 1), "summary": txt, "detail": detail}, "open_meteo_aq", 0.45, (meta.get("fetched_at") or today())[:10],
                "Среднее PM2.5 по месяцам за 12 месяцев, Open-Meteo Air Quality (CAMS)",
                [evidence(meta, f"{len(months)} мес. данных модели, среднее {mean:.1f} мкг/м³", "Open-Meteo Air Quality")])
        msg = f"PM2.5 {mean:.1f}".replace(".", ",") + (f"; сеть недоступна, сохранённый ответ ({meta['age_days']} дн.)" if meta["status"] == "stale_cache" else "")
        return Result(self.name, status_from([meta], True), msg, [o], [meta])


PROBE = Air()
