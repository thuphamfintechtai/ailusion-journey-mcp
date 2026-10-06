"""Tool tìm sự kiện, concert, lễ hội có ngày cụ thể ở một địa phương (Tavily)."""

from datetime import date, timedelta
from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.tavily_client import TavilyError, tavily_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._serpapi_common import doc_ngay
from ._tavily_common import gon_ket_qua, loi_tavily

logger = structlog.get_logger(__name__)


def _thang_trong_khoang(bd: date, kt: date) -> list[str]:
    thang, d = [], bd.replace(day=1)
    while d <= kt and len(thang) < 3:
        thang.append(f"tháng {d.month}/{d.year}")
        d = (d + timedelta(days=32)).replace(day=1)
    return thang


def register_search_events_tool(mcp: FastMCP):
    @mcp.tool(
        name="search_events",
        title="Search Events (Tìm sự kiện)",
        description=(
            "Tìm sự kiện có ngày cụ thể ở một địa phương trong khoảng ngày đi: concert, lễ hội "
            "pháo hoa, festival, triển lãm, giải chạy, hội chợ... Nguồn là các trang web / báo "
            "đã đăng lịch sự kiện; `answer` là tóm tắt tự động, PHẢI đối chiếu với `results` "
            "trước khi đưa vào lịch trình.\n\n"
            "Khi nào dùng: biết có gì đặc biệt trong dịp đi, hoặc cảnh báo đông đúc / tăng giá.\n"
            "Lễ hội truyền thống theo âm lịch đã có trong vn_calendar (không tốn credit), dùng "
            "tool đó trước.\n\nMỗi lần gọi tốn 1 credit Tavily."
        ),
        tags={"journey", "tavily", "events"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "tavily:search"},
    )
    async def search_events(
        location: Annotated[str, Field(description="Địa phương, VD 'Đà Nẵng', 'Hà Nội'.")],
        start_date: Annotated[str, Field(description="Ngày bắt đầu chuyến đi, YYYY-MM-DD.")],
        end_date: Annotated[str | None, Field(description="Ngày kết thúc, YYYY-MM-DD. Bỏ trống = bằng start_date.")] = None,
        kind: Annotated[
            str | None, Field(description="Loại sự kiện cần tìm, VD 'concert', 'pháo hoa', 'chợ đêm', 'cho trẻ em'.")
        ] = None,
        max_results: Annotated[int, Field(ge=1, le=10, description="Số nguồn tối đa.")] = 6,
    ) -> dict[str, Any]:
        input_context = {"location": location, "start_date": start_date, "end_date": end_date}
        try:
            if not location.strip():
                return format_validation_error("location", location, "không được để trống")
            try:
                bd = doc_ngay(start_date, "start_date")
                kt = doc_ngay(end_date, "end_date") if end_date else bd
            except ValueError as e:
                return format_mcp_tool_error_response(str(e), error_code=-2, input_context=input_context)
            if kt < bd:
                return format_validation_error("end_date", end_date, "trước start_date")

            thang = " ".join(_thang_trong_khoang(bd, kt))
            loai = (kind or "sự kiện lễ hội concert festival").strip()
            cau = f"lịch {loai} {location.strip()} {thang}"
            data = await tavily_client.search(
                cau,
                topic="general",
                search_depth="basic",
                max_results=max_results,
                include_answer="basic",
                include_published_date=True,
                country="vietnam",
                # Lịch sự kiện thường công bố trước vài tuần đến vài tháng.
                start_date=(min(bd, date.today()) - timedelta(days=120)).isoformat(),
            )
            ket_qua = data.get("results") or []
            return drop_empty(
                {
                    "status": "success",
                    "location": location,
                    "range": {"from": bd.isoformat(), "to": kt.isoformat()},
                    "query_used": cau,
                    "answer": data.get("answer"),
                    "answer_note": "Tóm tắt tự động (có thể bằng tiếng Anh) từ các nguồn bên dưới, cần đối chiếu ngày giờ" if data.get("answer") else None,
                    "results": [gon_ket_qua(r) for r in ket_qua],
                    "note": None if ket_qua else "Không tìm thấy sự kiện, thử đổi `kind` hoặc tên địa phương",
                }
            )

        except TavilyError as e:
            logger.error("search_events gọi Tavily thất bại", error=str(e))
            return loi_tavily(e, input_context)
        except Exception as e:
            logger.error("search_events thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không tìm được sự kiện", exception=e, input_context=input_context
            )

    return search_events
