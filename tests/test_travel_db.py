"""Test phần đọc dữ liệu đã lưu trong Postgres: hàm thuần, tool đọc DB và fallback. Không gọi DB thật."""

import asyncio
from datetime import datetime

import pytest
from fastmcp import Client

from src.config.settings import settings
from src.services.serpapi_client import serpapi_client
from src.services.travel_db import TravelDbError, khoa_diem_den, khoa_tinh, travel_db
from src.tools._db_common import doc_thu, khung_gio, trang_thai_mo_cua, tu_khoa

GIO_CHUA = {t: ["07:00–17:00"] for t in ("Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật")}
DONG_KHACH = {"Saturday": {"6": 0, "7": 20, "8": 40, "9": 90, "10": 100, "11": 70, "17": 0}}

CHUA = {
    "cid": "1", "place_id": "ChIJchua", "destination": "hue", "seed_query": "Chùa Thiên Mụ, Huế", "best_match": True,
    "title": "Chùa Thiên Mụ", "category": "Chùa phật giáo", "address": "Kim Long, Huế", "lat": 16.4532, "lng": 107.5448,
    "rating": 4.6, "review_count": 20000, "price_range": None, "summary": "Ngôi chùa 7 tầng", "website": None,
    "phone": None, "wheelchair": True, "fetched_at": "2026-10-06T10:00:00+00:00",
}
QUAN = {
    **CHUA, "cid": "2", "place_id": "ChIJquan", "destination": "hue", "seed_query": "các nhà hàng tốt nhất ở Huế",
    "best_match": False, "title": "Bún bò Huế O Cương", "category": "Nhà hàng Việt Nam", "lat": 16.4600, "lng": 107.5900,
    "rating": 4.8, "review_count": 50, "wheelchair": False,
}
CAFE_HN = {**QUAN, "cid": "3", "place_id": "ChIJcafe", "destination": "ha-noi", "title": "Café Giảng", "category": "Quán cà phê", "lat": 21.03, "lng": 105.85}
CONG_HN = {**QUAN, "cid": "4", "place_id": "ChIJconghn", "destination": "ha-noi", "title": "Cộng Cà Phê", "address": "Hà Nội", "review_count": 900, "lat": 21.0, "lng": 105.8}
CONG_DN = {**QUAN, "cid": "5", "place_id": "ChIJcongdn", "destination": "da-nang", "title": "Cộng Cà Phê", "address": "Đà Nẵng", "review_count": 500, "lat": 16.0, "lng": 108.2}
CAU = {**CHUA, "cid": "6", "place_id": "ChIJcau", "destination": "da-nang", "title": "Cầu Rồng", "category": None, "rating": 0.0, "review_count": 0, "lat": 16.06, "lng": 108.22}
HO = {**CHUA, "cid": "7", "place_id": "ChIJho", "destination": "ha-noi", "title": "Hồ Hoàn Kiếm", "category": "Hồ", "lat": 21.03, "lng": 105.85}
PHONG = {**CHUA, "cid": "8", "place_id": "ChIJphong", "destination": "ha-noi", "title": "Jinzo", "category": "Phòng thoát hiểm", "lat": 21.02, "lng": 105.84}
CHI_TIET = {
    "1": {**CHUA, "open_hours": GIO_CHUA, "popular_times": DONG_KHACH, "reviews": [{"rating": 2, "date": "2026-09-01", "text": "Đông quá"}]},
    "4": {**CONG_HN, "open_hours": GIO_CHUA},
    "5": {**CONG_DN, "open_hours": GIO_CHUA},
    "6": {**CAU, "open_hours": None, "reviews": None},
}
LE_HOI = [
    {"name": "Festival Huế 2026", "start_date": "2026-10-10", "end_date": "2026-10-12", "location_name": "Đại Nội",
     "address_region": "Huế", "description": "Bản đăng trùng", "highlights": [], "tags": [], "url": "u2"},
    {"name": "Festival Huế 2026", "start_date": "2026-10-10", "end_date": "2026-10-12", "location_name": "Đại Nội",
     "address_region": "Huế", "description": "Lễ hội nghệ thuật", "highlights": ["Áo dài"], "tags": [], "url": "u"},
]


