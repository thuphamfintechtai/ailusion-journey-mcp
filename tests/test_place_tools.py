"""Test nhóm tool khai thác gmaps_place: chỗ ở theo ngân sách, nhu cầu, so sánh, rating, điểm thay thế,
lọc theo giá, và plan_day_route tự lấy giờ mở cửa đã lưu.

Không gọi DB thật: `fetch` trả dữ liệu giả; lọc nhu cầu bằng SQL được giả lập bằng danh_gia() (cùng
luật), còn bản thân câu SQL được kiểm tra riêng ở test_dieu_kien_sql_*.
"""

import asyncio
import json
import re
import sys
from datetime import date, timedelta

import httpx
import pytest
import respx
from fastmcp import Client

from src.config.settings import settings
from src.services.serpapi_client import serpapi_client
from src.services.travel_db import travel_db
from src.services.vietmap_client import vietmap_client
from src.tools._db_common import (
    doc_thoi_diem,
    gia_phong,
    khoang_gia_an,
    loai_cho_o,
    nhom_dia_diem,
    rating_hieu_chinh,
    ty_le_sao,
)
from src.tools._nhu_cau import NHU_CAU, NhuCau, danh_gia, dieu_kien_sql, luu_y

KXL = "Phù hợp cho người khuyết tật"
GIO = {t: ["07:00–17:00"] for t in ("Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật")}
GIO_DEM = {t: ["18:00–23:30"] for t in GIO}
DONG = {"Saturday": {str(h): v for h, v in zip(range(7, 17), [20, 40, 90, 100, 80, 60, 50, 40, 30, 20])}}
VANG = {"Saturday": {str(h): 10 for h in range(7, 17)}}
GOC = {"seed_query": None, "best_match": False, "address": "Huế", "price_range": None, "summary": None,
       "website": None, "phone": None, "wheelchair": False, "fetched_at": "2026-10-06T10:00:00+00:00", "destination": "hue"}


def _p(cid, title, category, lat, lng, rating=4.5, n=500, **kw):
    return {**GOC, "cid": cid, "place_id": f"ChIJ{cid}", "title": title, "category": category, "lat": lat, "lng": lng,
            "rating": rating, "review_count": n, **kw}


DS = [
    _p("c1", "Chùa Thiên Mụ", "Chùa phật giáo", 16.4532, 107.5448, 4.6, 20000),
    _p("c2", "Chùa Từ Hiếu", "Chùa phật giáo", 16.4389, 107.5720, 4.8, 2000),
    _p("c3", "Chùa Diệu Đế", "Chùa phật giáo", 16.4700, 107.5900, 4.5, 800),
    _p("q1", "Bún bò O Cương", "Nhà hàng Việt Nam", 16.4600, 107.5900, 4.7, 900, price_range="1-100.000 ₫"),
    _p("q2", "Nhà hàng Chay Liên Hoa", "Nhà hàng chay", 16.4610, 107.5910, 4.6, 700, price_range="100-200 N ₫"),
    _p("k1", "Khách sạn Sông Hương", "Khách sạn", 16.4650, 107.5950, 4.4, 1200, summary="650.000\xa0₫"),
    _p("k2", "Vedana Lagoon Resort", "Khách sạn nghỉ dưỡng", 16.3500, 107.8000, 4.8, 3000, summary="3.500.000\xa0₫"),
    _p("k3", "Quiri Hotel", "Khách sạn", 16.4660, 107.5960, 4.9, 40, summary="520\xa0₫"),
    _p("k4", "Mộc Homestay", "Lưu trú nhà dân", 16.4670, 107.5970, 4.9, 300, summary="350.000\xa0₫"),
    _p("o1", "Ốc Phân Cực", "Nhà hàng hải sản", 16.4620, 107.5920, 4.2, 2400,
       reviews_per_rating={"1": 418, "2": 37, "3": 44, "4": 54, "5": 1863}),
    # Hội An cào chung với Đà Nẵng (destination 'danang'), nhận ra qua địa chỉ.
    _p("h1", "Chùa Cầu", "Điểm mốc lịch sử", 15.8770, 108.3260, 4.3, 29000, destination="danang",
       address="Nguyễn Thị Minh Khai, Minh An, Hội An, Quảng Nam"),
    _p("d1", "Cầu Rồng", "Cầu", 16.0610, 108.2270, 4.6, 30000, destination="danang", address="Hải Châu, Đà Nẵng"),
]
CHI_TIET = {
    "c1": {"open_hours": GIO, "popular_times": DONG, "attributes": {KXL: ["Lối vào cho xe lăn"], "Thanh toán": ["Phí vào cổng"]},
           "reviews_per_rating": {"1": 100, "2": 50, "3": 500, "4": 3000, "5": 16350}},
    "c2": {"open_hours": GIO, "popular_times": VANG, "attributes": {KXL: ["Lối vào cho xe lăn"]}},
    "c3": {"open_hours": GIO, "popular_times": VANG, "attributes_not_available": {KXL: ["Lối vào cho xe lăn"]}},
    "q1": {"open_hours": GIO, "attributes": {"Trẻ em": ["Phù hợp cho trẻ em"], "Thanh toán": ["Thẻ tín dụng", "Chỉ nhận tiền mặt"],
                                             "Bãi đỗ xe": ["Bãi đỗ xe miễn phí"]},
           "attributes_not_available": {KXL: ["Lối vào cho xe lăn"]}},
    "q2": {"open_hours": GIO_DEM, "attributes": {"Dịch vụ": ["Các món chay"], "Bầu không khí": ["Lãng mạn"],
                                                 "Lên kế hoạch": ["Bắt buộc đặt chỗ trước"]}},
    "k1": {"attributes": {"Tiện nghi": ["Wi-Fi"]}},
    "k4": {"attributes_not_available": {"Tiện nghi": ["Wi-Fi"]}},
    "o1": {"reviews_per_rating": {"1": 418, "2": 37, "3": 44, "4": 54, "5": 1863},
           "reviews": [{"rating": 1, "date": "2026-08-04", "text": "Chặt chém"}, {"rating": 5, "date": "2026-09-01", "text": "Ngon"}]},
}


