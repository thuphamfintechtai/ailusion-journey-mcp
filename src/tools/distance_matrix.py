"""Tool ma trận thời gian / quãng đường giữa nhiều điểm ở Việt Nam bằng Vietmap."""

from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.vietmap_client import VietmapError, vietmap_client
from ..utils.error_handler import format_mcp_tool_error_response
from ..utils.response_utils import drop_empty
from ._vietmap_common import MO_TA_DIEM, NHAN_PHUONG_TIEN, gon_diem, loi_vietmap, resolve_nhieu

logger = structlog.get_logger(__name__)

_TOI_DA_DIEM = 25


def register_distance_matrix_tool(mcp: FastMCP):
    @mcp.tool(
        name="distance_matrix",
        title="Distance Matrix (Ma trận khoảng cách)",
        description=(
            "Tính thời gian (phút) và quãng đường (km) từ mỗi điểm đi tới mỗi điểm đến trong MỘT "
            "lượt gọi. Có profile xe máy.\n\n"
            "Khi nào dùng: chọn khách sạn gần các điểm tham quan nhất, lọc điểm quá xa, kiểm "
            "tra lịch trình có kịp giờ không, chuẩn bị dữ liệu cho bước lập phương án.\n"
            "Khi nào KHÔNG dùng: chỉ cần một tuyến (get_route) hoặc cần thứ tự đi tốt nhất "
            "(optimize_route).\n\n"
            f"Tối đa {_TOI_DA_DIEM} điểm (đi + đến). " + MO_TA_DIEM
        ),
        tags={"journey", "vietmap", "routing"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "vietmap:matrix/v4"},
    )
    async def distance_matrix(
        origins: Annotated[list[str], Field(min_length=1, description="Các điểm đi.")],
        destinations: Annotated[
            list[str] | None, Field(description="Các điểm đến. Bỏ trống = dùng chính `origins` (ma trận vuông).")
        ] = None,
        vehicle: Annotated[
            Literal["motorcycle", "car", "truck"],
            Field(description="Phương tiện.", json_schema_extra=NHAN_PHUONG_TIEN),
        ] = "car",
    ) -> dict[str, Any]:
        input_context = {"origins": origins, "destinations": destinations, "vehicle": vehicle}
        try:
            vuong = not destinations
            tat_ca = origins if vuong else [*origins, *destinations]  # type: ignore[misc]
            if len(tat_ca) > _TOI_DA_DIEM:
                return format_mcp_tool_error_response(
                    f"Tổng số điểm vượt {_TOI_DA_DIEM}, chia nhỏ rồi gọi nhiều lần",
                    error_code=-2,
                    input_context=input_context,
                )
            if len(tat_ca) < 2:
                return format_mcp_tool_error_response("Cần ít nhất 2 điểm", error_code=-2, input_context=input_context)

            diem = await resolve_nhieu(tat_ca)
            nguon = list(range(len(origins)))
            dich = nguon if vuong else list(range(len(origins), len(tat_ca)))
            data = await vietmap_client.matrix(diem, vehicle, nguon, dich)

            tg = data.get("durations") or []
            qd = data.get("distances") or []
            hang = []
            for i, o in enumerate(nguon):
                for j, d in enumerate(dich):
                    if o == d:
                        continue
                    giay = tg[i][j] if i < len(tg) and j < len(tg[i]) else None
                    met = qd[i][j] if i < len(qd) and j < len(qd[i]) else None
                    hang.append(
                        {
                            "from": o,
                            "to": d,
                            "duration_min": round(giay / 60, 1) if giay is not None else None,
                            "distance_km": round(met / 1000, 2) if met is not None else None,
                        }
                    )

            return drop_empty(
                {
                    "status": "success",
                    "vehicle": vehicle,
                    "points": [{"index": i, **gon_diem(d)} for i, d in enumerate(diem)],
                    "pairs": hang,
                    "note": "`from` / `to` là index trong `points`. Thời gian chưa tính kẹt xe giờ cao điểm.",
                }
            )

        except VietmapError as e:
            logger.error("distance_matrix gọi Vietmap thất bại", error=str(e))
            return loi_vietmap(e, input_context)
        except Exception as e:
            logger.error("distance_matrix thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không tính được ma trận khoảng cách", exception=e, input_context=input_context
            )

    return distance_matrix