@pytest.fixture
def db_gia(monkeypatch):
    """Bật DB và thay `fetch` bằng dữ liệu giả, định tuyến theo câu SQL."""
    monkeypatch.setattr(settings, "pg_host", "db-gia")
    cau_sql: list[str] = []

    async def fetch(sql: str, *args):
        cau_sql.append(sql)
        await asyncio.sleep(0)  # nhường event loop để lộ lỗi nạp cache trùng khi gọi song song
        if "cid = ANY" in sql:
            cids, ids = args
            return [
                {**v, "_cid": v["cid"], "_pid": v["place_id"], "_did": None}
                for v in CHI_TIET.values()
                if v["cid"] in cids or v["place_id"] in ids
            ]
        if "FROM gmaps_place" in sql:
            return [dict(p) for p in (CHUA, QUAN, CAFE_HN, CONG_HN, CONG_DN, CAU, HO, PHONG)]
        if "DISTINCT address_region" in sql:
            return [{"address_region": "Huế"}, {"address_region": "Hà Nội"}]
        if "FROM event" in sql:
            return [e for e in LE_HOI if e["address_region"] in args[2]]
        return []

    monkeypatch.setattr(travel_db, "fetch", fetch)
    return cau_sql


async def _goi(ten: str, args: dict) -> dict:
    from src.fastmcp_server import mcp

    async with Client(mcp) as c:
        r = await c.call_tool(ten, args, raise_on_error=False)
    return r.structured_content


# --- hàm thuần ---------------------------------------------------------------


@pytest.mark.parametrize(
    "vao,ra",
    [("ha-noi", "hanoi"), ("Ha Noi", "hanoi"), ("Hà Nội", "hanoi"), ("Thành phố Hồ Chí Minh", "hochiminh"),
     ("TP. HCM", "hochiminh"), ("Sài Gòn", "hochiminh"), ("Đà Lạt", "dalat")],
)
def test_khoa_diem_den_gom_ba_kieu_ghi(vao, ra):
    assert khoa_diem_den(vao) == ra


def test_khoa_tinh_theo_sap_nhap():
    assert khoa_tinh("Hội An") == "danang" and khoa_tinh("Đà Lạt") == "lamdong"


def test_tu_khoa_bo_ten_diem_den_va_tu_thua():
    assert tu_khoa("quán chay ngon nhất ở Hà Nội", "hanoi") == ["quán", "chay"]
    assert tu_khoa("chợ đêm cho bé", None) == ["chợ", "đêm", "bé"]


def test_doc_thu():
    assert doc_thu("Thứ Bảy") == 5 and doc_thu("thu 7") == 5 and doc_thu("Sunday") == 6 and doc_thu("CN") == 6
    assert doc_thu("abc") is None


def test_khung_gio_qua_nua_dem_ca_ngay_va_dong_cua():
    gio = {"Thứ Hai": ["18:00–02:00"], "Thứ Ba": ["Mở cửa cả ngày"], "Thứ Tư": ["Đóng cửa"]}
    assert khung_gio(gio, 0) == [(1080, 1560)]
    assert khung_gio(gio, 1) == [(0, 1440)]
    assert khung_gio(gio, 2) == []
    assert khung_gio(gio, 3) is None


def test_trang_thai_mo_cua():
    gio = {"Thứ Hai": ["18:00–02:00"], "Thứ Ba": ["07:30–11:30", "13:30–17:30"]}
    # 2026-10-06 là Thứ Ba: 01:00 vẫn thuộc khung 18:00-02:00 của Thứ Hai.
    dem = trang_thai_mo_cua(gio, datetime(2026, 10, 6, 1, 0), 30)
    assert dem["status"] == "open" and dem["closes_in_min"] == 60 and dem["fits_stay"]
    trua = trang_thai_mo_cua(gio, datetime(2026, 10, 6, 12, 0), 30)
    assert trua["status"] == "closed" and trua["next_open_today"] == "13:30"
    sat_gio = trang_thai_mo_cua(gio, datetime(2026, 10, 6, 17, 0), 60)
    assert sat_gio["status"] == "open" and not sat_gio["fits_stay"]


# --- tool đọc DB ---------------------------------------------------------------


