"""Tool che dữ liệu cá nhân trong văn bản trước khi lưu, ghi log hay gửi trace."""

from collections import Counter
from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..utils.error_handler import format_mcp_tool_error_response
from ..utils.pii import NHAN_PII, che_pii
from ._serpapi_common import nhan_lua_chon

logger = structlog.get_logger(__name__)


def register_mask_pii_tool(mcp: FastMCP):
    @mcp.tool(
        name="mask_pii",
        title="Mask PII (Che dữ liệu nhạy cảm)",
        description=(
            "Che dữ liệu cá nhân trong văn bản tiếng Việt: số điện thoại (di động, máy bàn, +84), "
            "email, CCCD 12 số, CMND / số tài khoản / hộ chiếu (khi có từ khoá đi kèm), số thẻ "
            "ngân hàng (kiểm Luhn), biển số xe. Chạy cục bộ, không gửi văn bản đi đâu.\n\n"
            "Khi nào dùng: trước khi ghi yêu cầu người dùng vào bộ nhớ dài hạn, log, trace, hay "
            "gửi sang dịch vụ ngoài. CHƯA che tên người và địa chỉ nhà (cần model NER)."
        ),
        tags={"journey", "privacy"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "regex nội bộ"},
    )
    async def mask_pii(
        text: Annotated[str, Field(max_length=50000, description="Văn bản cần che.")],
        style: Annotated[
            Literal["label", "partial"],
            Field(
                description="Cách che.",
                json_schema_extra=nhan_lua_chon(
                    {"label": "thay bằng nhãn [SO_DIEN_THOAI]", "partial": "giữ 3 ký tự cuối"}
                ),
            ),
        ] = "label",
        types: Annotated[
            list[Literal["email", "card", "cccd", "id_card", "bank_account", "passport", "phone", "license_plate"]] | None,
            Field(description="Chỉ che các loại này; bỏ trống = che tất cả."),
        ] = None,
    ) -> dict[str, Any]:
        try:
            da_che, ds = che_pii(text, style=style, kinds=set(types) if types else None)
            # Không trả lại giá trị gốc: chỉ báo loại và số lượng.
            return {
                "status": "success",
                "masked_text": da_che,
                "found": dict(Counter(m.kind for m in ds)),
                "labels": {k: v for k, v in NHAN_PII.items() if any(m.kind == k for m in ds)} if style == "label" else {},
                "note": "Chưa che tên người và địa chỉ nhà.",
            }
        except Exception as e:
            logger.error("mask_pii thất bại", error=str(e), exc_info=True)
            # Không đưa văn bản gốc vào input_context để tránh lộ PII trong response lỗi.
            return format_mcp_tool_error_response("Không che được văn bản", exception=e)

    return mask_pii
