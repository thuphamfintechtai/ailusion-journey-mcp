"""Test 2 tool đơn vị hành chính trên dữ liệu thật của thư viện vietnam-provinces (offline).

Các ca lấy từ thử nghiệm ngày 2026-10-06 với data_version 2026-09-20; nâng phiên bản
thư viện mà test lệch thì kiểm tra lại dữ liệu chính thức trước khi sửa test.
"""

from fastmcp import Client

from src.services import admin_units as au
from src.utils.text import mo_rong_viet_tat, ten_goc_co_dau, ten_goc_hanh_chinh


def test_chuan_hoa_ten():
    assert ten_goc_hanh_chinh("P. Bến Nghé") == "ben nghe"
    assert ten_goc_hanh_chinh("TP. Hồ Chí Minh") == "ho chi minh"
    assert ten_goc_hanh_chinh("Q.3") == "3"
    assert mo_rong_viet_tat("Bà Rịa - Vũng Tàu") == "Bà Rịa Vũng Tàu"
    assert ten_goc_co_dau("Phường Hạ Long") != ten_goc_co_dau("Xã Hà Long")


def test_service_offline():
    assert len(au.provinces()) == 34
    assert au.find_province("TP HCM").name == "Thành phố Hồ Chí Minh"
    assert au.legacy_find_province("Bà Rịa - Vũng Tàu").name == "Tỉnh Bà Rịa - Vũng Tàu"
    assert au.new_from_legacy_province(77).name == "Thành phố Hồ Chí Minh"


async def _goi(ten, args):
    from src.fastmcp_server import mcp

    async with Client(mcp) as c:
        r = await c.call_tool(ten, args, raise_on_error=False)
    return r.structured_content


async def test_cu_sang_moi_viet_tat():
    kq = await _goi("convert_admin_unit", {"ward": "P. 1", "district": "Q.3", "province": "TP HCM"})
    r = kq["results"]
    assert len(r) == 1
    assert r[0]["old"]["district"] == "Quận 3" and r[0]["old"]["province"] == "Thành phố Hồ Chí Minh"
    assert r[0]["new"][0]["ward"] == "Phường Bàn Cờ"
    assert kq["data_version"]


async def test_cu_sang_moi_ten_rieng_khong_can_quan():
    kq = await _goi("convert_admin_unit", {"ward": "P. Bến Nghé"})
    assert kq["results"][0]["new"][0]["ward"] == "Phường Sài Gòn"


async def test_ten_pho_bien_khong_kem_quan_thi_hoi_lai():
    kq = await _goi("convert_admin_unit", {"ward": "Phường 1"})
    assert kq["status"] == "error" and "thêm `district`" in kq["error"]


async def test_quan_cu_va_tinh_cu():
    quan = await _goi("convert_admin_unit", {"district": "Quận 1", "province": "Hồ Chí Minh"})
    assert set(quan["new"]["wards"]) == {"Phường Bến Thành", "Phường Cầu Ông Lãnh", "Phường Sài Gòn", "Phường Tân Định"}
    tinh = await _goi("convert_admin_unit", {"province": "Bà Rịa - Vũng Tàu"})
    assert tinh["new"]["province"] == "Thành phố Hồ Chí Minh"


async def test_moi_sang_cu():
    kq = await _goi("convert_admin_unit", {"direction": "new_to_old", "ward": "Bàn Cờ", "province": "Hồ Chí Minh"})
    gop = kq["results"][0]["merged_from"]
    assert {g["ward"] for g in gop} == {f"Phường {i}" for i in range(1, 6)}
    assert all(g["district"] == "Quận 3" for g in gop)

    tinh = await _goi("convert_admin_unit", {"direction": "new_to_old", "province": "Hồ Chí Minh"})
    assert "Tỉnh Bà Rịa - Vũng Tàu" in tinh["merged_from_provinces"]


async def test_moi_sang_cu_khong_nhan_quan():
    kq = await _goi("convert_admin_unit", {"direction": "new_to_old", "ward": "Bàn Cờ", "district": "Quận 3"})
    assert kq["status"] == "error"


async def test_list_admin_units_ba_che_do():
    tinh = await _goi("list_admin_units", {})
    assert tinh["level"] == "province" and tinh["total_found"] == 34

    phuong = await _goi("list_admin_units", {"province": "Lâm Đồng", "limit": 3})
    assert len(phuong["items"]) == 3 and phuong["total_found"] > 100 and phuong["note"]

    tim = await _goi("list_admin_units", {"search": "Hạ Long"})
    assert tim["items"][0]["name"] == "Phường Hạ Long" and tim["items"][0]["exact"] is True
    assert "exact" not in tim["items"][1]


async def test_list_admin_units_tinh_cu_khong_con():
    kq = await _goi("list_admin_units", {"province": "Bà Rịa Vũng Tàu"})
    assert kq["status"] == "error" and "34" in kq["error"]
