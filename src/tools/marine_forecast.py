"""Tool dự báo biển (WeatherAPI Marine): sóng, gió giật, nhiệt độ nước, thuỷ triều."""

from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.weather_client import WeatherError, weather_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._weather_common import GIO_DAI_DIEN, MO_TA_VI_TRI, goi_theo_vi_tri, gon_vi_tri, loi_thoi_tiet

logger = structlog.get_logger(__name__)

# Ngưỡng tham khảo cho tàu nhỏ, cano, tắm biển; không thay cho lệnh cấm biển của chính quyền.
_SONG_CAO = 2.0
_SONG_VUA = 1.25
_GIO_GIAT_MANH = 50.0


def _canh_bao(song: float | None, gio_giat: float | None) -> list[str]:
    cb = []
    if song is not None:
        if song >= _SONG_CAO:
            cb.append(f"sóng cao {song} m: tàu nhỏ / cano / lặn biển nên hoãn, có thể bị cấm ra đảo")
        elif song >= _SONG_VUA:
            cb.append(f"sóng {song} m: dễ say sóng, hạn chế tắm biển xa bờ")
    if gio_giat is not None and gio_giat >= _GIO_GIAT_MANH:
        cb.append(f"gió giật {gio_giat:.0f} km/h: nguy hiểm cho tàu nhỏ")
    return cb


def register_marine_forecast_tool(mcp: FastMCP):
    @mcp.tool(
        name="marine_forecast",
        title="Marine Forecast (Biển & đảo)",
        description=(
            "Dự báo biển theo ngày: độ cao sóng lớn nhất (m), sóng lừng, gió giật, nhiệt độ nước, "
            "lịch thuỷ triều (giờ nước lên / ròng), kèm `warnings` theo ngưỡng tham khảo cho tàu "
            "nhỏ, cano, tắm biển. Có các mốc giờ 6h-21h để chọn giờ ra đảo.\n\n"
            "Khi nào dùng: đi đảo (Côn Đảo, Phú Quốc, Cô Tô, Lý Sơn, Cù Lao Chàm, Nam Du), lặn "
            "ngắm san hô, tắm biển, chụp ảnh lúc nước ròng.\n"
            "Lưu ý: lệnh cấm biển / tàu ngưng chạy do chính quyền quyết định — kiểm tra thêm bằng "
            "local_news. Dữ liệu biển có thể cần gói WeatherAPI cao hơn Free.\n\n" + MO_TA_VI_TRI
        ),
        tags={"journey", "weather", "sea"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "weatherapi:marine"},
    )
    async def marine_forecast(
        location: Annotated[str | None, Field(description="Đảo / bãi biển, VD 'Côn Đảo', 'Cô Tô'.")] = None,
        lat: Annotated[float | None, Field(ge=-90, le=90, description="Vĩ độ điểm trên biển hoặc sát bờ.")] = None,
        lng: Annotated[float | None, Field(ge=-180, le=180, description="Kinh độ.")] = None,
        days: Annotated[int, Field(ge=1, le=7, description="Số ngày tới (tính cả hôm nay).")] = 3,
    ) -> dict[str, Any]:
        input_context = {"location": location, "lat": lat, "lng": lng, "days": days}
        try:
            if (lat is None) != (lng is None):
                return format_validation_error("lat/lng", input_context, "phải truyền cả hai hoặc bỏ cả hai")
            if lat is None and not (location and location.strip()):
                return format_validation_error("location", location, "cần `location` hoặc `lat`/`lng`")

            data = await goi_theo_vi_tri(lambda q: weather_client.marine(q, days), location, lat, lng)
            ngay = []
            for fd in (data.get("forecast") or {}).get("forecastday") or []:
                gio = fd.get("hour") or []
                song = max((h["sig_ht_mt"] for h in gio if isinstance(h.get("sig_ht_mt"), int | float)), default=None)
                long_ = max((h["swell_ht_mt"] for h in gio if isinstance(h.get("swell_ht_mt"), int | float)), default=None)
                giat = max((h["gust_kph"] for h in gio if isinstance(h.get("gust_kph"), int | float)), default=None)
                nuoc = [h["water_temp_c"] for h in gio if isinstance(h.get("water_temp_c"), int | float)]
                trieu = [t for nhom in (fd.get("day") or {}).get("tides") or [] for t in nhom.get("tide") or []]
                ngay.append(
                    {
                        "date": fd.get("date"),
                        "condition": ((fd.get("day") or {}).get("condition") or {}).get("text"),
                        "max_wave_m": song,
                        "max_swell_m": long_,
                        "max_gust_kph": giat,
                        "water_temp_c": round(sum(nuoc) / len(nuoc), 1) if nuoc else None,
                        "tides": [
                            {
                                "time": (t.get("tide_time") or "")[-5:],
                                "type": "nước lên" if t.get("tide_type") == "HIGH" else "nước ròng",
                                "height_m": t.get("tide_height_mt"),
                            }
                            for t in trieu
                        ],
                        "hours": [
                            {"time": h.get("time", "")[-5:], "wave_m": h.get("sig_ht_mt"), "gust_kph": h.get("gust_kph")}
                            for h in gio
                            if int(h.get("time", "00:00")[-5:-3]) in GIO_DAI_DIEN
                        ],
                        "warnings": _canh_bao(song, giat),
                    }
                )

            return drop_empty(
                {
                    "status": "success",
                    "location": gon_vi_tri(data),
                    "days": ngay,
                    "note": "Ngưỡng cảnh báo chỉ để tham khảo; lệnh cấm biển của biên phòng / cảng vụ là quyết định cuối.",
                }
            )

        except WeatherError as e:
            logger.error("marine_forecast gọi WeatherAPI thất bại", error=str(e))
            return loi_thoi_tiet(e, input_context)
        except Exception as e:
            logger.error("marine_forecast thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không lấy được dự báo biển", exception=e, input_context=input_context
            )

    return marine_forecast
