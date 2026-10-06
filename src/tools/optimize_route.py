"""Tool sắp thứ tự đi qua các điểm cho quãng đường ngắn nhất (TSP) bằng Vietmap."""

import math
from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.vietmap_client import Diem, VietmapError, vietmap_client
from ..utils.error_handler import format_mcp_tool_error_response
from ..utils.response_utils import drop_empty
from ._vietmap_common import MO_TA_DIEM, NHAN_PHUONG_TIEN, gon_diem, link_google_maps, loi_vietmap, resolve_nhieu

logger = structlog.get_logger(__name__)

_TOI_DA_DIEM = 15


def thu_tu_tu_snapped(diem: list[Diem], snapped: list[list[float]]) -> list[int]:
    """Suy thứ tự đi từ `snapped_waypoints` ([lng, lat] theo thứ tự đi).

    TSP của Vietmap không trả index; mỗi điểm snapped là điểm đầu vào đã bám
    vào đường, nên ghép với điểm đầu vào gần nhất chưa dùng.
    """
    con_lai = set(range(len(diem)))
    thu_tu: list[int] = []
    for lng, lat in snapped:
        if not con_lai:
            break  # roundtrip: điểm cuối lặp lại điểm đầu
        gan_nhat = min(con_lai, key=lambda i: math.hypot(diem[i].lat - lat, diem[i].lng - lng))
        thu_tu.append(gan_nhat)
        con_lai.discard(gan_nhat)
    return thu_tu + sorted(con_lai)


def register_optimize_route_tool(mcp: FastMCP):
    @mcp.tool(
        name="optimize_route",
        title="Optimize Route (Sắp thứ tự điểm)",
        description=(
            "Sắp thứ tự đi qua các điểm để tổng quãng đường / thời gian nhỏ nhất (bài toán "
            "người du lịch - TSP). Điểm ĐẦU TIÊN luôn là điểm xuất phát (VD khách sạn).\n\n"
            "Khi nào dùng: một ngày đi nhiều điểm tham quan, cần thứ tự hợp lý nhất.\n"
            "Lưu ý: chưa xét giờ mở cửa hay khung giờ; kiểm tra lại bằng get_place_details / "
            "vn_calendar nếu có điểm chỉ mở buổi sáng hoặc tối.\n\n"
            f"Tối đa {_TOI_DA_DIEM} điểm. " + MO_TA_DIEM
        ),
        tags={"journey", "vietmap", "routing"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "vietmap:tsp/v4"},
    )
    async def optimize_route(
        points: Annotated[
            list[str],
            Field(min_length=3, max_length=_TOI_DA_DIEM, description="Các điểm; điểm đầu là nơi xuất phát."),
        ],
        vehicle: Annotated[
            Literal["motorcycle", "car", "truck"],
            Field(description="Phương tiện.", json_schema_extra=NHAN_PHUONG_TIEN),
        ] = "car",
        return_to_start: Annotated[bool, Field(description="Quay về điểm xuất phát cuối ngày.")] = True,
        end_at_last: Annotated[
            bool, Field(description="Bắt buộc kết thúc ở điểm cuối danh sách (chỉ khi return_to_start=false).")
        ] = False,
    ) -> dict[str, Any]:
        input_context = {"points": points, "vehicle": vehicle}
        try:
            diem = await resolve_nhieu(points)
            data = await vietmap_client.tsp(diem, vehicle, return_to_start, end_at_last)
            paths = data.get("paths") or []
            if not paths:
                return drop_empty({"status": "success", "order": None, "note": "Không tìm được lộ trình"})
            p = paths[0]

            snapped = (p.get("snapped_waypoints") or {}).get("coordinates") or []
            thu_tu = thu_tu_tu_snapped(diem, snapped)
            diem_theo_thu_tu = [diem[i] for i in thu_tu]
            if return_to_start:
                diem_theo_thu_tu.append(diem[0])

            return drop_empty(
                {
                    "status": "success",
                    "vehicle": vehicle,
                    "order": thu_tu,
                    "stops": [{"step": n + 1, "input_index": i, **gon_diem(diem[i])} for n, i in enumerate(thu_tu)],
                    "return_to_start": return_to_start,
                    "total_distance_km": round(p.get("distance", 0) / 1000, 2),
                    "total_duration_min": round(p.get("time", 0) / 60000, 1),
                    "google_maps_url": link_google_maps(diem_theo_thu_tu, vehicle),
                    "note": "`order` là index của `points` đầu vào theo thứ tự nên đi.",
                }
            )

        except VietmapError as e:
            logger.error("optimize_route gọi Vietmap thất bại", error=str(e))
            return loi_vietmap(e, input_context)
        except Exception as e:
            logger.error("optimize_route thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không sắp được thứ tự điểm", exception=e, input_context=input_context
            )

    return optimize_route
