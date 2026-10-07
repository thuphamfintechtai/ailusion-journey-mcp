"""Tool dự báo thời tiết theo khoảng ngày (WeatherAPI): tự chọn forecast / future / history."""

import asyncio
from datetime import date, timedelta
from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.travel_db import travel_db
from ..services.weather_client import WeatherError, WeatherPlanError, weather_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._db_common import fallback_db
from ._serpapi_common import doc_ngay
from ._weather_common import (
    GIO_DAI_DIEN,
    MO_TA_VI_TRI,
    canh_bao_ngay,
    gio_24,
    goi_theo_vi_tri,
    gon_vi_tri,
    hom_nay_vn,
    loi_thoi_tiet,
    nhan_aqi,
)

logger = structlog.get_logger(__name__)

_TOI_DA_NGAY = 14
_NGAY_DU_BAO = 14  # forecast.json trả tối đa 14 ngày (gói Free chỉ 3)
_XA_NHAT = 300  # future.json: 14-300 ngày tới
_TOI_DA_GOI_XA = 7  # mỗi ngày xa / quá khứ là 1 lượt gọi riêng


def _gon_ngay(fd: dict[str, Any], nguon: str, chi_tiet_gio: bool) -> dict[str, Any]:
    d = fd.get("day") or {}
    astro = fd.get("astro") or {}
    aq = (d.get("air_quality") or {}).get("us-epa-index")
    return {
        "date": fd.get("date"),
        "source": nguon,
        "condition": (d.get("condition") or {}).get("text"),
        "temp_min_c": d.get("mintemp_c"),
        "temp_max_c": d.get("maxtemp_c"),
        "chance_of_rain_pct": d.get("daily_chance_of_rain"),
        "precip_mm": d.get("totalprecip_mm"),
        "max_wind_kph": d.get("maxwind_kph"),
        "humidity_pct": d.get("avghumidity"),
        "uv": d.get("uv"),
        "visibility_km": d.get("avgvis_km"),
        "sunrise": gio_24(astro.get("sunrise")),
        "sunset": gio_24(astro.get("sunset")),
        "air_quality": nhan_aqi(aq),
        "warnings": canh_bao_ngay(d),
        "hours": [
            {
                "time": h.get("time", "")[-5:],
                "temp_c": h.get("temp_c"),
                "feels_like_c": h.get("feelslike_c"),
                "chance_of_rain_pct": h.get("chance_of_rain"),
                "condition": (h.get("condition") or {}).get("text"),
            }
            for h in fd.get("hour") or []
            if int(h.get("time", "00:00")[-5:-3]) in GIO_DAI_DIEN
        ]
        if chi_tiet_gio
        else None,
    }


async def _tu_db(
    location: str | None, lat: float | None, lng: float | None, bd: date, kt: date
) -> dict[str, Any] | None:
    """Dự báo đã lưu (chỉ các điểm đến phổ biến, 7 ngày kể từ lần cào gần nhất)."""
    kq = await travel_db.weather(location, lat, lng, bd, kt)
    if not kq or not kq["days"]:
        return None
    ht = kq["current"]
    co = {str(d.get("forecast_date")) for d in kq["days"]}
    thieu = [(bd + timedelta(days=i)).isoformat() for i in range((kt - bd).days + 1)]
    thieu = [d for d in thieu if d not in co]
    return {
        "status": "success",
        "location": {"name": ht.get("destination"), "country": ht.get("country"), "lat": ht.get("lat"), "lon": ht.get("lon")},
        "current": {
            "observed_at": ht.get("observed_at"),
            "temp_c": ht.get("temperature"),
            "humidity_pct": ht.get("humidity"),
            "wind_kph": ht.get("wind_speed"),
            "condition": ht.get("weather_desc"),
        },
        "days": [
            {
                "date": d.get("forecast_date"),
                "source": "cache_db",
                "condition": d.get("weather_desc"),
                "temp_min_c": d.get("temp_min"),
                "temp_max_c": d.get("temp_max"),
                "chance_of_rain_pct": d.get("precipitation_prob"),
            }
            for d in kq["days"]
        ],
        "notes": [
            "Dữ liệu đã lưu chỉ có nhiệt độ, khả năng mưa và mô tả; không có UV, gió, cảnh báo, giờ mặt trời.",
            *([f"Không có dự báo đã lưu cho: {', '.join(thieu)}."] if thieu else []),
        ],
        "data_as_of": str(kq["days"][0].get("fetched_at") or "")[:10] or None,
    }


