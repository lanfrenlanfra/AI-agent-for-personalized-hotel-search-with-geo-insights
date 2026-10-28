"""Загрузка сырых данных (data/raw/ostrovok/*.json) в базу."""
from __future__ import annotations

import json
from pathlib import Path

from . import db
from .config import settings


def raw_dir() -> Path:
    return settings.data_dir / "raw" / "ostrovok"


def list_raw() -> list[Path]:
    d = raw_dir()
    return sorted(d.glob("*.json")) if d.exists() else []


def load_record(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def photos_of(rec: dict) -> dict:
    return {"images": rec.get("images", []), "room_groups": [{"name": g["name"], "images": g["images"]} for g in rec.get("room_groups", [])],
            "review_images": rec.get("review_images", [])}


def upsert_hotel(rec: dict) -> str:
    hid = rec["slug"] or rec["master_id"]
    card = {
        "facts": rec.get("facts"), "amenities": rec.get("amenities"), "policies": rec.get("policies"),
        "detailed_ratings": rec.get("detailed_ratings"), "description": rec.get("description"),
        "room_groups": rec.get("room_groups"), "nearest_subways": rec.get("nearest_subways"),
        "nearest_beaches": rec.get("nearest_beaches"), "pois": rec.get("pois"), "neighborhood": rec.get("neighborhood"),
        "region_center": rec.get("region_center"), "kind": rec.get("kind"), "synthetic": bool(rec.get("synthetic")),
    }
    with db.tx() as c:
        c.execute(
            "INSERT INTO hotels(id, master_id, slug, name, city, address, lat, lon, stars, rating, reviews_count, price_from, url, photos, card, collected_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "name=excluded.name, city=excluded.city, address=excluded.address, lat=excluded.lat, lon=excluded.lon, stars=excluded.stars, "
            "rating=excluded.rating, reviews_count=excluded.reviews_count, price_from=excluded.price_from, url=excluded.url, "
            "photos=excluded.photos, card=excluded.card, collected_at=excluded.collected_at",
            (hid, rec["master_id"], rec.get("slug"), rec["name"], rec.get("city"), rec.get("address"), rec.get("lat"), rec.get("lon"),
             rec.get("stars"), rec.get("rating"), rec.get("num_reviews"), rec.get("price_from"), rec.get("url"),
             db.dumps(photos_of(rec)), db.dumps(card), rec.get("fetched_at")),
        )
        c.execute("DELETE FROM reviews WHERE hotel_id=?", (hid,))
        for r in rec.get("reviews", []):
            c.execute(
                "INSERT OR REPLACE INTO reviews(id, hotel_id, date, rating, plus, minus, text, room, traveller_type, images) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (f'{hid}:{r["id"]}', hid, r.get("date"), r.get("rating"), r.get("plus"), r.get("minus"), r.get("text"),
                 r.get("room"), r.get("traveller"), db.dumps({"images": r.get("images", []), "detailed": r.get("detailed", {}), "wifi_quality": r.get("wifi_quality")})),
            )
    return hid


def ingest_all() -> list[str]:
    ids = []
    for p in list_raw():
        rec = load_record(p)
        if rec.get("lat") is None or rec.get("lon") is None:
            continue
        ids.append(upsert_hotel(rec))
    return ids
