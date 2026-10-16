"""Phần dùng chung cho các tool đọc dữ liệu đã cào trong Postgres (src/services/travel_db.py).

Gồm: lọc địa điểm theo từ khoá không dấu / bán kính, đọc giờ mở cửa và giờ đông khách
của Google Maps, và `fallback_db()` để tool gọi API ngoài trả dữ liệu đã lưu khi API lỗi.
File bắt đầu bằng `_` nên `auto_register` không coi đây là tool.
"""

import math
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

import structlog

from ..config.settings import settings
from ..services.rating_prior import sao_tin_duoc
from ..services.travel_db import TravelDbError, khoang_cach_km, thuoc_diem_den, travel_db
from ..utils.error_handler import format_mcp_tool_error_response
from ..utils.response_utils import drop_empty
from ..utils.text import bo_dau, co_dau, go_co_dau, khop_tu, tach_tu, van_ban_de_khop

logger = structlog.get_logger(__name__)

NGUON_DB = "ailusion_db"
GHI_CHU_DB = (
    "Dữ liệu đã lưu sẵn (cào từ Google Maps / lehoivietnam.com.vn), không phải thời gian thực; "
    "xem `data_as_of` để biết độ mới."
)

# Thứ trong tuần: index Python (0 = Thứ Hai) <-> key trong open_hours / popular_times.
THU_VI = ("Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật")
_THU_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_THU_TU_KHOA = (
    {bo_dau(t): i for i, t in enumerate(THU_VI)}
    | {t.lower(): i for i, t in enumerate(_THU_EN)}
    | {t[:3].lower(): i for i, t in enumerate(_THU_EN)}
    | {"t2": 0, "t3": 1, "t4": 2, "t5": 3, "t6": 4, "t7": 5, "cn": 6, "chu nhat": 6}
)

# Từ không mang nghĩa lọc trong truy vấn tự nhiên ('quán chay ngon nhất ở Hội An'). So khớp giữ
# dấu: 'cho' là từ thừa nhưng 'chợ' thì không. Bản không dấu chỉ gồm từ không mơ hồ khi bỏ dấu.
_TU_BO = {"ở", "tại", "gần", "khu", "vực", "tốt", "nhất", "ngon", "các", "những", "nào", "cho", "và", "the", "best", "in", "near", "top"}
_TU_BO_KHONG_DAU = {"tai", "nhat", "ngon", "cac", "nhung", "the", "best", "in", "near", "top"}
_KHUNG_GIO = re.compile(r"^(\d{1,2}):(\d{2})\s*[–-]\s*(\d{1,2}):(\d{2})$")


async def fallback_db(
    ten_tool: str,
    loi_goc: dict[str, Any],
    lay_tu_db: Callable[[], Awaitable[dict[str, Any] | None]],
) -> dict[str, Any]:
    """Khi API ngoài lỗi: thử lấy dữ liệu đã lưu; DB không có / lỗi thì trả lỗi gốc."""
    if not (settings.pg_fallback_enabled and travel_db.enabled):
        return loi_goc
    try:
        kq = await lay_tu_db()
    except TravelDbError as e:
        logger.warning("Fallback DB thất bại", tool=ten_tool, error=str(e))
        return loi_goc
    except Exception as e:
        logger.error("Fallback DB lỗi không mong đợi", tool=ten_tool, error=str(e), exc_info=True)
        return loi_goc
    if not kq:
        return loi_goc
    logger.info("Đã dùng dữ liệu DB thay API ngoài", tool=ten_tool)
    return drop_empty(
        {
            **kq,
            "data_source": NGUON_DB,
            "fallback_reason": f"{loi_goc.get('error')} Đang trả dữ liệu đã lưu thay thế.",
            "data_note": GHI_CHU_DB,
        }
    )


def loi_db(e: Exception, input_context: dict[str, Any]) -> dict[str, Any]:
    return format_mcp_tool_error_response(
        error_message="Không đọc được dữ liệu đã lưu (Postgres). Thử lại sau hoặc dùng tool gọi API trực tuyến.",
        exception=e,
        error_code=-3,
        input_context=input_context,
    )