def _day_du(cid: str) -> dict:
    p = next(x for x in DS if x["cid"] == cid)
    return {**p, **CHI_TIET.get(cid, {}), "_cid": cid, "_pid": p["place_id"], "_did": None}


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "pg_host", "db-gia")

    async def fetch(sql: str, *args):
        await asyncio.sleep(0)
        if sql.startswith("SELECT cid AS _cid"):  # details_by_cid
            return [_day_du(c) for c in args[0] if any(x["cid"] == c for x in DS)]
        if "cid = ANY" in sql:  # find_places
            cids, ids = args
            return [_day_du(x["cid"]) for x in DS if x["cid"] in cids or x["cid"] in ids or x["place_id"] in ids]
        if "FROM gmaps_place" in sql:
            return [dict(x) for x in DS]
        return []

    async def cids_matching(cids, dk, ts, diem="0", ts_diem=None):
        # Giả lập SQL bằng danh_gia(): cùng luật yes / no / unknown. Kiểm tra số tham số khớp số $n.
        tat_ca = sorted({int(x) for x in re.findall(r"\$(\d+)", f"{dk} {diem}")})
        assert tat_ca == list(range(2, 2 + len(ts) + len(ts_diem or [])))
        nhu_cau = cids_matching.nhu_cau
        chac_chan = "attributes_not_available" not in dk
        kq = {}
        for c in cids:
            ct = CHI_TIET.get(c, {})
            tt = [danh_gia(n, ct.get("attributes"), ct.get("attributes_not_available")) for n in nhu_cau]
            if (chac_chan and all(v == "yes" for v in tt)) or (not chac_chan and "no" not in tt):
                kq[c] = tt.count("yes")
        return kq

    cids_matching.nhu_cau = []
    goc_dieu_kien = dieu_kien_sql

    def ghi_nhu_cau(nhu_cau, bat_dau, chac_chan=True):
        cids_matching.nhu_cau = nhu_cau
        return goc_dieu_kien(nhu_cau, bat_dau, chac_chan)

    for mod in ("src.tools._cho_o", "src.tools.find_places"):
        monkeypatch.setattr(f"{mod}.dieu_kien_sql", ghi_nhu_cau)
    monkeypatch.setattr(travel_db, "fetch", fetch)
    monkeypatch.setattr(travel_db, "cids_matching", cids_matching)


async def _goi(ten: str, args: dict) -> dict:
    from src.fastmcp_server import mcp

    async with Client(mcp) as c:
        r = await c.call_tool(ten, args, raise_on_error=False)
    return r.structured_content


