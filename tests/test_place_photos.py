"""Test ảnh địa điểm: thumbnail trong browse_places / search_places / get_place_details và tool
get_place_photos (DB trước, thiếu thì tra SerpApi rồi ghi lại). Không gọi DB thật, HTTP mock bằng respx."""

import asyncio

import httpx
import pytest
import respx
from fastmcp import Client

from src.config.settings import settings
from src.services.serpapi_client import serpapi_client
from src.services.travel_db import _COT_RUT_GON, travel_db

SEARCH_URL = f"{serpapi_client.base_url}/search.json"
GOC = {"seed_query": None, "best_match": False, "address": "Huế", "price_range": None, "summary": None,
       "website": None, "phone": None, "wheelchair": False, "fetched_at": "2026-10-06T10:00:00+00:00",
       "destination": "hue", "category": "Chùa", "lat": 16.45, "lng": 107.55, "rating": 4.6, "review_count": 100}
DS = [
    {**GOC, "cid": "111", "place_id": "ChIJa", "data_id": "0xa", "title": "Chùa Thiên Mụ", "thumbnail": "https://img/a.jpg",
     "images": ["https://img/a1.jpg", "https://img/a2.jpg"]},
    {**GOC, "cid": "222", "place_id": "ChIJb", "data_id": "0xb", "title": "Chùa Từ Hiếu", "thumbnail": None},
    {**GOC, "cid": "333", "place_id": None, "data_id": "0xc", "title": "Chùa Diệu Đế", "thumbnail": None},
]


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "pg_host", "db-gia")
    monkeypatch.setattr(settings, "pg_data_database", "db-gia")
    ghi: list[tuple] = []

    async def fetch(sql: str, *args):
        await asyncio.sleep(0)
        if sql.startswith("UPDATE gmaps_place"):
            url, id_ = args
            ghi.append((url, id_))
            return [{"cid": p["cid"]} for p in DS if id_ in (p["cid"], p["place_id"]) and not p["thumbnail"]]
        if "cid = ANY" in sql:  # find_places
            cids, ids = args
            return [
                {**p, "_cid": p["cid"], "_pid": p["place_id"], "_did": p["data_id"]}
                for p in DS
                if p["cid"] in cids or p["cid"] in ids or p["place_id"] in ids or p["data_id"] in ids
            ]
        if "FROM gmaps_place" in sql:
            return [dict(p) for p in DS]
        return []

    monkeypatch.setattr(travel_db, "fetch", fetch)
    return ghi


@pytest.fixture
def serp(monkeypatch):
    monkeypatch.setattr(serpapi_client, "api_keys", ["k1"])
    monkeypatch.setattr(serpapi_client, "_idx", 0)
    monkeypatch.setattr(serpapi_client, "_nghi_den", {})


async def _goi(ten: str, args: dict) -> dict:
    from src.fastmcp_server import mcp

    async with Client(mcp) as c:
        r = await c.call_tool(ten, args, raise_on_error=False)
    return r.structured_content


def _tra_anh(request: httpx.Request) -> httpx.Response:
    p = request.url.params
    khoa = p.get("place_id") or p.get("data_cid")
    return httpx.Response(200, json={"place_results": {"title": khoa, "thumbnail": f"https://serp/{khoa}.jpg"}})


def test_danh_sach_rut_gon_co_thumbnail():
    assert "thumbnail" in _COT_RUT_GON


async def test_browse_places_tra_thumbnail(db):
    kq = await _goi("browse_places", {"destination": "Huế"})
    theo_ten = {p["name"]: p for p in kq["places"]}
    assert theo_ten["Chùa Thiên Mụ"]["thumbnail"] == "https://img/a.jpg"
    assert "thumbnail" not in theo_ten["Chùa Từ Hiếu"]  # null bị drop_empty bỏ


async def test_get_place_details_db_co_thumbnail_va_images(db):
    kq = await _goi("get_place_details", {"place_id": "ChIJa", "source": "db"})
    assert kq["place"]["thumbnail"] == "https://img/a.jpg"
    assert kq["place"]["images"] == ["https://img/a1.jpg", "https://img/a2.jpg"]


