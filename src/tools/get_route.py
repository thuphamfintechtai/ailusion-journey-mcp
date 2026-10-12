"""Tool chỉ đường và ước tính thời gian ở Việt Nam (xe máy / ô tô / xe tải) bằng Vietmap."""

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.vietmap_client import VietmapError, vietmap_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._vietmap_common import MO_TA_DIEM, NHAN_PHUONG_TIEN, gon_diem, link_google_maps, loi_vietmap, resolve_nhieu

logger = structlog.get_logger(__name__)

_TOI_DA_DIEM = 10
_TOI_DA_BUOC = 40


def register_get_route_tool(mcp: FastMCP):
    @mcp.tool(
        name="get_route",
        title="Get Route (Chỉ đường)",
        description=(
            "Chỉ đường qua 2 điểm trở lên ở Việt Nam theo đúng thứ tự truyền vào: quãng đường "
            "(km), thời gian (phút), phí cầu đường (VND, theo loại xe) và các bước rẽ. Có profile "
            "XE MÁY. Kèm `google_maps_url` để người dùng mở chỉ đường.\n\n"
            "Khi nào dùng: tính thời gian di chuyển giữa các điểm trong lịch trình, ước chi phí "
            "cầu đường cho chuyến tự lái.\n"
            "Khi nào KHÔNG dùng: so nhiều cặp điểm cùng lúc (dùng distance_matrix); muốn tìm "
            "thứ tự đi tốt nhất (dùng plan_day_route).\n\n" + MO_TA_DIEM
        ),
        tags={"journey", "vietmap", "routing"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "vietmap:route/v4"},
    )
    async def get_route(
        points: Annotated[
            list[str],
            Field(min_length=2, max_length=_TOI_DA_DIEM, description="Các điểm theo thứ tự đi: điểm đầu, điểm dừng, điểm cuối."),
        ],
        vehicle: Annotated[
            Literal["motorcycle", "car", "truck"],
            Field(description="Phương tiện.", json_schema_extra=NHAN_PHUONG_TIEN),
        ] = "car",
        avoid_ferry: Annotated[bool, Field(description="Tránh đi phà.")] = False,
        depart_at: Annotated[
            str | None,
            Field(description="Giờ khởi hành ISO-8601, VD '2026-11-20T07:30:00+07:00'. Bỏ trống = bây giờ."),
        ] = None,
        include_steps: Annotated[bool, Field(description="Kèm các bước rẽ (tối đa 40 bước).")] = False,
    ) -> dict[str, Any]:
        input_context = {"points": points, "vehicle": vehicle}
        try:
            thoi_diem = None
            if depart_at:
                try:
                    dt = datetime.fromisoformat(depart_at)
                except ValueError:
                    return format_validation_error("depart_at", depart_at, "phải theo ISO-8601")
                if dt.tzinfo is None:
                    return format_validation_error("depart_at", depart_at, "cần múi giờ, VD +07:00")
                thoi_diem = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            diem = await resolve_nhieu(points)
            data = await vietmap_client.route(
                diem,
                vehicle,
                annotations="toll",
                avoid="ferry" if avoid_ferry else None,
                time=thoi_diem,
            )
            paths = data.get("paths") or []
            if not paths:
                return drop_empty({"status": "success", "route": None, "note": "Không tìm được đường đi"})
            p = paths[0]

            return drop_empty(
                {
                    "status": "success",
                    "vehicle": vehicle,
                    "points": [gon_diem(d) for d in diem],
                    "distance_km": round(p.get("distance", 0) / 1000, 2),
                    "duration_min": round(p.get("time", 0) / 60000, 1),
                    "toll_cost_vnd": p.get("toll_cost") or None,
                    "tolls": [
                        {"name": t.get("name"), "price_vnd": t.get("price")} for t in (p.get("tolls") or [])
                    ]
                    or None,
                    "steps": [
                        {
                            "text": s.get("text"),
                            "distance_m": round(s.get("distance", 0)),
                            "duration_min": round(s.get("time", 0) / 60000, 1),
                        }
                        for s in (p.get("instructions") or [])[:_TOI_DA_BUOC]
                    ]
                    if include_steps
                    else None,
                    "google_maps_url": link_google_maps(diem, vehicle),
                    "note": "Thời gian ước tính, chưa tính kẹt xe giờ cao điểm ở thành phố lớn.",
                }
            )

        except VietmapError as e:
            logger.error("get_route gọi Vietmap thất bại", error=str(e))
            return loi_vietmap(e, input_context)
        except Exception as e:
            logger.error("get_route thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không tính được đường đi", exception=e, input_context=input_context
            )

    return get_route
