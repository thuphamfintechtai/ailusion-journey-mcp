"""Tool chất lượng không khí hiện tại và vài ngày tới (WeatherAPI, chỉ số US EPA)."""

from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.weather_client import WeatherError, weather_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._weather_common import MO_TA_VI_TRI, goi_theo_vi_tri, gon_vi_tri, loi_thoi_tiet, nhan_aqi

logger = structlog.get_logger(__name__)


def _so(x: Any) -> float | None:
    return round(x, 1) if isinstance(x, int | float) else None


def register_air_quality_tool(mcp: FastMCP):
    @mcp.tool(
        name="air_quality",
        title="Air Quality (Chất lượng không khí)",
        description=(
            "Chất lượng không khí hiện tại và theo ngày (tối đa 3 ngày tới): mức theo chỉ số US "
            "EPA (Tốt -> Nguy hại), bụi mịn PM2.5 / PM10 (µg/m³), giờ ô nhiễm nhất trong ngày và "
            "lời khuyên cho nhóm nhạy cảm (trẻ nhỏ, người già, người bệnh hô hấp).\n\n"
            "Khi nào dùng: đoàn có trẻ nhỏ / người già, đi Hà Nội hoặc thành phố lớn mùa khô "
            "(tháng 10 - 3), lên kế hoạch hoạt động ngoài trời.\n\n" + MO_TA_VI_TRI
        ),
        tags={"journey", "weather"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "weatherapi:forecast aqi=yes"},
    )
    async def air_quality(
        location: Annotated[str | None, Field(description="Tên nơi, VD 'Hà Nội'.")] = None,
        lat: Annotated[float | None, Field(ge=-90, le=90, description="Vĩ độ.")] = None,
        lng: Annotated[float | None, Field(ge=-180, le=180, description="Kinh độ.")] = None,
        days: Annotated[int, Field(ge=1, le=3, description="Số ngày tới (tính cả hôm nay).")] = 1,
    ) -> dict[str, Any]:
        input_context = {"location": location, "lat": lat, "lng": lng}
        try:
            if (lat is None) != (lng is None):
                return format_validation_error("lat/lng", input_context, "phải truyền cả hai hoặc bỏ cả hai")
            if lat is None and not (location and location.strip()):
                return format_validation_error("location", location, "cần `location` hoặc `lat`/`lng`")

            data = await goi_theo_vi_tri(
                lambda q: weather_client.forecast(q, days, aqi="yes", alerts="no"), location, lat, lng
            )
            hien_tai = (data.get("current") or {}).get("air_quality") or {}
            ngay = []
            for fd in (data.get("forecast") or {}).get("forecastday") or []:
                aq = (fd.get("day") or {}).get("air_quality") or {}
                gio = [h for h in fd.get("hour") or [] if (h.get("air_quality") or {}).get("pm2_5") is not None]
                te_nhat = max(gio, key=lambda h: h["air_quality"]["pm2_5"], default=None)
                ngay.append(
                    {
                        "date": fd.get("date"),
                        **(nhan_aqi(aq.get("us-epa-index")) or {}),
                        "pm2_5": _so(aq.get("pm2_5")),
                        "pm10": _so(aq.get("pm10")),
                        "worst_hour": te_nhat["time"][-5:] if te_nhat else None,
                        "worst_hour_pm2_5": _so(te_nhat["air_quality"]["pm2_5"]) if te_nhat else None,
                    }
                )

            return drop_empty(
                {
                    "status": "success",
                    "location": gon_vi_tri(data),
                    "current": {
                        **(nhan_aqi(hien_tai.get("us-epa-index")) or {}),
                        "pm2_5": _so(hien_tai.get("pm2_5")),
                        "pm10": _so(hien_tai.get("pm10")),
                        "updated": (data.get("current") or {}).get("last_updated"),
                    },
                    "days": ngay,
                    "note": "Số liệu mô hình, có thể lệch với trạm quan trắc thực tế.",
                }
            )

        except WeatherError as e:
            logger.error("air_quality gọi WeatherAPI thất bại", error=str(e))
            return loi_thoi_tiet(e, input_context)
        except Exception as e:
            logger.error("air_quality thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không lấy được chất lượng không khí", exception=e, input_context=input_context
            )

    return air_quality
