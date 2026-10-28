"""Разбор данных Островка.

Страница отеля — Next.js: всё нужное лежит в <script id="__NEXT_DATA__"> →
props.pageProps.hotel (карточка), pageProps.reviews.list (первые отзывы).
Остальные отзывы отдаёт https://ostrovok.ru/hotel/search/v1/site/ost_reviews?master_id=…
Список отелей региона — pageProps.serpData.hotels (20 штук на странице, ?page=N).

Модуль чистый (без сети), поэтому его можно проверять на сохранённых страницах.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
CDN = "https://cdn.ostrovok.ru"
SIZES = {"thumb": "240x240", "card": "640x400", "wide": "828x560", "hero": "1200x616"}


def parse_next_data(html: str) -> dict:
    m = NEXT_DATA_RE.search(html)
    if not m:
        raise ValueError("на странице нет __NEXT_DATA__ (возможно, антибот или другой формат)")
    return json.loads(m.group(1))["props"]["pageProps"]


def photo_url(rel: str, size: str = "card") -> str:
    """relativePath вида /t/{size}/extranet/10/7d/….JPEG → полный URL CDN."""
    return CDN + rel.replace("{size}", SIZES.get(size, size))


def _date(s: str | None) -> str | None:
    if not s:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else None


def _rel(img: dict) -> str | None:
    return img.get("relativePath") or img.get("relative_path")


def normalize_review(r: dict) -> dict:
    """Приводит отзыв из __NEXT_DATA__ (camelCase) и из ost_reviews (snake_case) к одному виду."""
    oc = r.get("orderContext") or r.get("order_context") or {}
    det = r.get("detailed") or {}
    imgs = [p for p in (_rel(i) for i in (r.get("images") or [])) if p]
    return {
        "id": str(r.get("id")),
        "date": _date(r.get("reviewDate") or r.get("review_date")),
        "plus": (r.get("reviewPlus") if "reviewPlus" in r else r.get("review_plus")) or "",
        "minus": (r.get("reviewMinus") if "reviewMinus" in r else r.get("review_minus")) or "",
        "text": (r.get("reviewText") if "reviewText" in r else r.get("review_text")) or "",
        "rating": r.get("rating"),
        "room": oc.get("authorRoom") or oc.get("author_room"),
        "traveller": r.get("travellerType") or r.get("traveller_type"),
        "trip": r.get("tripType") or r.get("trip_type"),
        "adults": oc.get("adultNum", oc.get("adult_num")),
        "children": oc.get("childNum", oc.get("child_num")),
        "detailed": {k: v for k, v in det.items() if isinstance(v, (int, float))},
        "wifi_quality": (r.get("wifiQuality") or r.get("wifi_quality") or "").lower() or None,
        "images": imgs[:4],
    }


def _clean_policy(p: Any) -> Any:
    if not isinstance(p, dict):
        return None
    return p.get("value")


def build_record(page: dict, slug: str, region: str, url: str, serp: dict | None = None, api_reviews: dict | None = None) -> dict:
    """Собрать компактную запись об отеле для data/raw/ostrovok/<masterId>.json."""
    h = page["hotel"]
    loc = h.get("location") or {}
    rating = h.get("rating") or {}
    ost = rating.get("ostrovok") or {}
    reviews_src = []
    review_images: list[dict] = []
    if api_reviews and api_reviews.get("reviews"):
        reviews_src = api_reviews["reviews"]
        review_images = [{"review_id": str(i.get("review_id")), "path": _rel(i)} for i in (api_reviews.get("images") or []) if _rel(i)]
    else:
        reviews_src = (page.get("reviews") or {}).get("list") or []
    reviews = [normalize_review(r) for r in reviews_src]
    pol = h.get("policies") or {}
    facts = h.get("facts") or {}
    s = serp or {}
    price = None
    for src in (s, page):                       # цена «от»: из каталога, а если её нет — со страницы отеля (там поле price)
        for k in ("datefullRate", "datelessRate"):
            v = src.get(k)
            if isinstance(v, dict):
                amount = v.get("amount") or v.get("price")
                if amount:
                    price = float(amount)
                    break
        if price:
            break
    return {
        "schema": 1,
        "master_id": str(h.get("masterId")),
        "slug": h.get("id") or slug,
        "region": region,
        "url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "name": h.get("name"),
        "city": loc.get("city"),
        "address": loc.get("address"),
        "postal_code": loc.get("postalCode"),
        "neighborhood": (loc.get("neighborhood") or {}).get("name"),
        "lat": loc.get("latitude"),
        "lon": loc.get("longitude"),
        "stars": facts.get("starRating") or s.get("stars") or 0,
        "rating": ost.get("rating") if ost.get("rating") is not None else rating.get("total"),
        "num_reviews": ost.get("numReviews") or rating.get("reviewsCount") or (api_reviews or {}).get("total") or 0,
        "price_from": price,
        "kind": facts.get("kind"),
        "facts": {
            "floors": facts.get("floorsNumber") or 0,
            "rooms": facts.get("roomsNumber") or 0,
            "year_built": facts.get("yearBuilt") or 0,
            "year_renovated": facts.get("yearRenovated") or 0,
        },
        "region_center": (page.get("hotelRegionData") or {}).get("center"),
        "nearest_subways": [{"name": x.get("name"), "distance": x.get("distance")} for x in (loc.get("nearestSubways") or [])][:5],
        "nearest_beaches": s.get("nearestBeaches") or [],
        "pois": [{"name": x.get("name"), "distance": x.get("distance"), "top": bool(x.get("isTopPoi")), "hub": bool(x.get("isTransportHub"))}
                 for x in (loc.get("pois") or [])][:30],
        "amenities": {g.get("slug"): {"name": g.get("name"), "items": [{"id": a.get("amenityId"), "name": a.get("name"), "free": a.get("isFree")} for a in g.get("amenities", [])]}
                      for g in (h.get("amenityGroups") or [])},
        "policies": {
            "check_in_out": _clean_policy(pol.get("checkInCheckOut")),
            "pets": _clean_policy(pol.get("pets")),
            "parking": _clean_policy(pol.get("parking")),
            "children": _clean_policy(pol.get("children")),
            "extra_bed": _clean_policy(pol.get("extraBed")),
            "cradle": _clean_policy(pol.get("cradle")),
            "deposit": _clean_policy(pol.get("deposit")),
            "internet": _clean_policy(pol.get("internet")),
            "meal": _clean_policy(pol.get("meal")),
            "extra_info": _clean_policy(pol.get("extraInfo")),
        },
        "detailed_ratings": ost.get("detailedRatings") or {},
        "description": h.get("descriptionStruct") or [],
        "images": [p for p in (_rel(i) for i in (h.get("images") or [])) if p][:24],
        "room_groups": [
            {
                "name": (g.get("nameStruct") or {}).get("mainName"),
                "bedding": (g.get("nameStruct") or {}).get("beddingType"),
                "size": g.get("size"),
                "amenities": g.get("roomAmenityData") or [],
                "images": [p for p in (_rel(i) for i in (g.get("images") or [])) if p][:4],
            }
            for g in (h.get("roomGroups") or [])
        ],
        "reviews": reviews,
        "review_images": review_images[:80],
    }


def serp_items(page: dict) -> list[dict]:
    return ((page.get("serpData") or {}).get("hotels")) or []


def serp_summary(item: dict) -> dict:
    loc = item.get("location") or {}
    return {
        "master_id": str(item.get("masterId")),
        "slug": item.get("id"),
        "name": item.get("name"),
        "lat": loc.get("latitude"),
        "lon": loc.get("longitude"),
        "num_reviews": item.get("numReviews") or 0,
        "rating": item.get("rating"),
        "stars": item.get("stars"),
        "region_slug": item.get("regionCatalogSlug"),
        "serp": {k: item.get(k) for k in ("datelessRate", "datefullRate", "stars", "nearestBeaches", "nearestSubways", "searchRegionCenterDistance")},
    }
