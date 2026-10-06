"""Tool tin tức địa phương gần đây (Tavily, topic=news): cấm đường, sạt lở, bão, quy định mới."""

from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.tavily_client import TavilyError, tavily_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._tavily_common import BAO_VN, gon_ket_qua, loi_tavily

logger = structlog.get_logger(__name__)

_CHU_DE_MAC_DINH = "cấm đường sạt lở mưa lũ bão giao thông quy định mới"


def register_local_news_tool(mcp: FastMCP):
    @mcp.tool(
        name="local_news",
        title="Local News (Tin tức địa phương)",
        description=(
            "Tin tức gần đây về một địa phương ở Việt Nam, xếp mới nhất trước, có ngày đăng và "
            "`source_type` (chinh_thong / bao_chi / khac). Ưu tiên báo chính thống.\n\n"
            "Khi nào dùng: trước khi chốt lịch trình, kiểm tra cấm đường, đèo sạt lở, mưa bão, "
            "tàu xe ngưng chạy, cấm biển, điểm tham quan đóng cửa, quy định mới. Truyền "
            "`topic` cụ thể (VD 'đèo Prenn', 'cấm biển', 'cáp treo') để kết quả trúng hơn.\n"
            "Khi nào KHÔNG dùng: tìm thông tin không mang tính thời sự (dùng web_search).\n\n"
            "Mỗi lần gọi tốn 1 credit Tavily."
        ),
        tags={"journey", "tavily", "news"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "tavily:search topic=news"},
    )
    async def local_news(
        location: Annotated[str, Field(description="Địa phương, VD 'Đà Lạt', 'Hà Giang', 'Côn Đảo'.")],
        topic: Annotated[
            str | None,
            Field(description="Chủ đề cần dò, VD 'sạt lở đèo', 'cấm biển'. Bỏ trống = giao thông, thời tiết, quy định."),
        ] = None,
        days: Annotated[int, Field(ge=1, le=60, description="Lấy tin trong bao nhiêu ngày gần đây.")] = 7,
        max_results: Annotated[int, Field(ge=1, le=15, description="Số tin tối đa.")] = 8,
    ) -> dict[str, Any]:
        input_context = {"location": location, "topic": topic, "days": days}
        try:
            if not location.strip():
                return format_validation_error("location", location, "không được để trống")

            tu_ngay = date.today() - timedelta(days=days)
            data = await tavily_client.search(
                f"{(topic or _CHU_DE_MAC_DINH).strip()} {location.strip()}",
                topic="news",
                search_depth="basic",
                max_results=max_results,
                start_date=tu_ngay.isoformat(),
                include_domains=list(BAO_VN),
                include_domains_mode="prefer",
            )
            tin = [gon_ket_qua(r) for r in data.get("results") or []]
            # Ngày đăng dạng 'Tue, 15 Sep 2026 13:00:00 GMT'; sắp theo thời điểm, tin không có ngày xuống cuối.
            def moc(t: dict[str, Any]) -> float:
                try:
                    return parsedate_to_datetime(t["published_date"]).timestamp()
                except Exception:
                    return 0.0

            tin.sort(key=moc, reverse=True)
            return drop_empty(
                {
                    "status": "success",
                    "location": location,
                    "since": tu_ngay.isoformat(),
                    "total_found": len(tin),
                    "news": tin,
                    "note": None
                    if tin
                    else f"Không thấy tin trong {days} ngày qua; không có tin không có nghĩa là an toàn tuyệt đối",
                }
            )

        except TavilyError as e:
            logger.error("local_news gọi Tavily thất bại", error=str(e))
            return loi_tavily(e, input_context)
        except Exception as e:
            logger.error("local_news thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không lấy được tin tức", exception=e, input_context=input_context
            )

    return local_news
