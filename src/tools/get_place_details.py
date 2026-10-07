"""Tool lấy chi tiết một địa điểm Google Maps: giờ mở cửa, giờ đông khách, tiếp cận."""

from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.serpapi_client import SerpApiError, serpapi_client
from ..services.travel_db import travel_db
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._db_common import THU_VI, fallback_db, muc_dong_theo_gio
from ._serpapi_common import khong_co_ket_qua, loi_serpapi, tham_so_vn

logger = structlog.get_logger(__name__)


def _gon_gio_dong_khach(popular_times: dict[str, Any] | None) -> dict[str, Any] | None:
    """Rút `popular_times` về {thứ: [[giờ, mức đông 0-100], ...]} bỏ giờ đóng cửa."""
    if not popular_times:
        return None
    theo_ngay = {}
    for ngay, cac_gio in (popular_times.get("graph_results") or {}).items():
        diem = [
            [g.get("time"), g.get("busyness_score")]
            for g in cac_gio
            if g.get("busyness_score")
        ]
        if diem:
            theo_ngay[ngay] = diem
    return drop_empty(
        {
            "live": (popular_times.get("live_hash") or {}).get("info"),
            "by_day": theo_ngay or None,
        }
    ) or None


async def _tu_db(place_id: str) -> dict[str, Any] | None:
    p = await travel_db.find_place(place_id)
    if not p:
        return None
    dong_khach = {
        THU_VI[thu]: [[f"{g:02d}:00", v] for g, v in sorted(muc.items()) if v]
        for thu in range(7)
        if (muc := muc_dong_theo_gio(p.get("popular_times"), thu))
    }
    return {
        "status": "success",
        "place": {
            "name": p.get("title"),
            "place_id": p.get("place_id"),
            "data_id": p.get("data_id"),
            # rating 0 với 0 review là chưa có đánh giá, không phải bị chấm 0 sao.
            "rating": p.get("rating") or None,
            "reviews": p.get("review_count"),
            "reviews_per_rating": p.get("reviews_per_rating"),
            "price": p.get("price_range"),
            "type": p.get("category"),
            "address": p.get("address"),
            "gps": {"latitude": p["lat"], "longitude": p["lng"]} if p.get("lat") is not None else None,
            "phone": p.get("phone"),
            "website": p.get("website"),
            "hours": p.get("open_hours"),
            "popular_times": {"by_day": dong_khach} if dong_khach else None,
            "attributes": p.get("attributes"),
            "attributes_not_available": p.get("attributes_not_available"),
            "description": p.get("description") or p.get("summary"),
        },
        "other_matches": p.get("_other_matches"),
        "data_as_of": str(p.get("fetched_at") or "")[:10] or None,
    }


def register_get_place_details_tool(mcp: FastMCP):
    @mcp.tool(
        name="get_place_details",
        title="Get Place Details (Chi tiết địa điểm)",
        description=(
            "Lấy chi tiết một địa điểm Google Maps theo `place_id` (lấy từ search_places): "
            "giờ mở cửa từng ngày, giờ đông khách theo giờ (0-100), mức giá, số điện thoại, "
            "website, và nhóm thuộc tính `attributes` (tiếp cận xe lăn, có món chay, hợp "
            "trẻ em, chỗ đỗ xe, thanh toán...) khi Google có dữ liệu.\n\n"
            "Khi nào dùng: kiểm tra một điểm có mở vào giờ dự định không, tránh giờ đông, "
            "kiểm tra ràng buộc tiếp cận / ăn kiêng.\n"
            "Mỗi lần gọi tốn 1 lượt quota — chỉ gọi cho điểm đã lọt vào danh sách ngắn. "
            "Khi SerpApi lỗi / hết quota tool tự trả dữ liệu đã lưu (`data_source`='ailusion_db')."
        ),
        tags={"journey", "serpapi", "maps"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "serpapi:google_maps"},
    )
    async def get_place_details(
        place_id: Annotated[
            str, Field(description="Google place_id, VD 'ChIJ...'. Lấy từ kết quả search_places.")
        ],
    ) -> dict[str, Any]:
        input_context = {"place_id": place_id}
        try:
            if not place_id.strip():
                return format_validation_error("place_id", place_id, "không được để trống")

            data = await serpapi_client.search(
                "google_maps", type="place", place_id=place_id.strip(), **tham_so_vn()
            )

            ghi_chu = khong_co_ket_qua(data)
            p = data.get("place_results") or {}
            if not p:
                return drop_empty(
                    {"status": "success", "place_id": place_id, "place": None, "note": ghi_chu or "Không tìm thấy địa điểm"}
                )

            return drop_empty(
                {
                    "status": "success",
                    "place": {
                        "name": p.get("title"),
                        "place_id": p.get("place_id") or place_id,
                        "data_id": p.get("data_id"),
                        "rating": p.get("rating"),
                        "reviews": p.get("reviews"),
                        "price": p.get("price"),
                        "type": p.get("type"),
                        "address": p.get("address"),
                        "gps": p.get("gps_coordinates"),
                        "phone": p.get("phone"),
                        "website": p.get("website"),
                        "open_state": p.get("open_state"),
                        "hours": p.get("hours") or p.get("operating_hours"),
                        "popular_times": _gon_gio_dong_khach(p.get("popular_times")),
                        "attributes": p.get("extensions"),
                        "description": p.get("description"),
                        "review_topics": [
                            {"keyword": t.get("keyword"), "mentions": t.get("mentions")}
                            for t in (p.get("user_reviews") or {}).get("summary") or []
                        ][:10],
                    },
                    "note": ghi_chu,
                }
            )

        except SerpApiError as e:
            logger.error("get_place_details gọi SerpApi thất bại", error=str(e))
            return await fallback_db(
                "get_place_details", loi_serpapi(e, input_context), lambda: _tu_db(place_id.strip())
            )
        except Exception as e:
            logger.error("get_place_details thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không lấy được chi tiết địa điểm", exception=e, input_context=input_context
            )

    return get_place_details
