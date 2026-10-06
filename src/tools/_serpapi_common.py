"""Phần dùng chung cho các tool lấy dữ liệu từ SerpApi.

File bắt đầu bằng `_` nên `auto_register` không coi đây là tool.
"""

from datetime import date
from typing import Any

from ..config.settings import settings
from ..services.serpapi_client import SerpApiError
from ..utils.error_handler import format_mcp_tool_error_response

# Mã lỗi riêng: hết key / hết quota SerpApi, model không nên gọi lại ngay.
MA_LOI_HET_QUOTA = -4


def tham_so_vn(currency: bool = False) -> dict[str, str]:
    """Tham số ngôn ngữ/quốc gia mặc định cho người dùng Việt Nam.

    Giữ cố định để các lần gọi trùng truy vấn trúng cache 1 giờ của SerpApi.
    """
    params = {"hl": settings.default_language, "gl": "vn"}
    if currency:
        params["currency"] = settings.default_currency
    return params


def doc_ngay(gia_tri: str, ten: str) -> date:
    """Parse 'YYYY-MM-DD', raise ValueError với thông báo đọc được."""
    try:
        return date.fromisoformat(gia_tri.strip())
    except ValueError as e:
        raise ValueError(f"`{ten}` phải có dạng YYYY-MM-DD, nhận '{gia_tri}'") from e


def khong_co_ket_qua(data: dict[str, Any]) -> str | None:
    """SerpApi trả 200 kèm `error` khi truy vấn không ra gì."""
    loi = data.get("error") if isinstance(data, dict) else None
    return str(loi) if loi else None


def loi_serpapi(e: SerpApiError, input_context: dict[str, Any]) -> dict[str, Any]:
    if e.het_key:
        return format_mcp_tool_error_response(
            error_message=(
                "Tạm thời hết lượt tra cứu dữ liệu (SerpApi). Không gọi lại tool này "
                "ngay; báo người dùng dữ liệu giá/địa điểm trực tuyến chưa sẵn sàng."
            ),
            exception=e,
            error_code=MA_LOI_HET_QUOTA,
            input_context=input_context,
        )
    return format_mcp_tool_error_response(
        error_message="Không lấy được dữ liệu từ SerpApi. Kiểm tra lại tham số hoặc thử lại sau.",
        exception=e,
        error_code=-3,
        input_context=input_context,
    )


def nhan_lua_chon(nhan: dict[Any, str]):
    """Gắn nhãn tiếng Việt cho tham số Literal mà không đổi giá trị gửi lên.

    Giữ `enum` và thêm `enumNames` vào JSON Schema để client (MCP Inspector...)
    hiện "restaurants (nhà hàng)" trong dropdown nhưng model vẫn gửi
    "restaurants". Không dùng `oneOf + const/title` vì Inspector hiện thành ô
    nhập chữ thay vì dropdown. Pydantic vẫn validate theo Literal như cũ.

    Dùng: Field(description=..., json_schema_extra=nhan_lua_chon({...}))
    """

    def _sua(schema: dict[str, Any]) -> None:
        gia_tri = schema.get("enum") or list(nhan)
        schema["enumNames"] = [f"{v} ({nhan[v]})" if v in nhan else str(v) for v in gia_tri]

    return _sua
