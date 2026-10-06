"""Tool chấm độ tin cậy nguồn web: loại nguồn, thứ hạng domain (Tranco), tuổi domain (Wayback)."""

import asyncio
from datetime import date
from typing import Annotated, Any
from urllib.parse import urlparse

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.open_data_client import open_data_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._tavily_common import loai_nguon, ten_mien

logger = structlog.get_logger(__name__)

_TOI_DA = 10


def cham_diem(loai: str, hang: int | None, tuoi_nam: float | None, biet_tuoi: bool = True) -> tuple[int, list[str]]:
    """Điểm 0-100 theo kinh nghiệm; trả kèm lý do để model giải thích được."""
    diem, ly_do = 40, []
    if loai == "chinh_thong":
        diem += 40
        ly_do.append("trang .gov.vn")
    elif loai == "bao_chi":
        diem += 30
        ly_do.append("báo có giấy phép")
    if hang is not None:
        cong = 20 if hang <= 1000 else 15 if hang <= 10000 else 10 if hang <= 100000 else 5
        diem += cong
        ly_do.append(f"hạng Tranco {hang:,}".replace(",", "."))
    else:
        ly_do.append("không có trong top 1 triệu domain (Tranco)")
    if tuoi_nam is not None:
        if tuoi_nam >= 10:
            diem += 10
            ly_do.append(f"domain có từ {tuoi_nam:.0f} năm trước")
        elif tuoi_nam >= 3:
            diem += 5
            ly_do.append(f"domain có từ {tuoi_nam:.0f} năm trước")
        elif tuoi_nam < 1:
            diem -= 15
            ly_do.append("domain mới xuất hiện dưới 1 năm")
    elif biet_tuoi:
        diem -= 10
        ly_do.append("Internet Archive chưa từng lưu domain này")
    else:
        ly_do.append("không tra được tuổi domain")
    return max(0, min(100, diem)), ly_do


def register_source_trust_tool(mcp: FastMCP):
    @mcp.tool(
        name="source_trust",
        title="Source Trust (Chấm tin cậy nguồn)",
        description=(
            "Chấm độ tin cậy (0-100, cao / trung bình / thấp) cho các URL hoặc domain dựa trên: "
            "loại nguồn (.gov.vn, báo có giấy phép, khác), thứ hạng phổ biến toàn cầu (Tranco top "
            "1 triệu), tuổi domain (lần đầu Internet Archive lưu). Trả kèm lý do.\n\n"
            "Khi nào dùng: bước Phản biện / Dò mâu thuẫn — nguồn nào đáng tin hơn khi hai nguồn "
            "nói khác nhau; cảnh giác trang bán vé / tour mới lập, không rõ lai lịch.\n"
            "Lưu ý: điểm theo kinh nghiệm, không phải chứng nhận; trang chính hãng nhỏ có thể điểm "
            "thấp. Không tốn quota trả phí (Tranco giới hạn 1 lượt / giây nên tối đa 10 domain)."
        ),
        tags={"journey", "verification"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "tranco + wayback cdx"},
    )
    async def source_trust(
        urls: Annotated[
            list[str], Field(min_length=1, max_length=_TOI_DA, description="URL hoặc domain, VD 'https://vnexpress.net/abc'.")
        ],
    ) -> dict[str, Any]:
        input_context = {"urls": urls}
        try:
            mien: list[str] = []
            for u in urls:
                d = ten_mien(u if "://" in u else f"https://{u}")
                if not d or "." not in d:
                    return format_validation_error("urls", u, "không đọc được domain")
                if d not in mien:
                    mien.append(d)

            async def mot(d: str) -> dict[str, Any]:
                hang, ngay = await asyncio.gather(
                    open_data_client.tranco_rank(d), open_data_client.wayback_first_seen(d), return_exceptions=True
                )
                ngay_goc = ngay
                ghi_chu = [f"{ten}: {r}" for ten, r in (("tranco", hang), ("wayback", ngay)) if isinstance(r, Exception)]
                hang = hang if isinstance(hang, int) else None
                ngay = ngay if isinstance(ngay, str) else None
                tuoi = (date.today() - date.fromisoformat(ngay)).days / 365.25 if ngay else None
                loai = loai_nguon(f"https://{d}")
                # Wayback lỗi thì không biết tuổi domain: đừng trừ điểm như "chưa từng lưu".
                diem, ly_do = cham_diem(loai, hang, tuoi, biet_tuoi=not isinstance(ngay_goc, Exception))
                return {
                    "domain": d,
                    "source_type": loai,
                    "tranco_rank": hang,
                    "first_seen": ngay,
                    "score": diem,
                    "level": "cao" if diem >= 70 else "trung bình" if diem >= 45 else "thấp",
                    "reasons": ly_do,
                    "errors": ghi_chu or None,
                }

            ket_qua = await asyncio.gather(*(mot(d) for d in mien))
            return drop_empty(
                {
                    "status": "success",
                    "results": sorted(ket_qua, key=lambda r: -r["score"]),
                    "note": "Điểm theo kinh nghiệm để so sánh nguồn, không phải chứng nhận độ tin cậy.",
                }
            )
        except Exception as e:
            logger.error("source_trust thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response("Không chấm được nguồn", exception=e, input_context=input_context)

    return source_trust