@respx.mock
async def test_get_place_details_serpapi_toi_da_6_anh(serp):
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={"place_results": {"title": "Chợ Hội An", "place_id": "p1", "thumbnail": "https://t.jpg",
                                    "images": [{"title": f"Ảnh {i}", "thumbnail": f"https://i/{i}.jpg"} for i in range(9)]}},
        )
    )
    kq = await _goi("get_place_details", {"place_id": "p1", "source": "live"})
    assert kq["place"]["thumbnail"] == "https://t.jpg"
    assert kq["place"]["images"] == [f"https://i/{i}.jpg" for i in range(6)]


@respx.mock
async def test_search_places_giu_thumbnail(serp):
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"local_results": [{"title": "Quán A", "place_id": "p1", "thumbnail": "https://q.jpg"}]})
    )
    kq = await _goi("search_places", {"query": "quán chay Hội An"})
    assert kq["places"][0]["thumbnail"] == "https://q.jpg"


@respx.mock
async def test_get_place_photos_db_truoc_thieu_thi_tra_serpapi_va_ghi_lai(db, serp):
    route = respx.get(SEARCH_URL).mock(side_effect=_tra_anh)
    kq = await _goi("get_place_photos", {"places": ["ChIJa", "ChIJb", "0xc", "ChIJa"]})

    assert kq["photos"] == {
        "ChIJa": "https://img/a.jpg",
        "ChIJb": "https://serp/ChIJb.jpg",
        "0xc": "https://serp/333.jpg",  # không có place_id -> tra theo cid
    }
    assert (kq["from_db"], kq["from_serpapi"]) == (1, 2)
    assert route.call_count == 2
    assert sorted(db) == [("https://serp/333.jpg", "333"), ("https://serp/ChIJb.jpg", "222")]


@respx.mock
async def test_get_place_photos_source_db_khong_goi_serpapi(db, serp):
    route = respx.get(SEARCH_URL).mock(side_effect=_tra_anh)
    kq = await _goi("get_place_photos", {"places": ["ChIJa", "ChIJb"], "source": "db"})
    assert kq["photos"] == {"ChIJa": "https://img/a.jpg", "ChIJb": None}
    assert (kq["from_db"], kq["from_serpapi"]) == (1, 0)
    assert route.call_count == 0


@respx.mock
async def test_get_place_photos_theo_ten_va_gioi_han_12_lan_tra(db, serp, monkeypatch):
    route = respx.get(SEARCH_URL).mock(side_effect=_tra_anh)
    ids = [f"ChIJx{i}" for i in range(15)]
    kq = await _goi("get_place_photos", {"places": ["Chùa Thiên Mụ", *ids], "destination": "Huế"})

    assert kq["photos"]["Chùa Thiên Mụ"] == "https://img/a.jpg"
    assert route.call_count == 12
    assert kq["from_serpapi"] == 12
    assert sum(1 for v in kq["photos"].values() if v is None) == 3


@respx.mock
async def test_get_place_photos_khong_co_db_serpapi_loi_tra_null(serp):
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(500, json={"error": "lỗi"}))
    kq = await _goi("get_place_photos", {"places": ["ChIJz", "Quán không id"]})
    assert kq["status"] == "success"
    assert kq["photos"] == {"ChIJz": None, "Quán không id": None}
    assert (kq["from_db"], kq["from_serpapi"]) == (0, 0)


async def test_get_place_photos_danh_sach_rong():
    kq = await _goi("get_place_photos", {"places": ["  "]})
    assert kq["status"] == "error"


async def test_save_thumbnail_sua_cache(db):
    await travel_db.places()
    assert await travel_db.save_thumbnail("ChIJb", "https://moi.jpg") == 1
    p = next(x for x in await travel_db.places() if x["cid"] == "222")
    assert p["thumbnail"] == "https://moi.jpg"