async def test_find_places_khong_doc_thuoc_tinh_khi_khong_co_needs(db, monkeypatch):
    async def khong_can_thuoc_tinh(*args, **kwargs):
        pytest.fail("Tìm địa điểm thường không được đọc thuộc tính chi tiết")

    monkeypatch.setattr(travel_db, "cids_matching", khong_can_thuoc_tinh)
    monkeypatch.setattr(travel_db, "details_by_cid", khong_can_thuoc_tinh)
    result = await _goi("find_places", {"destination": "Huế", "limit": 1})
    assert result["status"] == "success"
    assert len(result["places"]) == 1
    assert "needs_status" not in result["places"][0]


# --- hàm thuần --------------------------------------------------------------------


def test_literal_nhu_cau_khop_danh_muc():
    assert set(NhuCau.__args__) == set(NHU_CAU)


@pytest.mark.parametrize(
    "nhu_cau,attrs,khong,ra",
    [
        ("wheelchair", {KXL: ["Lối vào cho xe lăn"]}, None, "yes"),
        ("wheelchair", None, {KXL: ["Lối vào cho xe lăn"]}, "no"),
        ("wheelchair", {"Tiện nghi": ["Wi-Fi"]}, None, "unknown"),
        ("card_payment", {"Thanh toán": ["Thẻ tín dụng", "Chỉ nhận tiền mặt"]}, None, "no"),
        ("car_parking", None, {"Bãi đỗ xe": ["Chỗ đỗ xe"]}, "no"),
        ("kids", {"Khách hàng": ["Dành cho gia đình"]}, None, "yes"),
    ],
)
def test_danh_gia(nhu_cau, attrs, khong, ra):
    assert danh_gia(nhu_cau, attrs, khong) == ra


def test_luu_y():
    assert luu_y({"Thanh toán": ["Chỉ nhận tiền mặt", "Phí vào cổng"], "Lên kế hoạch": ["Thường phải đợi"]}) == [
        "Chỉ nhận tiền mặt", "Có thu phí vào cổng", "Thường phải xếp hàng đợi",
    ]


@pytest.mark.parametrize("chac_chan", [True, False])
@pytest.mark.parametrize("nhu_cau", [["wheelchair"], ["card_payment", "kids"], list(NHU_CAU)])
def test_dieu_kien_sql_danh_so_tham_so_lien_tuc(nhu_cau, chac_chan):
    dk, ts = dieu_kien_sql(nhu_cau, 2, chac_chan)
    so = sorted({int(x) for x in re.findall(r"\$(\d+)", dk)})
    assert so == list(range(2, 2 + len(ts)))
    assert all(isinstance(t, dict) and len(t) == 1 for t in ts)


@pytest.mark.parametrize(
    "vao,ra",
    [("1-100.000 ₫", (0, 100000)), ("100-200 N ₫", (100000, 200000)), ("Trên 1 Tr ₫", (1000000, None)),
     ("200-300\xa0N\xa0₫", (200000, 300000)), ("abc", None), (None, None)],
)
def test_khoang_gia_an(vao, ra):
    assert khoang_gia_an(vao) == ra


def test_gia_phong_bo_gia_vo_ly_va_khong_phai_cho_o():
    assert gia_phong({"category": "Khách sạn", "summary": "827.411\xa0₫"}) == 827411
    assert gia_phong({"category": "Khách sạn", "summary": "520\xa0₫"}) is None
    assert gia_phong({"category": "Nhà hàng", "summary": "600.000 ₫"}) is None


@pytest.mark.parametrize(
    "loai,ra",
    [("Khách sạn", "hotel"), ("Khách sạn nghỉ dưỡng", "resort"), ("Căn hộ", "apartment"), ("Biệt thự", "villa"),
     ("Lưu trú nhà dân", "homestay"), ("Nhà trọ", "hostel"), ("Nhà hàng", None)],
)
def test_loai_cho_o(loai, ra):
    assert loai_cho_o(loai) == ra


@pytest.mark.parametrize(
    "loai,ra",
    [("Phố đi bộ", "sightseeing"), ("Quán cà phê sân vườn", "food_drink"), ("Công viên nước", "entertainment"),
     ("Công viên", "sightseeing"), ("Quán bia sân vườn", "nightlife"), ("Nơi thờ cúng", "sightseeing"),
     ("Trung tâm mua sắm", "shopping"), ("Spa mát xa", "relax"), (None, None)],
)
def test_nhom_dia_diem_theo_tien_to(loai, ra):
    assert nhom_dia_diem(loai) == ra


