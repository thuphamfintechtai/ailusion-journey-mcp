"""Tool cảnh báo thiên tai gần Việt Nam (GDACS - hệ thống cảnh báo thiên tai toàn cầu của LHQ / EU)."""

import asyncio
import math
from datetime import timedelta
from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.open_data_client import OpenDataError, open_data_client
from ..services.vietmap_client import VietmapError, vietmap_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._weather_common import hom_nay_vn

logger = structlog.get_logger(__name__)

# Khung bao Việt Nam + Biển Đông (bão hình thành ngoài khơi vẫn cần theo dõi).
_LAT, _LNG = (4.0, 24.5), (100.0, 118.0)
_LOAI = {
    "TC": "bão / áp thấp nhiệt đới",
    "FL": "lũ lụt",
    "EQ": "động đất",
    "VO": "núi lửa",
    "DR": "hạn hán",
    "WF": "cháy rừng",
    "TS": "sóng thần",
}
_MUC = {"Red": ("đỏ", 3), "Orange": ("cam", 2), "Green": ("xanh", 1)}


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def _gan_viet_nam(f: dict[str, Any]) -> bool:
    p = f.get("properties") or {}
    nuoc = (p.get("country") or "").lower()
    if "viet nam" in nuoc or "vietnam" in nuoc:
        return True
    g = f.get("geometry") or {}
    if g.get("type") == "Point":
        lng, lat = g["coordinates"][:2]
        return _LAT[0] <= lat <= _LAT[1] and _LNG[0] <= lng <= _LNG[1]
    return False


def register_disaster_alerts_tool(mcp: FastMCP):
    @mcp.tool(
        name="disaster_alerts",
        title="Disaster Alerts (Cảnh báo thiên tai)",
        description=(
            "Cảnh báo thiên tai đang / vừa xảy ra ở Việt Nam và Biển Đông từ GDACS (hệ thống "
            "cảnh báo toàn cầu của Liên Hợp Quốc và Ủy ban châu Âu): bão / áp thấp nhiệt đới, lũ "
            "lụt, động đất, cháy rừng... với mức cảnh báo xanh / cam / đỏ, vị trí, khoảng cách tới "
            "điểm đến, link báo cáo.\n\n"
            "Khi nào dùng: trước chuyến đi biển / đảo / miền Trung mùa bão (tháng 8-12), khi "
            "weather_forecast báo gió mạnh, mưa to. GDACS chỉ có sự kiện quy mô lớn; tin cấm đường, "
            "sạt lở cục bộ, công điện của Ban Chỉ đạo thì dùng local_news.\n"
            "Không tốn quota."
        ),
        tags={"journey", "weather", "safety"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "gdacs"},
    )
    async def disaster_alerts(
        days: Annotated[int, Field(ge=1, le=60, description="Xem sự kiện trong bao nhiêu ngày gần đây.")] = 14,
        location: Annotated[
            str | None, Field(description="Điểm đến để tính khoảng cách, VD 'Côn Đảo', 'Hội An'.")
        ] = None,
        lat: Annotated[float | None, Field(ge=-90, le=90, description="Vĩ độ điểm đến.")] = None,
        lng: Annotated[float | None, Field(ge=-180, le=180, description="Kinh độ điểm đến.")] = None,
        min_level: Annotated[
            int, Field(ge=1, le=3, description="Mức tối thiểu: 1 xanh, 2 cam, 3 đỏ.")
        ] = 1,
    ) -> dict[str, Any]:
        input_context = {"days": days, "location": location, "lat": lat, "lng": lng}
        try:
            if (lat is None) != (lng is None):
                return format_validation_error("lat/lng", input_context, "phải truyền cả hai hoặc bỏ cả hai")
            dich = (lat, lng) if lat is not None else None
            ghi_chu = []
            if dich is None and location:
                try:
                    d = await vietmap_client.resolve_area(location)
                    dich = (d.lat, d.lng)
                except VietmapError as e:
                    ghi_chu.append(f"Không xác định được '{location}' để tính khoảng cách: {e}")

            den = hom_nay_vn()
            tu = den - timedelta(days=days)
            chung = {"fromdate": tu.isoformat(), "todate": den.isoformat(), "alertlevel": "green;orange;red"}
            bao, trong_nuoc = await asyncio.gather(
                open_data_client.gdacs_events(eventlist="TC", **chung),
                open_data_client.gdacs_events(country="Viet Nam", **chung),
            )

            gom: dict[tuple, dict[str, Any]] = {}
            for f in [*bao, *trong_nuoc]:
                if not _gan_viet_nam(f):
                    continue
                p = f.get("properties") or {}
                muc_ten, muc_so = _MUC.get(p.get("alertlevel") or "", ("không rõ", 0))
                if muc_so < min_level:
                    continue
                g = f.get("geometry") or {}
                vi_tri = (g["coordinates"][1], g["coordinates"][0]) if g.get("type") == "Point" else None
                gom[(p.get("eventtype"), p.get("eventid"))] = {
                    "type": _LOAI.get(p.get("eventtype"), p.get("eventtype")),
                    "name": p.get("name"),
                    "alert_level": muc_ten,
                    "is_current": str(p.get("iscurrent")).lower() == "true",
                    "from": (p.get("fromdate") or "")[:10],
                    "to": (p.get("todate") or "")[:10],
                    "severity": (p.get("severitydata") or {}).get("severitytext"),
                    "countries": p.get("country"),
                    "lat": vi_tri[0] if vi_tri else None,
                    "lng": vi_tri[1] if vi_tri else None,
                    "distance_km": round(_km(dich, vi_tri)) if dich and vi_tri else None,
                    "report_url": (p.get("url") or {}).get("report"),
                    "_muc": muc_so,
                }
            ds = sorted(gom.values(), key=lambda e: (not e["is_current"], -e["_muc"], e.get("distance_km") or 1e9))
            for e in ds:
                e.pop("_muc")

            return drop_empty(
                {
                    "status": "success",
                    "since": tu.isoformat(),
                    "events": ds,
                    "notes": ghi_chu or None,
                    "note": None
                    if ds
                    else f"Không có cảnh báo thiên tai quy mô lớn gần Việt Nam trong {days} ngày qua (GDACS). "
                    "Vẫn nên kiểm tra local_news cho sạt lở, cấm đường cục bộ.",
                }
            )

        except OpenDataError as e:
            logger.error("disaster_alerts gọi GDACS thất bại", error=str(e))
            return format_mcp_tool_error_response(str(e), exception=e, error_code=-3, input_context=input_context)
        except Exception as e:
            logger.error("disaster_alerts thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response("Không lấy được cảnh báo thiên tai", exception=e, input_context=input_context)

    return disaster_alerts
