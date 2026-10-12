"""Tool tìm tin mới theo từ khoá qua Google News RSS (miễn phí, không cần key).

Chỉ dùng cho bản thử: điều khoản feed cấm dùng thương mại, xem src/services/google_news_client.py.
Tắt bằng GOOGLE_NEWS_RSS_ENABLED=false (tool không được đăng ký lên server).
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..config.settings import settings
from ..services.google_news_client import GoogleNewsError, google_news_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._tavily_common import loai_nguon

logger = structlog.get_logger(__name__)


def register_google_news_tool(mcp: FastMCP):
    if not settings.google_news_rss_enabled:
        logger.info("Bỏ qua tool google_news vì GOOGLE_NEWS_RSS_ENABLED=false")
        return None

    @mcp.tool(
        name="google_news",
        title="Google News (Tin tức theo từ khoá)",
        description=(
            "Tìm tin tức mới theo từ khoá trên Google Tin tức (Việt Nam, tiếng Việt), xếp mới "
            "nhất trước, có tên báo, ngày đăng và `source_type` (chinh_thong / bao_chi / khac).\n\n"
            "Khi nào dùng: tin địa phương gần đây (cấm đường, sạt lở, bão, quy định mới) hoặc "
            "quét nhanh nhiều tin về một sự kiện cụ thể: 'cấm đường Đà Lạt', 'sạt lở đèo Prenn', "
            "'cấm biển Cô Tô', 'lễ hội pháo hoa Đà Nẵng'. Chỉ có tiêu đề, muốn đọc nội dung thì "
            "dùng fetch_page với `link`.\n"
            "Không tốn quota."
        ),
        tags={"journey", "news"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "google news rss (phi thương mại)"},
    )
    async def google_news(
        query: Annotated[str, Field(description="Từ khoá, VD 'sạt lở đèo Prenn', 'cấm biển Cô Tô'.")],
        location: Annotated[
            str | None, Field(description="Địa phương ghép thêm vào từ khoá, VD 'Đà Lạt'.")
        ] = None,
        days: Annotated[int, Field(ge=1, le=90, description="Chỉ lấy tin trong bao nhiêu ngày gần đây.")] = 7,
        max_results: Annotated[int, Field(ge=1, le=30, description="Số tin tối đa.")] = 10,
    ) -> dict[str, Any]:
        input_context = {"query": query, "location": location, "days": days}
        try:
            if not query.strip():
                return format_validation_error("query", query, "không được để trống")
            cau = " ".join(x.strip() for x in (query, location or "") if x and x.strip())
            tin = await google_news_client.search(f"{cau} when:{days}d")

            moc = datetime.now(timezone.utc) - timedelta(days=days)
            loc = []
            for t in tin:
                ngay = datetime.fromisoformat(t["published"]) if t.get("published") else None
                if ngay and ngay < moc:
                    continue  # "when:Nd" của Google đôi khi lọt tin cũ hơn
                loc.append({**t, "source_type": loai_nguon(t["source_url"]) if t.get("source_url") else None})
            loc.sort(key=lambda t: t.get("published") or "", reverse=True)

            return drop_empty(
                {
                    "status": "success",
                    "query": cau,
                    "since": moc.date().isoformat(),
                    "total_found": len(loc),
                    "news": loc[:max_results],
                    "note": None if loc else f"Không thấy tin trong {days} ngày qua",
                }
            )
        except GoogleNewsError as e:
            logger.error("google_news gọi Google News thất bại", error=str(e))
            return format_mcp_tool_error_response(str(e), exception=e, error_code=-3, input_context=input_context)
        except Exception as e:
            logger.error("google_news thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response("Không lấy được tin tức", exception=e, input_context=input_context)

    return google_news
