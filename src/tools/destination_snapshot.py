"""Tool tổng quan nhanh một điểm đến từ dữ liệu đã lưu: thời tiết, địa điểm nổi bật, lễ hội."""

import asyncio
from datetime import timedelta
from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.travel_db import TravelDbError, thuoc_diem_den, travel_db
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._db_common import NGUON_DB, diem_pho_bien, gon_le_hoi, goi_y_diem_den, loi_db, moi_nhat, nhom_dia_diem
from ._serpapi_common import doc_ngay
from ._weather_common import hom_nay_vn

logger = structlog.get_logger(__name__)

def register_destination_snapshot_tool(mcp: FastMCP):
    @mcp.tool(
        name="destination_snapshot",
        title="Destination Snapshot (Tổng quan điểm đến)",
        description=(
            "Tổng quan nhanh một điểm đến trong một lần gọi, từ dữ liệu đã lưu (không tốn quota): dự báo "
            "thời tiết các ngày tới, top địa điểm theo nhóm (sightseeing = tham quan, food_drink = ăn "
            "uống, stay = lưu trú, relax = spa), và lễ hội diễn ra trong khoảng ngày.\n\n"
            "Khi nào dùng: bước đầu khi người dùng hỏi 'đi X có gì', để có khung trước khi gọi các tool "
            "chi tiết (find_places, weather_forecast, find_festivals).\n"
            "Thời tiết chỉ có cho các điểm đến phổ biến và tối đa 7 ngày kể từ lần cập nhật gần nhất; "
            "phần nào không có dữ liệu sẽ vắng mặt trong kết quả."
        ),
        tags={"journey", "db", "overview"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "postgres:gmaps_place,event,weather_forecast"},
    )
    async def destination_snapshot(
        destination: Annotated[str, Field(description="Điểm đến, VD 'Hà Nội', 'Huế', 'Đà Lạt'.")],
        start_date: Annotated[str | None, Field(description="Ngày bắt đầu, YYYY-MM-DD. Bỏ trống = hôm nay.")] = None,
        days: Annotated[int, Field(ge=1, le=14, description="Số ngày của chuyến đi.")] = 3,
        per_group: Annotated[int, Field(ge=1, le=10, description="Số địa điểm mỗi nhóm.")] = 5,
    ) -> dict[str, Any]:
        input_context = {"destination": destination, "start_date": start_date, "days": days}
        try:
            if not destination.strip():
                return format_validation_error("destination", destination, "không được để trống")
            try:
                bd = doc_ngay(start_date, "start_date") if start_date else hom_nay_vn()
            except ValueError as e:
                return format_mcp_tool_error_response(str(e), error_code=-2, input_context=input_context)
            kt = bd + timedelta(days=days - 1)

            cac_dest, thoi_tiet, le_hoi = await asyncio.gather(
                travel_db.find_destinations(destination),
                travel_db.weather(destination, None, None, bd, kt),
                travel_db.festivals(destination.strip(), bd, kt, limit=8),
            )

            dest = cac_dest[0] if cac_dest else None
            nhom: dict[str, list[dict[str, Any]]] = {}
            cac_diem: list[dict[str, Any]] = []
            if dest:
                cac_diem = [
                    p
                    for p in await travel_db.places()
                    if thuoc_diem_den(p, dest) and (p.get("review_count") or 0) >= 20
                ]
                cac_diem.sort(key=diem_pho_bien, reverse=True)
                for p in cac_diem:
                    n = nhom_dia_diem(p.get("category"))
                    if n and len(nhom.setdefault(n, [])) < per_group:
                        nhom[n].append(
                            {
                                "name": p.get("title"),
                                "place_id": p.get("place_id"),
                                "category": p.get("category"),
                                "rating": p.get("rating"),
                                "reviews": p.get("review_count"),
                                "price": p.get("price_range"),
                            }
                        )

            ngay_tt = (thoi_tiet or {}).get("days") or []
            if not nhom and not ngay_tt and not le_hoi:
                return drop_empty(
                    {
                        "status": "success",
                        "destination": destination,
                        "note": "Chưa có dữ liệu đã lưu cho điểm đến này; dùng search_places, weather_forecast, search_events.",
                        "available_destinations": await goi_y_diem_den(),
                    }
                )

            return drop_empty(
                {
                    "status": "success",
                    "destination": destination,
                    "range": {"from": bd.isoformat(), "to": kt.isoformat()},
                    "weather": [
                        {
                            "date": d.get("forecast_date"),
                            "condition": d.get("weather_desc"),
                            "temp_min_c": d.get("temp_min"),
                            "temp_max_c": d.get("temp_max"),
                            "chance_of_rain_pct": d.get("precipitation_prob"),
                        }
                        for d in ngay_tt
                    ]
                    or None,
                    "top_places": nhom or None,
                    "festivals": [gon_le_hoi(e) for e in le_hoi] or None,
                    "missing": [
                        ten
                        for ten, co in (("weather", ngay_tt), ("top_places", nhom), ("festivals", le_hoi))
                        if not co
                    ]
                    or None,
                    "note": f"Nhận ra nhiều điểm đến, chỉ tổng hợp '{(await travel_db.destinations())[dest]['name']}'; "
                    "gọi riêng cho từng nơi."
                    if len(cac_dest) > 1
                    else None,
                    "data_source": NGUON_DB,
                    "data_as_of": moi_nhat(cac_diem),
                }
            )

        except TravelDbError as e:
            logger.error("destination_snapshot đọc DB thất bại", error=str(e))
            return loi_db(e, input_context)
        except Exception as e:
            logger.error("destination_snapshot thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không lấy được tổng quan điểm đến", exception=e, input_context=input_context
            )

    return destination_snapshot
