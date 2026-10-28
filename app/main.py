"""HTTP-сервис: JSON API + статический интерфейс. Порт 8080."""
from __future__ import annotations

import logging
import re
import secrets
import sys
from datetime import date

import uvicorn
from contextlib import asynccontextmanager
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import db, freshness, hotelier, horizon, ingest, llm, metrics, ostrovok, pipeline, reconcile, registry, search
from .collectors.base import SCOPE_TITLES, SOURCES
from .config import BASE_DIR, settings

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("lupa")
TOKENS: dict[str, str] = {}


def ok(data=None, status: int = 200) -> JSONResponse:
    return JSONResponse(data if data is not None else {"ok": True}, status_code=status, headers={"Cache-Control": "no-store"})


def err(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


async def body(request: Request) -> dict:
    try:
        d = await request.json()
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def need_hotelier(request: Request) -> str | None:
    tok = request.headers.get("x-token") or request.cookies.get("lupa_token")
    return TOKENS.get(tok or "")


# ───────────────────────── служебное ─────────────────────────

async def health(request: Request):
    return JSONResponse({"status": "ok"})


async def meta(request: Request):
    seeded = db.row("SELECT value FROM meta WHERE key='seeded_at'")
    return ok({
        "hotels": db.row("SELECT COUNT(*) AS n FROM hotels")["n"], "features": len(registry.all_features()),
        "llm": llm.available(), "demo_synthetic": bool(getattr(settings, "demo_mode", False)),
        "region": settings.region_slug, "seeded_at": seeded["value"] if seeded else None, "today": date.today().isoformat(),
        "sources": SOURCES, "scopes": SCOPE_TITLES, "groups": registry.GROUP_TITLES,
        "statuses": reconcile.STATUS_TITLES,
    })


# ───────────────────────── отели ─────────────────────────

def photo_urls(rel: list[str], size: str = "card") -> list[str]:
    return [ostrovok.photo_url(p, size) for p in rel]


def hotel_base(r) -> dict:
    h = search.hotel_row(r)
    photos = db.loads(h["photos"], {}) or {}
    synthetic = bool((db.loads(h["card"], {}) or {}).get("synthetic"))
    return {"id": h["id"], "name": h["name"], "stars": h["stars"], "rating": h["rating"], "reviews": h["reviews_count"], "price_from": h["price_from"],
            "lat": h["lat"], "lon": h["lon"], "address": h["address"], "city": h["city"], "url": h["url"],
            "photo": photo_urls(photos.get("images", [])[:1], "card")[0] if photos.get("images") else None,
            "collected_at": h["collected_at"], "synthetic": synthetic}


async def hotels_list(request: Request):
    return ok([hotel_base(r) for r in db.rows("SELECT * FROM hotels ORDER BY reviews_count DESC")])


def fact_view(f, feats) -> dict:
    spec = feats.get(f["feature"]) or {}
    v = db.loads(f["value"], {})
    return {"feature": f["feature"], "title": spec.get("title", f["feature"]), "group": spec.get("group", "custom"), "kind": spec.get("kind"), "unit": spec.get("unit"),
            "scope": f["scope"], "scope_title": SCOPE_TITLES.get(f["scope"], f["scope"]), "value": v.get("v"), "summary": v.get("summary"), "status": f["status"],
            "confidence": round(f["confidence"], 2), "observed_at": f["observed_at"], "expires_at": f["expires_at"], "explanation": f["explanation"],
            "conflict": db.loads(f["conflict"], None) if f["conflict"] else None, "computed": bool(spec.get("computed"))}


async def hotel_detail(request: Request):
    hid = request.path_params["hid"]
    r = db.row("SELECT * FROM hotels WHERE id=?", (hid,))
    if not r:
        return err("отель не найден", 404)
    feats = registry.all_features()
    base = hotel_base(r)
    photos = db.loads(r["photos"], {}) or {}
    card = db.loads(r["card"], {}) or {}
    facts = [fact_view(f, feats) for f in db.rows("SELECT * FROM facts WHERE hotel_id=?", (hid,))]
    groups: dict[str, list] = {}
    for f in facts:
        groups.setdefault(f["group"], []).append(f)
    revs = [{"id": x["id"], "date": x["date"], "rating": x["rating"], "plus": x["plus"], "minus": x["minus"], "text": x["text"], "room": x["room"],
             "images": photo_urls((db.loads(x["images"], {}) or {}).get("images", []), "card")}
            for x in db.rows("SELECT * FROM reviews WHERE hotel_id=? ORDER BY date DESC LIMIT 40", (hid,))]
    qs = [dict(q) for q in db.rows("SELECT id, feature, text, reason, status, created_at FROM questions WHERE hotel_id=? AND status='open' ORDER BY id", (hid,))]
    for q in qs:
        q["title"] = (feats.get(q["feature"]) or {}).get("title", q["feature"])
    base.update({
        "description": search.flatten_description(card.get("description")),
        "stats": {"floors": (card.get("facts") or {}).get("floors"), "rooms": (card.get("facts") or {}).get("rooms"), "year_built": (card.get("facts") or {}).get("year_built")},
        "photos": photo_urls(photos.get("images", []), "wide"), "photos_thumb": photo_urls(photos.get("images", []), "thumb"),
        "room_groups": [{"name": g.get("name"), "images": photo_urls(g.get("images", []), "card")} for g in (photos.get("room_groups") or [])],
        "guest_photos": photo_urls([i["path"] for i in photos.get("review_images", [])][:24], "card"),
        "facts": facts, "groups": [{"key": k, "title": registry.GROUP_TITLES.get(k, k), "facts": v} for k, v in groups.items()],
        "reviews_list": revs, "questions": qs, "events": freshness.events(15, hid),
        "overlay": bool(__import__("app.collectors.geo", fromlist=["x"]).load_overlay(hid)),
    })
    return ok(base)


async def fact_detail(request: Request):
    hid, feature = request.path_params["hid"], request.path_params["feature"]
    scope = request.query_params.get("scope", "hotel")
    feats = registry.all_features()
    f = db.row("SELECT * FROM facts WHERE hotel_id=? AND feature=? AND scope=?", (hid, feature, scope))
    if not f:
        return err("нет такого факта", 404)
    obs = []
    for o in db.rows("SELECT * FROM observations WHERE hotel_id=? AND feature=? AND scope=? ORDER BY superseded, observed_at DESC", (hid, feature, scope)):
        v = db.loads(o["value"], {})
        w, state = reconcile.freshness(o["observed_at"], int((feats.get(feature) or {}).get("ttl_days", 365)))
        obs.append({"id": o["id"], "source": o["source"], "source_title": SOURCES.get(o["source"], {}).get("title", o["source"]), "value": v.get("v"), "summary": v.get("summary"),
                    "detail": v.get("detail"), "confidence": round(o["confidence"], 2), "observed_at": o["observed_at"], "collected_at": o["collected_at"], "method": o["method"],
                    "evidence": db.loads(o["evidence"], []), "simulated": bool(o["simulated"]), "superseded": bool(o["superseded"]), "freshness": state, "freshness_weight": round(w, 2)})
    spec = feats.get(feature) or {}
    return ok({"fact": fact_view(f, feats), "observations": obs, "spec": {k: spec.get(k) for k in ("key", "title", "description", "kind", "ttl_days", "unit", "good", "question", "computed")},
               "scopes": [fact_view(x, feats) for x in db.rows("SELECT * FROM facts WHERE hotel_id=? AND feature=? ORDER BY scope", (hid, feature))]})


async def hotel_horizon(request: Request):
    hid = request.path_params["hid"]
    q = request.query_params
    try:
        res = horizon.horizon(hid, float(q.get("az", 270)), max(1, int(q.get("floor", 5))), min(12, max(1, int(q.get("month", 6)))))
    except ValueError:
        return err("неверные параметры")
    return ok(res)


async def hotel_map(request: Request):
    return ok(horizon.map_buildings(request.path_params["hid"]))


# ───────────────────────── поиск / сравнение ─────────────────────────

async def api_search(request: Request):
    limit = int(request.query_params.get("limit", 30))
    if request.method == "GET":
        return ok(search.search(request.query_params.get("q", "").strip(), limit))
    d = await body(request)
    parsed = None
    if isinstance(d.get("intents"), list):       # пользователь убрал или поправил критерии разобранного запроса
        parsed = {"query": d.get("q", ""), "intents": d["intents"], "filters": d.get("filters") or {}, "text_terms": [], "parsed_by": "edited"}
    return ok(search.search((d.get("q") or "").strip(), limit, parsed))


async def api_parse(request: Request):
    return ok(search.parse_query(request.query_params.get("q", "")))


async def api_compare(request: Request):
    ids = [x for x in request.query_params.get("ids", "").split(",") if x][:4]
    if len(ids) < 2:
        return err("нужно минимум два отеля")
    res = search.compare(ids)
    names = {r["id"]: r["name"] for r in db.rows("SELECT id, name FROM hotels")}
    res["names"] = [names.get(i, i) for i in ids]
    return ok(res)


# ───────────────────────── характеристики ─────────────────────────

TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
STOPW = set("есть быть чтобы можно очень для или как что это был была были при без номер номера отель отеле отеля наличие".split())


def slugify(title: str) -> str:
    s = "".join(TR.get(c, c) for c in title.lower())
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:36]
    return s if re.match(r"[a-z]", s or "") else "f_" + s


