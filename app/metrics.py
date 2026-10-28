"""Метрики качества: полнота, достоверность, свежесть, нагрузка на отельера и точность на размеченной выборке.

Точность считается по ручной разметке (data/eval/labels.json + то, что добавлено через интерфейс):
для каждой пары «отель × характеристика × область» — истинное значение, которое проверил человек
(в отеле, по фото/картам или со слов отельера). Разметка не придумывается: пока её нет, показываем это честно.
"""
from __future__ import annotations

import json
import math

from . import db, registry
from .collectors.base import SOURCES
from .config import settings


def _labels_paths():
    return [settings.data_dir / "eval" / "labels.json", settings.runtime_dir / "labels.json"]


def load_labels() -> list[dict]:
    out: dict[tuple, dict] = {}
    for p in _labels_paths():
        try:
            for x in json.loads(p.read_text(encoding="utf-8")):
                out[(x["hotel_id"], x["feature"], x.get("scope", "hotel"))] = x
        except (OSError, ValueError, KeyError):
            continue
    return list(out.values())


def add_label(hotel_id: str, feature: str, scope: str, value, note: str = "") -> dict:
    p = settings.runtime_dir / "labels.json"
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    try:
        cur = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cur = []
    cur = [x for x in cur if (x["hotel_id"], x["feature"], x.get("scope", "hotel")) != (hotel_id, feature, scope)]
    item = {"hotel_id": hotel_id, "feature": feature, "scope": scope, "value": value, "note": note}
    cur.append(item)
    p.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    return item


