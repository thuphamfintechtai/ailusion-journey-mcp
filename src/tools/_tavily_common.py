"""Phần dùng chung cho các tool Tavily. File bắt đầu bằng `_` nên không bị đăng ký thành tool."""

from typing import Any
from urllib.parse import urlparse

from ..services.tavily_client import TavilyError
from ..utils.error_handler import format_mcp_tool_error_response
from ._serpapi_common import MA_LOI_HET_QUOTA

# Báo có giấy phép, hay đưa tin giao thông / thời tiết / sự kiện địa phương.
BAO_VN = (
    "vnexpress.net", "tuoitre.vn", "thanhnien.vn", "dantri.com.vn", "vietnamnet.vn",
    "nld.com.vn", "laodong.vn", "tienphong.vn", "vtv.vn", "vov.vn", "zingnews.vn",
    "znews.vn", "baomoi.com", "vietnamplus.vn", "sggp.org.vn", "hanoimoi.vn",
    "plo.vn", "kinhtedothi.vn", "baochinhphu.vn", "nhandan.vn", "qdnd.vn",
    "daidoanket.vn", "vnanet.vn", "cafef.vn", "tinnhanhchungkhoan.vn", "vneconomy.vn",
    "congan.com.vn", "cand.com.vn", "suckhoedoisong.vn", "giaothongvantai.com.vn",
)
_MAX_NOI_DUNG = 1200  # ký tự mỗi kết quả khi lấy nội dung trang trong web_search


def ten_mien(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def loai_nguon(url: str) -> str:
    """Xếp nguồn để model cân độ tin cậy: chinh_thong (.gov.vn) > bao_chi > khac."""
    host = ten_mien(url)
    if host.endswith(".gov.vn") or host == "gov.vn":
        return "chinh_thong"
    if any(host == b or host.endswith("." + b) for b in BAO_VN):
        return "bao_chi"
    # Báo đảng bộ tỉnh / thành: baodanang.vn, baolamdong.vn, baoquangninh.vn...
    if host.startswith("bao") and host.endswith(".vn") and host.count(".") == 1:
        return "bao_chi"
    return "khac"


def gon_ket_qua(r: dict[str, Any], lay_noi_dung: bool = False) -> dict[str, Any]:
    url = r.get("url") or ""
    noi_dung = r.get("raw_content") if lay_noi_dung else None
    return {
        "title": r.get("title"),
        "url": url,
        "domain": ten_mien(url),
        "source_type": loai_nguon(url),
        "published_date": r.get("published_date"),
        "snippet": r.get("content"),
        "content": noi_dung[:_MAX_NOI_DUNG] if noi_dung else None,
        "score": round(r["score"], 3) if isinstance(r.get("score"), int | float) else None,
    }


def loi_tavily(e: TavilyError, input_context: dict[str, Any]) -> dict[str, Any]:
    if e.het_key:
        return format_mcp_tool_error_response(
            error_message=(
                "Tạm thời hết lượt tìm kiếm web (Tavily). Không gọi lại ngay; trả lời "
                "bằng dữ liệu đã có và nói rõ chưa kiểm chứng được trên web."
            ),
            exception=e,
            error_code=MA_LOI_HET_QUOTA,
            input_context=input_context,
        )
    return format_mcp_tool_error_response(
        error_message=f"Không tìm kiếm được trên web: {e}",
        exception=e,
        error_code=-3,
        input_context=input_context,
    )
