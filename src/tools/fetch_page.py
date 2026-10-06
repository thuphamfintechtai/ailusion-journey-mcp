"""Tool đọc nội dung trang web thành markdown (Tavily Extract)."""

from typing import Annotated, Any
from urllib.parse import urlparse

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.tavily_client import TavilyError, tavily_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._tavily_common import loai_nguon, loi_tavily, ten_mien

logger = structlog.get_logger(__name__)

_TOI_DA_URL = 5


def register_fetch_page_tool(mcp: FastMCP):
    @mcp.tool(
        name="fetch_page",
        title="Fetch Page (Đọc trang web)",
        description=(
            "Đọc nội dung các trang web đã biết URL, trả về dạng markdown sạch.\n\n"
            "Khi nào dùng: cần đọc kỹ một bài báo / thông báo / bảng giá tìm được từ "
            "web_search hoặc local_news. Truyền `focus` để chỉ lấy các đoạn liên quan "
            "(VD 'giá vé người lớn', 'giờ mở cửa') thay vì cả trang — đỡ tốn token nhiều.\n\n"
            f"Tối đa {_TOI_DA_URL} URL mỗi lần, tốn 1 credit Tavily cho mỗi 5 URL."
        ),
        tags={"journey", "tavily", "web"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "tavily:extract"},
    )
    async def fetch_page(
        urls: Annotated[
            list[str], Field(min_length=1, max_length=_TOI_DA_URL, description="Các URL cần đọc (http/https).")
        ],
        focus: Annotated[
            str | None,
            Field(description="Chỉ lấy các đoạn liên quan tới nội dung này. Bỏ trống = lấy cả trang."),
        ] = None,
        max_chars: Annotated[
            int, Field(ge=500, le=20000, description="Số ký tự tối đa mỗi trang trả về.")
        ] = 6000,
    ) -> dict[str, Any]:
        input_context = {"urls": urls, "focus": focus}
        try:
            for u in urls:
                if urlparse(u).scheme not in ("http", "https") or not urlparse(u).hostname:
                    return format_validation_error("urls", u, "phải là URL http/https đầy đủ")

            data = await tavily_client.extract(
                urls,
                format="markdown",
                extract_depth="basic",
                query=focus.strip() if focus and focus.strip() else None,
                chunks_per_source=5 if focus else None,
            )
            trang = []
            for r in data.get("results") or []:
                noi_dung = r.get("raw_content") or ""
                url = r.get("url") or ""
                trang.append(
                    {
                        "url": url,
                        "domain": ten_mien(url),
                        "source_type": loai_nguon(url),
                        "content": noi_dung[:max_chars],
                        "truncated": len(noi_dung) > max_chars or None,
                    }
                )
            loi = [
                {"url": f.get("url"), "error": f.get("error")} for f in data.get("failed_results") or []
            ]
            return drop_empty({"status": "success", "pages": trang, "failed": loi or None})

        except TavilyError as e:
            logger.error("fetch_page gọi Tavily thất bại", error=str(e))
            return loi_tavily(e, input_context)
        except Exception as e:
            logger.error("fetch_page thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không đọc được trang", exception=e, input_context=input_context
            )

    return fetch_page