async def chon_diem_den(
    destination: str | None, co_toa_do: bool = False
) -> tuple[str | None, list[str]]:
    """(khoá điểm đến, ghi chú). Nhiều nơi trong chuỗi ('Hà Nội - Huế') thì lấy nơi đầu và ghi
    chú; có toạ độ thì báo đã bỏ qua `destination`."""
    if co_toa_do:
        return None, ["Có lat/lng nên lọc theo bán kính, bỏ qua `destination`."] if destination and destination.strip() else []
    cac_dest = await travel_db.find_destinations(destination)
    if not cac_dest:
        return None, []
    ghi_chu = []
    if len(cac_dest) > 1:
        ten = (await travel_db.destinations())[cac_dest[0]]["name"]
        ghi_chu.append(f"Nhận ra nhiều điểm đến, chỉ lấy '{ten}'; gọi riêng cho từng nơi.")
    return cac_dest[0], ghi_chu


async def goi_y_diem_den() -> list[str]:
    """Tên các điểm đến có dữ liệu địa điểm, để model biết hỏi lại cho đúng."""
    return sorted({d["name"] for d in (await travel_db.destinations()).values()})


# --- địa điểm ------------------------------------------------------------------


def tu_khoa(query: str | None, destination_key: str | None = None) -> list[str]:
    """Tách truy vấn thành từ (giữ dấu), bỏ từ thừa, từ lặp và cụm từ ghép thành tên điểm đến
    ('quán chay Hà Nội' + 'hanoi' -> ['quán', 'chay'])."""
    toks = tach_tu(query)
    if destination_key:
        for i in range(len(toks)):
            for j in range(i + 1, min(i + 5, len(toks)) + 1):
                if "".join(bo_dau(t) for t in toks[i:j]) == destination_key:
                    toks = toks[:i] + toks[j:]
                    break
            else:
                continue
            break
    ket_qua: list[str] = []
    for t in toks:
        # 'cho' tự nó không dấu nên vẫn là từ thừa; 'chợ' có dấu thì không bị bỏ.
        thua = t in _TU_BO or (not co_dau(t) and t in _TU_BO_KHONG_DAU)
        if not thua and t not in ket_qua:
            ket_qua.append(t)
    return ket_qua


def khop_du_tu_khoa(toks: list[str], van_ban: str | None) -> bool:
    """Mọi từ khoá đều có trong văn bản (so khớp theo luật của khop_tu)."""
    co, khong = van_ban_de_khop(van_ban)
    giu_dau = go_co_dau(toks)
    return all(khop_tu(t, co, khong, giu_dau) for t in toks)


def diem_pho_bien(p: dict[str, Any]) -> float:
    """Điểm sắp "popular" = sao × log10(số review + 10) — công thức TỰ ĐẶT, để 4.9 sao / 3 review
    không vượt 4.6 sao / 5.000 review và chỗ nổi tiếng được ưu tiên.

    Chưa đổi sang sao Bayes: so top 30 ở Đà Nẵng / Huế / Hà Nội (10/2026), Bayes chỉ trùng 1–5/30
    chỗ với cách này — số review ảo thật chỉ ~9–20 nên Bayes đo chất lượng, không đo độ nổi tiếng;
    trộn mọi loại hình thì spa / chỗ 5★ không phải du lịch chiếm hết, Bà Nà, Kinh thành Huế, Văn
    Miếu rơi khỏi danh sách. Bayes dùng cho sắp theo `rating_adjusted`, chỗ ở và bước chấm (LLM)."""
    return (p.get("rating") or 0) * math.log10((p.get("review_count") or 0) + 10)