def test_rating_hieu_chinh_va_ty_le_sao():
    def cho(sao, n):
        return {"rating": sao, "review_count": n, "category": "Quán cà phê", "_dest": "dalat"}

    assert rating_hieu_chinh(cho(5.0, 3)) < rating_hieu_chinh(cho(4.7, 3000))
    assert rating_hieu_chinh(cho(0, 0)) is None
    assert ty_le_sao({"1": 1, "5": 3}) == {"1": 0.25, "2": 0.0, "3": 0.0, "4": 0.0, "5": 0.75}
    assert ty_le_sao({"1": 0}) is None


@pytest.mark.parametrize("s", ["2026-10-10 9h", "2026-10-10T09:00", "2026-10-10T02:00:00Z"])
def test_doc_thoi_diem(s):
    assert doc_thoi_diem(s).strftime("%Y-%m-%d %H:%M") == "2026-10-10 09:00"


@pytest.mark.parametrize("s", ["2026-10-10", "", None, "abc"])
def test_doc_thoi_diem_sai(s):
    assert doc_thoi_diem(s) is None


# --- find_stays / search_hotels ------------------------------------------------------


async def test_find_stays_theo_ngan_sach_bo_gia_vo_ly(db):
    kq = await _goi("find_stays", {"destination": "Huế", "max_price_vnd": 700000, "sort_by": "price_low"})
    assert [s["name"] for s in kq["stays"]] == ["Mộc Homestay", "Khách sạn Sông Hương"]
    assert kq["price_stats_vnd"]["median"] == 650000  # Quiri '520 ₫' không tính
    assert "price_note" in kq


async def test_find_stays_loai_va_ngan_sach_qua_thap(db):
    kq = await _goi("find_stays", {"destination": "Huế", "kind": "resort"})
    assert [s["name"] for s in kq["stays"]] == ["Vedana Lagoon Resort"]
    kq = await _goi("find_stays", {"destination": "Huế", "max_price_vnd": 100000})
    assert kq["stays"] == [] and "Ngân sách thấp hơn" in kq["note"]


async def test_find_stays_needs_khong_chac_chan_va_trang_thai(db):
    kq = await _goi("find_stays", {"destination": "Huế", "needs": ["wifi"], "sort_by": "price_low"})
    ten = [s["name"] for s in kq["stays"]]
    assert "Mộc Homestay" not in ten  # Google ghi rõ không có Wi-Fi
    assert kq["stays"][0]["needs_status"] == {"wifi": "yes"} or kq["stays"][0]["needs_status"] == {"wifi": "unknown"}
    kq = await _goi("find_stays", {"destination": "Huế", "needs": ["wifi"], "strict_needs": True})
    assert [s["name"] for s in kq["stays"]] == ["Khách sạn Sông Hương"]


@pytest.mark.parametrize(
    "args",
    [{}, {"lat": 16.0}, {"destination": "Huế", "sort_by": "distance"}, {"destination": "Huế", "min_price_vnd": 9, "max_price_vnd": 1}],
)
async def test_find_stays_tham_so_sai(db, args):
    kq = await _goi("find_stays", args)
    assert kq["status"] == "error" and kq["error_code"] == -2


async def test_search_hotels_fallback_find_stays(db, monkeypatch):
    monkeypatch.setattr(serpapi_client, "api_keys", [])
    d1 = (date.today() + timedelta(days=10)).isoformat()
    d2 = (date.today() + timedelta(days=12)).isoformat()
    kq = await _goi("search_hotels", {"destination": "Huế", "check_in_date": d1, "check_out_date": d2,
                                      "max_price": 700000, "sort_by": "lowest_price"})
    assert kq["data_source"] == "ailusion_db" and kq["hotels"][0]["name"] == "Mộc Homestay"


# --- find_places needs / check_place_needs ---------------------------------------


async def test_find_places_needs(db):
    kq = await _goi("find_places", {"destination": "Huế", "needs": ["vegetarian", "romantic"]})
    assert [p["name"] for p in kq["places"]] == ["Nhà hàng Chay Liên Hoa"]
    assert kq["places"][0]["heads_up"] == ["Bắt buộc đặt chỗ trước"]


