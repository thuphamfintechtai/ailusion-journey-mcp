"""Tool đọc số tiền trong câu tiếng Việt ('500k', '2tr5', '3 củ', '$50') ra số chuẩn."""

from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.fx_client import FxError, fx_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.money import parse_amounts
from ..utils.response_utils import drop_empty

logger = structlog.get_logger(__name__)


def register_parse_money_tool(mcp: FastMCP):
    @mcp.tool(
        name="parse_money",
        title="Parse Money (Đọc số tiền)",
        description=(
            "Đọc mọi số tiền trong một câu tiếng Việt ra số chuẩn: '500k', '800 nghìn', '2tr5' "
            "(2,5 triệu), '3 củ', '1 tỏi', '1.500.000đ', '2,5 triệu', '$50', '50 đô', '3000 yên', "
            "khoảng '2-3 triệu', kèm đơn vị tính '/người', '/đêm', 'mỗi người'.\n\n"
            "Khi nào dùng: bước trích slot ngân sách / giá từ câu người dùng, review, bài viết; "
            "không tự nhẩm, vì dễ sai '2tr5' hay '1.500' (nghìn hay thập phân). Bật "
            "`convert_to_vnd` để quy ngoại tệ ra VND theo tỷ giá Vietcombank.\n"
            "Không tốn quota. Số không kèm đơn vị chỉ nhận khi >= 1.000 và gắn `ambiguous`."
        ),
        tags={"journey", "money"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "luật nội bộ + vietcombank"},
    )
    async def parse_money(
        text: Annotated[str, Field(description="Câu cần đọc, VD 'đi 3 người tầm 5tr, khách sạn 800k/đêm'.")],
        default_currency: Annotated[
            str, Field(description="Tiền tệ khi câu không ghi rõ, mã ISO 3 chữ.", min_length=3, max_length=3)
        ] = "VND",
        convert_to_vnd: Annotated[bool, Field(description="Quy ngoại tệ ra VND (tỷ giá chuyển khoản Vietcombank).")] = False,
    ) -> dict[str, Any]:
        input_context = {"text": text}
        try:
            if not text.strip():
                return format_validation_error("text", text, "không được để trống")
            ds = parse_amounts(text, default_currency.upper())
            ket_qua = []
            ghi_chu = []
            for a in ds:
                muc: dict[str, Any] = {
                    "text": a.text,
                    "value_min": a.value_min,
                    "value_max": a.value_max if a.value_max != a.value_min else None,
                    "currency": a.currency,
                    "per": a.per,
                    "ambiguous": a.ambiguous or None,
                }
                if convert_to_vnd and a.currency != "VND":
                    try:
                        tg = await fx_client.vnd_per_unit(a.currency)
                        muc["vnd_min"] = round(a.value_min * tg["rate"])
                        muc["vnd_max"] = round(a.value_max * tg["rate"]) if a.value_max != a.value_min else None
                        muc["fx_rate"] = tg["rate"]
                        muc["fx_source"] = tg["source"]
                    except FxError as e:
                        ghi_chu.append(f"Không quy đổi được {a.currency}: {e}")
                ket_qua.append(muc)

            return drop_empty(
                {
                    "status": "success",
                    "amounts": ket_qua,
                    "notes": ghi_chu or None,
                    "note": None if ket_qua else "Không thấy số tiền nào trong câu",
                }
            )
        except Exception as e:
            logger.error("parse_money thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response("Không đọc được số tiền", exception=e, input_context=input_context)

    return parse_money
