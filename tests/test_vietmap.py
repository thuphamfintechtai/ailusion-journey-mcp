"""Test Vietmap client (fallback key, cache, chọn điểm) và 5 tool bản đồ. Không gọi Vietmap thật."""

import httpx
import pytest
import respx
from fastmcp import Client

from src.services.vietmap_client import Diem, VietmapClient, VietmapError, vietmap_client

BASE = vietmap_client.base_url


def _client(keys, **kw) -> VietmapClient:
    return VietmapClient(api_keys=keys, base_url=BASE, timeout=5, **kw)


@pytest.fixture(autouse=True)
def _key_gia(monkeypatch):
    monkeypatch.setattr(vietmap_client, "api_keys", ["k1", "k2"])
    monkeypatch.setattr(vietmap_client, "_idx", 0)
    monkeypatch.setattr(vietmap_client, "_nghi_den", {})
    vietmap_client._cache.clear()


# --- client ------------------------------------------------------------------


@respx.mock
async def test_401_doi_key_va_cho_nghi():
    def tra_loi(req):
        if req.url.params["apikey"] == "k1":
            return httpx.Response(401)
        return httpx.Response(200, json=[{"ref_id": "auto:x"}])

    respx.get(f"{BASE}/autocomplete/v4").mock(side_effect=tra_loi)
    c = _client(["k1", "k2"], cache_ttl=0)
    assert await c.autocomplete("abc") == [{"ref_id": "auto:x"}]
    assert c._idx == 1 and 0 in c._nghi_den


@respx.mock
async def test_over_daily_limit_trong_body_cung_doi_key():
    def tra_loi(req):
        if req.url.params["apikey"] == "k1":
            return httpx.Response(200, json={"code": "OVER_DAILY_LIMIT", "messages": None})
        return httpx.Response(200, json={"code": "OK", "paths": []})

    respx.get(f"{BASE}/route/v4").mock(side_effect=tra_loi)
    c = _client(["k1", "k2"], cache_ttl=0)
    assert (await c.route([Diem(1, 2, "a"), Diem(3, 4, "b")], "car"))["code"] == "OK"


@respx.mock
async def test_loi_tham_so_khong_doi_key():
    route = respx.get(f"{BASE}/route/v4").mock(
        return_value=httpx.Response(200, json={"code": "INVALID_REQUEST", "messages": "bad point"})
    )
    with pytest.raises(VietmapError) as e:
        await _client(["k1", "k2"], cache_ttl=0).route([Diem(1, 2, "a"), Diem(3, 4, "b")], "car")
    assert e.value.code == "INVALID_REQUEST" and route.call_count == 1


@respx.mock
async def test_moi_key_hong_thi_het_key():
    respx.get(f"{BASE}/autocomplete/v4").mock(return_value=httpx.Response(401))
    c = _client(["k1", "k2"], cache_ttl=0)
    with pytest.raises(VietmapError) as e:
        await c.autocomplete("abc")
    assert e.value.het_key is True


@respx.mock
async def test_cache_khong_goi_lai():
    route = respx.get(f"{BASE}/place/v4").mock(return_value=httpx.Response(200, json={"lat": 1, "lng": 2}))
    c = _client(["k1"], cache_ttl=60)
    await c.place("auto:a")
    await c.place("auto:a")
    assert route.call_count == 1
    assert "apikey" not in str(next(iter(c._cache)))  # key không nằm trong khoá cache


@respx.mock
async def test_resolve_toa_do_khong_goi_api():
    route = respx.get(url__startswith=BASE)
    d = await vietmap_client.resolve(" 10.77, 106.69 ")
    assert (d.lat, d.lng) == (10.77, 106.69) and route.call_count == 0


@respx.mock
async def test_resolve_ten_uu_tien_trung_ten():
    respx.get(f"{BASE}/autocomplete/v4").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"name": "Sân Bay Tân Sơn Nhất Tphcm", "ref_id": "auto:1"},
                {"name": "Sân Bay Tân Sơn Nhất", "ref_id": "auto:2"},
            ],
        )
    )
    place = respx.get(f"{BASE}/place/v4").mock(
        return_value=httpx.Response(200, json={"lat": 10.8, "lng": 106.6, "display": "Sân Bay Tân Sơn Nhất"})
    )
    d = await vietmap_client.resolve("sân bay tân sơn nhất")
    assert d.ref_id == "auto:2"
    assert place.calls.last.request.url.params["refid"] == "auto:2"


@respx.mock
async def test_resolve_khong_thay():
    respx.get(f"{BASE}/autocomplete/v4").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{BASE}/search/v4").mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(VietmapError) as e:
        await vietmap_client.resolve("xyz")
    assert e.value.code == "NOT_FOUND"


async def _goi(ten, args):
    from src.fastmcp_server import mcp

    async with Client(mcp) as c:
        r = await c.call_tool(ten, args, raise_on_error=False)
    return r.structured_content