async def test_find_places_mat_bang_gia_tinh_tren_moi_quan_khop(db, monkeypatch):
    # Mặt bằng giá tính trên mọi quán khớp, không chỉ chỗ trả về (`limit`) hay chỗ qua lọc giá.
    them = _p("q3", "Nhà hàng Ancient", "Nhà hàng", 16.4630, 107.5930, price_range="200-300 N ₫")
    monkeypatch.setattr(sys.modules[__name__], "DS", [*DS, them])
    kq = await _goi("find_places", {"destination": "Huế", "category": "Nhà hàng", "limit": 1})
    assert len(kq["places"]) == 1
    assert kq["price_stats_vnd"] == {"p25": 50000, "median": 150000, "p75": 250000, "count": 3}
    kq = await _goi("find_places", {"destination": "Huế", "category": "Nhà hàng", "max_price_vnd": 100000})
    assert "Nhà hàng Ancient" not in {p["name"] for p in kq["places"]}
    assert kq["price_stats_vnd"]["median"] == 150000
    # Dưới 3 quán có giá: chưa đủ để nói mặt bằng.
    kq = await _goi("find_places", {"destination": "Huế", "category": "Nhà hàng chay"})
    assert "price_stats_vnd" not in kq


async def test_find_places_needs_khong_chac_chan_loai_no(db):
    kq = await _goi("find_places", {"destination": "Huế", "needs": ["wheelchair"], "category": "Chùa"})
    assert {p["name"] for p in kq["places"]} == {"Chùa Thiên Mụ", "Chùa Từ Hiếu"}
    kq = await _goi("find_places", {"destination": "Huế", "needs": ["wheelchair"], "category": "Chùa", "strict_needs": False})
    assert "Chùa Diệu Đế" not in {p["name"] for p in kq["places"]}


async def test_find_places_needs_khong_co_ket_qua_goi_y(db):
    kq = await _goi("find_places", {"destination": "Huế", "needs": ["pets", "rooftop"]})
    assert kq["places"] == [] and "strict_needs=false" in kq["note"]


async def test_check_place_needs(db):
    kq = await _goi("check_place_needs", {"places": ["Bún bò O Cương", "Chùa Thiên Mụ", "không có"],
                                          "needs": ["wheelchair", "card_payment", "kids"]})
    quan, chua, khong = kq["results"]
    assert quan["needs_status"] == {"wheelchair": "no", "card_payment": "no", "kids": "yes"}
    assert quan["heads_up"] == ["Chỉ nhận tiền mặt"] and quan["all_met"] is False
    assert chua["heads_up"] == ["Có thu phí vào cổng"]
    assert khong["status"] == "not_found"
    assert "Bún bò O Cương" in kq["not_met"]


async def test_check_place_needs_khong_hoi_chi_tra_dieu_biet_chac(db):
    kq = await _goi("check_place_needs", {"places": ["Bún bò O Cương"]})
    tt = kq["results"][0]["needs_status"]
    assert "unknown" not in tt.values() and tt["kids"] == "yes" and tt["free_parking"] == "yes"


# --- compare_places / rating_insights / find_alternatives -----------------------------


async def test_compare_places_va_dua_vao_score_options(db):
    kq = await _goi("compare_places", {"places": ["Chùa Thiên Mụ", "Chùa Từ Hiếu", "Chùa Diệu Đế"],
                                       "at": "2026-10-10 10:00", "origin_lat": 16.46, "origin_lng": 107.59,
                                       "needs": ["wheelchair"]})
    ten_tieu_chi = [c["name"] for c in kq["score_options_input"]["criteria"]]
    assert {"rating_adjusted", "busyness", "distance_km", "needs_met"} <= set(ten_tieu_chi)
    assert "one_star_pct" in kq["criteria_skipped"]  # chỉ Thiên Mụ có phân bố sao
    # Đầu ra phải dùng thẳng được cho score_options.
    xh = await _goi("score_options", kq["score_options_input"])
    assert xh["status"] == "success"


async def test_compare_places_khong_tron_gia_phong_voi_gia_an(db):
    kq = await _goi("compare_places", {"places": ["Khách sạn Sông Hương", "Bún bò O Cương"]})
    assert "price_vnd" in kq["criteria_skipped"]