async def test_browse_places_loc_theo_diem_den_va_xe_lan(db_gia):
    kq = await _goi("browse_places", {"destination": "Huế", "wheelchair": True})
    assert kq["status"] == "success" and kq["data_source"] == "ailusion_db"
    assert [p["name"] for p in kq["places"]] == ["Chùa Thiên Mụ"]


async def test_browse_places_diem_den_chua_co_goi_y(db_gia):
    kq = await _goi("browse_places", {"destination": "Phú Quốc"})
    assert kq["places"] == [] and "Huế" in kq["available_destinations"]


async def test_browse_places_theo_ban_kinh(db_gia):
    kq = await _goi("browse_places", {"lat": 16.4532, "lng": 107.5448, "radius_km": 1, "sort_by": "distance"})
    assert [p["name"] for p in kq["places"]] == ["Chùa Thiên Mụ"]


async def test_check_opening_hours_tim_theo_ten_va_seed(db_gia):
    kq = await _goi("check_opening_hours", {"places": ["Chùa Thiên Mụ", "không có"], "at": "2026-10-10 16:30", "stay_minutes": 60})
    chua, khong = kq["results"]
    assert chua["status"] == "open" and chua["closes_in_min"] == 30 and chua["fits_stay"] is False
    assert khong["status"] == "not_found"


async def test_best_time_to_visit_chi_tinh_gio_mo_cua(db_gia):
    kq = await _goi("best_time_to_visit", {"place": "ChIJchua", "day": "Thứ Bảy"})
    ngay = kq["days"][0]
    assert ngay["peak_hour"]["hour"] == "10:00"
    assert ngay["quietest_hours"][0] == {"hour": "07:00", "busyness": 20}
    assert ngay["best_2h_window"] == "07:00-09:00"
    assert "06" not in ngay["by_hour"]


async def test_find_festivals(db_gia):
    kq = await _goi("find_festivals", {"location": "Huế", "start_date": "2026-10-11"})
    assert kq["events"][0]["name"] == "Festival Huế 2026"


# --- fallback --------------------------------------------------------------------


@pytest.fixture
def het_key_serpapi(monkeypatch):
    monkeypatch.setattr(serpapi_client, "api_keys", [])


async def test_search_places_fallback_db_khi_serpapi_loi(db_gia, het_key_serpapi):
    kq = await _goi("search_places", {"query": "bún bò Huế"})
    assert kq["status"] == "success" and kq["data_source"] == "ailusion_db"
    assert kq["places"][0]["name"] == "Bún bò Huế O Cương"
    assert "SerpApi" in kq["fallback_reason"]


async def test_search_places_fallback_khong_khop_tra_loi_goc(db_gia, het_key_serpapi):
    kq = await _goi("search_places", {"query": "quán chay Huế"})
    assert kq["status"] == "error" and kq["error_code"] == -4


async def test_fallback_tat_thi_tra_loi_goc(db_gia, het_key_serpapi, monkeypatch):
    monkeypatch.setattr(settings, "pg_fallback_enabled", False)
    kq = await _goi("get_place_details", {"place_id": "ChIJchua"})
    assert kq["status"] == "error" and db_gia == []


async def test_get_place_reviews_fallback(db_gia, het_key_serpapi):
    kq = await _goi("get_place_reviews", {"place_id": "ChIJchua", "sort_by": "lowest"})
    assert kq["data_source"] == "ailusion_db" and kq["reviews"][0]["text"] == "Đông quá"


# --- edge case -------------------------------------------------------------------


async def test_goi_song_song_chi_tai_danh_sach_mot_lan(db_gia):
    await asyncio.gather(*(travel_db.places() for _ in range(8)))
    assert sum(1 for q in db_gia if "SELECT cid" in q) == 1


async def test_le_hoi_escape_ky_tu_dai_dien(db_gia, monkeypatch):
    tham_so = []

    async def fetch(sql, *args):
        tham_so.append(args)
        return []

    monkeypatch.setattr(travel_db, "fetch", fetch)
    await _goi("find_festivals", {"location": "50%_off", "start_date": "2026-10-10"})
    assert tham_so[-1][3] == "%50\\%\\_off%"


