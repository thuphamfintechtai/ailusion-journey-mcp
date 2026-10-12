"""Test 10 tool lập kế hoạch / tiện ích: tiền tệ, chi phí, chấm điểm, xuất lịch, che PII,
lộ trình có khung giờ, độ dốc, tin cậy nguồn, thiên tai. Không gọi dịch vụ thật."""

import re
from datetime import timedelta

import httpx
import pytest
import respx
from fastmcp import Client

from src.services.fx_client import fx_client, phan_tich_vcb
from src.services.open_data_client import open_data_client
from src.services.vietmap_client import vietmap_client
from src.tools._weather_common import hom_nay_vn
from src.tools.elevation_profile import lay_mau, phan_tich
from src.utils.money import parse_amounts
from src.utils.pii import che_pii

VCB_XML = """<!--For reference only. Only one request every 5 minutes!-->
<ExrateList>
  <DateTime>10/6/2026 9:30:16 PM</DateTime>
  <Exrate CurrencyCode="USD" CurrencyName="US DOLLAR" Buy="25,770.00" Transfer="25,800.00" Sell="26,180.00" />
  <Exrate CurrencyCode="DKK" CurrencyName="DANISH KRONE" Buy="-" Transfer="3,838.00" Sell="3,984.83" />
</ExrateList>"""
VCB_URL = "https://portal.vietcombank.com.vn/Usercontrols/TVPortal.TyGia/pXML.aspx"
VB = vietmap_client.base_url


@pytest.fixture(autouse=True)
def _sach(monkeypatch):
    fx_client.cache.clear()
    open_data_client.cache_lau.clear()
    open_data_client.cache_ngan.clear()
    monkeypatch.setattr(vietmap_client, "api_keys", ["v1"])
    monkeypatch.setattr(vietmap_client, "_nghi_den", {})
    vietmap_client._cache.clear()


async def _goi(ten, args):
    from src.fastmcp_server import mcp

    async with Client(mcp) as c:
        r = await c.call_tool(ten, args, raise_on_error=False)
    return r.structured_content


# --- đọc tiền / tỷ giá -------------------------------------------------------


@pytest.mark.parametrize(
    "cau,mong_doi",
    [
        ("5 triệu", (5e6, 5e6, "VND", None)),
        ("500k/người", (5e5, 5e5, "VND", "person")),
        ("2tr5", (2.5e6, 2.5e6, "VND", None)),
        ("1tr25 một đêm", (1.25e6, 1.25e6, "VND", "night")),
        ("3 củ", (3e6, 3e6, "VND", None)),
        ("1.500.000đ", (1.5e6, 1.5e6, "VND", None)),
        ("2,5 triệu", (2.5e6, 2.5e6, "VND", None)),
        ("2-3 triệu/người", (2e6, 3e6, "VND", "person")),
        ("$50", (50, 50, "USD", None)),
        ("3000 yên", (3000, 3000, "JPY", None)),
    ],
)
def test_parse_amounts(cau, mong_doi):
    a = parse_amounts(cau)[0]
    assert (a.value_min, a.value_max, a.currency, a.per) == mong_doi


@pytest.mark.parametrize("cau", ["cách 500m", "2 lít xăng", "đi 3 người 2 đêm", "15 năm"])
def test_parse_amounts_khong_bat_nham(cau):
    assert parse_amounts(cau) == []


def test_phan_tich_vcb():
    d = phan_tich_vcb(VCB_XML)
    assert d["rates"]["USD"]["transfer"] == 25800 and d["rates"]["DKK"]["buy"] is None


@respx.mock
async def test_convert_currency_va_cache_5_phut():
    route = respx.get(VCB_URL).mock(return_value=httpx.Response(200, text=VCB_XML))
    kq = await _goi("convert_currency", {"amount": 100, "from_currency": "usd", "rate_type": "sell"})
    assert kq["result"] == 2618000 and kq["source"] == "vietcombank"
    await _goi("convert_currency", {"amount": 1, "from_currency": "USD"})
    assert route.call_count == 1
    assert fx_client.cache.ttl >= 300