async def test_compare_places_it_hon_hai_diem(db):
    kq = await _goi("compare_places", {"places": ["Chùa Thiên Mụ", "không có"]})
    assert "Cần ít nhất 2 điểm" in kq["note"]


async def test_rating_insights_phan_cuc(db):
    kq = await _goi("rating_insights", {"place": "Ốc Phân Cực"})
    assert kq["polarized"] is True and kq["low_star_reviews"][0]["text"] == "Chặt chém"
    assert kq["star_distribution_pct"]["1"] == 17.3


async def test_find_alternatives_dong_cua_va_nhu_cau(db):
    # 18:30 chùa đóng; nhà hàng chay mở tới 23:30 -> là thay thế cùng nhóm ăn uống cho quán bún.
    kq = await _goi("find_alternatives", {"place": "Bún bò O Cương", "at": "2026-10-10 18:30"})
    assert kq["target"]["open_status"] == "closed"
    ten = [a["name"] for a in kq["alternatives"]]
    # Chắc chắn mở xếp trước; điểm chưa có giờ mở cửa (Ốc Phân Cực) vẫn giữ nhưng xếp sau.
    assert ten == ["Nhà hàng Chay Liên Hoa", "Ốc Phân Cực"]
    assert kq["alternatives"][1].get("open_status") == "unknown"


async def test_find_alternatives_uu_tien_cung_loai_it_dong(db):
    kq = await _goi("find_alternatives", {"place": "Chùa Thiên Mụ", "at": "2026-10-10 10:00", "radius_km": 10,
                                          "needs": ["wheelchair"]})
    ten = [a["name"] for a in kq["alternatives"]]
    assert ten[0] == "Chùa Từ Hiếu" and "Chùa Diệu Đế" not in ten
    assert kq["alternatives"][0]["less_busy_than_target"] is True
    assert kq["excluded"]["needs"] == 1



# --- đợt edge case 6 ----------------------------------------------------------------


async def test_khong_chac_chan_xep_noi_dap_ung_nhieu_len_truoc(db):
    # Chay Liên Hoa đáp ứng chắc 2/2 nhu cầu nên đứng đầu dù kém phổ biến hơn các nơi chưa rõ (0/2).
    kq = await _goi("find_places", {"destination": "Huế", "needs": ["vegetarian", "romantic"], "strict_needs": False})
    assert kq["places"][0]["name"] == "Nhà hàng Chay Liên Hoa"


async def test_nhieu_diem_den_co_note_o_tool_moi(db, monkeypatch):
    async def hai_noi(text):
        return ["hue", "hanoi"]

    monkeypatch.setattr(travel_db, "find_destinations", hai_noi)
    kq = await _goi("find_stays", {"destination": "Huế - Hà Nội"})
    assert "nhiều điểm đến" in kq["note"]
    kq = await _goi("find_places", {"destination": "Huế", "lat": 16.46, "lng": 107.59, "needs": ["kids"]})
    assert "bỏ qua `destination`" in kq["note"]


async def test_search_hotels_fallback_ngan_sach_qua_thap_van_tra_loi_giai_thich(db, monkeypatch):
    monkeypatch.setattr(serpapi_client, "api_keys", [])
    d1 = (date.today() + timedelta(days=10)).isoformat()
    d2 = (date.today() + timedelta(days=12)).isoformat()
    kq = await _goi("search_hotels", {"destination": "Huế", "check_in_date": d1, "check_out_date": d2, "max_price": 50000})
    assert kq["status"] == "success" and kq["hotels"] == [] and "thấp hơn 75%" in kq["note"]


@pytest.mark.parametrize(
    "loai,ra",
    [("Câu lạc bộ spa và sức khỏe", "relax"), ("Bãi chờ tàu hỏa", None), ("Cầu", "sightseeing"),
     ("Giường & bữa sáng", "stay"), ("Phòng cho thuê", "stay")],
)
def test_nhom_dia_diem_giu_dau(loai, ra):
    assert nhom_dia_diem(loai) == ra


def test_bieu_thuc_so_yes_danh_so_noi_tiep():
    from src.tools._nhu_cau import bieu_thuc_so_yes

    dk, ts = dieu_kien_sql(["kids", "card_payment"], 2, False)
    bt, tb = bieu_thuc_so_yes(["kids", "card_payment"], 2 + len(ts))
    so = sorted({int(x) for x in re.findall(r"\$(\d+)", f"{dk} {bt}")})
    assert so == list(range(2, 2 + len(ts) + len(tb)))



