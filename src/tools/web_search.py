"""Tool tìm kiếm web (Tavily), ưu tiên kết quả Việt Nam, có ngày đăng và loại nguồn."""

from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.tavily_client import TavilyError, tavily_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._serpapi_common import nhan_lua_chon
from ._tavily_common import gon_ket_qua, loi_tavily

logger = structlog.get_logger(__name__)


def register_web_search_tool(mcp: FastMCP):
    @mcp.tool(
        name="web_search",
        title="Web Search (Tìm kiếm web)",
        description=(
            "Tìm kiếm web, trả tiêu đề, URL, đoạn trích liên quan, ngày đăng (ước tính) và "
            "`source_type` (chinh_thong = trang .gov.vn, bao_chi = báo có giấy phép, khac).\n\n"
            "Khi nào dùng: kiểm chứng thông tin (giá vé tham quan, quy định, giờ mở cửa mới), "
            "tìm kinh nghiệm / blog, phản biện một nhận định. Mặc định ưu tiên kết quả Việt Nam.\n"
            "Khi nào KHÔNG dùng: tin tức thời sự gần đây theo địa phương (dùng local_news); "
            "đọc kỹ một trang đã biết URL (dùng fetch_page).\n\n"
            "Chi phí: depth 'basic' 1 credit, 'advanced' 2 credit (quota 1.000 credit/tháng)."
        ),
        tags={"journey", "tavily", "web"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "tavily:search"},
    )
    async def web_search(
        query: Annotated[str, Field(description="Câu tìm kiếm, nên viết tiếng Việt nếu hỏi về Việt Nam.")],
        max_results: Annotated[int, Field(ge=1, le=10, description="Số kết quả tối đa.")] = 5,
        time_range: Annotated[
            Literal["any", "day", "week", "month", "year"],
            Field(
                description="Chỉ lấy trang đăng / cập nhật trong khoảng này.",
                json_schema_extra=nhan_lua_chon(
                    {"any": "không giới hạn", "day": "24 giờ", "week": "7 ngày", "month": "30 ngày", "year": "1 năm"}
                ),
            ),
        ] = "any",
        include_domains: Annotated[
            list[str] | None, Field(description="Chỉ tìm trong các domain này, VD ['vnexpress.net'].")
        ] = None,
        exclude_domains: Annotated[list[str] | None, Field(description="Bỏ các domain này.")] = None,
        depth: Annotated[
            Literal["basic", "advanced"],
            Field(
                description="'advanced' chính xác hơn nhưng tốn 2 credit.",
                json_schema_extra=nhan_lua_chon({"basic": "nhanh, 1 credit", "advanced": "kỹ, 2 credit"}),
            ),
        ] = "basic",
        include_content: Annotated[
            bool, Field(description="Kèm nội dung trang (tối đa 1.200 ký tự mỗi kết quả), tốn token hơn.")
        ] = False,
        vietnam_first: Annotated[bool, Field(description="Ưu tiên kết quả từ Việt Nam.")] = True,
    ) -> dict[str, Any]:
        input_context = {"query": query, "time_range": time_range}
        try:
            if not query.strip():
                return format_validation_error("query", query, "không được để trống")

            data = await tavily_client.search(
                query.strip(),
                topic="general",
                search_depth=depth,
                max_results=max_results,
                time_range=None if time_range == "any" else time_range,
                include_domains=include_domains or None,
                exclude_domains=exclude_domains or None,
                include_raw_content="markdown" if include_content else None,
                include_published_date=True,
                country="vietnam" if vietnam_first else None,
            )
            ket_qua = data.get("results") or []
            return drop_empty(
                {
                    "status": "success",
                    "query": query,
                    "total_found": len(ket_qua),
                    "results": [gon_ket_qua(r, include_content) for r in ket_qua],
                    "note": None if ket_qua else "Không có kết quả, thử từ khoá khác hoặc bỏ bộ lọc thời gian",
                }
            )

        except TavilyError as e:
            logger.error("web_search gọi Tavily thất bại", error=str(e))
            return loi_tavily(e, input_context)
        except Exception as e:
            logger.error("web_search thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không tìm kiếm được", exception=e, input_context=input_context
            )

    return web_search