def register_weather_forecast_tool(mcp: FastMCP):
    @mcp.tool(
        name="weather_forecast",
        title="Weather Forecast (Dự báo thời tiết)",
        description=(
            "Thời tiết theo ngày cho một khoảng ngày: nhiệt độ thấp/cao, khả năng mưa, lượng mưa, "
            "gió, UV, tầm nhìn, giờ mặt trời mọc/lặn, chất lượng không khí và `warnings` (nắng nóng, "
            "rét đậm, mưa to, UV cao, gió mạnh, tầm nhìn kém theo ngưỡng của KTTV Việt Nam). Kèm "
            "`alerts` thiên tai chính thức nếu có.\n\n"
            "Tự chọn nguồn theo ngày: trong 14 ngày tới = dự báo; 14-300 ngày tới = dự báo xa "
            "(độ tin cậy thấp, chỉ để tham khảo); ngày đã qua = số liệu thực tế. `source` của "
            "từng ngày cho biết loại nào.\n"
            "Khi nào dùng: chọn ngày đi, chuẩn bị đồ, quyết định hoạt động ngoài trời / trong nhà, "
            "cảnh báo đường đèo khi mưa.\n\n" + MO_TA_VI_TRI + f" Khoảng tối đa {_TOI_DA_NGAY} ngày. "
            "Khi WeatherAPI lỗi / hết quota tool tự trả dự báo đã lưu của các điểm đến phổ biến "
            "(`data_source`='ailusion_db', ít trường hơn)."
        ),
        tags={"journey", "weather"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "weatherapi:forecast,future,history"},
    )
    async def weather_forecast(
        start_date: Annotated[str, Field(description="Ngày đầu, YYYY-MM-DD.")],
        end_date: Annotated[str | None, Field(description="Ngày cuối, YYYY-MM-DD. Bỏ trống = chỉ start_date.")] = None,
        location: Annotated[str | None, Field(description="Tên nơi, VD 'Đà Lạt'.")] = None,
        lat: Annotated[float | None, Field(ge=-90, le=90, description="Vĩ độ.")] = None,
        lng: Annotated[float | None, Field(ge=-180, le=180, description="Kinh độ.")] = None,
        hourly: Annotated[
            bool, Field(description="Kèm thời tiết các mốc 6h, 9h, 12h, 15h, 18h, 21h mỗi ngày.")
        ] = False,
    ) -> dict[str, Any]:
        input_context = {"location": location, "lat": lat, "lng": lng, "start_date": start_date, "end_date": end_date}
        try:
            if (lat is None) != (lng is None):
                return format_validation_error("lat/lng", input_context, "phải truyền cả hai hoặc bỏ cả hai")
            if lat is None and not (location and location.strip()):
                return format_validation_error("location", location, "cần `location` hoặc `lat`/`lng`")
            try:
                bd = doc_ngay(start_date, "start_date")
                kt = doc_ngay(end_date, "end_date") if end_date else bd
            except ValueError as e:
                return format_mcp_tool_error_response(str(e), error_code=-2, input_context=input_context)
            if kt < bd:
                return format_validation_error("end_date", end_date, "trước start_date")
            if (kt - bd).days + 1 > _TOI_DA_NGAY:
                return format_validation_error("end_date", end_date, f"khoảng ngày vượt {_TOI_DA_NGAY} ngày")
            hom_nay = hom_nay_vn()
            if kt > hom_nay + timedelta(days=_XA_NHAT):
                return format_validation_error(
                    "end_date", end_date, f"chỉ dự báo được tới {_XA_NHAT} ngày tới; xa hơn dùng vn_calendar để biết mùa"
                )

            cac_ngay = [bd + timedelta(days=i) for i in range((kt - bd).days + 1)]
            gan = [d for d in cac_ngay if hom_nay <= d < hom_nay + timedelta(days=_NGAY_DU_BAO)]
            xa = [d for d in cac_ngay if d >= hom_nay + timedelta(days=_NGAY_DU_BAO)]
            qua = [d for d in cac_ngay if d < hom_nay]
            if len(xa) + len(qua) > _TOI_DA_GOI_XA:
                return format_validation_error(
                    "end_date", end_date, f"tối đa {_TOI_DA_GOI_XA} ngày ngoài khoảng 14 ngày tới mỗi lần gọi"
                )

            theo_ngay: dict[str, dict[str, Any]] = {}
            vi_tri: dict[str, Any] = {}  # response đầy đủ đầu tiên, để lấy location
            alerts: list[dict[str, Any]] = []
            ghi_chu: list[str] = []

            if gan:
                so_ngay = (max(gan) - hom_nay).days + 1
                data = await goi_theo_vi_tri(
                    lambda q: weather_client.forecast(q, so_ngay, aqi="yes", alerts="yes"), location, lat, lng
                )
                vi_tri = data
                for fd in (data.get("forecast") or {}).get("forecastday") or []:
                    theo_ngay[fd["date"]] = _gon_ngay(fd, "forecast", hourly)
                tra_ve = len((data.get("forecast") or {}).get("forecastday") or [])
                if tra_ve < so_ngay:
                    ghi_chu.append(f"Gói WeatherAPI hiện tại chỉ trả {tra_ve} ngày dự báo; các ngày sau chưa có.")
                alerts = [
                    {
                        "headline": a.get("headline"),
                        "event": a.get("event"),
                        "severity": a.get("severity"),
                        "effective": a.get("effective"),
                        "expires": a.get("expires"),
                        "description": (a.get("desc") or "")[:500] or None,
                    }
                    for a in (data.get("alerts") or {}).get("alert") or []
                ]

            async def mot_ngay(d: date, loai: str) -> tuple[date, str, dict[str, Any] | Exception]:
                lam = weather_client.future if loai == "future" else weather_client.history
                try:
                    return d, loai, await goi_theo_vi_tri(lambda q: lam(q, d.isoformat()), location, lat, lng)
                except WeatherPlanError as e:
                    return d, loai, e

            ket_qua = await asyncio.gather(*[mot_ngay(d, "future") for d in xa], *[mot_ngay(d, "history") for d in qua])
            bi_chan = []
            for d, loai, r in ket_qua:
                if isinstance(r, Exception):
                    bi_chan.append(d.isoformat())
                    continue
                vi_tri = vi_tri or r
                for fd in (r.get("forecast") or {}).get("forecastday") or []:
                    theo_ngay[fd["date"]] = _gon_ngay(fd, loai, hourly)
            if bi_chan:
                ghi_chu.append(
                    "Gói WeatherAPI hiện tại không có dữ liệu cho: " + ", ".join(bi_chan)
                    + " (dự báo xa / lịch sử cần gói cao hơn)."
                )
            if any(n.get("source") == "future" for n in theo_ngay.values()):
                ghi_chu.append("Ngày có source=future là dự báo xa, độ tin cậy thấp, chỉ coi như tham khảo khí hậu.")

            return drop_empty(
                {
                    "status": "success",
                    "location": gon_vi_tri(vi_tri) if vi_tri else None,
                    "days": [theo_ngay[d.isoformat()] for d in cac_ngay if d.isoformat() in theo_ngay],
                    "alerts": alerts or None,
                    "notes": ghi_chu or None,
                }
            )

        except WeatherError as e:
            logger.error("weather_forecast gọi WeatherAPI thất bại", error=str(e))
            return await fallback_db(
                "weather_forecast", loi_thoi_tiet(e, input_context), lambda: _tu_db(location, lat, lng, bd, kt)
            )
        except Exception as e:
            logger.error("weather_forecast thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không lấy được thời tiết", exception=e, input_context=input_context
            )

    return weather_forecast
