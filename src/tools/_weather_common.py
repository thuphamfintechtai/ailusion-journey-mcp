"""Phần dùng chung cho các tool thời tiết. File bắt đầu bằng `_` nên không bị đăng ký thành tool."""

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from ..services.vietmap_client import VietmapError, vietmap_client
from ..services.weather_client import WeatherError, WeatherNotFound, WeatherPlanError
from ..utils.error_handler import format_mcp_tool_error_response
from ..utils.text import bo_dau
from ._serpapi_common import MA_LOI_HET_QUOTA

logger = structlog.get_logger(__name__)

GIO_VN = ZoneInfo("Asia/Ho_Chi_Minh")
MO_TA_VI_TRI = (
    "Vị trí: `location` là tên nơi ở Việt Nam (VD 'Đà Lạt', 'Sa Pa', 'Côn Đảo'); tên trùng "
    "giữa nhiều tỉnh thì ghi kèm tỉnh sau dấu phẩy (VD 'Cô Tô, Quảng Ninh'). Hoặc truyền "
    "`lat` + `lng` (VD toạ độ lấy từ find_nearby / geocode_vn). Kiểm tra `location.name` "
    "trong kết quả để chắc đúng nơi."
)
# Mốc giờ đại diện trong ngày để không trả cả 24 giờ.
GIO_DAI_DIEN = (6, 9, 12, 15, 18, 21)

# us-epa-index của WeatherAPI (1-6)
_AQI = {
    1: ("Tốt", None),
    2: ("Trung bình", "Người rất nhạy cảm nên hạn chế vận động mạnh ngoài trời lâu."),
    3: ("Kém cho nhóm nhạy cảm", "Trẻ nhỏ, người già, người bệnh hô hấp nên hạn chế ở ngoài trời lâu, cân nhắc khẩu trang."),
    4: ("Kém", "Mọi người nên hạn chế hoạt động ngoài trời; trẻ nhỏ, người già nên ở trong nhà."),
    5: ("Rất kém", "Tránh hoạt động ngoài trời, đeo khẩu trang lọc bụi mịn khi ra ngoài."),
    6: ("Nguy hại", "Ở trong nhà, đóng cửa; đổi lịch các hoạt động ngoài trời."),
}

_TRANG = {
    "New Moon": "Trăng non",
    "Waxing Crescent": "Trăng lưỡi liềm đầu tháng",
    "First Quarter": "Trăng bán nguyệt đầu tháng",
    "Waxing Gibbous": "Trăng gần tròn đầu tháng",
    "Full Moon": "Trăng tròn",
    "Waning Gibbous": "Trăng gần tròn cuối tháng",
    "Last Quarter": "Trăng bán nguyệt cuối tháng",
    "Waning Crescent": "Trăng lưỡi liềm cuối tháng",
}


def hom_nay_vn() -> date:
    return datetime.now(GIO_VN).date()


def gio_24(s: str | None) -> str | None:
    """'05:59 AM' -> '05:59'; 'No sunrise' giữ nguyên."""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%I:%M %p").strftime("%H:%M")
    except ValueError:
        return s


def ten_trang(phase: str | None) -> str | None:
    return _TRANG.get(phase or "", phase)


def nhan_aqi(chi_so: Any) -> dict[str, Any] | None:
    if not isinstance(chi_so, int | float) or int(chi_so) not in _AQI:
        return None
    nhan, loi_khuyen = _AQI[int(chi_so)]
    return {"us_epa_index": int(chi_so), "level": nhan, "advice": loi_khuyen}


def canh_bao_ngay(day: dict[str, Any]) -> list[str]:
    """Cảnh báo theo ngưỡng của Trung tâm Dự báo KTTV quốc gia (nắng nóng, rét) và WHO (UV)."""
    cb: list[str] = []
    tmax, tavg = day.get("maxtemp_c"), day.get("avgtemp_c")
    if isinstance(tmax, int | float):
        if tmax >= 39:
            cb.append("nắng nóng đặc biệt gay gắt (>=39°C)")
        elif tmax >= 37:
            cb.append("nắng nóng gay gắt (>=37°C)")
        elif tmax >= 35:
            cb.append("nắng nóng (>=35°C)")
    if isinstance(tavg, int | float):
        if tavg <= 13:
            cb.append("rét hại (nhiệt độ trung bình <=13°C)")
        elif tavg <= 15:
            cb.append("rét đậm (nhiệt độ trung bình <=15°C)")
    mua = day.get("totalprecip_mm") or 0
    if mua >= 50:
        cb.append(f"mưa rất to ({mua:.0f} mm)")
    elif mua >= 20 or (day.get("daily_chance_of_rain") or 0) >= 80:
        cb.append("mưa nhiều, nên có phương án trong nhà")
    uv = day.get("uv") or 0
    if uv >= 11:
        cb.append("UV cực đại (>=11), tránh nắng 10h-15h")
    elif uv >= 8:
        cb.append("UV rất cao (>=8), che chắn kỹ")
    gio = day.get("maxwind_kph") or 0
    if gio >= 39:
        cb.append(f"gió mạnh ({gio:.0f} km/h, từ cấp 6)")
    tam_nhin = day.get("avgvis_km")
    if isinstance(tam_nhin, int | float) and tam_nhin < 2:
        cb.append(f"tầm nhìn kém ({tam_nhin} km), cẩn thận đường đèo")
    return cb