@respx.mock
async def test_convert_currency_du_phong_khi_vcb_khong_niem_yet():
    respx.get(VCB_URL).mock(return_value=httpx.Response(200, text=VCB_XML))
    respx.get(url__regex=r".*currency-api.*vnd\.json").mock(
        return_value=httpx.Response(200, json={"date": "2026-10-06", "vnd": {"idr": 0.5}})
    )
    kq = await _goi("convert_currency", {"amount": 1000, "from_currency": "IDR"})
    assert kq["result"] == 2000 and "fawazahmed0" in kq["source"]


@respx.mock
async def test_parse_money_quy_doi():
    respx.get(VCB_URL).mock(return_value=httpx.Response(200, text=VCB_XML))
    kq = await _goi("parse_money", {"text": "vé cáp 50 đô/người, phòng 800k", "convert_to_vnd": True})
    assert kq["amounts"][0]["vnd_min"] == 1290000 and kq["amounts"][1]["value_min"] == 800000


# --- chấm điểm -----------------------------------------------------------


async def test_score_options_nhieu_bo_trong_so():
    kq = await _goi(
        "score_options",
        {
            "options": [
                {"name": "A", "metrics": {"cost": 6.5, "rating": 4.5}},
                {"name": "B", "metrics": {"cost": 5.0, "rating": 4.0}},
                {"name": "C", "metrics": {"cost": 8.0, "rating": 4.9}},
            ],
            "criteria": [{"name": "cost", "direction": "min"}, {"name": "rating", "direction": "max"}],
            "weight_sets": {"tiết kiệm": {"cost": 5, "rating": 1}, "chất lượng": {"cost": 1, "rating": 5}},
        },
    )
    assert kq["winner_by_weight_set"] == {"tiết kiệm": "B", "chất lượng": "C"}
    assert "robust_winner" not in kq and kq["note"]
    assert kq["best_per_criterion"] == {"cost": ["B"], "rating": ["C"]}


async def test_score_options_thieu_gia_tri():
    kq = await _goi(
        "score_options",
        {"options": [{"name": "A", "metrics": {}}, {"name": "B", "metrics": {"cost": 1}}], "criteria": [{"name": "cost", "direction": "min"}]},
    )
    assert kq["status"] == "error" and "A: cost" in kq["error"]


# --- che PII / xuất lịch -----------------------------------------------------


def test_che_pii():
    van_ban = (
        "sđt 0912 345 678, (028) 3822 1234, mail lan@gmail.com, CCCD 079203001234, CMND 271234567, "
        "STK 0071000123456, thẻ 4111 1111 1111 1111, xe 51F-123.45, hộ chiếu C1234567, giá 1.500.000đ, mã 123456789"
    )
    ra, ds = che_pii(van_ban)
    assert sorted(m.kind for m in ds) == sorted(
        ["phone", "phone", "email", "cccd", "id_card", "bank_account", "card", "license_plate", "passport"]
    )
    assert "1.500.000đ" in ra and "123456789" in ra  # số tiền, mã đơn không bị che
    assert "0912" not in ra and "079203001234" not in ra


async def test_mask_pii_khong_tra_gia_tri_goc():
    kq = await _goi("mask_pii", {"text": "gọi 0912345678 nhé", "style": "partial"})
    assert kq["masked_text"] == "gọi *******678 nhé" and kq["found"] == {"phone": 1}
    assert "0912345678" not in str(kq)


