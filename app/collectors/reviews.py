"""Сборщик по отзывам: упоминания характеристик в тексте с учётом свежести и отрицаний.

Идея: отзыв — самое свежее свидетельство о том, как отель выглядит сейчас. Каждое
упоминание весит 0,5^(возраст / 540 дней); отрицание («нет лифта», «не шумно») переворачивает смысл;
в итоге получается значение, уверенность и цитаты-доказательства с датами.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime

from .. import registry
from .base import Obs, today

HALF_LIFE_DAYS = 540.0
NEG_WORDS = re.compile(r"\b(не|нет|без|ни|отсутствует|отсутствуют|никак)\b")
SPLIT = re.compile(r"(?<=[.!?;])\s+|\r?\n+")


def norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


@dataclass
class Pat:
    rx: re.Pattern
    polarity: bool          # True — подтверждает «да/хорошо»
    explicit: bool          # шаблон сам несёт полярность: отрицание не применяем
    section_aware: bool = False


def compile_feature(spec: dict) -> list[Pat]:
    pats: list[Pat] = []
    rv = spec.get("review") or {}
    for pol, key in ((True, "pos"), (False, "neg")):
        for p in rv.get(key) or []:
            explicit = p.startswith("!")
            try:
                pats.append(Pat(re.compile(norm(p.lstrip("!"))), pol, explicit))
            except re.error:
                continue
    if not pats:
        # характеристика, описанная только словами: упоминание = «да», с отрицанием = «нет»; раздел «минусы» = «нет»
        for syn in spec.get("synonyms") or []:
            s = norm(str(syn)).strip()
            if len(s) >= 3:
                pats.append(Pat(re.compile(re.escape(s)), True, False, section_aware=True))
    return pats


def _age_days(d: str | None, ref: date) -> float:
    try:
        return max(0.0, (ref - datetime.strptime(d[:10], "%Y-%m-%d").date()).days)
    except Exception:
        return 1500.0


def _snippet(sentence: str, start: int, width: int = 160) -> str:
    s = sentence.strip()
    if len(s) <= width:
        return s
    a = max(0, start - width // 2)
    return ("…" if a else "") + s[a:a + width].strip() + "…"


def extract_mentions(reviews: list[dict], spec: dict, ref: date | None = None) -> list[dict]:
    """[{polarity, w, date, review_id, quote, section}] — по одному на (отзыв, полярность)."""
    ref = ref or date.fromisoformat(today())
    pats = compile_feature(spec)
    out: list[dict] = []
    for r in reviews:
        found: dict[bool, dict] = {}
        for section in ("plus", "minus", "text"):
            body = r.get(section) or ""
            if len(body.strip()) < 4:
                continue
            for sent_raw in SPLIT.split(body):
                sent = norm(sent_raw)
                if len(sent) < 4:
                    continue
                for p in pats:
                    m = p.rx.search(sent)
                    if not m:
                        continue
                    pol = p.polarity
                    if p.section_aware:
                        pol = section != "minus"
                    if not p.explicit:
                        before = sent[max(0, m.start() - 14):m.start()]
                        if NEG_WORDS.search(before):
                            pol = not pol
                    if pol not in found:
                        found[pol] = {
                            "polarity": pol,
                            "w": 0.5 ** (_age_days(r.get("date"), ref) / HALF_LIFE_DAYS),
                            "date": r.get("date"),
                            "review_id": r.get("id"),
                            "quote": _snippet(sent_raw, m.start()),
                            "section": section,
                        }
                    break
        out.extend(found.values())
    return out


def aggregate(spec: dict, mentions: list[dict], n_reviews: int) -> Obs | None:
    if not mentions:
        return None
    P = sum(m["w"] for m in mentions if m["polarity"])
    N = sum(m["w"] for m in mentions if not m["polarity"])
    n_pos = sum(1 for m in mentions if m["polarity"])
    n_neg = len(mentions) - n_pos
    recent = [m for m in mentions if _age_days(m["date"], date.fromisoformat(today())) <= 365]
    detail = {
        "mentions": len(mentions), "positive": n_pos, "negative": n_neg,
        "recent_12m": {"positive": sum(1 for m in recent if m["polarity"]), "negative": sum(1 for m in recent if not m["polarity"])},
        "reviews_scanned": n_reviews,
    }

    def pick(pol: bool, k: int) -> list[dict]:
        ms = sorted((m for m in mentions if m["polarity"] == pol), key=lambda m: -m["w"])[:k]
        return [{"quote": m["quote"], "date": m["date"], "review_id": m["review_id"], "polarity": "за" if pol else "против", "kind": "review"} for m in ms]

    kind = spec["kind"]
    if kind == "bool":
        tot = P + N
        if tot < 0.12:
            return None
        win = P >= N
        agree = abs(P - N) / tot
        conf = 0.7 * (1 - math.exp(-0.9 * tot)) * (0.5 + 0.5 * agree)
        evid = pick(win, 3) + pick(not win, 1)
        latest = max((m["date"] for m in mentions if m["polarity"] == win and m["date"]), default=today())
        detail["weighted"] = {"pro": round(P, 2), "con": round(N, 2)}
        verb = "подтверждают" if win else "опровергают"
        summary = f"Гости {verb}: {n_pos if win else n_neg} из {len(mentions)} упоминаний"
        if recent:
            summary += f" (за последний год — {detail['recent_12m']['positive'] if win else detail['recent_12m']['negative']})"
        return Obs(spec["key"], {"v": win, "summary": summary, "detail": detail}, "reviews", conf, latest,
                   f"Поиск упоминаний в {n_reviews} отзывах; вес = 0,5^(возраст/{int(HALF_LIFE_DAYS)} дн.)", evid)
    if kind == "score":
        tot = P + N
        if tot < 0.6:
            return None
        v = int(round(100 * (P + 0.75) / (tot + 1.5)))
        conf = 0.65 * (1 - math.exp(-0.45 * tot))
        win = v >= 50
        latest = max((m["date"] for m in mentions if m["date"]), default=today())
        detail["weighted"] = {"pro": round(P, 2), "con": round(N, 2)}
        evid = pick(win, 2) + pick(not win, 2)
        return Obs(spec["key"], {"v": v, "summary": f"По отзывам: {n_pos} положительных и {n_neg} отрицательных упоминаний", "detail": detail},
                   "reviews", conf, latest, f"Тональность упоминаний в {n_reviews} отзывах с учётом свежести", evid)
    return None


def collect(reviews: list[dict], only: list[str] | None = None) -> list[Obs]:
    feats = registry.all_features()
    res: list[Obs] = []
    for key, spec in feats.items():
        if only and key not in only:
            continue
        if spec.get("computed") and not (spec.get("review") or {}).get("pos"):
            continue
        if spec["kind"] not in ("bool", "score"):
            continue
        ms = extract_mentions(reviews, spec)
        o = aggregate(spec, ms, len(reviews))
        if o:
            res.append(o)
    return res


WIFI_Q = {"perfect": 95, "excellent": 95, "good": 75, "ok": 55, "normal": 55, "average": 55, "poor": 20, "bad": 10, "slow": 20}
RATING_KEYS = {"cleanness": "cleanliness", "wifi": "wifi_quality", "meal": "breakfast_quality"}


def collect_ratings(reviews: list[dict]) -> list[Obs]:
    """Числовые оценки гостей по категориям (0 = нет оценки), свежие весомее."""
    ref = date.fromisoformat(today())
    acc: dict[str, list[tuple[float, float, str]]] = {}
    for r in reviews:
        w = 0.5 ** (_age_days(r.get("date"), ref) / HALF_LIFE_DAYS)
        det = r.get("detailed") or {}
        for k, feat in RATING_KEYS.items():
            v = det.get(k)
            if isinstance(v, (int, float)) and v > 0:
                acc.setdefault(feat, []).append((v * 10, w, r.get("date") or ""))
        q = (r.get("wifi_quality") or "").lower()
        if q in WIFI_Q:
            acc.setdefault("wifi_quality", []).append((WIFI_Q[q], w, r.get("date") or ""))
    out = []
    for feat, items in acc.items():
        tot = sum(w for _, w, _ in items)
        if len(items) < 3 or tot < 0.5:
            continue
        mean = sum(v * w for v, w, _ in items) / tot
        latest = max(d for _, _, d in items if d) if any(d for _, _, d in items) else today()
        out.append(Obs(feat, {"v": int(round(mean)), "summary": f"Средняя оценка гостей с учётом свежести: {mean / 10:.1f} из 10 ({len(items)} оценок)",
                              "detail": {"ratings": len(items), "mean_10": round(mean / 10, 2)}},
                       "review_ratings", 0.7 * (1 - math.exp(-0.25 * tot)), latest, "Оценки по категориям в отзывах; свежие весомее"))
    return out