async def test_diem_den_chua_co_thi_khong_khop_thanh_pho_khac(db_gia):
    kq = await _goi("check_opening_hours", {"places": ["Chùa Thiên Mụ"], "at": "2026-10-10 09:00", "destination": "Phú Quốc"})
    assert kq["results"][0]["status"] == "not_found"


@pytest.mark.parametrize("at", ["2026-10-10", "10/10/2026 9h", "2026-02-30 09:00", ""])
async def test_check_opening_hours_at_sai_dang(db_gia, at):
    kq = await _goi("check_opening_hours", {"places": ["Chùa Thiên Mụ"], "at": at})
    assert kq["status"] == "error" and kq["error_code"] == -2


async def test_check_opening_hours_doi_mui_gio(db_gia):
    # 01:30 UTC = 08:30 giờ Việt Nam -> chùa (07:00-17:00) đang mở.
    kq = await _goi("check_opening_hours", {"places": ["ChIJchua"], "at": "2026-10-10T01:30:00Z"})
    assert kq["at"] == "2026-10-10 08:30" and kq["results"][0]["status"] == "open"


@pytest.mark.parametrize("day", ["thứ 8", "2026-13-01"])
async def test_best_time_day_sai(db_gia, day):
    kq = await _goi("best_time_to_visit", {"place": "ChIJchua", "day": day})
    assert kq["status"] == "error" and kq["error_code"] == -2


async def test_best_time_ngay_khong_co_du_lieu(db_gia):
    kq = await _goi("best_time_to_visit", {"place": "ChIJchua", "day": "Thứ Hai"})
    assert kq["days"] == [] and "note" in kq


async def test_browse_places_khong_khop_co_note_dung(db_gia):
    kq = await _goi("browse_places", {"destination": "Huế", "query": "drop table"})
    assert kq["total_found"] == 0 and "Không có địa điểm khớp" in kq["note"]


async def test_browse_places_chi_co_tu_thua_tra_ca_diem_den(db_gia):
    kq = await _goi("browse_places", {"destination": "Huế", "query": "ngon nhất ở"})
    assert kq["total_found"] == 2


@pytest.mark.parametrize(
    "args",
    [{"lat": 16.0}, {}, {"destination": "  "}, {"destination": "Huế", "sort_by": "distance"}],
)
async def test_browse_places_tham_so_sai(db_gia, args):
    kq = await _goi("browse_places", args)
    assert kq["status"] == "error" and kq["error_code"] == -2


async def test_db_loi_ket_noi_thi_nghi_khong_thu_lai_ngay(monkeypatch):
    monkeypatch.setattr(settings, "pg_host", "db-sap")
    monkeypatch.setattr(travel_db, "_nghi_den", 0.0)
    so_lan = {"n": 0}

    async def sap():
        so_lan["n"] += 1
        raise OSError("connection refused")

    monkeypatch.setattr(travel_db, "_lay_pool", sap)
    for _ in range(3):
        with pytest.raises(TravelDbError):
            await travel_db.fetch("SELECT 1")
    assert so_lan["n"] == 1


async def test_db_sap_tool_tra_loi_db_va_fallback_tra_loi_goc(monkeypatch, het_key_serpapi):
    monkeypatch.setattr(settings, "pg_host", "db-sap")
    monkeypatch.setattr(travel_db, "_nghi_den", 0.0)

    async def sap():
        raise OSError("connection refused")

    monkeypatch.setattr(travel_db, "_lay_pool", sap)
    kq = await _goi("browse_places", {"destination": "Huế"})
    assert kq["status"] == "error" and kq["error_code"] == -3
    kq = await _goi("search_places", {"query": "chùa Huế"})
    assert kq["status"] == "error" and kq["error_code"] == -4


async def test_search_places_khong_neu_khu_vuc_co_canh_bao(db_gia, het_key_serpapi):
    kq = await _goi("search_places", {"query": "cà phê"})
    assert {p["name"] for p in kq["places"]} >= {"Café Giảng", "Cộng Cà Phê"} and "nhiều thành phố" in kq["note"]