def draft_spec(description: str) -> dict:
    """Черновик характеристики по описанию: через LLM, а без него — по ключевым словам."""
    res = llm.chat_json(
        "Ты помогаешь описать новую характеристику отеля для поиска. По описанию верни JSON: {\"title\":короткое название,\"kind\":\"bool\"|\"score\"|\"number\","
        "\"synonyms\":[основы слов, по которым гость будет искать, 4-8],\"review_pos\":[regex для отзывов, подтверждающие],\"review_neg\":[regex, опровергающие],"
        "\"question\":вопрос отельеру,\"ttl_days\":число,\"unit\":для number или null,\"good\":\"high\"|\"low\"}. Regex на русском в нижнем регистре, без заглавных.",
        description) if llm.available() else None
    if isinstance(res, dict) and res.get("title"):
        return {"title": res["title"], "kind": res.get("kind", "bool"), "synonyms": res.get("synonyms") or [], "review": {"pos": res.get("review_pos") or [], "neg": res.get("review_neg") or []},
                "question": res.get("question") or f"Есть ли у вас: {description}?", "ttl_days": res.get("ttl_days") or 365, "unit": res.get("unit"), "good": res.get("good", "high"),
                "description": description, "drafted_by": "llm"}
    words = [w for w in re.findall(r"[а-яёa-z-]{4,}", description.lower().replace("ё", "е")) if w not in STOPW]
    stems = list(dict.fromkeys(w[:-2] if len(w) > 6 else w[:-1] if len(w) > 4 else w for w in words))[:6]
    title = " ".join(description.strip().split()[:4]).capitalize().rstrip(".,")
    return {"title": title, "kind": "bool", "synonyms": stems, "review": {"pos": [], "neg": []}, "question": f"Подтвердите, пожалуйста: {description.strip().rstrip('.?')}?",
            "ttl_days": 365, "description": description, "drafted_by": "rules"}