async def test_export_itinerary_ics_hop_le():
    kq = await _goi(
        "export_itinerary",
        {
            "title": "Đà Lạt 2N1Đ",
            "days": [
                {
                    "date": "2026-11-20",
                    "activities": [
                        {"time": "10:00", "title": "Chợ Đà Lạt, ăn bánh căn", "location": "Chợ Đà Lạt"},
                        {"time": "08:00", "title": "Hồ Xuân Hương", "lat": 11.94, "lng": 108.44, "note": "x" * 120},
                    ],
                }
            ],
        },
    )
    ics = kq["ics_content"]
    assert kq["ics_filename"] == "da-lat-2n1d.ics" and kq["events"] == 2
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert all(len(d.encode()) <= 75 for d in ics.split("\r\n"))  # gấp dòng đúng RFC 5545
    # Hoạt động được sắp theo giờ; 08:00 kết thúc lúc 10:00 (hoạt động kế tiếp)
    assert "DTSTART;TZID=Asia/Ho_Chi_Minh:20261120T080000\r\nDTEND;TZID=Asia/Ho_Chi_Minh:20261120T100000" in ics
    assert "SUMMARY:Chợ Đà Lạt\\, ăn bánh căn" in ics
    assert kq["days"][0]["route_url"].startswith("https://www.google.com/maps/dir/")


async def test_export_itinerary_gio_sai():
    kq = await _goi("export_itinerary", {"title": "x", "days": [{"date": "2026-11-20", "activities": [{"time": "8h", "title": "a"}]}]})
    assert kq["status"] == "error"


# --- lộ trình có khung giờ ---------------------------------------------------


@respx.mock
async def test_plan_day_route_khung_gio_va_diem_khong_kip():
    route = respx.post(f"{VB}/vrp").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 0,
                "summary": {"duration": 600, "distance": 5000, "waiting_time": 1200},
                "unassigned": [{"id": 2}],
                "routes": [
                    {
                        "steps": [
                            {"type": "start", "arrival": 28800, "duration": 0, "distance": 0},
                            {"type": "job", "id": 1, "arrival": 29100, "duration": 300, "distance": 2500, "service": 3600, "waiting_time": 1200},
                            {"type": "break", "arrival": 40000, "duration": 300, "distance": 2500, "service": 3600},
                            {"type": "end", "arrival": 44200, "duration": 600, "distance": 5000},
                        ]
                    }
                ],
            },
        )
    )
    kq = await _goi(
        "plan_day_route",
        {
            "start": "10.77,106.69",
            "vehicle": "motorcycle",
            "stops": [
                {"place": "10.78,106.70", "visit_minutes": 60, "open": "08:30", "close": "11:00"},
                {"place": "10.80,106.71", "visit_minutes": 90, "open": "08:00", "close": "09:00"},
            ],
        },
    )
    body = route.calls.last.request.read()
    import json as _json

    gui = _json.loads(body)
    # Khung giờ bắt đầu tham quan = [mở, đóng - thời gian ở lại]
    assert gui["jobs"][0]["time_windows"] == [[30600, 39600 - 3600]]
    assert gui["vehicles"][0]["breaks"][0]["service"] == 3600 and gui["vehicles"][0]["profile"] == "motorcycle"

    lich = kq["schedule"]
    tham_quan = lich[1]
    assert tham_quan["arrive"] == "08:05" and tham_quan["start"] == "08:25" and tham_quan["leave"] == "09:25"
    assert lich[2]["type"] == "lunch" and lich[-1]["type"] == "end"
    assert kq["unassigned"][0]["reason"] == "thời gian tham quan dài hơn khung giờ mở cửa"


async def test_plan_day_route_gio_sai():
    kq = await _goi("plan_day_route", {"start": "1,2", "stops": [{"place": "3,4", "open": "8h"}]})
    assert kq["status"] == "error"


# --- độ dốc ------------------------------------------------------------------


def test_lay_mau_va_phan_tich_doc():
    mau = lay_mau([(11.0, 108.0), (11.0, 108.01)], 11)  # ~1,09 km theo kinh tuyến
    assert len(mau) == 11 and mau[0][0] == 0 and abs(mau[-1][0] - 1.09) < 0.01
    km = [i * 0.1 for i in range(11)]
    cao = [1000 + (i * 12 if i <= 5 else 60) for i in range(11)]  # 12 m / 100 m = 12% ở nửa đầu
    kq = phan_tich(km, cao)
    assert kq["total_ascent_m"] == 60 and kq["max_grade_pct"] == 12.0
    assert kq["steep_segments"][0]["level"] == "rất dốc" and kq["steep_segments"][0]["direction"] == "lên"