# --- lọc giá, giá đã quy ra VND, rating hiệu chỉnh ----------------------------------


async def test_find_places_loc_gia_giu_noi_chua_co_gia(db):
    kq = await _goi("find_places", {"destination": "Huế", "category": "Nhà hàng", "max_price_vnd": 100000})
    # Bún bò ~50k lọt, nhà hàng chay ~150k bị loại, Ốc Phân Cực chưa có giá vẫn được giữ.
    assert sorted(p["name"] for p in kq["places"]) == ["Bún bò O Cương", "Ốc Phân Cực"]


async def test_find_places_loc_gia_chat_va_gia_toi_thieu(db):
    kq = await _goi(
        "find_places", {"destination": "Huế", "category": "Nhà hàng", "max_price_vnd": 100000, "strict_price": True}
    )
    assert [p["name"] for p in kq["places"]] == ["Bún bò O Cương"]
    kq = await _goi(
        "find_places", {"destination": "Huế", "category": "Nhà hàng", "min_price_vnd": 100000, "strict_price": True}
    )
    assert [p["name"] for p in kq["places"]] == ["Nhà hàng Chay Liên Hoa"]


async def test_find_places_khoang_gia_nguoc_bao_loi(db):
    kq = await _goi("find_places", {"destination": "Huế", "min_price_vnd": 200000, "max_price_vnd": 100000})
    assert kq["status"] == "error"


async def test_find_places_tra_gia_vnd_va_rating_hieu_chinh(db):
    kq = await _goi("find_places", {"destination": "Huế", "query": "Liên Hoa"})
    chay = kq["places"][0]
    assert chay["price"] == "100-200 N ₫"
    assert chay["price_vnd"] == {"kind": "per_person", "min_vnd": 100000, "max_vnd": 200000, "vnd": 150000}
    # Bayes: nằm giữa sao tiên nghiệm và sao thật, nghiêng về sao thật vì 700 review.
    basis = chay["rating_basis"]
    assert basis["method"] == "bayes"
    lo, hi = sorted((basis["prior_mean"], 4.6))
    assert lo <= chay["rating_adjusted"] <= hi
    assert abs(chay["rating_adjusted"] - 4.6) < abs(basis["prior_mean"] - 4.6)


async def test_find_places_needs_loc_gia(db):
    kq = await _goi("find_places", {"destination": "Huế", "needs": ["vegetarian"], "max_price_vnd": 100000})
    assert kq["places"] == []
    kq = await _goi("find_places", {"destination": "Huế", "needs": ["vegetarian"], "max_price_vnd": 200000})
    assert [p["name"] for p in kq["places"]] == ["Nhà hàng Chay Liên Hoa"]


# --- plan_day_route tự lấy giờ mở cửa và toạ độ đã lưu --------------------------------


@pytest.fixture
def vietmap(monkeypatch):
    monkeypatch.setattr(vietmap_client, "api_keys", ["v1"])
    monkeypatch.setattr(vietmap_client, "_nghi_den", {})
    vietmap_client._cache.clear()


def _vrp_tra(*steps, unassigned=()):
    return httpx.Response(
        200,
        json={
            "code": 0,
            "summary": {"duration": 900, "distance": 6000},
            "unassigned": [{"id": i} for i in unassigned],
            "routes": [{"steps": list(steps)}],
        },
    )