async def features_list(request: Request):
    return ok(list(registry.all_features().values()))


async def features_suggest(request: Request):
    d = await body(request)
    text = (d.get("description") or "").strip()
    if len(text) < 6:
        return err("опишите характеристику хотя бы парой слов")
    return ok(draft_spec(text))


async def features_add(request: Request):
    d = await body(request)
    if not d.get("title") and d.get("description"):
        d = {**draft_spec(d["description"]), **d}
    if not d.get("title"):
        return err("нужно название или описание")
    d.setdefault("key", slugify(d["title"]))
    d.setdefault("group", "custom")
    try:
        spec = registry.add_custom(d)
    except ValueError as e:
        return err(str(e))
    res = pipeline.apply_feature(spec["key"])
    return ok({"feature": spec, "applied": res}, 201)


async def features_delete(request: Request):
    if not registry.delete_custom(request.path_params["key"]):
        return err("удалить можно только свою характеристику", 400)
    return ok()


# ───────────────────────── отельер ─────────────────────────

async def questions_list(request: Request):
    hid = request.query_params.get("hotel_id")
    st = request.query_params.get("status", "open")
    feats = registry.all_features()
    names = {r["id"]: r["name"] for r in db.rows("SELECT id, name FROM hotels")}
    sql = "SELECT * FROM questions WHERE status=?" + (" AND hotel_id=?" if hid else "") + " ORDER BY id DESC LIMIT 300"
    out = []
    for q in db.rows(sql, (st, hid) if hid else (st,)):
        d = dict(q)
        d["title"] = (feats.get(q["feature"]) or {}).get("title", q["feature"])
        d["hotel"] = names.get(q["hotel_id"])
        d["parsed"] = db.loads(q["parsed"], None) if q["parsed"] else None
        out.append(d)
    return ok(out)


