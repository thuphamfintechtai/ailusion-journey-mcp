"""Test các tool dùng SerpApi: gọi qua MCP client in-memory, mock HTTP bằng respx."""

from datetime import date, timedelta

import httpx
import pytest
import respx
from fastmcp import Client

from src.services.serpapi_client import serpapi_client

SEARCH_URL = f"{serpapi_client.base_url}/search.json"
NGAY = (date.today() + timedelta(days=30)).isoformat()
NGAY_SAU = (date.today() + timedelta(days=32)).isoformat()


@pytest.fixture(autouse=True)
def _key_gia(monkeypatch):
    monkeypatch.setattr(serpapi_client, "api_keys", ["k1", "k2"])
    monkeypatch.setattr(serpapi_client, "_idx", 0)
    monkeypatch.setattr(serpapi_client, "_nghi_den", {})


async def _goi(ten: str, args: dict) -> dict:
    from src.fastmcp_server import mcp

    async with Client(mcp) as c:
        r = await c.call_tool(ten, args, raise_on_error=False)
    return r.structured_content


@respx.mock
async def test_search_hotels_gon_ket_qua_va_tham_so_vn():
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "properties": [
                    {
                        "name": "Hotel A",
                        "overall_rating": 4.5,
                        "rate_per_night": {"extracted_lowest": 900000},
                        "prices": [{"source": "Agoda", "rate_per_night": {"extracted_lowest": 880000}}],
                        "amenities": ["Hồ bơi"],
                        "property_token": "tok",
                    }
                ]
            },
        )
    )
    kq = await _goi(
        "search_hotels",
        {
            "destination": "Hội An",
            "check_in_date": NGAY,
            "check_out_date": NGAY_SAU,
            "children_ages": [4, 8],
            "hotel_class": [5, 4],
            "sort_by": "lowest_price",
        },
    )

    assert kq["status"] == "success"
    assert kq["nights"] == 2
    assert kq["hotels"][0]["price_per_night_vnd"] == 900000
    assert kq["hotels"][0]["prices_by_source"][0]["source"] == "Agoda"
    p = route.calls.last.request.url.params
    assert p["currency"] == "VND" and p["gl"] == "vn" and p["hl"] == "vi"
    assert p["children"] == "2" and p["children_ages"] == "4,8"
    assert p["hotel_class"] == "4,5" and p["sort_by"] == "3"


async def test_search_hotels_ngay_tra_truoc_ngay_nhan():
    kq = await _goi(
        "search_hotels",
        {"destination": "Huế", "check_in_date": NGAY_SAU, "check_out_date": NGAY},
    )
    assert kq["status"] == "error" and kq["error_code"] == -2


@respx.mock
async def test_search_flights_khu_hoi():
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "best_flights": [
                    {
                        "price": 2500000,
                        "total_duration": 80,
                        "flights": [
                            {
                                "departure_airport": {"id": "SGN", "time": f"{NGAY} 06:00"},
                                "arrival_airport": {"id": "DAD", "time": f"{NGAY} 07:20"},
                                "airline": "Vietjet",
                                "flight_number": "VJ 620",
                                "duration": 80,
                            }
                        ],
                        "departure_token": "dep",
                    }
                ],
                "other_flights": [{"price": 3000000, "flights": []}],
                "price_insights": {"lowest_price": 2500000, "price_level": "low"},
            },
        )
    )
    kq = await _goi(
        "search_flights",
        {"departure_id": "sgn", "arrival_id": "dad", "outbound_date": NGAY, "return_date": NGAY_SAU},
    )

    assert kq["trip_type"] == "round_trip"
    assert kq["flights"][0]["best"] is True
    assert kq["flights"][0]["legs"][0]["airline"] == "Vietjet"
    assert "best" not in kq["flights"][1]
    assert kq["price_insights"]["lowest_price_vnd"] == 2500000
    p = route.calls.last.request.url.params
    assert p["type"] == "1" and p["departure_id"] == "SGN" and "stops" not in p


async def test_search_flights_ten_khong_xac_dinh_bao_dung_find_airports():
    kq = await _goi(
        "search_flights", {"departure_id": "Bangkok", "arrival_id": "DAD", "outbound_date": NGAY}
    )
    assert kq["status"] == "error"
    assert "find_airports" in kq["error"]


@respx.mock
async def test_search_flights_nhan_ten_thanh_pho_vn():
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"best_flights": []}))
    kq = await _goi(
        "search_flights", {"departure_id": "Sài Gòn", "arrival_id": "Hội An", "outbound_date": NGAY}
    )
    assert kq["route"] == "SGN -> DAD"
    p = route.calls.last.request.url.params
    assert p["departure_id"] == "SGN" and p["arrival_id"] == "DAD"


