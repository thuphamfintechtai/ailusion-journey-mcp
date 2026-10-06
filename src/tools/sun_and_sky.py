"""Tool giờ mặt trời / mặt trăng và gợi ý săn mây, ngắm bình minh, ngắm sao (WeatherAPI)."""

from datetime import timedelta
from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.weather_client import WeatherError, weather_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._serpapi_common import doc_ngay
from ._weather_common import (
    MO_TA_VI_TRI,
    gio_24,
    goi_theo_vi_tri,
    gon_vi_tri,
    hom_nay_vn,
    loi_thoi_tiet,
    ten_trang,
)

logger = structlog.get_logger(__name__)

_GIO_SANG = range(4, 8)  # 4h-7h: giờ săn mây, ngắm bình minh
_GIO_DEM = (20, 21, 22, 23)


def _cong_gio(hhmm: str | None, phut: int) -> str | None:
    if not hhmm or ":" not in hhmm:
        return None
    h, m = map(int, hhmm.split(":"))
    tong = (h * 60 + m + phut) % (24 * 60)
    return f"{tong // 60:02d}:{tong % 60:02d}"


def _goi_y(sang: list[dict[str, Any]], dem: list[dict[str, Any]], do_sang_trang: int | None) -> dict[str, Any]:
    """Ước lượng theo kinh nghiệm, không phải dự báo chuyên ngành."""
    goi_y: dict[str, Any] = {}
    if sang:
        am = max(h.get("humidity") or 0 for h in sang)
        gio_max = max(h.get("wind_kph") or 0 for h in sang)
        may = sum(h.get("cloud") or 0 for h in sang) / len(sang)
        mua = max(h.get("chance_of_rain") or 0 for h in sang)
        if am >= 90 and gio_max <= 10 and mua < 50:
            goi_y["cloud_hunting"] = "khá thuận: độ ẩm cao, gió nhẹ sáng sớm dễ tạo sương / mây thấp (cần đứng chỗ cao)"
        elif mua >= 60:
            goi_y["cloud_hunting"] = "kém: sáng sớm có thể mưa"
        else:
            goi_y["cloud_hunting"] = "không chắc: độ ẩm hoặc gió chưa lý tưởng"
        goi_y["sunrise_view"] = "dễ thấy" if may <= 40 else ("có thể bị mây che" if may <= 75 else "khó thấy, trời nhiều mây")
    if dem:
        may_dem = sum(h.get("cloud") or 0 for h in dem) / len(dem)
        if may_dem <= 30 and (do_sang_trang or 0) <= 30:
            goi_y["stargazing"] = "tốt: trời quang, trăng mờ"
        elif may_dem <= 30:
            goi_y["stargazing"] = "trung bình: trời quang nhưng trăng sáng"
        else:
            goi_y["stargazing"] = "kém: nhiều mây ban đêm"
    return goi_y


def register_sun_and_sky_tool(mcp: FastMCP):
    @mcp.tool(
        name="sun_and_sky",
        title="Sun & Sky (Bình minh, hoàng hôn & săn mây)",
        description=(
            "Giờ mặt trời mọc / lặn, giờ vàng chụp ảnh, giờ trăng mọc / lặn, pha trăng của một "
            "ngày. Nếu ngày trong 14 ngày tới còn kèm `suggestions` ước lượng: săn mây sáng sớm, "
            "khả năng thấy bình minh, ngắm sao — dựa trên độ ẩm, gió, mây theo giờ.\n\n"
            "Khi nào dùng: Đà Lạt, Tà Xùa, Y Tý, Sa Pa, Bà Nà (săn mây); lịch chụp ảnh bình "
            "minh / hoàng hôn; cắm trại ngắm sao. `suggestions` là ước lượng kinh nghiệm, "
            "nói rõ với người dùng là không chắc chắn.\n\n" + MO_TA_VI_TRI
        ),
        tags={"journey", "weather"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "weatherapi:astronomy + forecast"},
    )
    async def sun_and_sky(
        date: Annotated[str, Field(description="Ngày, YYYY-MM-DD.")],
        location: Annotated[str | None, Field(description="Tên nơi, VD 'Tà Xùa', 'Đà Lạt'.")] = None,
        lat: Annotated[float | None, Field(ge=-90, le=90, description="Vĩ độ.")] = None,
        lng: Annotated[float | None, Field(ge=-180, le=180, description="Kinh độ.")] = None,
    ) -> dict[str, Any]:
        input_context = {"date": date, "location": location, "lat": lat, "lng": lng}
        try:
            if (lat is None) != (lng is None):
                return format_validation_error("lat/lng", input_context, "phải truyền cả hai hoặc bỏ cả hai")
            if lat is None and not (location and location.strip()):
                return format_validation_error("location", location, "cần `location` hoặc `lat`/`lng`")
            try:
                ngay = doc_ngay(date, "date")
            except ValueError as e:
                return format_mcp_tool_error_response(str(e), error_code=-2, input_context=input_context)

            data = await goi_theo_vi_tri(
                lambda q: weather_client.astronomy(q, ngay.isoformat()), location, lat, lng
            )
            astro = (data.get("astronomy") or {}).get("astro") or {}
            moc = gio_24(astro.get("sunrise"))
            lan = gio_24(astro.get("sunset"))
            sang_trang = astro.get("moon_illumination")
            sang_trang = int(sang_trang) if isinstance(sang_trang, int | float | str) and str(sang_trang).isdigit() else None

            goi_y: dict[str, Any] = {}
            hom_nay = hom_nay_vn()
            if hom_nay <= ngay < hom_nay + timedelta(days=14):
                so_ngay = (ngay - hom_nay).days + 1
                du_bao = await goi_theo_vi_tri(
                    lambda q: weather_client.forecast(q, so_ngay, aqi="no", alerts="no"), location, lat, lng
                )
                gio = []
                for fd in (du_bao.get("forecast") or {}).get("forecastday") or []:
                    if fd.get("date") == ngay.isoformat():
                        gio = fd.get("hour") or []
                sang = [h for h in gio if int(h.get("time", "00:00")[-5:-3]) in _GIO_SANG]
                dem = [h for h in gio if int(h.get("time", "00:00")[-5:-3]) in _GIO_DEM]
                goi_y = _goi_y(sang, dem, sang_trang)

            return drop_empty(
                {
                    "status": "success",
                    "location": gon_vi_tri(data),
                    "date": ngay.isoformat(),
                    "sunrise": moc,
                    "sunset": lan,
                    "golden_hour_morning": f"{moc} - {_cong_gio(moc, 60)}" if moc else None,
                    "golden_hour_evening": f"{_cong_gio(lan, -60)} - {lan}" if lan else None,
                    "moonrise": gio_24(astro.get("moonrise")),
                    "moonset": gio_24(astro.get("moonset")),
                    "moon_phase": ten_trang(astro.get("moon_phase")),
                    "moon_illumination_pct": sang_trang,
                    "suggestions": goi_y or None,
                    "note": "suggestions là ước lượng theo kinh nghiệm, không phải dự báo chuyên ngành."
                    if goi_y
                    else "Chỉ có gợi ý săn mây / ngắm sao cho ngày trong 14 ngày tới.",
                }
            )

        except WeatherError as e:
            logger.error("sun_and_sky gọi WeatherAPI thất bại", error=str(e))
            return loi_thoi_tiet(e, input_context)
        except Exception as e:
            logger.error("sun_and_sky thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không lấy được giờ mặt trời", exception=e, input_context=input_context
            )

    return sun_and_sky
