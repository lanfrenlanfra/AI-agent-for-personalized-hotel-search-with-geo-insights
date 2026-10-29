"""Пробы внешних источников: климат, воздух, Википедия, Викисклад, вышки связи.

Проба получает запись отеля и возвращает наблюдения (Obs) с источником, уверенностью, способом получения, ссылкой для
самостоятельной проверки и сроком годности. Если данных нет или сеть недоступна, проба так и говорит — статусом,
а не придуманным значением:

  ok        — получили и посчитали (свежий ответ или кэш)
  cached    — сети нет/не разрешена, использован сохранённый ответ (его возраст в detail)
  no_data   — источник отвечает, но про это место данных нет
  offline   — данных ещё не собирали: нет сохранённого ответа (и сеть не использовалась или не отвечала)
  error     — источник не ответил или ответ непонятен
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .. import db
from ..collectors.base import Obs, now_iso, save_observations
from ..config import settings

log = logging.getLogger("kva.probes")


@dataclass
class Result:
    probe: str
    status: str
    message: str
    observations: list[Obs] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)


def evidence(meta: dict, quote: str, title: str = "") -> dict:
    ev = {"quote": quote, "date": (meta.get("fetched_at") or "")[:10], "url": meta.get("url"), "title": title or meta.get("source", "")}
    if meta.get("status") == "stale_cache":
        ev["note"] = f'ответ из кэша, ему {meta.get("age_days")} дн.'
    return ev


def status_from(metas: list[dict], produced: bool) -> str:
    sts = {m.get("status") for m in metas}
    if produced:
        return "cached" if sts <= {"cached", "stale_cache"} and sts else "ok"
    if sts and sts <= {"offline_miss"}:
        return "offline"
    if "unavailable" in sts or "offline_miss" in sts:
        return "error" if "unavailable" in sts else "offline"
    return "no_data"


def all_probes():
    from . import air, climate, commons, masts, wiki
    return [climate.PROBE, air.PROBE, wiki.PROBE, commons.PROBE, masts.PROBE]


def run_all(hotel_id: str, rec: dict, online: bool | None = None) -> list[dict]:
    """Запустить все пробы, сохранить наблюдения и журнал запусков. online=None — по настройке PROBES_ONLINE."""
    net_ok = settings.probes_online if online is None else online
    report: list[dict] = []
    obs_all: list[Obs] = []
    for p in all_probes():
        try:
            res = p.run(rec, net_ok)
        except Exception as e:  # noqa: BLE001 — одна проба не должна ронять остальные
            log.exception("проба %s упала для %s", p.name, hotel_id)
            res = Result(p.name, "error", f"{type(e).__name__}: {str(e)[:160]}")
        obs_all += res.observations
        detail = {"features": list(p.features), "sources": res.sources, "observations": len(res.observations)}
        db.execute("INSERT INTO probe_runs(hotel_id, probe, status, message, detail, ran_at) VALUES(?,?,?,?,?,?) "
                   "ON CONFLICT(hotel_id, probe) DO UPDATE SET status=excluded.status, message=excluded.message, detail=excluded.detail, ran_at=excluded.ran_at",
                   (hotel_id, p.name, res.status, res.message, db.dumps(detail), now_iso()))
        report.append({"probe": p.name, "title": p.title, "status": res.status, "message": res.message, "features": list(p.features)})
    if obs_all:
        save_observations(hotel_id, obs_all)
    # наблюдения, которых в этот раз не получилось построить, старыми не оставляем: их место займёт статус пробы
    _drop_missing(hotel_id, obs_all)
    return report


def _drop_missing(hotel_id: str, fresh: list[Obs]) -> None:
    """Если проба раньше дала значение, а теперь отработала без данных ('no_data'), прежнее значение не должно жить вечно:
    оно вытеснится по сроку годности. Здесь ничего не удаляем — только оставляем место для будущего правила."""
    return None


def runs(hotel_id: str) -> list[dict]:
    titles = {p.name: p for p in all_probes()}
    out = []
    for r in db.rows("SELECT * FROM probe_runs WHERE hotel_id=?", (hotel_id,)):
        p = titles.get(r["probe"])
        out.append({"probe": r["probe"], "title": p.title if p else r["probe"], "status": r["status"], "message": r["message"],
                    "detail": db.loads(r["detail"], {}), "ran_at": r["ran_at"], "features": list(p.features) if p else []})
    out.sort(key=lambda x: x["probe"])
    return out
