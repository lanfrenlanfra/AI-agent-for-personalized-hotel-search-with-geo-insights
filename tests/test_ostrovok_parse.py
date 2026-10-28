"""Разбор страницы Островка. Страница ниже СИНТЕТИЧЕСКАЯ: собрана по структуре настоящих страниц (ключи __NEXT_DATA__),
чтобы ловить поломки парсера; настоящие данные в тесты не кладём."""
import json

from app import ostrovok

PAGE = {
    "hotel": {
        "masterId": 123, "id": "test_hotel", "name": "Тестовый отель",
        "location": {"city": "Санкт-Петербург", "address": "ул. Тестовая, 1", "latitude": 59.93, "longitude": 30.33,
                     "nearestSubways": [{"name": "Тестовая", "distance": 350}], "pois": [{"name": "Площадь", "distance": 500, "isTopPoi": True}]},
        "facts": {"starRating": 4, "floorsNumber": 6, "roomsNumber": 40, "yearBuilt": 1905},
        "rating": {"ostrovok": {"rating": 9.1, "numReviews": 50, "detailedRatings": {"cleanness": 9.4, "wifi": 8.1}}},
        "amenityGroups": [{"slug": "general", "name": "Общее", "amenities": [{"amenityId": 1, "name": "Лифт", "isFree": True}]}],
        "policies": {"extraInfo": "Часы работы стойки регистрации 10:00 - 22:00.", "pets": {"text": "С животными нельзя"}},
        "descriptionStruct": [{"title": "Об отеле", "paragraphs": ["Описание"]}],
        "images": [{"relativePath": "/t/{size}/extranet/aa/bb/x.jpeg"}],
        "roomGroups": [{"nameStruct": {"mainName": "Стандарт"}, "roomAmenityData": ["desk"], "images": []}],
    },
    "reviews": {"list": [{"id": 7, "reviewDate": "2026-05-01T10:00:00", "reviewPlus": "Тихо", "reviewMinus": "Нет лифта", "reviewText": "", "rating": 9,
                          "detailed": {"cleanness": 9}, "orderContext": {"authorRoom": "Стандарт"}}]},
    "hotelRegionData": {"center": {"lat": 59.9398, "lng": 30.3146}},
}


def test_next_data_roundtrip():
    html = '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": PAGE}}) + "</script></html>"
    assert ostrovok.parse_next_data(html)["hotel"]["masterId"] == 123


def test_build_record():
    rec = ostrovok.build_record(PAGE, "test_hotel", "russia/st._petersburg", "https://example/", {"datelessRate": {"amount": 5000}})
    assert rec["master_id"] == "123" and rec["lat"] == 59.93 and rec["stars"] == 4
    assert rec["price_from"] == 5000 and rec["facts"]["floors"] == 6 and rec["facts"]["year_built"] == 1905
    assert rec["reviews"][0]["date"] == "2026-05-01" and rec["reviews"][0]["minus"] == "Нет лифта"
    assert rec["images"] == ["/t/{size}/extranet/aa/bb/x.jpeg"]
    assert ostrovok.photo_url(rec["images"][0], "wide").startswith("https://cdn.ostrovok.ru/t/828x560/")


def test_normalize_api_review_snake_case():
    r = ostrovok.normalize_review({"id": 1, "review_date": "2025-01-02", "review_plus": "+", "review_minus": "-", "review_text": "t", "rating": 8, "wifi_quality": "Good"})
    assert r["date"] == "2025-01-02" and r["wifi_quality"] == "good"


def test_price_from_page_when_no_catalog_item():
    page = {**PAGE, "datelessRate": {"currencyCode": "RUB", "price": 1869}}
    assert ostrovok.build_record(page, "x", "r", "u")["price_from"] == 1869
    assert ostrovok.build_record(page, "x", "r", "u", {"datelessRate": {"amount": 2500}})["price_from"] == 2500