async def question_answer(request: Request):
    qid = int(request.path_params["qid"])
    d = await body(request)
    text = (d.get("text") or "").strip()
    simulated = not need_hotelier(request)
    if not text:
        return err("пустой ответ")
    if simulated and not d.get("simulated"):
        return err("нужен вход отельера", 401)
    try:
        return ok(hotelier.answer_question(qid, text, simulated=bool(simulated)))
    except KeyError:
        return err("вопрос не найден", 404)


async def question_simulate(request: Request):
    qid = int(request.path_params["qid"])
    q = db.row("SELECT * FROM questions WHERE id=?", (qid,))
    if not q:
        return err("вопрос не найден", 404)
    text = hotelier.simulate_answer(q["hotel_id"], q["feature"])
    res = hotelier.answer_question(qid, text, simulated=True)
    res["answer_text"] = text
    return ok(res)


async def hotelier_login(request: Request):
    d = await body(request)
    if d.get("login") == settings.hotelier_login and d.get("password") == settings.hotelier_password:
        tok = secrets.token_urlsafe(24)
        TOKENS[tok] = d["login"]
        resp = ok({"token": tok, "login": d["login"]})
        resp.set_cookie("lupa_token", tok, httponly=True, samesite="lax", max_age=8 * 3600)
        return resp
    return err("неверный логин или пароль", 401)


async def hotelier_confirm(request: Request):
    if not need_hotelier(request):
        return err("нужен вход отельера", 401)
    d = await body(request)
    try:
        return ok(hotelier.confirm_fact(d["hotel_id"], d["feature"], d.get("scope", "hotel"), d.get("value")))
    except (KeyError, ValueError) as e:
        return err(str(e))


# ───────────────────────── актуализация ─────────────────────────

async def api_freshness(request: Request):
    return ok({**freshness.scan(int(request.query_params.get("days", 30))), "events": freshness.events(30)})


async def api_events(request: Request):
    return ok(freshness.events(int(request.query_params.get("limit", 50)), request.query_params.get("hotel_id")))


async def hotel_refresh(request: Request):
    d = await body(request)
    try:
        return ok(freshness.refresh_hotel(request.path_params["hid"], bool(d.get("online"))))
    except KeyError:
        return err("отель не найден", 404)


async def refresh_stale(request: Request):
    d = await body(request)
    freshness.recompute_statuses()
    return ok(freshness.refresh_stale(int(d.get("limit", 10)), bool(d.get("online"))))


async def demo_building(request: Request):
    hid = request.path_params["hid"]
    if request.method == "DELETE":
        return ok(freshness.reset_buildings(hid))
    d = await body(request)
    return ok(freshness.add_building(hid, float(d.get("bearing", 270)), float(d.get("dist", 45)), float(d.get("height", 40))))


# ───────────────────────── метрики и разметка ─────────────────────────

async def api_metrics(request: Request):
    return ok(metrics.overview())


