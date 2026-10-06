"""Tool đổi tiền theo tỷ giá Vietcombank (dự phòng: tỷ giá trung bình thị trường)."""

from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.fx_client import FxError, fx_client
from ..utils.error_handler import format_mcp_tool_error_response
from ..utils.response_utils import drop_empty
from ._serpapi_common import nhan_lua_chon

logger = structlog.get_logger(__name__)


def register_convert_currency_tool(mcp: FastMCP):
    @mcp.tool(
        name="convert_currency",
        title="Convert Currency (Đổi tiền tệ)",
        description=(
            "Đổi một số tiền giữa hai loại tiền tệ theo tỷ giá Vietcombank trong ngày (mua tiền "
            "mặt / mua chuyển khoản / bán ra), là số tiền thật người dùng trả hay nhận ở ngân "
            "hàng. Tiền Vietcombank không niêm yết thì dùng tỷ giá trung bình thị trường và ghi "
            "rõ trong `source`.\n\n"
            "Khi nào dùng: khách nước ngoài cần biết giá theo tiền nhà; người Việt đi nước ngoài "
            "cần quy ngân sách. Đọc số tiền từ câu chữ thì dùng parse_money trước.\n"
            "rate_type: 'sell' khi người dùng MUA ngoại tệ của ngân hàng (đổi VND sang ngoại tệ để "
            "đi nước ngoài), 'buy_cash' / 'buy_transfer' khi người dùng BÁN ngoại tệ lấy VND."
        ),
        tags={"journey", "money"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "vietcombank + fawazahmed0"},
    )
    async def convert_currency(
        amount: Annotated[float, Field(ge=0, description="Số tiền cần đổi.")],
        from_currency: Annotated[str, Field(min_length=3, max_length=3, description="Mã ISO, VD 'USD'.")],
        to_currency: Annotated[str, Field(min_length=3, max_length=3, description="Mã ISO, VD 'VND'.")] = "VND",
        rate_type: Annotated[
            Literal["buy_transfer", "buy_cash", "sell"],
            Field(
                description="Loại tỷ giá ngân hàng.",
                json_schema_extra=nhan_lua_chon(
                    {"buy_transfer": "mua chuyển khoản", "buy_cash": "mua tiền mặt", "sell": "bán ra"}
                ),
            ),
        ] = "buy_transfer",
    ) -> dict[str, Any]:
        input_context = {"amount": amount, "from_currency": from_currency, "to_currency": to_currency}
        try:
            loai = {"buy_transfer": "transfer", "buy_cash": "buy", "sell": "sell"}[rate_type]
            tu, den = from_currency.upper(), to_currency.upper()
            a = await fx_client.vnd_per_unit(tu, loai)
            b = await fx_client.vnd_per_unit(den, loai)
            ket_qua = amount * a["rate"] / b["rate"]
            return drop_empty(
                {
                    "status": "success",
                    "amount": amount,
                    "from": tu,
                    "to": den,
                    "result": round(ket_qua, 0 if den in ("VND", "JPY", "KRW") else 2),
                    "rate": round(a["rate"] / b["rate"], 6),
                    "vnd_per_unit": {tu: a["rate"], den: b["rate"]},
                    "source": a["source"] if tu != "VND" else b["source"],
                    "rate_type": rate_type,
                    "updated": a.get("updated") or b.get("updated"),
                }
            )
        except FxError as e:
            logger.error("convert_currency lấy tỷ giá thất bại", error=str(e))
            return format_mcp_tool_error_response(str(e), exception=e, error_code=-3, input_context=input_context)
        except Exception as e:
            logger.error("convert_currency thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response("Không đổi được tiền", exception=e, input_context=input_context)

    return convert_currency