def overview() -> dict:
    feats = registry.all_features()
    n_hotels = db.row("SELECT COUNT(*) AS n FROM hotels")["n"]
    facts = [dict(r) for r in db.rows("SELECT * FROM facts")]
    hotel_facts = [f for f in facts if f["scope"] == "hotel"]
    by_status: dict[str, int] = {}
    for f in facts:
        by_status[f["status"]] = by_status.get(f["status"], 0) + 1
    hist = [0] * 5
    for f in facts:
        hist[min(4, int(f["confidence"] * 5))] += 1
    cov = []
    for k, spec in feats.items():
        have = {f["hotel_id"] for f in hotel_facts if f["feature"] == k}
        good = {f["hotel_id"] for f in hotel_facts if f["feature"] == k and f["status"] in ("confirmed", "likely")}
        cov.append({"feature": k, "title": spec["title"], "group": spec["group"], "computed": bool(spec.get("computed")), "builtin": bool(spec.get("builtin", True)),
                    "coverage": round(len(have) / n_hotels, 3) if n_hotels else 0, "reliable": round(len(good) / n_hotels, 3) if n_hotels else 0})
    cov.sort(key=lambda r: -r["coverage"])
    # источники и независимость
    src_count: dict[str, int] = {}
    for r in db.rows("SELECT source, COUNT(*) AS n FROM observations WHERE superseded=0 GROUP BY source"):
        src_count[r["source"]] = r["n"]
    multi = 0
    for f in hotel_facts:
        ids = db.loads(f["obs_ids"], []) or []
        if len(ids) >= 2:
            srcs = {r["source"] for r in db.rows(f"SELECT source FROM observations WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else set()
            multi += len(srcs) >= 2
    geo = [f for f in hotel_facts if feats.get(f["feature"], {}).get("computed")]
    q_open = db.row("SELECT COUNT(*) AS n FROM questions WHERE status='open'")["n"]
    q_done = db.row("SELECT COUNT(*) AS n FROM questions WHERE status='answered'")["n"]
    q_all = db.row("SELECT COUNT(*) AS n FROM questions")["n"]
    auth = sum(1 for f in facts if (f["observed_at"] or "") and "hotelier" in (f["explanation"] or "").lower())
    n_total = len(facts) or 1
    return {
        "hotels": n_hotels, "features": len(feats), "facts": len(facts),
        "facts_per_hotel": round(len(hotel_facts) / n_hotels, 1) if n_hotels else 0,
        "reviews": db.row("SELECT COUNT(*) AS n FROM reviews")["n"],
        "by_status": by_status,
        "share": {
            "confirmed_or_likely": round((by_status.get("confirmed", 0) + by_status.get("likely", 0)) / n_total, 3),
            "disputed": round(by_status.get("disputed", 0) / n_total, 3),
            "stale": round(by_status.get("stale", 0) / n_total, 3),
            "multi_source": round(multi / (len(hotel_facts) or 1), 3),
            "from_geodata": round(len(geo) / (len(hotel_facts) or 1), 3),
            "hotelier_verified": round(auth / n_total, 3),
        },
        "confidence_hist": [{"from": i * 20, "to": i * 20 + 20, "count": c} for i, c in enumerate(hist)],
        "coverage": cov,
        "sources": [{"source": s, "title": SOURCES.get(s, {}).get("title", s), "observations": n} for s, n in sorted(src_count.items(), key=lambda t: -t[1])],
        "questions": {"open": q_open, "answered": q_done, "total": q_all},
        "evaluation": evaluate(),
    }


def evaluate() -> dict:
    labels = load_labels()
    feats = registry.all_features()
    if not labels:
        return {"labeled": 0, "note": "Разметки пока нет: добавьте проверенные значения на вкладке «Качество» или в data/eval/labels.json"}
    rows = []
    for lb in labels:
        f = db.row("SELECT * FROM facts WHERE hotel_id=? AND feature=? AND scope=?", (lb["hotel_id"], lb["feature"], lb.get("scope", "hotel")))
        spec = feats.get(lb["feature"])
        if not spec:
            continue
        rows.append((lb, f, spec))
    covered = [(lb, f, s) for lb, f, s in rows if f]
    bools = [(lb, f, s) for lb, f, s in covered if s["kind"] == "bool"]
    nums = [(lb, f, s) for lb, f, s in covered if s["kind"] in ("score", "number")]
    res: dict = {"labeled": len(rows), "covered": len(covered), "coverage": round(len(covered) / len(rows), 3) if rows else 0}
    if bools:
        ok = [bool(db.loads(f["value"], {}).get("v")) == bool(lb["value"]) for lb, f, _ in bools]
        res["bool_accuracy"] = round(sum(ok) / len(ok), 3)
        res["bool_n"] = len(ok)
        # калибровка: насколько заявленная уверенность соответствует доле верных
        bins = [[] for _ in range(5)]
        for (lb, f, _), good in zip(bools, ok):
            bins[min(4, int(f["confidence"] * 5))].append((f["confidence"], good))
        ece, total = 0.0, len(ok)
        cal = []
        for i, b in enumerate(bins):
            if not b:
                continue
            mc, acc = sum(c for c, _ in b) / len(b), sum(g for _, g in b) / len(b)
            ece += len(b) / total * abs(mc - acc)
            cal.append({"bin": f"{i * 20}–{i * 20 + 20}%", "n": len(b), "mean_confidence": round(mc, 2), "accuracy": round(acc, 2)})
        res["ece"] = round(ece, 3)
        res["calibration"] = cal
        confident = [g for (lb, f, _), g in zip(bools, ok) if f["status"] in ("confirmed", "likely")]
        if confident:
            res["precision_confident"] = round(sum(confident) / len(confident), 3)
        res["disputed_caught"] = sum(1 for (lb, f, _), g in zip(bools, ok) if not g and f["status"] in ("disputed", "stale", "weak"))
        res["wrong_total"] = sum(1 for g in ok if not g)
    if nums:
        err = [abs(db.loads(f["value"], {}).get("v") - float(lb["value"])) for lb, f, _ in nums]
        res["numeric_mae"] = round(sum(err) / len(err), 1)
        res["numeric_n"] = len(err)
    return res