async def test_save_images_gioi_han_6_va_dieu_kien_sql(monkeypatch):
    monkeypatch.setattr(settings, "pg_host", "db-gia")
    monkeypatch.setattr(settings, "pg_data_database", "db-gia")
    goi: list[tuple] = []

    async def fetch(sql: str, *args):
        goi.append((sql, args))
        return [{"cid": "222"}]

    monkeypatch.setattr(travel_db, "fetch", fetch)
    urls = ["https://a.jpg", "https://a.jpg", *[f"https://{i}.jpg" for i in range(8)]]
    assert await travel_db.save_images("ChIJb", urls) == 1
    sql, (ds, id_) = goi[0]
    assert "images = $1::jsonb" in sql and "jsonb_array_length(images) < 3" in sql
    assert id_ == "ChIJb" and ds == ["https://a.jpg", *[f"https://{i}.jpg" for i in range(5)]]
    assert await travel_db.save_images("ChIJb", []) == 0 and len(goi) == 1


def _serp_co_anh() -> httpx.Response:
    return httpx.Response(
        200,
        json={"place_results": {"title": "Chợ", "place_id": "p1", "thumbnail": "https://t.jpg",
                                "images": [{"thumbnail": f"https://i/{i}.jpg"} for i in range(3)]}},
    )


@respx.mock
async def test_get_place_details_live_ghi_anh_vao_db(serp, monkeypatch):
    monkeypatch.setattr(settings, "pg_host", "db-gia")
    monkeypatch.setattr(settings, "pg_data_database", "db-gia")
    ghi: list[tuple] = []

    async def save_images(id_, urls):
        ghi.append(("images", id_, urls))
        return 1

    async def save_thumbnail(id_, url):
        ghi.append(("thumbnail", id_, url))
        return 1

    monkeypatch.setattr(travel_db, "save_images", save_images)
    monkeypatch.setattr(travel_db, "save_thumbnail", save_thumbnail)
    respx.get(SEARCH_URL).mock(return_value=_serp_co_anh())

    kq = await _goi("get_place_details", {"place_id": "p1", "source": "live"})

    assert kq["place"]["images"] == [f"https://i/{i}.jpg" for i in range(3)]
    assert ghi == [("images", "p1", [f"https://i/{i}.jpg" for i in range(3)]), ("thumbnail", "p1", "https://t.jpg")]


@respx.mock
async def test_get_place_details_ghi_anh_loi_van_tra_ket_qua(serp, monkeypatch):
    from src.services.travel_db import TravelDbError

    monkeypatch.setattr(settings, "pg_host", "db-gia")
    monkeypatch.setattr(settings, "pg_data_database", "db-gia")

    async def hong(*_a):
        raise TravelDbError("mất kết nối")

    monkeypatch.setattr(travel_db, "save_images", hong)
    monkeypatch.setattr(travel_db, "save_thumbnail", hong)
    respx.get(SEARCH_URL).mock(return_value=_serp_co_anh())

    kq = await _goi("get_place_details", {"place_id": "p1", "source": "live"})

    assert kq["status"] == "success"
    assert kq["place"]["thumbnail"] == "https://t.jpg"


def test_anh_serpapi_lay_anh_review_khi_khong_co_images():
    from src.tools.get_place_details import anh_serpapi

    p = {
        "thumbnail": "t",
        "user_reviews": {
            "most_relevant": [
                {"username": "A", "images": [{"thumbnail": "a1"}, {"thumbnail": "a2"}, {"thumbnail": "a3"}]},
                {"username": "B", "images": [{"thumbnail": "b1"}]},
                {"username": "C", "images": [{"thumbnail": "c1"}, {"thumbnail": "c2"}]},
            ]
        },
    }
    # Mỗi review một ảnh trước, rồi mới tới ảnh thứ hai; tối đa 6.
    assert anh_serpapi(p) == ["a1", "b1", "c1", "a2", "c2", "a3"]
    assert anh_serpapi({"images": [{"thumbnail": "x"}]}) == ["x"]
    assert anh_serpapi({}) is None