async def eval_candidates(request: Request):
    """Факты для ручной проверки: сначала самые неуверенные и спорные."""
    feats = registry.all_features()
    names = {r["id"]: r["name"] for r in db.rows("SELECT id, name FROM hotels")}
    done = {(x["hotel_id"], x["feature"], x.get("scope", "hotel")) for x in metrics.load_labels()}
    rows = []
    for f in db.rows("SELECT * FROM facts ORDER BY CASE status WHEN 'disputed' THEN 0 WHEN 'stale' THEN 1 ELSE 2 END, confidence LIMIT 400"):
        spec = feats.get(f["feature"])
        if not spec or spec["kind"] != "bool" or (f["hotel_id"], f["feature"], f["scope"]) in done:
            continue
        rows.append({**fact_view(f, feats), "hotel_id": f["hotel_id"], "hotel": names.get(f["hotel_id"])})
        if len(rows) >= 30:
            break
    return ok(rows)


async def eval_label(request: Request):
    d = await body(request)
    for k in ("hotel_id", "feature", "value"):
        if k not in d:
            return err(f"нет поля {k}")
    return ok(metrics.add_label(d["hotel_id"], d["feature"], d.get("scope", "hotel"), d["value"], d.get("note", "")))


# ───────────────────────── приложение ─────────────────────────

async def index(request: Request):
    return FileResponse(settings.web_dir / "index.html")


async def not_found(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return err("не найдено", 404)
    p = settings.web_dir / "index.html"
    return FileResponse(p) if p.exists() else JSONResponse({"error": "not found"}, 404)


def startup():
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    pick_data_dir()
    r = pipeline.bootstrap()
    log.info("готово: %s, демо-синтетика: %s", r, getattr(settings, "demo_mode", False))
    freshness.start_background()


def pick_data_dir() -> None:
    """Нет собранных данных → синтетический демо-набор (честно помечается в интерфейсе)."""
    settings.demo_mode = False
    if ingest.list_raw():
        return
    demo = BASE_DIR / "data" / "demo"
    if not (demo / "raw" / "ostrovok").exists():
        from . import synthetic
        synthetic.build(demo)
    settings.data_dir = demo
    settings.demo_mode = True


routes = [
    Route("/health", health),
    Route("/api/meta", meta),
    Route("/api/hotels", hotels_list),
    Route("/api/hotels/{hid}", hotel_detail),
    Route("/api/hotels/{hid}/facts/{feature}", fact_detail),
    Route("/api/hotels/{hid}/horizon", hotel_horizon),
    Route("/api/hotels/{hid}/map", hotel_map),
    Route("/api/hotels/{hid}/refresh", hotel_refresh, methods=["POST"]),
    Route("/api/hotels/{hid}/demo/building", demo_building, methods=["POST", "DELETE"]),
    Route("/api/search", api_search, methods=["GET", "POST"]),
    Route("/api/parse", api_parse),
    Route("/api/compare", api_compare),
    Route("/api/features", features_list, methods=["GET"]),
    Route("/api/features", features_add, methods=["POST"]),
    Route("/api/features/suggest", features_suggest, methods=["POST"]),
    Route("/api/features/{key}", features_delete, methods=["DELETE"]),
    Route("/api/questions", questions_list),
    Route("/api/questions/{qid:int}/answer", question_answer, methods=["POST"]),
    Route("/api/questions/{qid:int}/simulate", question_simulate, methods=["POST"]),
    Route("/api/hotelier/login", hotelier_login, methods=["POST"]),
    Route("/api/hotelier/confirm", hotelier_confirm, methods=["POST"]),
    Route("/api/freshness", api_freshness),
    Route("/api/freshness/refresh", refresh_stale, methods=["POST"]),
    Route("/api/events", api_events),
    Route("/api/metrics", api_metrics),
    Route("/api/eval/candidates", eval_candidates),
    Route("/api/eval/labels", eval_label, methods=["POST"]),
    Route("/", index),
]
if settings.web_dir.exists():
    routes.append(Mount("/static", StaticFiles(directory=str(settings.web_dir)), name="static"))

BOOT = True


@asynccontextmanager
async def lifespan(_app):
    if BOOT:
        startup()
    yield


app = Starlette(routes=routes, lifespan=lifespan, exception_handlers={404: not_found})


def run():
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, log_level="info")


if __name__ == "__main__":
    run()
