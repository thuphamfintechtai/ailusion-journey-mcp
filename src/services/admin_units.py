"""Đơn vị hành chính Việt Nam trước / sau sáp nhập 1/7/2025, đọc offline từ thư viện `vietnam-provinces`.

Nguồn chính thức của dự án (danh mục của Cục Thống kê, tác giả Nguyễn Hồng Quân). Lưu ý:
- Giấy phép GPL-3.0, đã được chủ dự án chấp nhận (2026-10). Kiểm tra lại với pháp chế
  nếu phân phối mã nguồn / image ra ngoài.
- Dùng API Python (`Province`, `Ward`, `vietnam_provinces.legacy`), KHÔNG dùng file
  `data/nested-divisions.json` đi kèm: kiểm tra bản 2026.9.0 thì file JSON còn dữ liệu
  cũ (6 thành phố trung ương) trong khi API Python theo `__data_version__` 2026-09-20
  (9 thành phố, gồm Quảng Ninh, Bắc Ninh, Đồng Nai).
- Cập nhật dữ liệu = nâng phiên bản thư viện trong requirements.txt.

Import thư viện mất ~0,7 giây nên để lười: chỉ import ở lần tra đầu tiên.
"""

from functools import cache
from types import SimpleNamespace
from typing import Any

from ..utils.text import bo_dau, mo_rong_viet_tat, ten_goc_hanh_chinh

# Tên gọi tắt hay gặp mà ô tìm kiếm của thư viện không hiểu.
_BIET_DANH_TINH = {
    "hcm": "Hồ Chí Minh",
    "tphcm": "Hồ Chí Minh",
    "tp hcm": "Hồ Chí Minh",
    "sai gon": "Hồ Chí Minh",
    "saigon": "Hồ Chí Minh",
    "hn": "Hà Nội",
    "brvt": "Bà Rịa Vũng Tàu",
    "vung tau": "Bà Rịa Vũng Tàu",
}


@cache
def _lib() -> SimpleNamespace:
    import vietnam_provinces as vp
    from vietnam_provinces import Province, Ward
    from vietnam_provinces.legacy import District as LDistrict
    from vietnam_provinces.legacy import Province as LProvince
    from vietnam_provinces.legacy import Ward as LWard

    return SimpleNamespace(
        version=vp.__data_version__,
        Province=Province,
        Ward=Ward,
        LProvince=LProvince,
        LDistrict=LDistrict,
        LWard=LWard,
    )


def data_version() -> str:
    return str(_lib().version)


def _q(ten: str) -> str:
    """Chuỗi gửi vào hàm search của thư viện: mở rộng viết tắt, bỏ dấu, bỏ gạch nối."""
    return bo_dau(mo_rong_viet_tat(ten))


def _bi_danh(ten: str) -> str:
    return _BIET_DANH_TINH.get(ten_goc_hanh_chinh(ten), ten)


def _chon(ds: tuple | list, ten: str) -> Any | None:
    """Phần tử trùng tên (bỏ dấu, bỏ 'Phường / Quận / Tỉnh...'), không có thì phần tử đầu."""
    q = ten_goc_hanh_chinh(ten)
    return next((x for x in ds if ten_goc_hanh_chinh(x.name) == q), ds[0] if ds else None)


def trung_ten(ds: tuple | list, ten: str) -> list:
    """Các phần tử trùng tên; không có cái nào trùng hẳn thì trả cả danh sách."""
    q = ten_goc_hanh_chinh(ten)
    return [x for x in ds if ten_goc_hanh_chinh(x.name) == q] or list(ds)


# --- sau sáp nhập ------------------------------------------------------------


def provinces() -> list[Any]:
    return list(_lib().Province.iter_all())


def find_province(ten: str) -> Any | None:
    ten = _bi_danh(ten)
    return _chon(_lib().Province.search(_q(ten)), ten)


def province_by_code(code: int) -> Any | None:
    try:
        return _lib().Province.from_code(code)
    except (ValueError, KeyError):
        return None


def wards_of_province(code: int) -> list[Any]:
    return list(_lib().Ward.iter_by_province(code))


def search_wards(ten: str, province_code: int | None = None) -> list[Any]:
    return list(_lib().Ward.search(_q(ten), province=province_code))


def legacy_sources(ward: Any) -> list[Any]:
    """Các phường / xã cũ đã gộp thành `ward`."""
    return list(ward.get_legacy_sources())


# --- trước sáp nhập ----------------------------------------------------------


def legacy_find_province(ten: str) -> Any | None:
    ten = _bi_danh(ten)
    return _chon(_lib().LProvince.search(_q(ten)), ten)


def legacy_find_district(ten: str, province_code: int | None) -> Any | None:
    return _chon(_lib().LDistrict.search(_q(ten), province=province_code), ten)


def legacy_search_wards(ten: str, district_code: int | None, province_code: int | None) -> list[Any]:
    return list(_lib().LWard.search(_q(ten), district=district_code, province=province_code))


def legacy_district(code: int) -> Any | None:
    try:
        return _lib().LDistrict.from_code(code)
    except (ValueError, KeyError):
        return None


def legacy_province(code: int) -> Any | None:
    try:
        return _lib().LProvince.from_code(code)
    except (ValueError, KeyError):
        return None


def new_from_legacy_ward(code: int) -> list[Any]:
    """Phường / xã cũ -> phường / xã mới (nhiều phần tử nếu phường cũ bị chia)."""
    return [x.ward for x in _lib().Ward.search_from_legacy(code=code)]


def new_from_legacy_district(code: int) -> list[Any]:
    """Quận / huyện cũ (đã bỏ) -> các phường / xã mới phủ lên địa bàn đó."""
    da_co: dict[int, Any] = {}
    for x in _lib().Ward.search_from_legacy_district(code=code):
        da_co.setdefault(int(x.ward.code), x.ward)
    return list(da_co.values())


def new_from_legacy_province(code: int) -> Any | None:
    kq = _lib().Province.search_from_legacy(code=code)
    return kq[0].province if kq else None