async def test_weather_fallback_khong_co_diem_den_tra_loi_goc(db_gia, monkeypatch):
    from src.services.key_pool import KeyPool
    from src.services.weather_client import weather_client

    monkeypatch.setattr(weather_client, "pool", KeyPool([], "weatherapi"))
    kq = await _goi("weather_forecast", {"location": "Atlantis", "start_date": "2026-10-08"})
    assert kq["status"] == "error" and "data_source" not in kq


# --- so khớp có dấu / không dấu, input bẩn, định dạng giờ -----------------------


@pytest.mark.parametrize(
    "tu,van_ban,giu_dau,khop",
    [
        ("bò", "Câu lạc bộ spa", False, False),
        ("bò", "Bò tơ quán Mộc", False, True),
        ("bo", "Câu lạc bộ spa", False, True),
        ("phở", "Phố cổ", False, False),
        ("hoa", "văn hóa", True, False),
        ("hoa", "văn hóa", False, True),
        ("hoa", "Festival Hoa", True, True),
        ("chợ", "CHỢ ĐÊM", False, True),
    ],
)
def test_khop_tu_co_dau(tu, van_ban, giu_dau, khop):
    from src.utils.text import khop_tu, van_ban_de_khop

    co, khong = van_ban_de_khop(van_ban)
    assert khop_tu(tu, co, khong, giu_dau) is khop


def test_tu_khoa_nfd_va_chu_hoa():
    import unicodedata

    assert tu_khoa(unicodedata.normalize("NFD", "BÚN CHẢ Hà Nội"), "hanoi") == ["bún", "chả"]


async def test_query_co_dau_khong_khop_nham(db_gia):
    # Gõ có dấu 'bộ' không được khớp 'bò'; gõ không dấu 'bo' thì khớp.
    kq = await _goi("browse_places", {"destination": "Huế", "query": "bộ"})
    assert kq["total_found"] == 0
    kq = await _goi("browse_places", {"destination": "Huế", "query": "bo"})
    assert [p["name"] for p in kq["places"]] == ["Bún bò Huế O Cương"]


def test_ky_tu_nul_bi_loai_truoc_khi_vao_db():
    from src.services import travel_db as mod

    assert mod._sach("Huế\x00") == "Huế"
    assert mod._sach(["a\x00b"]) == ["ab"]


@pytest.mark.parametrize("at,gio", [("2026-10-10 9:00", "09:00"), ("2026-10-10 09h00", "09:00"), ("2026-10-10 9h", "09:00"), ("2026-10-10T16:30", "16:30")])
async def test_check_opening_hours_nhan_nhieu_kieu_gio(db_gia, at, gio):
    kq = await _goi("check_opening_hours", {"places": ["ChIJchua"], "at": at})
    assert kq["status"] == "success" and kq["at"].endswith(gio)


@pytest.mark.parametrize("day,thu", [("Mon", "Thứ Hai"), ("sat", "Thứ Bảy"), ("thu bay", "Thứ Bảy"), ("chủ nhật", "Chủ Nhật")])
def test_doc_thu_viet_tat(day, thu):
    from src.tools._db_common import THU_VI

    assert THU_VI[doc_thu(day)] == thu


async def test_le_hoi_keyword_co_dau(db_gia):
    kq = await _goi("find_festivals", {"location": "Huế", "start_date": "2026-10-11", "keyword": "áo dài"})
    assert kq["total_found"] == 1
    kq = await _goi("find_festivals", {"location": "Huế", "start_date": "2026-10-11", "keyword": "ao dai"})
    assert kq["total_found"] == 1
    kq = await _goi("find_festivals", {"location": "Huế", "start_date": "2026-10-11", "keyword": "ảo đài"})
    assert kq["total_found"] == 0


# --- đợt 3: điểm đến lồng trong từ, chi nhánh, dữ liệu thiếu, JSON hỏng, tải nặng ------


@pytest.mark.parametrize(
    "text,khoa",
    [("Khuê Trung", None), ("thuê xe máy", None), ("bún bò Huế ở Hà Nội", "hanoi"), ("Huế, Việt Nam", "hue"),
     ("phố cổ Hà Nội", "hanoi"), ("Sài Gòn", None), ("đi Đà Nẵng chơi", "danang")],
)
async def test_find_destination_theo_ranh_gioi_tu(db_gia, text, khoa):
    assert await travel_db.find_destination(text) == khoa