def loc_dia_diem(
    ds: list[dict[str, Any]],
    *,
    query: str | None = None,
    destination_key: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float | None = None,
    category: str | None = None,
    min_rating: float | None = None,
    min_reviews: int | None = None,
    wheelchair: bool = False,
    sort_by: str = "popular",
    max_price_vnd: int | None = None,
    min_price_vnd: int | None = None,
    strict_price: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    """Lọc + xếp hạng. Trả (danh sách, khớp_đủ). khớp_đủ=False nghĩa là không điểm nào
    chứa đủ mọi từ khoá nên đã nới ra các điểm khớp một phần."""
    toks = tu_khoa(query, destination_key)
    giu_dau = go_co_dau(tach_tu(query))
    loai = tach_tu(category)
    ung_vien = []
    for p in ds:
        if destination_key and lat is None and not thuoc_diem_den(p, destination_key):
            continue
        if min_rating is not None and (p.get("rating") or 0) < min_rating:
            continue
        if min_reviews is not None and (p.get("review_count") or 0) < min_reviews:
            continue
        # So nguyên từ: category 'Hồ' không được khớp 'Phòng', 'Phở'.
        if loai and not khop_du_tu_khoa(loai, p.get("category")):
            continue
        if wheelchair and not p.get("wheelchair"):
            continue
        if (max_price_vnd is not None or min_price_vnd is not None) and not trong_khoang_gia(
            p, max_price_vnd, min_price_vnd, strict_price
        ):
            continue
        kc = None
        if lat is not None and lng is not None:
            if p.get("lat") is None:
                continue
            kc = khoang_cach_km(lat, lng, p["lat"], p["lng"])
            if radius_km is not None and kc > radius_km:
                continue
        khop = sum(1 for t in toks if khop_tu(t, p["_text_cd"], p["_text"], giu_dau))
        ung_vien.append((khop, kc, p))

    khop_du = True
    if toks:
        du = [u for u in ung_vien if u[0] == len(toks)]
        if du:
            ung_vien = du
        else:
            # Chỉ nới khi truy vấn dài (>= 3 từ) và khớp gần đủ; 'quán chay' mà chỉ khớp 'quán'
            # thì trả về quán cà phê là sai hẳn ý người dùng.
            khop_du = False
            nguong = len(toks) - 1 if len(toks) >= 3 else len(toks)
            ung_vien = [u for u in ung_vien if u[0] >= nguong]

    if sort_by == "distance" and lat is not None:
        ung_vien.sort(key=lambda u: u[1])
    elif sort_by == "rating":
        ung_vien.sort(key=lambda u: (-(u[0]), -(u[2].get("rating") or 0), -(u[2].get("review_count") or 0)))
    else:
        ung_vien.sort(key=lambda u: (-(u[0]), -diem_pho_bien(u[2])))
    return [{**p, "_distance_km": kc} for _, kc, p in ung_vien], khop_du


def gon_dia_diem(p: dict[str, Any]) -> dict[str, Any]:
    """Dạng gọn chung cho mọi tool trả địa điểm từ DB."""
    kc = p.get("_distance_km")
    return {
        "name": p.get("title"),
        "place_id": p.get("place_id"),
        "category": p.get("category"),
        "rating": p.get("rating") or None,  # 0 sao + 0 review = chưa có đánh giá
        **truong_sao_tin_duoc(p),
        "reviews": p.get("review_count"),
        "price": p.get("price_range"),
        "price_vnd": gia_uoc_tinh(p),
        "address": p.get("address"),
        "lat": p.get("lat"),
        "lng": p.get("lng"),
        "distance_km": round(kc, 2) if kc is not None else None,
        "phone": p.get("phone"),
        "website": p.get("website"),
        "summary": p.get("summary"),
        "thumbnail": p.get("thumbnail"),
    }


def moi_nhat(ds: list[dict[str, Any]]) -> str | None:
    """`fetched_at` mới nhất (chuỗi ISO), dùng làm `data_as_of`."""
    cac_moc = [str(p["fetched_at"]) for p in ds if p.get("fetched_at")]
    return max(cac_moc)[:10] if cac_moc else None


# --- giờ mở cửa / giờ đông khách ---------------------------------------------


def doc_thu(s: str) -> int | None:
    """'Thứ Bảy' / 'thu 7' / 'saturday' / 'T7' / 'CN' -> 0-6 (0 = Thứ Hai)."""
    k = bo_dau(s)
    if k in _THU_TU_KHOA:
        return _THU_TU_KHOA[k]
    m = re.fullmatch(r"thu ([2-7])", k)
    return int(m.group(1)) - 2 if m else None


def _theo_thu(bang: dict[str, Any] | None, thu: int) -> Any:
    if not bang:
        return None
    for k, v in bang.items():
        if doc_thu(k) == thu:
            return v
    return None


def khung_gio(open_hours: dict[str, Any] | None, thu: int) -> list[tuple[int, int]] | None:
    """Các khung mở cửa của một thứ, tính bằng phút từ 0h. None = không có dữ liệu.

    [] = đóng cửa, [(0, 1440)] = mở cả ngày; khung qua nửa đêm có giờ kết thúc > 1440.
    """
    ds = _theo_thu(open_hours, thu)
    if ds is None:
        return None
    khung: list[tuple[int, int]] = []
    for s in ds:
        k = bo_dau(str(s))
        if "ca ngay" in k or "24 gio" in k:
            return [(0, 1440)]
        if "dong cua" in k:
            continue
        m = _KHUNG_GIO.match(str(s).strip())
        if not m:
            continue
        bd = int(m.group(1)) * 60 + int(m.group(2))
        kt = int(m.group(3)) * 60 + int(m.group(4))
        if kt <= bd:
            kt += 1440
        khung.append((bd, kt))
    return khung


def phut_sang_gio(phut: int) -> str:
    phut %= 1440
    return f"{phut // 60:02d}:{phut % 60:02d}"


def trang_thai_mo_cua(open_hours: dict[str, Any] | None, luc: datetime, o_lai_phut: int = 0) -> dict[str, Any]:
    """Mở hay đóng tại `luc`, còn bao lâu đóng, có đủ thời gian ở lại `o_lai_phut` không."""
    thu = luc.weekday()
    hom_nay = khung_gio(open_hours, thu)
    if hom_nay is None:
        return {"status": "unknown"}
    phut = luc.hour * 60 + luc.minute
    # Khung của hôm trước kéo qua nửa đêm (VD 18:00-02:00) tính như khung hôm nay âm.
    hom_truoc = [(bd - 1440, kt - 1440) for bd, kt in (khung_gio(open_hours, (thu - 1) % 7) or []) if kt > 1440]
    for bd, kt in hom_truoc + hom_nay:
        if bd <= phut < kt:
            con_lai = kt - phut
            return {
                "status": "open",
                "open_until": None if (bd, kt) == (0, 1440) else phut_sang_gio(kt),
                "closes_in_min": None if (bd, kt) == (0, 1440) else con_lai,
                "fits_stay": (bd, kt) == (0, 1440) or con_lai >= o_lai_phut,
            }
    sau = sorted(bd for bd, _ in hom_nay if bd > phut)
    return {"status": "closed", "next_open_today": phut_sang_gio(sau[0]) if sau else None, "fits_stay": False}


def mo_ta_gio(open_hours: dict[str, Any] | None, thu: int) -> str | None:
    ds = _theo_thu(open_hours, thu)
    return ", ".join(map(str, ds)) if ds else None


def muc_dong_theo_gio(popular_times: dict[str, Any] | None, thu: int) -> dict[int, int]:
    """{giờ: mức đông 0-100} của một thứ trong tuần."""
    ngay = _theo_thu(popular_times, thu) or {}
    if isinstance(ngay, dict):
        return {int(g): int(v or 0) for g, v in ngay.items() if str(g).isdigit()}
    return {}


# --- lễ hội --------------------------------------------------------------------


def gon_le_hoi(e: dict[str, Any]) -> dict[str, Any]:
    gia = e.get("price")
    return {
        "name": e.get("name"),
        "start_date": e.get("start_date"),
        "end_date": e.get("end_date") if e.get("end_date") != e.get("start_date") else None,
        "date_display": e.get("date_display"),
        "location": ", ".join(x for x in (e.get("location_name"), e.get("address_region")) if x) or None,
        "organizer": e.get("organizer"),
        "price": f"{gia:,.0f} {e.get('price_currency') or 'VND'}" if gia else None,
        "highlights": (e.get("highlights") or [])[:4] or None,
        "url": e.get("url"),
    }


# --- giá, loại chỗ ở, chất lượng rating ------------------------------------------

# Google Maps ghi giá phòng / đêm vào `summary` của chỗ ở ('827.411 ₫', dấu cách là NBSP).
_GIA_PHONG = re.compile(r"^\s*([\d.]+)\s*₫\s*$")
# Giá dưới mức này là lỗi cào (VD 'Quiri Hotel: 520 ₫'), bỏ qua thay vì coi là phòng rẻ.
GIA_PHONG_TOI_THIEU = 100_000

LOAI_CHO_O = {
    "hotel": ("khach san",),
    "resort": ("khach san nghi duong", "resort", "nha nghi duong"),
    "apartment": ("can ho", "nha can ho du lich", "cho o duoc phuc vu"),
    "villa": ("biet thu",),
    "homestay": ("luu tru nha dan", "nha o", "homestay", "nha khach", "phong cho thue", "giuong bua sang"),
    "hostel": ("nha tro", "nha nghi", "hostel"),
}
_TU_CHO_O = tuple(t for ds in LOAI_CHO_O.values() for t in ds) + ("luu tru", "nha thuyen")


def la_cho_o(category: str | None) -> bool:
    loai = f" {bo_dau(category or '')} "
    return any(f" {t} " in loai for t in _TU_CHO_O)


def loai_cho_o(category: str | None) -> str | None:
    """Phân loại chỗ ở; 'resort' xét trước 'hotel' vì 'Khách sạn nghỉ dưỡng' chứa 'khách sạn'."""
    loai = f" {bo_dau(category or '')} "
    for k in ("resort", "apartment", "villa", "homestay", "hostel", "hotel"):
        if any(f" {t} " in loai for t in LOAI_CHO_O[k]):
            return k
    return None


def gia_phong(p: dict[str, Any]) -> int | None:
    """Giá phòng / đêm (VND) của chỗ ở, None nếu không có hoặc giá vô lý."""
    if not la_cho_o(p.get("category")):
        return None
    m = _GIA_PHONG.match((p.get("summary") or "").replace("\xa0", " "))
    if not m:
        return None
    gia = int(m.group(1).replace(".", ""))
    return gia if gia >= GIA_PHONG_TOI_THIEU else None


_KHOANG_GIA = re.compile(r"^\s*(?:(trên)\s*)?([\d.,]+)\s*(?:-\s*([\d.,]+))?\s*(n|tr)?\s*₫?\s*$", re.IGNORECASE)


def khoang_gia_an(price_range: str | None) -> tuple[int, int | None] | None:
    """'1-100.000 ₫' -> (0, 100000); '100-200 N ₫' -> (100000, 200000); 'Trên 1 Tr ₫' -> (1000000, None)."""
    if not price_range:
        return None
    m = _KHOANG_GIA.match(price_range.replace("\xa0", " "))
    if not m:
        return None
    tren, a, b, don_vi = m.groups()
    he_so = {"n": 1_000, "tr": 1_000_000}.get((don_vi or "").lower(), 1)

    def so(x: str) -> float:
        return float(x.replace(".", "").replace(",", "."))

    if tren:
        return int(so(a) * he_so), None
    if b is None:
        return int(so(a) * he_so), int(so(a) * he_so)
    hi = int(so(b) * he_so)
    lo = int(so(a) * he_so)
    # '1-100.000 ₫' nghĩa là dưới 100k: số đầu không có đơn vị riêng.
    return (0 if lo < 1_000 else lo), hi


def gia_uoc_tinh(p: dict[str, Any]) -> dict[str, Any] | None:
    """Giá để so sánh: chỗ ở = giá phòng / đêm; quán = khoảng giá mỗi người."""
    gp = gia_phong(p)
    if gp:
        return {"kind": "per_night", "vnd": gp}
    kg = khoang_gia_an(p.get("price_range"))
    if kg:
        lo, hi = kg
        return {"kind": "per_person", "min_vnd": lo, "max_vnd": hi, "vnd": (lo + hi) // 2 if hi else lo}
    return None


def trong_khoang_gia(
    p: dict[str, Any], toi_da: int | None, toi_thieu: int | None, chac_chan: bool = False
) -> bool:
    """Giá ước tính (quán: giữa khoảng giá / người; chỗ ở: giá / đêm) nằm trong [toi_thieu, toi_da].

    Nơi chưa có giá được giữ lại, vì không biết là vượt ngân sách; `chac_chan` (strict_price) thì loại.
    """
    gia = gia_uoc_tinh(p)
    if gia is None:
        return not chac_chan
    if toi_da is not None and gia["vnd"] > toi_da:
        return False
    return not (toi_thieu is not None and gia["vnd"] < toi_thieu)


def _diem_den_cua(p: dict[str, Any]) -> str | None:
    return p.get("_dest") or p.get("destination")


def danh_gia_tin_duoc(p: dict[str, Any]) -> dict[str, Any] | None:
    """Sao tin được của 1 dòng địa điểm (Bayes theo tiên nghiệm của điểm đến × loại hình, thiếu
    tiên nghiệm thì Hoeffding) — xem src/services/rating_prior.py."""
    return sao_tin_duoc(p.get("rating"), p.get("review_count"), _diem_den_cua(p), p.get("category"))


def rating_hieu_chinh(p: dict[str, Any]) -> float | None:
    """Số sao tin được của 1 dòng địa điểm (chỉ con số), để 5.0 / 3 review không vượt 4.7 / 3.000."""
    d = danh_gia_tin_duoc(p)
    return d["value"] if d else None


def truong_sao_tin_duoc(p: dict[str, Any]) -> dict[str, Any]:
    """Trường gắn vào kết quả tool: `rating_adjusted` (số) + `rating_basis` (cách tính)."""
    d = danh_gia_tin_duoc(p)
    if not d:
        return {"rating_adjusted": None}
    return {"rating_adjusted": d["value"], "rating_basis": {k: v for k, v in d.items() if k != "value"}}


def ty_le_sao(reviews_per_rating: dict[str, Any] | None) -> dict[str, float] | None:
    """{'1': 0.12, ..., '5': 0.70} từ số lượng từng mức sao."""
    if not reviews_per_rating:
        return None
    dem = {str(k): int(v or 0) for k, v in reviews_per_rating.items() if str(k) in "12345"}
    tong = sum(dem.values())
    if not tong:
        return None
    return {k: round(dem.get(k, 0) / tong, 3) for k in "12345"}


# Nhóm địa điểm theo từ khoá trong `category`, so GIỮ DẤU và nguyên từ: tên loại của Google luôn có
# dấu, bỏ dấu thì 'Câu lạc bộ spa' thành 'cầu', 'Bãi chờ tàu' thành 'chợ'.
NHOM_DIA_DIEM = {
    "stay": ("khách sạn", "căn hộ", "nhà nghỉ", "homestay", "resort", "nhà ở", "nhà khách", "biệt thự",
             "lưu trú", "nhà trọ", "nhà thuyền", "phòng cho thuê", "giường & bữa sáng"),
    "relax": ("spa", "mát xa", "massage", "xoa bóp", "thẩm mỹ viện", "chăm sóc sức khỏe", "câu lạc bộ spa"),
    "nightlife": ("quán bar", "bar", "quán bia", "quán rượu", "hộp đêm", "karaoke"),
    "entertainment": ("trung tâm vui chơi", "sân chơi", "rạp chiếu phim", "bô-linh", "công viên nước",
                      "công viên giải trí", "tổ hợp thể thao", "khu trò chơi", "trung tâm trò chơi"),
    "shopping": ("trung tâm mua sắm", "trung tâm thương mại", "siêu thị", "đại siêu thị", "cửa hàng"),
    "food_drink": ("nhà hàng", "quán ăn", "quán cà phê", "cà phê", "tiệm bánh", "quán phở", "quán bún", "phở",
                   "bún", "bánh", "hải sản", "ăn nhanh", "ăn chay", "trà", "quán mì", "khu ăn uống", "quán ăn nhỏ"),
    "sightseeing": ("điểm thu hút", "chùa", "đền", "bảo tàng", "công viên", "nhà thờ", "di tích", "lăng", "làng",
                    "bãi biển", "khu vui chơi", "cầu", "hồ", "thác", "núi", "chợ", "phố đi bộ", "lâu đài", "đình",
                    "thờ cúng", "lịch sử", "điểm mốc", "thắng cảnh", "vườn", "đảo", "cáp treo", "bến du thuyền",
                    "khu cắm trại", "trang trại", "khu du lịch", "quảng trường", "tượng đài", "nhà hát", "di sản",
                    "địa điểm ngắm cảnh"),
}


def nhom_dia_diem(category: str | None) -> str | None:
    """Nhóm của một loại địa điểm Google Maps.

    Tên loại tiếng Việt đặt danh từ chính ở đầu ('Quán cà phê sân vườn', 'Phố đi bộ', 'Công viên
    nước') nên xét tiền tố dài nhất trước; không có tiền tố nào khớp mới xét từ khoá ở giữa.
    """
    loai = loai_co_ky_hieu(category) if "&" in (category or "") else " ".join(tach_tu(category))
    if not loai:
        return None
    tot = None
    for nhom, ds in NHOM_DIA_DIEM.items():
        for t in ds:
            if f"{loai} ".startswith(f"{t} ") and (tot is None or len(t) > len(tot[1])):
                tot = (nhom, t)
    if tot:
        return tot[0]
    for nhom, ds in NHOM_DIA_DIEM.items():
        if any(f" {t} " in f" {loai} " for t in ds):
            return nhom
    return None


def loai_co_ky_hieu(category: str) -> str:
    """'Giường & bữa sáng' -> 'giường & bữa sáng' (tach_tu bỏ '&', ở đây giữ lại để khớp từ khoá)."""
    return " & ".join(" ".join(tach_tu(phan)) for phan in category.split("&"))
    tot = None
    for nhom, ds in NHOM_DIA_DIEM.items():
        for t in ds:
            if f"{loai} ".startswith(f"{t} ") and (tot is None or len(t) > len(tot[1])):
                tot = (nhom, t)
    if tot:
        return tot[0]
    for nhom, ds in NHOM_DIA_DIEM.items():
        if any(f" {t} " in f" {loai} " for t in ds):
            return nhom
    return None


# --- thời điểm -----------------------------------------------------------------

_GIO_VN = ZoneInfo("Asia/Ho_Chi_Minh")
_THOI_DIEM = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{1,2})[:hH](\d{2})?(.*)")


def doc_thoi_diem(s: str | None) -> datetime | None:
    """'2026-10-10 9:00' / '09h00' / '9h' / ISO có offset -> datetime giờ Việt Nam (naive).

    Chỉ có ngày thì trả None: không biết giờ đến, đừng ngầm hiểu là 00:00.
    """
    if not s or not s.strip():
        return None
    m = _THOI_DIEM.fullmatch(s.strip())
    chuan = f"{m[1]} {int(m[2]):02d}:{m[3] or '00'}{m[4]}" if m else s.strip()
    if ":" not in chuan:
        return None
    try:
        luc = datetime.fromisoformat(chuan)
    except ValueError:
        return None
    if luc.tzinfo is not None:
        luc = luc.astimezone(_GIO_VN).replace(tzinfo=None)
    return luc


def muc_dong_luc(popular_times: dict[str, Any] | None, luc: datetime) -> int | None:
    """Mức đông (0-100) của giờ `luc` theo biểu đồ giờ đông khách, None nếu không có dữ liệu."""
    muc = muc_dong_theo_gio(popular_times, luc.weekday())
    return muc.get(luc.hour) if muc else None
