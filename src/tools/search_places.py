"""Tool tìm địa điểm (quán ăn, điểm tham quan, cây xăng...) trên Google Maps qua SerpApi."""

from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.serpapi_client import SerpApiError, serpapi_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._serpapi_common import khong_co_ket_qua, loi_serpapi, tham_so_vn

logger = structlog.get_logger(__name__)


def _gon_dia_diem(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": p.get("title"),
        "place_id": p.get("place_id"),
        "data_id": p.get("data_id"),
        "rating": p.get("rating"),
        "reviews": p.get("reviews"),
        "price": p.get("price"),
        "type": p.get("type"),
        "address": p.get("address"),
        "gps": p.get("gps_coordinates"),
        "open_state": p.get("open_state"),
        "phone": p.get("phone"),
        "website": p.get("website"),
        "service_options": p.get("service_options"),
    }


def register_search_places_tool(mcp: FastMCP):
    @mcp.tool(
        name="search_places",
        title="Search Places (Tìm địa điểm)",
        description=(
            "Tìm địa điểm trên Google Maps theo truy vấn tự nhiên: quán ăn, quán chay, "
            "cà phê, điểm tham quan, bãi biển, chợ, bệnh viện, nhà thuốc, cây xăng...\n\n"
            "Khi nào dùng: lấy danh sách ứng viên kèm rating, số review, mức giá, địa chỉ, "
            "toạ độ, trạng thái mở cửa. Nên đưa thành phố/khu vực vào `query` "
            "(VD 'quán chay Hội An') hoặc truyền `lat`/`lng`.\n"
            "Khi nào KHÔNG dùng: khách sạn có giá phòng (dùng search_hotels). Cần giờ mở cửa "
            "từng ngày, giờ đông khách, thông tin tiếp cận xe lăn: gọi get_place_details với "
            "`place_id` lấy từ đây.\n\n"
            "Mỗi lần gọi tốn 1 lượt quota, trả tối đa 20 địa điểm."
        ),
        tags={"journey", "serpapi", "maps"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "serpapi:google_maps"},
    )
    async def search_places(
        query: Annotated[
            str, Field(description="Truy vấn tự nhiên, VD 'quán chay Hội An', 'bãi biển gần Quy Nhơn'.")
        ],
        lat: Annotated[
            float | None, Field(ge=-90, le=90, description="Vĩ độ tâm vùng tìm. Đi cùng `lng`.")
        ] = None,
        lng: Annotated[
            float | None, Field(ge=-180, le=180, description="Kinh độ tâm vùng tìm. Đi cùng `lat`.")
        ] = None,
        zoom: Annotated[
            int,
            Field(ge=3, le=21, description="Mức zoom khi có lat/lng: 14 ≈ cả quận, 17 ≈ vài con phố."),
        ] = 14,
        min_rating: Annotated[
            float | None, Field(ge=0, le=5, description="Bỏ các địa điểm rating thấp hơn mức này.")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=20, description="Số địa điểm tối đa trả về.")] = 10,
    ) -> dict[str, Any]:
        input_context = {"query": query, "lat": lat, "lng": lng}
        try:
            if not query.strip():
                return format_validation_error("query", query, "không được để trống")
            if (lat is None) != (lng is None):
                return format_validation_error("lat/lng", input_context, "phải truyền cả hai hoặc bỏ cả hai")

            data = await serpapi_client.search(
                "google_maps",
                type="search",
                q=query.strip(),
                ll=f"@{lat},{lng},{zoom}z" if lat is not None else None,
                **tham_so_vn(),
            )

            ghi_chu = khong_co_ket_qua(data)
            ket_qua = data.get("local_results") or []
            # Truy vấn đủ cụ thể thì Google trả thẳng một địa điểm.
            if not ket_qua and data.get("place_results"):
                ket_qua = [data["place_results"]]
            if min_rating is not None:
                ket_qua = [p for p in ket_qua if (p.get("rating") or 0) >= min_rating]

            return drop_empty(
                {
                    "status": "success",
                    "query": query,
                    "total_found": len(ket_qua),
                    "places": [_gon_dia_diem(p) for p in ket_qua[:limit]],
                    "note": ghi_chu,
                }
            )

        except SerpApiError as e:
            logger.error("search_places gọi SerpApi thất bại", error=str(e))
            return loi_serpapi(e, input_context)
        except Exception as e:
            logger.error("search_places thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không tìm được địa điểm", exception=e, input_context=input_context
            )

    return search_places