@respx.mock
async def test_find_nearby_quanh_toa_do():
    auto = respx.get(f"{BASE}/autocomplete/v4").mock(
        return_value=httpx.Response(
            200, json=[{"name": "Quán Chay A", "address": "Phường Hội An", "distance": 0.17, "categories": ["1005"], "ref_id": "auto:a"}]
        )
    )
    respx.get(f"{BASE}/place/v4").mock(return_value=httpx.Response(200, json={"lat": 15.88, "lng": 108.33}))
    kq = await _goi("find_nearby", {"query": "quán chay", "lat": 15.87, "lng": 108.32, "radius_m": 1000})

    assert kq["places"][0]["name"] == "Quán Chay A" and kq["places"][0]["lat"] == 15.88
    p = auto.calls.last.request.url.params
    assert p["circle_radius"] == "1000" and p["layers"] == "POI" and p["circle_center"] == "15.87,108.32"


async def test_find_nearby_thieu_tam():
    kq = await _goi("find_nearby", {"query": "cà phê"})
    assert kq["status"] == "error"


@respx.mock
async def test_geocode_vn_tra_ca_dia_chi_cu_va_moi():
    respx.get(f"{BASE}/search/v4").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "name": "",
                    "display": "12 Lý Tự Trọng, Phường Sài Gòn, Thành phố Hồ Chí Minh",
                    "boundaries": [{"type": 2, "full_name": "Phường Sài Gòn"}, {"type": 0, "full_name": "Thành phố Hồ Chí Minh"}],
                    "data_old": {
                        "display": "12 Lý Tự Trọng, Phường Bến Nghé, Quận 1, Thành phố Hồ Chí Minh",
                        "boundaries": [{"type": 2, "full_name": "Phường Bến Nghé"}, {"type": 1, "full_name": "Quận 1"}],
                    },
                    "ref_id": "geocode:a",
                }
            ],
        )
    )
    respx.get(f"{BASE}/place/v4").mock(return_value=httpx.Response(200, json={"lat": 10.77, "lng": 106.7}))
    kq = await _goi("geocode_vn", {"address": "12 Lý Tự Trọng, P. Bến Nghé, Q.1"})

    r = kq["results"][0]
    assert r["admin_new"]["ward"] == "Phường Sài Gòn"
    assert r["admin_old"]["district"] == "Quận 1"
    assert r["lat"] == 10.77


@respx.mock
async def test_get_route_xe_may_co_phi():
    route = respx.get(f"{BASE}/route/v4").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": "OK",
                "paths": [
                    {
                        "distance": 97960,
                        "time": 5112000,
                        "toll_cost": 44000,
                        "tolls": [{"name": "Trạm BOT", "price": 44000}],
                        "instructions": [{"text": "Rẽ phải", "distance": 120, "time": 30000}],
                    }
                ],
            },
        )
    )
    kq = await _goi(
        "get_route", {"points": ["10.77,106.69", "10.34,107.08"], "vehicle": "motorcycle", "include_steps": True}
    )

    assert kq["distance_km"] == 97.96 and kq["duration_min"] == 85.2 and kq["toll_cost_vnd"] == 44000
    assert kq["steps"][0]["text"] == "Rẽ phải"
    assert "two-wheeler" in kq["google_maps_url"]
    p = route.calls.last.request.url.params
    assert p["vehicle"] == "motorcycle" and p.get_list("point") == ["10.77,106.69", "10.34,107.08"]


async def test_get_route_gio_khong_co_mui_gio():
    kq = await _goi("get_route", {"points": ["1,2", "3,4"], "depart_at": "2026-11-20T07:30:00"})
    assert kq["status"] == "error"


@respx.mock
async def test_distance_matrix_nguon_dich():
    route = respx.get(f"{BASE}/matrix/v4").mock(
        return_value=httpx.Response(200, json={"code": "OK", "durations": [[300, 600]], "distances": [[2400, 5000]]})
    )
    kq = await _goi("distance_matrix", {"origins": ["1,2"], "destinations": ["3,4", "5,6"]})

    assert kq["pairs"] == [
        {"from": 0, "to": 1, "duration_min": 5.0, "distance_km": 2.4},
        {"from": 0, "to": 2, "duration_min": 10.0, "distance_km": 5.0},
    ]
    p = route.calls.last.request.url.params
    assert p["sources"] == "0" and p["destinations"] == "1;2"


@respx.mock
async def test_het_key_tra_ma_loi_rieng():
    respx.get(f"{BASE}/matrix/v4").mock(return_value=httpx.Response(429))
    kq = await _goi("distance_matrix", {"origins": ["1,2", "3,4"]})
    assert kq["error_code"] == -4


@respx.mock
async def test_get_route_kem_hinh_duong_di():
    respx.get(f"{BASE}/route/v4").mock(
        return_value=httpx.Response(
            200,
            json={"code": "OK", "paths": [{"distance": 2400, "time": 360000, "points": "_p~iF~ps|U_ulLnnqC"}]},
        )
    )
    co = await _goi("get_route", {"points": ["16.06,108.22", "16.05,108.24"], "include_path": True})
    assert co["path"] == "_p~iF~ps|U_ulLnnqC" and co["path_precision"] == 5

    khong = await _goi("get_route", {"points": ["16.06,108.22", "16.05,108.24"]})
    assert "path" not in khong and "path_precision" not in khong