async def test_category_so_nguyen_tu(db_gia):
    kq = await _goi("browse_places", {"destination": "Hà Nội", "category": "Hồ"})
    assert [p["name"] for p in kq["places"]] == ["Hồ Hoàn Kiếm"]


async def test_chi_nhanh_trung_ten_bao_other_matches(db_gia):
    kq = await _goi("check_opening_hours", {"places": ["Cộng Cà Phê"], "at": "2026-10-10 09:00"})
    r = kq["results"][0]
    assert r["place_id"] == "ChIJconghn"  # nhiều review hơn
    assert r["other_matches"][0]["place_id"] == "ChIJcongdn" and "note" in kq
    kq = await _goi("check_opening_hours", {"places": ["Cộng Cà Phê"], "at": "2026-10-10 09:00", "destination": "Đà Nẵng"})
    assert kq["results"][0]["place_id"] == "ChIJcongdn" and "other_matches" not in kq["results"][0]


async def test_tra_nhieu_dia_diem_mot_truy_van(db_gia):
    await _goi("check_opening_hours", {"places": ["Chùa Thiên Mụ", "Cộng Cà Phê", "ChIJchua", "không có"], "at": "2026-10-10 09:00"})
    assert sum(1 for q in db_gia if "cid = ANY" in q) == 1


async def test_rating_0_khong_review_la_chua_co_danh_gia(db_gia, het_key_serpapi):
    kq = await _goi("browse_places", {"destination": "Đà Nẵng", "query": "Cầu Rồng"})
    assert "rating" not in kq["places"][0]
    kq = await _goi("get_place_details", {"place_id": "Cầu Rồng"})
    assert "rating" not in kq["place"] and kq["place"]["reviews"] == 0


async def test_reviews_null_khong_loi(db_gia, het_key_serpapi):
    kq = await _goi("get_place_reviews", {"place_id": "Cầu Rồng"})
    assert kq["status"] == "success" and kq["reviews"] == []


async def test_gio_mo_cua_null_la_unknown(db_gia):
    kq = await _goi("check_opening_hours", {"places": ["Cầu Rồng"], "at": "2026-10-10 09:00"})
    assert kq["results"][0]["status"] == "unknown"


async def test_su_kien_trung_chi_giu_mot(db_gia):
    kq = await _goi("find_festivals", {"location": "Huế", "start_date": "2026-10-11"})
    assert kq["total_found"] == 1


async def test_serpapi_tra_html_van_fallback(db_gia, monkeypatch):
    import httpx
    import respx

    monkeypatch.setattr(serpapi_client, "api_keys", ["k1"])
    monkeypatch.setattr(serpapi_client, "_idx", 0)
    monkeypatch.setattr(serpapi_client, "_nghi_den", {})
    with respx.mock:
        respx.get(f"{serpapi_client.base_url}/search.json").mock(return_value=httpx.Response(200, text="<html>loi</html>"))
        kq = await _goi("search_places", {"query": "chùa Huế"})
    assert kq["status"] == "success" and kq["data_source"] == "ailusion_db"


# --- đợt 4: dữ liệu lạc vùng, tên kèm thành phố, truy vấn chậm, DB sống lại ---------


async def test_dia_diem_lac_vung_bi_loai_khi_loc_theo_diem_den(db_gia, monkeypatch):
    lac = {**QUAN, "cid": "9", "place_id": "ChIJlac", "destination": "hue", "title": "Đặc sản Huế ở Sài Gòn",
           "lat": 10.77, "lng": 106.70, "review_count": 99999}
    goc = travel_db.fetch

    async def fetch(sql, *args):
        if "FROM gmaps_place" in sql and "WHERE" not in sql:
            return [dict(p) for p in (CHUA, QUAN, {**CHUA, "cid": "10", "place_id": "ChIJx", "title": "Lăng Tự Đức"}, lac)]
        return await goc(sql, *args)

    monkeypatch.setattr(travel_db, "fetch", fetch)
    kq = await _goi("browse_places", {"destination": "Huế"})
    assert "Đặc sản Huế ở Sài Gòn" not in [p["name"] for p in kq["places"]]
    # Tìm theo toạ độ thì vẫn thấy (đúng vị trí thật của nó).
    kq = await _goi("browse_places", {"lat": 10.77, "lng": 106.70, "radius_km": 1})
    assert [p["name"] for p in kq["places"]] == ["Đặc sản Huế ở Sài Gòn"]


