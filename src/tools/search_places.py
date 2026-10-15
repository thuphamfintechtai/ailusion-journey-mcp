"""Tool tìm địa điểm (quán ăn, điểm tham quan, cây xăng...) trên Google Maps qua SerpApi."""

from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.serpapi_client import SerpApiError, serpapi_client
from ..services.travel_db import travel_db
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._db_common import fallback_db, gon_dia_diem, loc_dia_diem, moi_nhat
from ._serpapi_common import khong_co_ket_qua, loi_serpapi, tham_so_vn

logger = structlog.get_logger(__name__)


async def _tu_db(
    query: str, lat: float | None, lng: float | None, zoom: int, min_rating: float | None, limit: int
) -> dict[str, Any] | None:
    """Tìm trong gmaps_place đã lưu: theo toạ độ nếu có, không thì theo điểm đến nhắc trong query."""
    dest = await travel_db.find_destination(query)
    # zoom 14 ~ cả quận (~5 km), mỗi mức zoom gấp đôi / chia đôi bán kính.
    ban_kinh = 5 * 2 ** (14 - zoom) if lat is not None else None
    ket_qua, khop_du = loc_dia_diem(
        await travel_db.places(),
        query=query,
        destination_key=dest,
        lat=lat,
        lng=lng,
        radius_km=ban_kinh,
        min_rating=min_rating,
    )
    if not ket_qua:
        return None
    return {
        "status": "success",
        "query": query,
        "total_found": len(ket_qua),
        "places": [gon_dia_diem(p) for p in ket_qua[:limit]],
        "data_as_of": moi_nhat(ket_qua),
        "note": " ".join(
            g
            for g in (
                None if khop_du else "Không có điểm khớp đủ mọi từ khoá, đã trả các điểm khớp một phần.",
                None
                if dest or lat is not None
                else "Truy vấn không nêu khu vực nên kết quả gộp nhiều thành phố; xem `address`.",
            )
            if g
        )
        or None,
    }


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
        "thumbnail": p.get("thumbnail"),
    }


def register_search_places_tool(mcp: FastMCP):
    @mcp.tool(
        name="search_places",
        title="Search Places (Tìm địa điểm)",
        description=(
            "Tìm địa điểm trên Google Maps theo truy vấn tự nhiên: quán ăn, quán chay, "
            "cà phê, điểm tham quan, bãi biển, chợ, bệnh viện, nhà thuốc, cây xăng...\n\n"
            "Khi nào dùng: CHỈ khi cần rating, số review, mức giá hoặc trạng thái mở cửa để xếp "
            "hạng ứng viên. Chỉ cần tìm địa điểm / toạ độ / khoảng cách thì dùng find_nearby "
            "(Vietmap, không tốn quota SerpApi). Nên đưa thành phố/khu vực vào `query` "
            "(VD 'quán chay Hội An') hoặc truyền `lat`/`lng`.\n"
            "Khi nào KHÔNG dùng: khách sạn có giá phòng (dùng search_hotels). Cần giờ mở cửa "
            "từng ngày, giờ đông khách, thông tin tiếp cận xe lăn: gọi get_place_details với "
            "`place_id` lấy từ đây.\n\n"
            "Mỗi lần gọi tốn 1 lượt quota, trả tối đa 20 địa điểm. Khi SerpApi lỗi / hết quota tool "
            "tự trả dữ liệu đã lưu (`data_source`='ailusion_db'); muốn duyệt dữ liệu đã lưu chủ động "
            "(miễn phí) thì dùng find_places."
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
            return await fallback_db(
                "search_places",
                loi_serpapi(e, input_context),
                lambda: _tu_db(query.strip(), lat, lng, zoom, min_rating, limit),
            )
        except Exception as e:
            logger.error("search_places thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không tìm được địa điểm", exception=e, input_context=input_context
            )

    return search_places
