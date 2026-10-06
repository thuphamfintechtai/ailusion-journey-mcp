"""Tool tìm địa điểm trên Tripadvisor (qua SerpApi) — nguồn rating thứ hai bên cạnh Google."""

from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.serpapi_client import SerpApiError, serpapi_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._serpapi_common import khong_co_ket_qua, loi_serpapi, nhan_lua_chon

logger = structlog.get_logger(__name__)

_SSRC = {
    "all": "a",
    "restaurants": "r",
    "attractions": "A",
    "hotels": "h",
    "destinations": "g",
    "forums": "f",
}


def _gon_dia_diem(p: dict[str, Any]) -> dict[str, Any]:
    review_noi_bat = p.get("highlighted_review") or {}
    return {
        "name": p.get("title"),
        "place_id": p.get("place_id"),
        "place_type": p.get("place_type"),
        "rating": p.get("rating"),
        "reviews": p.get("reviews"),
        "location": p.get("location"),
        "highlighted_review": review_noi_bat.get("text") if isinstance(review_noi_bat, dict) else review_noi_bat,
        "link": p.get("link"),
    }


def register_search_tripadvisor_tool(mcp: FastMCP):
    @mcp.tool(
        name="search_tripadvisor",
        title="Search Tripadvisor (Tìm trên Tripadvisor)",
        description=(
            "Tìm nhà hàng, điểm tham quan, khách sạn, điểm đến hoặc bài diễn đàn trên "
            "Tripadvisor kèm rating và số review.\n\n"
            "Khi nào dùng: đối chiếu rating với Google (search_places) để phát hiện chênh "
            "lệch, hoặc tìm góc nhìn của khách quốc tế / bài hỏi đáp trên diễn đàn.\n"
            "Khi nào KHÔNG dùng: cần giá phòng (dùng search_hotels).\n"
            "Mỗi lần gọi tốn 1 lượt quota."
        ),
        tags={"journey", "serpapi", "reviews"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "serpapi:tripadvisor"},
    )
    async def search_tripadvisor(
        query: Annotated[str, Field(description="Truy vấn, VD 'Hoi An restaurants', 'Bà Nà Hills'.")],
        category: Annotated[
            Literal["all", "restaurants", "attractions", "hotels", "destinations", "forums"],
            Field(
                description="Loại kết quả.",
                json_schema_extra=nhan_lua_chon(
                    {
                        "all": "tất cả",
                        "restaurants": "nhà hàng",
                        "attractions": "điểm tham quan",
                        "hotels": "khách sạn",
                        "destinations": "điểm đến",
                        "forums": "diễn đàn",
                    }
                ),
            ),
        ] = "all",
        lat: Annotated[float | None, Field(ge=-90, le=90, description="Vĩ độ để tìm quanh một điểm.")] = None,
        lon: Annotated[float | None, Field(ge=-180, le=180, description="Kinh độ, đi cùng `lat`.")] = None,
        limit: Annotated[int, Field(ge=1, le=30, description="Số kết quả tối đa trả về.")] = 10,
    ) -> dict[str, Any]:
        input_context = {"query": query, "category": category}
        try:
            if not query.strip():
                return format_validation_error("query", query, "không được để trống")
            if (lat is None) != (lon is None):
                return format_validation_error("lat/lon", input_context, "phải truyền cả hai hoặc bỏ cả hai")

            data = await serpapi_client.search(
                "tripadvisor",
                q=query.strip(),
                ssrc=_SSRC[category],
                lat=lat,
                lon=lon,
                limit=limit,
            )

            ghi_chu = khong_co_ket_qua(data)
            if category == "forums":
                bai = [
                    {"title": f.get("title"), "snippet": f.get("snippet"), "link": f.get("link")}
                    for f in (data.get("forums") or [])[:limit]
                ]
                return drop_empty(
                    {"status": "success", "query": query, "forums": bai, "note": ghi_chu}
                )

            ds = data.get("places") or []
            return drop_empty(
                {
                    "status": "success",
                    "query": query,
                    "total_found": len(ds),
                    "places": [_gon_dia_diem(p) for p in ds[:limit]],
                    "note": ghi_chu,
                }
            )

        except SerpApiError as e:
            logger.error("search_tripadvisor gọi SerpApi thất bại", error=str(e))
            return loi_serpapi(e, input_context)
        except Exception as e:
            logger.error("search_tripadvisor thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không tìm được trên Tripadvisor", exception=e, input_context=input_context
            )

    return search_tripadvisor