@respx.mock
async def test_elevation_profile():
    respx.get(f"{VB}/route/v4").mock(
        return_value=httpx.Response(
            200,
            json={"code": "OK", "paths": [{"distance": 1090, "points": {"coordinates": [[108.0, 11.0], [108.01, 11.0]]}}]},
        )
    )
    topo = respx.get(url__regex=r"https://api\.opentopodata\.org/v1/srtm30m.*").mock(
        side_effect=lambda req: httpx.Response(
            200,
            json={"results": [{"elevation": 1000 + i * 1.2} for i in range(len(req.url.params["locations"].split("|")))]},
        )
    )
    kq = await _goi("elevation_profile", {"points": ["11.0,108.0", "11.0,108.01"], "profile_points": 5})
    assert len(topo.calls.last.request.url.params["locations"].split("|")) == 100
    assert kq["min_elevation_m"] == 1000 and kq["max_grade_pct"] > 10 and kq["advice"]


# --- tin cậy nguồn / thiên tai -----------------------------------------------


@respx.mock
async def test_source_trust_wayback_loi_khong_tru_diem():
    respx.get(url__regex=r"https://tranco-list\.eu/api/ranks/domain/vnexpress\.net").mock(
        return_value=httpx.Response(200, json={"ranks": [{"rank": 1197}]})
    )
    respx.get(url__regex=r"https://tranco-list\.eu/api/ranks/domain/moi\.vn").mock(return_value=httpx.Response(404))

    def wb(req):
        if req.url.params["url"] == "vnexpress.net":
            return httpx.Response(200, json=[["timestamp"], ["20010303024417"]])
        return httpx.Response(503)

    respx.get("https://web.archive.org/cdx/search/cdx").mock(side_effect=wb)
    kq = await _goi("source_trust", {"urls": ["https://vnexpress.net/abc", "moi.vn"]})
    vne, moi = kq["results"]
    assert vne["domain"] == "vnexpress.net" and vne["first_seen"] == "2001-03-03" and vne["level"] == "cao"
    assert moi["errors"] and "không tra được tuổi domain" in moi["reasons"]
    assert "Internet Archive chưa từng lưu domain này" not in moi["reasons"]


@respx.mock
async def test_disaster_alerts_loc_gan_viet_nam():
    hom_nay = hom_nay_vn()

    def su_kien(loai, ten, nuoc, lng, lat, muc, hien_tai="true"):
        return {
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "eventtype": loai, "eventid": hash(ten) % 10000, "name": ten, "country": nuoc, "alertlevel": muc,
                "iscurrent": hien_tai, "fromdate": f"{hom_nay}T00:00:00", "todate": f"{hom_nay}T00:00:00",
                "url": {"report": "https://gdacs.org/r"},
            },
        }

    def tra_loi(req):
        if req.url.params.get("eventlist") == "TC":
            return httpx.Response(
                200,
                json={"features": [
                    su_kien("TC", "Bão ngoài Biển Đông", "", 112.0, 15.0, "Orange"),
                    su_kien("TC", "Bão Đại Tây Dương", "USA", -70.0, 25.0, "Red"),
                ]},
            )
        return httpx.Response(204)

    respx.get(url__regex=r".*gdacs.*geteventlist.*").mock(side_effect=tra_loi)
    kq = await _goi("disaster_alerts", {"days": 7, "lat": 8.68, "lng": 106.6})
    assert [e["name"] for e in kq["events"]] == ["Bão ngoài Biển Đông"]
    e = kq["events"][0]
    assert e["type"] == "bão / áp thấp nhiệt đới" and e["alert_level"] == "cam" and 850 < e["distance_km"] < 1000


@respx.mock
async def test_disaster_alerts_khong_co_su_kien():
    respx.get(url__regex=r".*gdacs.*geteventlist.*").mock(return_value=httpx.Response(204))
    kq = await _goi("disaster_alerts", {"days": 3})
    assert kq["events"] == [] and "google_news" in kq["note"]
