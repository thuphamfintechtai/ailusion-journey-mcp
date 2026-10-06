"""Test tra mã sân bay: bảng nội bộ VN và nhánh SerpApi cho nước ngoài."""

import httpx
import pytest
import respx
from fastmcp import Client

from src.services.airport_lookup import doi_ra_ma_iata, tra_san_bay_vn
from src.services.serpapi_client import serpapi_client


@pytest.mark.parametrize(
    "vao,ma",
    [
        ("SGN", "SGN"),
        ("dad", "DAD"),
        ("Sài Gòn", "SGN"),
        ("tp hcm", "SGN"),
        ("HCM", "SGN"),
        ("Hue", "HUI"),
        ("Đà Lạt", "DLI"),
        ("da lat", "DLI"),
        ("Nha Trang", "CXR"),
        ("Hội An", "DAD"),
        ("Hạ Long", "VDO"),
        ("Sa Pa", "HAN"),
        ("Phong Nha", "VDH"),
        ("Quy Nhơn", "UIH"),
        ("sân bay Cát Bi", "HPH"),
        ("Đà Nẵng", "DAD"),  # city DAD thắng province của VCL
        ("BKK", "BKK"),  # mã nước ngoài giữ nguyên
        ("SGN,VCA", "SGN,VCA"),
    ],
)
def test_doi_ra_ma_iata(vao, ma):
    assert doi_ra_ma_iata(vao) == ma


@pytest.mark.parametrize("vao", ["Bangkok", "Tokyo", "", "   "])
def test_khong_doi_duoc(vao):
    assert doi_ra_ma_iata(vao) is None


def test_tinh_sau_sap_nhap_tra_ve_nhieu_san_bay():
    ma = {sb.code for sb, _, _ in tra_san_bay_vn("Gia Lai")}
    assert {"PXU", "UIH"} <= ma


async def _goi(args: dict) -> dict:
    from src.fastmcp_server import mcp

    async with Client(mcp) as c:
        r = await c.call_tool("find_airports", args, raise_on_error=False)
    return r.structured_content


async def test_find_airports_noi_dia_khong_goi_serpapi():
    with respx.mock(assert_all_called=False) as m:
        route = m.get(f"{serpapi_client.base_url}/search.json")
        kq = await _goi({"query": "Hội An"})
    assert kq["source"] == "vn_table"
    assert kq["airports"][0]["code"] == "DAD"
    assert "không có sân bay" in kq["airports"][0]["match"]
    assert route.call_count == 0


@respx.mock
async def test_find_airports_nuoc_ngoai(monkeypatch):
    monkeypatch.setattr(serpapi_client, "api_keys", ["k1"])
    monkeypatch.setattr(serpapi_client, "_nghi_den", {})
    route = respx.get(f"{serpapi_client.base_url}/search.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "suggestions": [
                    {
                        "name": "Bangkok, Thái Lan",
                        "type": "city",
                        "airports": [
                            {"name": "Suvarnabhumi", "id": "BKK", "city": "Bangkok"},
                            {"name": "Don Mueang", "id": "DMK", "city": "Bangkok"},
                        ],
                    }
                ]
            },
        )
    )
    kq = await _goi({"query": "Bangkok"})

    assert [a["code"] for a in kq["airports"]] == ["BKK", "DMK"]
    assert route.calls.last.request.url.params["engine"] == "google_flights_autocomplete"
