"""Достоверность: из наблюдений разных источников — одно значение + статус + уверенность.

Принципы:
* вес наблюдения = его уверенность × свежесть (свежесть падает с возрастом относительно TTL характеристики);
* источники независимы: внутри источника берём самое весомое, между источниками — «шумовое ИЛИ»
  (два слабых независимых свидетельства усиливают друг друга);
* если против значения-лидера есть заметный вес — статус «спорно», причём объяснение показывает обе стороны
  с цитатами и датами; система не выбирает молча;
* слово отельера весомо (он владелец информации), но его перевешивает подавляющее
  независимое свидетельство, а физика (расчёт по карте) не молчит — тогда тоже «спорно» и задаётся вопрос;
* имитация отельера не считается данными отеля: вес ограничен.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from . import db, registry
from .collectors.base import SOURCES, now_iso, today

STATUS_TITLES = {
    "confirmed": "Подтверждено",
    "likely": "Вероятно",
    "weak": "Слабое свидетельство",
    "disputed": "Спорно",
    "stale": "Устарело",
    "unknown": "Нет данных",
}
SOFT_CONFLICT = 0.04   # вес противоречащего свидетельства, начиная с которого показываем расхождение
AUTHORITY = {"hotelier", "hotelier_sim"}
SELF_REPORTED = {"ostrovok_card", "ostrovok_ratings"}   # данные, которые вносит сам отель
PHYSICAL = {"osm_geo", "dem_geo", "astronomy"}
MODEL_ONLY = {"osm_geo", "dem_geo", "open_meteo", "open_meteo_aq", "osm_masts"}   # расчёт или модель: одна такая запись «подтверждено» не даёт


def _d(s: str | None) -> date:
    try:
        return datetime.strptime((s or "")[:10], "%Y-%m-%d").date()
    except Exception:
        return date.fromisoformat(today())


def freshness(observed_at: str | None, ttl_days: int, ref: date | None = None) -> tuple[float, str]:
    ref = ref or date.fromisoformat(today())
    age = max(0, (ref - _d(observed_at)).days)
    if age <= 0.5 * ttl_days:
        return 1.0, "fresh"
    if age <= ttl_days:
        return 1.0 - 0.5 * (age - 0.5 * ttl_days) / (0.5 * ttl_days), "aging"
    return max(0.15, 0.5 * ttl_days / age), "stale"


def _noisy_or(ws: list[float]) -> float:
    p = 1.0
    for w in ws:
        p *= 1.0 - min(0.97, max(0.0, w))
    return 1.0 - p


def _src_title(src: str) -> str:
    return SOURCES.get(src, {}).get("title", src)


def _obs_view(o: dict, w: float, fr_state: str) -> dict:
    v = db.loads(o["value"], {})
    return {
        "id": o["id"], "source": o["source"], "title": _src_title(o["source"]), "value": v.get("v"), "summary": v.get("summary"),
        "confidence": round(o["confidence"], 2), "weight": round(w, 2), "observed_at": o["observed_at"], "collected_at": o["collected_at"],
        "freshness": fr_state, "method": o["method"], "simulated": bool(o["simulated"]),
        "evidence": db.loads(o["evidence"], []), "detail": v.get("detail"),
    }


def _fmt_value(spec: dict, v) -> str:
    if spec["kind"] == "bool":
        return "да" if v else "нет"
    if spec["kind"] == "score":
        return f"{v}/100"
    if spec["kind"] == "number":
        return f'{str(v).replace(".", ",")} {spec.get("unit", "")}'.strip()
    return str(v)


def _conflict_text(spec: dict, groups: list[tuple[object, list[dict]]]) -> str:
    parts = []
    for v, items in groups:
        srcs = []
        for it in items:
            tail = ""
            if it["source"] == "reviews":
                det = it.get("detail") or {}
                rc = (det.get("recent_12m") or {})
                n = rc.get("positive", 0) + rc.get("negative", 0)
                tail = f", за год упоминаний: {n}" if n else ""
            srcs.append(f'{it["title"].lower()} ({it["observed_at"][:10]}{tail})')
        parts.append(f'{_fmt_value(spec, v)} — {"; ".join(srcs)}')
    return "Источники расходятся: " + "  ⟷  ".join(parts)


def resolve(spec: dict, obs_rows: list[dict], ref: date | None = None) -> dict | None:
    """Свернуть наблюдения одной характеристики/области в итог."""
    if not obs_rows:
        return None
    ref = ref or date.fromisoformat(today())
    ttl = int(spec.get("ttl_days", 365))
    views: list[dict] = []
    for o in obs_rows:
        fr, state = freshness(o["observed_at"], ttl, ref)
        w = o["confidence"] * fr
        if o["simulated"]:
            w = min(w, 0.5)
        views.append(_obs_view(o, w, state))
    kind = spec["kind"]

    # лучший вес на источник и значение
    def best_per_source(items: list[dict]) -> list[dict]:
        by: dict[str, dict] = {}
        for it in items:
            if it["source"] not in by or it["weight"] > by[it["source"]]["weight"]:
                by[it["source"]] = it
        return list(by.values())

    if kind in ("bool", "text"):
        groups: dict[object, list[dict]] = {}
        for it in views:
            groups.setdefault(bool(it["value"]) if kind == "bool" else it["value"], []).append(it)
        scored = []
        for v, items in groups.items():
            bs = best_per_source(items)
            scored.append((v, bs, _noisy_or([i["weight"] for i in bs])))
        scored.sort(key=lambda t: -t[2])
        lead_v, lead_items, W_lead = scored[0]
        opp = scored[1:] if len(scored) > 1 else []
        W_opp = _noisy_or([i["weight"] for _, its, _ in opp for i in its]) if opp else 0.0
        opp_items = [i for _, its, _ in opp for i in its]
        # слово отельера: карточку заполняет тот же отель, поэтому свежий ответ её заменяет;
        # спорить с ним могут только независимые свидетельства, и устаревшие по отношению к ответу весят вдвое меньше
        hot = [i for i in views if i["source"] in AUTHORITY]
        if hot:
            h = max(hot, key=lambda i: i["weight"])
            hv = bool(h["value"]) if kind == "bool" else h["value"]
            same = [i for i in views if i["source"] not in AUTHORITY and (bool(i["value"]) if kind == "bool" else i["value"]) == hv]
            against = [i for i in views if i["source"] not in AUTHORITY and (bool(i["value"]) if kind == "bool" else i["value"]) != hv]
            indep = [dict(i, weight=i["weight"] * (0.5 if i["observed_at"] < h["observed_at"] else 1.0))
                     for i in against if i["source"] not in SELF_REPORTED]
            W_against = _noisy_or([i["weight"] for i in best_per_source(indep)])
            if hv == lead_v or W_against < 0.75:
                lead_v, lead_items = hv, best_per_source([h] + [i for i in same if i["source"] not in SELF_REPORTED or True])
                W_lead = _noisy_or([i["weight"] for i in lead_items])
                opp_items = best_per_source(indep)
                W_opp = _noisy_or([i["weight"] for i in opp_items]) if opp_items else 0.0
        disputed = bool(opp_items) and W_opp >= 0.25 and W_opp >= 0.5 * W_lead
        # физика против слова отельера → спорно
        phys_against = [i for i in opp_items if i["source"] in PHYSICAL and i["weight"] >= 0.3]
        if any(i["source"] in AUTHORITY for i in lead_items) and phys_against:
            disputed = True
        conf = W_lead * (1.0 - 0.8 * W_opp)
        best = max(lead_items, key=lambda i: i["weight"])
        value = {"v": lead_v, "summary": best["summary"], "detail": best.get("detail")}
        support = lead_items
        conflict = None
        soft = (not disputed) and bool(opp_items) and W_opp >= SOFT_CONFLICT
        if disputed or soft:
            opp_by_value: dict[object, list[dict]] = {}
            for i in opp_items:
                opp_by_value.setdefault(bool(i["value"]) if kind == "bool" else i["value"], []).append(i)
            ordered = [(lead_v, lead_items)] + list(opp_by_value.items())
            conflict = {
                "level": "hard" if disputed else "soft",
                "text": _conflict_text(spec, [(v, its) for v, its in ordered if its]),
                "sides": [{"value": v, "label": _fmt_value(spec, v), "sources": its} for v, its in ordered if its],
                "leader": lead_v,
            }
    else:
        # число / оценка: лидер — самое весомое; согласие — значения в пределах допуска
        tol = 25.0 if kind == "score" else None
        bs = best_per_source(views)
        bs.sort(key=lambda i: -i["weight"])
        lead = bs[0]
        hot = [i for i in bs if i["source"] in AUTHORITY]
        if hot:
            lead = max(hot, key=lambda i: i["weight"])

        def agrees(a, b) -> bool:
            if a is None or b is None:
                return False
            if kind == "score":
                return abs(a - b) <= tol
            return abs(a - b) <= max(3.0, 0.4 * max(abs(a), abs(b)))

        agreeing = [i for i in bs if agrees(i["value"], lead["value"])]
        against = [i for i in bs if i not in agreeing]
        W_lead = _noisy_or([i["weight"] for i in agreeing])
        W_opp = _noisy_or([i["weight"] for i in against]) if against else 0.0
        disputed = bool(against) and W_opp >= 0.25 and W_opp >= 0.5 * W_lead
        wsum = sum(i["weight"] for i in agreeing) or 1.0
        mean = sum(i["value"] * i["weight"] for i in agreeing) / wsum
        dec = int(spec.get("decimals") or 0)
        v = round(mean, dec) if (dec and kind == "number") else int(round(mean))
        conf = W_lead * (1.0 - 0.8 * W_opp)
        best = max(agreeing, key=lambda i: i["weight"])
        value = {"v": v, "summary": best["summary"], "detail": best.get("detail")}
        support = agreeing
        conflict = None
        soft = (not disputed) and bool(against) and W_opp >= SOFT_CONFLICT
        if disputed or soft:
            ordered = [(lead["value"], agreeing)] + [(i["value"], [i]) for i in against]
            conflict = {"level": "hard" if disputed else "soft", "text": _conflict_text(spec, ordered),
                        "sides": [{"value": vv, "label": _fmt_value(spec, vv), "sources": its} for vv, its in ordered],
                        "leader": v}

    states = [i["freshness"] for i in support]
    stale = all(s == "stale" for s in states)
    has_authority = any(i["source"] in AUTHORITY and not i["simulated"] for i in support)
    sim_only = any(i["simulated"] for i in support) and not has_authority
    model_only = all(i["source"] in MODEL_ONLY for i in support)
    if disputed:
        status = "disputed"
    elif stale:
        status = "stale"
    elif has_authority and conf >= 0.5:
        status = "confirmed"
    elif model_only and conf >= 0.3:
        status = "likely"            # расчёт по карте без независимого подтверждения — не «подтверждено»
    elif sim_only and conf >= 0.3:
        status = "likely"            # имитация отельера — не данные отеля
    elif conf >= 0.6 and (len({i["source"] for i in support}) >= 2 or conf >= 0.7):
        status = "confirmed"
    elif conf >= 0.3:
        status = "likely"
    else:
        status = "weak"
    exp = min((_d(i["observed_at"]) + timedelta(days=ttl) for i in support), default=ref)
    names = ", ".join(sorted({i["title"] for i in support}))
    expl = f'Основано на: {names}. Уверенность {int(round(conf * 100))}%.'
    if disputed:
        expl = conflict["text"] + f' Принято: {conflict["sides"][0]["label"]} (перевешивает по весу), нужен ответ отельера.'
    elif conflict:
        expl += " " + conflict["text"] + ". Расхождение пока слабое — стоит перепроверить у отельера."
    elif stale:
        expl += " Свидетельства устарели — требуется перепроверка."
    return {
        "value": value, "status": status, "confidence": round(conf, 3), "observed_at": max(i["observed_at"] for i in support),
        "expires_at": exp.isoformat(), "explanation": expl, "obs_ids": [i["id"] for i in views], "conflict": conflict,
    }


def reconcile_hotel(hotel_id: str, only: set[str] | None = None) -> dict[str, dict]:
    feats = registry.all_features()
    rows = db.rows("SELECT * FROM observations WHERE hotel_id=? AND superseded=0", (hotel_id,))
    by: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        d = dict(r)
        by.setdefault((d["feature"], d["scope"]), []).append(d)
    out: dict[str, dict] = {}
    with db.tx() as c:
        if only:
            for f in only:
                c.execute("DELETE FROM facts WHERE hotel_id=? AND feature=?", (hotel_id, f))
        else:
            c.execute("DELETE FROM facts WHERE hotel_id=?", (hotel_id,))
        for (feat, scope), items in by.items():
            spec = feats.get(feat)
            if not spec or (only and feat not in only):
                continue
            res = resolve(spec, items)
            if not res:
                continue
            out[f"{feat}|{scope}"] = res
            c.execute(
                "INSERT OR REPLACE INTO facts(hotel_id, feature, scope, value, status, confidence, observed_at, expires_at, explanation, obs_ids, conflict) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (hotel_id, feat, scope, db.dumps(res["value"]), res["status"], res["confidence"], res["observed_at"], res["expires_at"],
                 res["explanation"], db.dumps(res["obs_ids"]), db.dumps(res["conflict"]) if res["conflict"] else None),
            )
    return out


def reconcile_all() -> int:
    n = 0
    for r in db.rows("SELECT id FROM hotels"):
        reconcile_hotel(r["id"])
        n += 1
    return n
