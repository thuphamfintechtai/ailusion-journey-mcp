"""KHUÔN MẪU cho một MCP tool — copy file này, đổi tên, viết logic.

File bắt đầu bằng `_` nên `auto_register` bỏ qua, không đăng ký lên server.

Checklist khi viết tool mới
---------------------------
1. Tên file = tên tool  (vd: `build_itinerary.py` -> tool `build_itinerary`)
2. Hàm đăng ký tên `register_<ten>_tool(mcp)` — đúng prefix `register_`
3. `description` viết cho LLM đọc: nói rõ tool làm gì, khi nào dùng,
   đơn vị và định dạng của từng tham số. Đây là thứ quyết định model có
   gọi đúng tool hay không.
4. Mọi tham số dùng `Annotated[..., Field(description=...)]`
5. Bọc toàn bộ thân hàm trong try/except, log rồi trả
   `format_mcp_tool_error_response(...)` — không để exception lọt ra ngoài
6. Trả `dict`, đi qua `drop_empty()` để bỏ field rỗng cho đỡ tốn token
7. I/O gọi qua `src/services/`, không gọi httpx thẳng trong tool
8. Log và ghi chú viết bằng tiếng Việt, không dùng icon/emoji
"""

from typing import Annotated, Any

import httpx
import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.http_client import backend_client
from ..utils.error_handler import (
    format_mcp_tool_error_response,
    format_upstream_error,
)
from ..utils.response_utils import drop_empty

logger = structlog.get_logger(__name__)


def register_template_tool(mcp: FastMCP):
    """Đăng ký tool mẫu với FastMCP server."""

    @mcp.tool(
        name="template_tool",
        description=(
            "Mô tả một câu tool này làm gì.\n\n"
            "Khi nào dùng: ...\n"
            "Khi nào KHÔNG dùng: ...\n\n"
            "Trả về: cấu trúc dữ liệu gì, đơn vị gì."
        ),
        tags={"journey", "template"},
        meta={
            "version": "1.0.0",
            "author": "Ailusion team",
            "data_source": "ailusion-journey-be",
        },
    )
    async def template_tool(
        destination: Annotated[
            str,
            Field(description="Tên điểm đến. VD: 'Đà Nẵng', 'Hội An'."),
        ],
        days: Annotated[
            int,
            Field(default=3, ge=1, le=30, description="Số ngày của chuyến đi (1-30)."),
        ] = 3,
    ) -> dict[str, Any]:
        """Docstring ngắn cho người đọc code (LLM đọc `description` ở trên)."""
        try:
            if not destination.strip():
                return format_mcp_tool_error_response(
                    error_message="`destination` không được để trống",
                    error_code=-2,
                    input_context={"destination": destination},
                )

            data = await backend_client.get(
                "/api/v1/destinations",
                params={"q": destination, "days": days},
            )

            return drop_empty(
                {
                    "status": "success",
                    "destination": destination,
                    "days": days,
                    "data": data,
                }
            )

        except httpx.HTTPError as e:
            logger.error("Gọi backend thất bại", error=str(e), exc_info=True)
            return format_upstream_error("ailusion-journey-be", exception=e)

        except Exception as e:
            logger.error("template_tool thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không xử lý được yêu cầu",
                exception=e,
                input_context={"destination": destination, "days": days},
            )

    return template_tool