async def goi_theo_vi_tri(
    lam: Callable[[str], Awaitable[dict[str, Any]]],
    location: str | None,
    lat: float | None,
    lng: float | None,
) -> dict[str, Any]:
    """Gọi WeatherAPI theo toạ độ.

    Tên địa danh được Vietmap đổi ra toạ độ trước: thử thật (2026-10) bộ tra tên của
    WeatherAPI nhầm địa danh nhỏ ở Việt Nam ('Côn Đảo' -> 'Con' ở Bến Tre, 'Tà Xùa' ->
    'Ban Xua'). Chỉ khi chưa cấu hình Vietmap hoặc Vietmap không thấy mới gửi tên
    (bỏ dấu) cho WeatherAPI. Kết quả có thêm khoá `_resolved` để tool hiện đúng tên.
    """
    if lat is not None and lng is not None:
        return await lam(f"{lat},{lng}")
    ten = (location or "").strip()
    if vietmap_client.api_keys:
        try:
            diem = await vietmap_client.resolve_area(ten)
        except VietmapError as e:
            if e.code in ("AMBIGUOUS", "NOT_FOUND"):
                # Không đoán bằng tên qua WeatherAPI: tra nhầm tỉnh còn tệ hơn hỏi lại.
                raise WeatherNotFound(str(e), 1006) from None
            logger.warning("Vietmap lỗi, dùng tên cho WeatherAPI", dia_diem=ten, error=str(e))
        else:
            data = await lam(diem.latlng)
            # Bản sao: `data` có thể là object đang nằm trong cache của WeatherClient.
            return {**data, "_resolved": {"label": diem.label, "lat": diem.lat, "lng": diem.lng}}
    return await lam(bo_dau(ten))


def gon_vi_tri(data: dict[str, Any]) -> dict[str, Any]:
    """Vị trí hiển thị: ưu tiên tên Vietmap (đúng địa danh VN), toạ độ thật đã dùng."""
    loc = data.get("location") or {}
    da_doi = data.get("_resolved") or {}
    return {
        "name": da_doi.get("label") or loc.get("name"),
        "region": None if da_doi else (loc.get("region") or None),
        "country": loc.get("country"),
        "lat": da_doi.get("lat", loc.get("lat")),
        "lng": da_doi.get("lng", loc.get("lon")),
    }


def loi_thoi_tiet(e: WeatherError, input_context: dict[str, Any]) -> dict[str, Any]:
    if isinstance(e, WeatherNotFound):
        chi_tiet = str(e) if "trùng tên" in str(e) or "ghi kèm tỉnh" in str(e) else "thử tên khác"
        return format_mcp_tool_error_response(
            f"Không xác định được địa điểm: {chi_tiet}. Có thể truyền lat/lng.",
            exception=e,
            error_code=-2,
            input_context=input_context,
        )
    if isinstance(e, WeatherPlanError):
        return format_mcp_tool_error_response(
            "Gói WeatherAPI hiện tại không hỗ trợ dữ liệu này (thường là dự báo xa / dữ liệu biển).",
            exception=e,
            error_code=-5,
            input_context=input_context,
        )
    if e.het_key:
        return format_mcp_tool_error_response(
            "Tạm thời hết lượt tra thời tiết (WeatherAPI). Không gọi lại ngay; báo người dùng dữ liệu thời tiết chưa sẵn sàng.",
            exception=e,
            error_code=MA_LOI_HET_QUOTA,
            input_context=input_context,
        )
    return format_mcp_tool_error_response(
        f"Không lấy được dữ liệu thời tiết: {e}", exception=e, error_code=-3, input_context=input_context
    )
