"""Tool tra mã IATA sân bay theo tên thành phố / điểm đến."""

import re
from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.airport_lookup import tra_san_bay_vn
from ..services.serpapi_client import SerpApiError, serpapi_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._serpapi_common import loi_serpapi

logger = structlog.get_logger(__name__)

_MA_IATA = re.compile(r"^[A-Z]{3}$")
_GIAI_THICH_KHOP = {
    "code": "đúng mã sân bay",
    "city": "thành phố có sân bay",
    "name": "tên sân bay",
    "alias": "tên gọi khác của thành phố/tỉnh",
    "province": "cùng tỉnh/thành",
    "serves": "điểm đến không có sân bay, thường bay tới đây",
    "chua": "khớp một phần tên",
}


def _tu_serpapi(data: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """Rút danh sách sân bay từ kết quả google_flights_autocomplete."""
    ket_qua: list[dict[str, Any]] = []
    for goi_y in data.get("suggestions") or []:
        san_bay_con = goi_y.get("airports") or []
        if san_bay_con:
            for sb in san_bay_con:
                ket_qua.append(
                    {
                        "code": sb.get("id"),
                        "name": sb.get("name"),
                        "city": sb.get("city") or goi_y.get("name"),
                        "distance": sb.get("distance"),
                    }
                )
        elif _MA_IATA.match(str(goi_y.get("id") or "")):
            ket_qua.append({"code": goi_y["id"], "name": goi_y.get("name"), "city": goi_y.get("description")})
    # Bỏ trùng mã, giữ thứ tự
    da_co: set[str] = set()
    gon = []
    for sb in ket_qua:
        if sb["code"] and sb["code"] not in da_co:
            da_co.add(sb["code"])
            gon.append(sb)
    return gon[:limit]


def register_find_airports_tool(mcp: FastMCP):
    @mcp.tool(
        name="find_airports",
        title="Find Airports (Tra mã sân bay)",
        description=(
            "Tra mã IATA sân bay từ tên thành phố, tỉnh, tên sân bay hoặc điểm du lịch. "
            "Gọi tool này TRƯỚC search_flights khi chưa chắc mã sân bay — đừng tự đoán mã.\n\n"
            "Việt Nam: tra từ bảng nội bộ (miễn phí), hiểu cả tên tỉnh trước/sau sáp nhập "
            "1/7/2025 và điểm đến không có sân bay (VD 'Hội An' -> DAD, 'Hạ Long' -> VDO, "
            "'Sa Pa' -> HAN), có dấu hay không dấu đều được.\n"
            "Nước ngoài: tra qua Google Flights (tốn 1 lượt quota SerpApi).\n\n"
            "Trả về: `airports` xếp theo độ khớp, mỗi phần tử có `code` (IATA) và `match` "
            "giải thích vì sao khớp. Nếu kết quả đầu là 'serves' thì điểm đến không có sân "
            "bay — báo người dùng cần đi thêm đường bộ."
        ),
        tags={"journey", "flights", "lookup"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "bang-noi-bo + serpapi:google_flights_autocomplete"},
    )
    async def find_airports(
        query: Annotated[
            str,
            Field(description="Tên thành phố / tỉnh / sân bay / điểm đến. VD 'Đà Lạt', 'Hoi An', 'Bangkok', 'Tokyo'."),
        ],
        include_international: Annotated[
            bool,
            Field(description="Khi không thấy ở Việt Nam thì tra sân bay nước ngoài qua Google Flights."),
        ] = True,
        limit: Annotated[int, Field(ge=1, le=10, description="Số sân bay tối đa trả về.")] = 5,
    ) -> dict[str, Any]:
        input_context = {"query": query}
        try:
            if not query.strip():
                return format_validation_error("query", query, "không được để trống")

            noi_dia = tra_san_bay_vn(query)
            if noi_dia:
                return drop_empty(
                    {
                        "status": "success",
                        "query": query,
                        "source": "vn_table",
                        "airports": [
                            {
                                "code": sb.code,
                                "name": f"Sân bay {sb.name}",
                                "city": sb.city,
                                "province": sb.province,
                                "international": sb.international,
                                "match": _GIAI_THICH_KHOP[loai],
                            }
                            for sb, _, loai in noi_dia[:limit]
                        ],
                    }
                )

            if not include_international:
                return {
                    "status": "success",
                    "query": query,
                    "airports": [],
                    "note": "Không thấy sân bay Việt Nam nào khớp",
                }

            data = await serpapi_client.search(
                "google_flights_autocomplete", q=query.strip(), hl="vi", gl="vn"
            )
            san_bay = _tu_serpapi(data, limit)
            return drop_empty(
                {
                    "status": "success",
                    "query": query,
                    "source": "google_flights",
                    "airports": san_bay,
                    "note": None if san_bay else "Không tìm thấy sân bay khớp, thử tên thành phố bằng tiếng Anh",
                }
            )

        except SerpApiError as e:
            logger.error("find_airports gọi SerpApi thất bại", error=str(e))
            return loi_serpapi(e, input_context)
        except Exception as e:
            logger.error("find_airports thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không tra được sân bay", exception=e, input_context=input_context
            )

    return find_airports
