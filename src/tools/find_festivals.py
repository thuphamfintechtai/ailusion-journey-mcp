"""Tool tra lễ hội / sự kiện đã lưu (lehoivietnam.com.vn) theo địa phương và khoảng ngày."""

from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.travel_db import TravelDbError, travel_db
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._db_common import NGUON_DB, gon_le_hoi, loi_db
from ._serpapi_common import doc_ngay

logger = structlog.get_logger(__name__)

_TOI_DA_NGAY = 366


def register_find_festivals_tool(mcp: FastMCP):
    @mcp.tool(
        name="find_festivals",
        title="Find Festivals (Tra lễ hội đã lưu)",
        description=(
            "Tra lễ hội, sự kiện văn hoá, hội chợ đã lưu từ lehoivietnam.com.vn diễn ra trong khoảng ngày "
            "ở một tỉnh / thành phố (theo đơn vị hành chính sau 1/7/2025, VD Hội An thuộc Đà Nẵng, Đà "
            "Lạt thuộc Lâm Đồng). Kèm thời gian, nơi tổ chức, điểm nổi bật, giá vé nếu có. Không tốn quota.\n\n"
            "Khi nào dùng: biết chuyến đi trùng lễ hội nào, gợi ý hoạt động văn hoá, cảnh báo đông đúc.\n"
            "Ngày lễ âm lịch / nghỉ lễ quốc gia -> vn_calendar. Concert, sự kiện mới công bố chưa có "
            "trong dữ liệu đã lưu -> search_events (Tavily, tốn credit).\n\n"
            f"Khoảng ngày tối đa {_TOI_DA_NGAY} ngày."
        ),
        tags={"journey", "db", "events"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "postgres:event"},
    )
    async def find_festivals(
        location: Annotated[str, Field(description="Tỉnh / thành phố / điểm đến, VD 'Hà Nội', 'Hội An', 'Bắc Ninh'.")],
        start_date: Annotated[str, Field(description="Ngày đầu, YYYY-MM-DD.")],
        end_date: Annotated[str | None, Field(description="Ngày cuối, YYYY-MM-DD. Bỏ trống = bằng start_date.")] = None,
        keyword: Annotated[
            str | None, Field(description="Lọc theo từ khoá trong tên / mô tả / chủ đề, VD 'chùa', 'ẩm thực', 'pháo hoa'.")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=30, description="Số sự kiện tối đa.")] = 10,
    ) -> dict[str, Any]:
        input_context = {"location": location, "start_date": start_date, "end_date": end_date, "keyword": keyword}
        try:
            if not location.strip():
                return format_validation_error("location", location, "không được để trống")
            try:
                bd = doc_ngay(start_date, "start_date")
                kt = doc_ngay(end_date, "end_date") if end_date else bd
            except ValueError as e:
                return format_mcp_tool_error_response(str(e), error_code=-2, input_context=input_context)
            if kt < bd:
                return format_validation_error("end_date", end_date, "trước start_date")
            if (kt - bd).days + 1 > _TOI_DA_NGAY:
                return format_validation_error("end_date", end_date, f"khoảng ngày vượt {_TOI_DA_NGAY} ngày")

            ds = await travel_db.festivals(location.strip(), bd, kt, keyword=keyword, limit=limit)
            return drop_empty(
                {
                    "status": "success",
                    "location": location,
                    "range": {"from": bd.isoformat(), "to": kt.isoformat()},
                    "total_found": len(ds),
                    "events": [gon_le_hoi(e) for e in ds],
                    "data_source": NGUON_DB,
                    "note": None
                    if ds
                    else "Không có lễ hội đã lưu trong khoảng này; thử search_events hoặc vn_calendar.",
                }
            )

        except TravelDbError as e:
            logger.error("find_festivals đọc DB thất bại", error=str(e))
            return loi_db(e, input_context)
        except Exception as e:
            logger.error("find_festivals thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không tra được lễ hội", exception=e, input_context=input_context
            )

    return find_festivals