@pytest.mark.parametrize(
    "text,ds",
    [("Hà Nội - Hải Phòng", ["hanoi", "haiphong"]), ("Ho Chi Minh City", ["hochiminh"]), ("Khuê Trung", []),
     ("Hải Phòng", ["haiphong"]), ("TP. HCM", ["hochiminh"])],
)
def test_tim_khoa_trong(text, ds):
    from src.services.travel_db import tim_khoa_trong

    assert tim_khoa_trong(text, {"hanoi", "haiphong", "hochiminh", "hue"}) == ds


@pytest.mark.parametrize(
    "text,khoa,ra",
    [("Văn Miếu, Hà Nội", "hanoi", "văn miếu"), ("Cầu Rồng (Đà Nẵng)", "danang", "cầu rồng"),
     ("Chùa Thiên Mụ - Huế City", "hue", "chùa thiên mụ")],
)
def test_bo_dia_danh(text, khoa, ra):
    from src.services.travel_db import bo_dia_danh

    assert bo_dia_danh(text, khoa) == ra


async def test_ten_kem_thanh_pho_van_tim_duoc(db_gia):
    kq = await _goi("check_opening_hours", {"places": ["Chùa Thiên Mụ, Huế", "Cộng Cà Phê (Đà Nẵng)"], "at": "2026-10-10 09:00"})
    assert [r.get("place_id") for r in kq["results"]] == ["ChIJchua", "ChIJcongdn"]


async def test_nhieu_diem_den_co_note(db_gia):
    kq = await _goi("browse_places", {"destination": "Hà Nội - Huế"})
    assert "nhiều điểm đến" in kq["note"] and "Hà Nội" in kq["note"]
    kq = await _goi("browse_places", {"destination": "Huế", "lat": 16.4532, "lng": 107.5448, "radius_km": 1})
    assert "bỏ qua `destination`" in kq["note"]


async def test_le_hoi_nhieu_tinh(db_gia):
    kq = await _goi("find_festivals", {"location": "Hà Nội, Huế", "start_date": "2026-10-11"})
    assert kq["total_found"] == 1


class _PoolGia:
    def __init__(self, loi_acquire=None, loi_fetch=None):
        self.loi_acquire, self.loi_fetch, self.terminated = loi_acquire, loi_fetch, False

    async def acquire(self, timeout=None):
        if self.loi_acquire:
            raise self.loi_acquire
        return self

    async def fetch(self, sql, *args, timeout=None):
        if self.loi_fetch:
            raise self.loi_fetch
        return []

    async def release(self, conn):
        pass

    def terminate(self):
        self.terminated = True


async def test_truy_van_cham_khong_lam_db_nghi(monkeypatch):
    monkeypatch.setattr(settings, "pg_host", "db")
    monkeypatch.setattr(travel_db, "_nghi_den", 0.0)
    pool = _PoolGia(loi_fetch=asyncio.TimeoutError())

    async def lay():
        return pool

    monkeypatch.setattr(travel_db, "_lay_pool", lay)
    with pytest.raises(TravelDbError, match="quá thời gian"):
        await travel_db.fetch("SELECT pg_sleep(99)")
    assert travel_db._nghi_den == 0.0


async def test_loi_ket_noi_bo_pool_de_tao_lai(monkeypatch):
    monkeypatch.setattr(settings, "pg_host", "db")
    monkeypatch.setattr(travel_db, "_nghi_den", 0.0)
    pool = _PoolGia(loi_acquire=OSError("refused"))
    monkeypatch.setattr(travel_db, "_pool", pool)

    async def lay():
        return pool

    monkeypatch.setattr(travel_db, "_lay_pool", lay)
    with pytest.raises(TravelDbError):
        await travel_db.fetch("SELECT 1")
    assert pool.terminated and travel_db._pool is None and travel_db._nghi_den > 0