@respx.mock
async def test_plan_day_route_lay_gio_mo_cua_da_luu(db, vietmap, monkeypatch):
    # Thứ Bảy 2026-10-10: chùa Diệu Đế đóng cửa, chùa Thiên Mụ mở 07:00–17:00.
    monkeypatch.setitem(CHI_TIET, "c3", {**CHI_TIET["c3"], "open_hours": {**GIO, "Thứ Bảy": ["Đóng cửa"]}})
    route = respx.post(f"{vietmap_client.base_url}/vrp").mock(
        return_value=_vrp_tra(
            {"type": "start", "arrival": 28800, "duration": 0, "distance": 0},
            {"type": "job", "id": 1, "arrival": 29400, "duration": 600, "distance": 3000, "service": 3600},
            {"type": "job", "id": 3, "arrival": 33600, "duration": 900, "distance": 4500, "service": 1800},
            {"type": "end", "arrival": 36300, "duration": 1200, "distance": 6000},
        )
    )
    kq = await _goi(
        "plan_day_route",
        {
            "start": "16.46,107.59",
            "date": "2026-10-10",
            "stops": [
                {"place": "ChIJc1", "visit_minutes": 60},
                {"place": "ChIJc3", "visit_minutes": 45},
                {"place": "16.47,107.60", "visit_minutes": 30},
            ],
        },
    )
    gui = json.loads(route.calls.last.request.read())
    # Toạ độ lấy từ DB (không gọi Vietmap tra tên); khung giờ = [mở, đóng - thời gian ở lại].
    assert [j["id"] for j in gui["jobs"]] == [1, 3]
    assert gui["jobs"][0]["location"] == [107.5448, 16.4532]
    assert gui["jobs"][0]["time_windows"] == [[7 * 3600, 17 * 3600 - 3600]]
    assert "time_windows" not in gui["jobs"][1]
    assert kq["points"][1]["label"] == "Chùa Thiên Mụ"
    assert kq["unassigned"] == [
        {"stop_index": 1, "place": "Chùa Diệu Đế", "reason": "đóng cửa vào Thứ Bảy (theo giờ mở cửa đã lưu)"}
    ]
    gio = {g["place"]: g.get("hours_that_day") for g in kq["opening_hours_used"]}
    assert gio == {"Chùa Thiên Mụ": "07:00–17:00", "Chùa Diệu Đế": "Đóng cửa"}


@respx.mock
async def test_plan_day_route_giu_open_close_truyen_vao(db, vietmap):
    route = respx.post(f"{vietmap_client.base_url}/vrp").mock(
        return_value=_vrp_tra(
            {"type": "start", "arrival": 28800, "duration": 0, "distance": 0},
            {"type": "end", "arrival": 29000, "duration": 200, "distance": 1000},
            unassigned=[1],
        )
    )
    await _goi(
        "plan_day_route",
        {
            "start": "16.46,107.59",
            "date": "2026-10-10",
            "stops": [{"place": "ChIJc1", "open": "09:00", "close": "10:00", "visit_minutes": 30}],
        },
    )
    gui = json.loads(route.calls.last.request.read())
    assert gui["jobs"][0]["time_windows"] == [[9 * 3600, 10 * 3600 - 1800]]


@respx.mock
async def test_plan_day_route_place_id_khong_co_trong_db(db, vietmap):
    kq = await _goi("plan_day_route", {"start": "16.46,107.59", "stops": [{"place": "ChIJkhongco"}]})
    assert kq["status"] == "error" and "ChIJkhongco" in str(kq)


async def test_plan_day_route_place_id_khi_tat_db():
    kq = await _goi("plan_day_route", {"start": "16.46,107.59", "stops": [{"place": "ChIJc1"}]})
    assert kq["status"] == "error"


async def test_plan_day_route_ngay_sai():
    kq = await _goi("plan_day_route", {"start": "1,2", "date": "10/10/2026", "stops": [{"place": "3,4"}]})
    assert kq["status"] == "error"


async def test_find_places_noi_nho_trong_tinh(db):
    """Hội An không có khoá riêng trong DB (cào chung 'danang'): lấy chỗ có Hội An trong địa chỉ."""
    kq = await _goi("find_places", {"destination": "Hội An", "limit": 10})
    assert [p["name"] for p in kq["places"]] == ["Chùa Cầu"]
    kq = await _goi("find_places", {"destination": "Đà Nẵng", "limit": 10})
    assert {p["name"] for p in kq["places"]} == {"Chùa Cầu", "Cầu Rồng"}


async def test_geocode_vn_het_credit_vietmap_thi_lay_tam_diem_den(db, monkeypatch):
    from src.services.vietmap_client import VietmapError

    async def het_credit(*a, **k):
        raise VietmapError("Vietmap từ chối tham số (HTTP 423): exceeded credit.")

    monkeypatch.setattr(vietmap_client, "search", het_credit)
    kq = await _goi("geocode_vn", {"address": "Hội An"})
    assert kq["status"] == "success" and kq["source"] == "ailusion_db"
    assert kq["results"][0]["lat"] == pytest.approx(15.877)
    kq = await _goi("geocode_vn", {"address": "Nơi không có trong dữ liệu"})
    assert kq["status"] == "error"