@respx.mock
async def test_search_places_loc_rating():
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "local_results": [
                    {"title": "Quán A", "place_id": "p1", "rating": 4.7},
                    {"title": "Quán B", "place_id": "p2", "rating": 3.9},
                ]
            },
        )
    )
    kq = await _goi(
        "search_places", {"query": "quán chay Hội An", "lat": 15.88, "lng": 108.33, "min_rating": 4.5}
    )

    assert [p["name"] for p in kq["places"]] == ["Quán A"]
    assert route.calls.last.request.url.params["ll"] == "@15.88,108.33,14z"


async def test_search_places_thieu_lng():
    kq = await _goi("search_places", {"query": "cafe", "lat": 10.7})
    assert kq["status"] == "error"


@respx.mock
async def test_get_place_details_gon_gio_dong_khach():
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "place_results": {
                    "title": "Chợ Hội An",
                    "place_id": "p1",
                    "popular_times": {
                        "graph_results": {
                            "monday": [
                                {"time": "6 AM", "busyness_score": 0},
                                {"time": "9 AM", "busyness_score": 70},
                            ]
                        }
                    },
                    "extensions": [{"accessibility": ["Lối vào cho xe lăn"]}],
                }
            },
        )
    )
    kq = await _goi("get_place_details", {"place_id": "p1"})

    assert kq["place"]["popular_times"]["by_day"] == {"monday": [["9 AM", 70]]}
    assert kq["place"]["attributes"][0]["accessibility"] == ["Lối vào cho xe lăn"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("People typically spend 45 min to 1.5 hr here", {"low": 45, "high": 90}),
        ("Mọi người thường dành 1-2 giờ ở đây", {"low": 60, "high": 120}),
        ("Mọi người thường dành 15 phút ở đây", {"low": 15, "high": 15}),
        ("People typically spend up to 1 hr here", {"low": 60, "high": 60}),
        ("Mọi người thường dành 1,5 tiếng tới 3 tiếng ở đây", {"low": 90, "high": 180}),
        ("không có số", None),
        (None, None),
    ],
)
def test_doc_thoi_gian_o_lai(text, expected):
    from src.tools.get_place_details import thoi_gian_o_lai

    assert thoi_gian_o_lai(text) == expected


@respx.mock
async def test_get_place_details_tra_thoi_gian_o_lai():
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "place_results": {
                    "title": "Chùa Cầu",
                    "place_id": "p2",
                    "popular_times": {
                        "live_hash": {"time_spent": "Mọi người thường dành 30 phút đến 1 giờ ở đây"}
                    },
                }
            },
        )
    )
    kq = await _goi("get_place_details", {"place_id": "p2"})
    assert kq["place"]["time_spent"] == "Mọi người thường dành 30 phút đến 1 giờ ở đây"
    assert kq["place"]["stay_min"] == {"low": 30, "high": 60}


@respx.mock
async def test_get_place_reviews_nhieu_trang():
    trang = iter(
        [
            httpx.Response(
                200,
                json={
                    "place_info": {"title": "Quán A"},
                    "topics": [{"keyword": "đỗ xe", "mentions": 12}],
                    "reviews": [{"rating": 5, "snippet": "ngon", "iso_date": "2026-09-01"}],
                    "serpapi_pagination": {"next_page_token": "t2"},
                },
            ),
            httpx.Response(200, json={"reviews": [{"rating": 2, "snippet": "chặt chém"}]}),
        ]
    )
    route = respx.get(SEARCH_URL).mock(side_effect=lambda req: next(trang))
    kq = await _goi("get_place_reviews", {"place_id": "p1", "pages": 3})

    assert kq["returned"] == 2
    assert kq["topics"][0]["keyword"] == "đỗ xe"
    assert route.call_count == 2  # trang 2 hết token thì dừng
    assert route.calls.last.request.url.params["next_page_token"] == "t2"


@respx.mock
async def test_het_key_tra_ma_loi_rieng():
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(429, json={"error": "Your account has run out of searches."})
    )
    kq = await _goi("search_places", {"query": "cafe Đà Lạt"})

    assert kq["status"] == "error"
    assert kq["error_code"] == -4


@respx.mock
async def test_khong_co_ket_qua_van_success():
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"error": "Google hasn't returned any results for this query."})
    )
    kq = await _goi("search_places", {"query": "xyz không tồn tại"})

    assert kq["status"] == "success"
    assert kq["places"] == []
    assert "returned any results" in kq["note"]
