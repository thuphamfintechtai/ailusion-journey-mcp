"""Tool duyệt địa điểm Google Maps đã lưu trong DB: lọc theo điểm đến, loại, rating, bán kính."""

from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.travel_db import TravelDbError, travel_db
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._db_common import NGUON_DB, chon_diem_den, gon_dia_diem, goi_y_diem_den, loc_dia_diem, loi_db, moi_nhat
from ._serpapi_common import nhan_lua_chon

logger = structlog.get_logger(__name__)


def register_browse_places_tool(mcp: FastMCP):
    @mcp.tool(
        name="browse_places",
        title="Browse Saved Places (Duyệt địa điểm đã lưu)",
        description=(
            "Duyệt địa điểm Google Maps đã lưu sẵn (khách sạn, nhà hàng, quán cà phê, chùa, điểm tham "
            "quan, spa, bar, công viên...) kèm rating, `rating_adjusted` (hiệu chỉnh theo số review), số "
            "review, mức giá (chuỗi gốc `price` và `price_vnd` đã quy ra VND), địa chỉ, toạ độ. Lọc theo "
            "điểm đến, từ khoá, loại, rating tối thiểu, số review tối thiểu, tiếp cận xe lăn, khoảng giá "
            "(`max_price_vnd` / `min_price_vnd`), hoặc bán kính quanh một toạ độ.\n\n"
            "Khi nào dùng: lập danh sách ứng viên nhanh và MIỄN PHÍ (không tốn quota) cho các điểm đến "
            "có dữ liệu; chọn top theo độ phổ biến. Chỉ có một số điểm đến (xem `available_destinations` "
            "khi không khớp).\n"
            "Khi nào KHÔNG dùng: điểm đến chưa có dữ liệu hoặc cần trạng thái mở cửa thời gian thực -> "
            "search_places / find_nearby.\n\n"
            "`place_id` trả về dùng được cho get_place_details, check_opening_hours, best_time_to_visit."
        ),
        tags={"journey", "db", "maps"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "postgres:gmaps_place"},
    )
    async def browse_places(
        destination: Annotated[
            str | None, Field(description="Điểm đến, VD 'Hà Nội', 'Đà Lạt', 'Hải Phòng'. Bỏ trống nếu dùng lat/lng.")
        ] = None,
        query: Annotated[
            str | None, Field(description="Từ khoá trong tên / loại / mô tả, VD 'bún chả', 'chay', 'rooftop'.")
        ] = None,
        category: Annotated[
            str | None,
            Field(description="Loại địa điểm theo Google Maps tiếng Việt, VD 'Khách sạn', 'Quán cà phê', 'Chùa', 'Bảo tàng'."),
        ] = None,
        lat: Annotated[float | None, Field(ge=-90, le=90, description="Vĩ độ tâm, đi cùng `lng`.")] = None,
        lng: Annotated[float | None, Field(ge=-180, le=180, description="Kinh độ tâm, đi cùng `lat`.")] = None,
        radius_km: Annotated[float, Field(gt=0, le=50, description="Bán kính quanh lat/lng, km.")] = 3,
        min_rating: Annotated[float | None, Field(ge=0, le=5, description="Rating tối thiểu.")] = None,
        min_reviews: Annotated[int | None, Field(ge=0, description="Số review tối thiểu, lọc điểm ít người đánh giá.")] = None,
        wheelchair: Annotated[bool, Field(description="Chỉ lấy điểm có thông tin tiếp cận xe lăn.")] = False,
        max_price_vnd: Annotated[
            int | None,
            Field(
                ge=0,
                description="Giá tối đa, VND: mỗi người với quán ăn / cà phê, mỗi đêm với chỗ ở. So với giá "
                "ước tính (giữa khoảng giá Google Maps ghi, VD '100-200 N ₫' = 150.000).",
            ),
        ] = None,
        min_price_vnd: Annotated[
            int | None, Field(ge=0, description="Giá tối thiểu, VND, cùng cách tính với `max_price_vnd`.")
        ] = None,
        strict_price: Annotated[
            bool,
            Field(
                description="Khi lọc giá: true = loại cả nơi chưa có giá; false (mặc định) = chỉ loại nơi có giá "
                "nằm ngoài khoảng, giữ nơi chưa có giá (xem `price_vnd` từng nơi)."
            ),
        ] = False,
        sort_by: Annotated[
            Literal["popular", "rating", "distance"],
            Field(
                description="'popular' = rating có trọng số số review; 'distance' cần lat/lng.",
                json_schema_extra=nhan_lua_chon(
                    {"popular": "phổ biến", "rating": "rating cao", "distance": "gần nhất"}
                ),
            ),
        ] = "popular",
        limit: Annotated[int, Field(ge=1, le=30, description="Số địa điểm tối đa.")] = 10,
    ) -> dict[str, Any]:
        input_context = {"destination": destination, "query": query, "category": category, "lat": lat, "lng": lng}
        try:
            if (lat is None) != (lng is None):
                return format_validation_error("lat/lng", input_context, "phải truyền cả hai hoặc bỏ cả hai")
            if lat is None and not (destination and destination.strip()):
                return format_validation_error("destination", destination, "cần `destination` hoặc `lat`/`lng`")
            if sort_by == "distance" and lat is None:
                return format_validation_error("sort_by", sort_by, "'distance' cần `lat`/`lng`")
            if max_price_vnd is not None and min_price_vnd is not None and min_price_vnd > max_price_vnd:
                return format_validation_error("min_price_vnd", min_price_vnd, "phải nhỏ hơn hoặc bằng `max_price_vnd`")

            dest, ghi_chu = await chon_diem_den(destination, co_toa_do=lat is not None)
            if lat is None:
                if not dest:
                    return drop_empty(
                        {
                            "status": "success",
                            "destination": destination,
                            "places": [],
                            "available_destinations": await goi_y_diem_den(),
                            "note": "Chưa có dữ liệu đã lưu cho điểm đến này; dùng search_places hoặc find_nearby.",
                        }
                    )

            ket_qua, khop_du = loc_dia_diem(
                await travel_db.places(),
                query=query,
                destination_key=dest,
                lat=lat,
                lng=lng,
                radius_km=radius_km if lat is not None else None,
                category=category,
                min_rating=min_rating,
                min_reviews=min_reviews,
                wheelchair=wheelchair,
                sort_by=sort_by,
                max_price_vnd=max_price_vnd,
                min_price_vnd=min_price_vnd,
                strict_price=strict_price,
            )
            return drop_empty(
                {
                    "status": "success",
                    "destination": destination,
                    "total_found": len(ket_qua),
                    "places": [gon_dia_diem(p) for p in ket_qua[:limit]],
                    "data_source": NGUON_DB,
                    "data_as_of": moi_nhat(ket_qua),
                    "note": " ".join(
                        ghi_chu
                        + (
                            ["Không có địa điểm khớp điều kiện; nới bộ lọc hoặc dùng search_places."]
                            if not ket_qua
                            else []
                            if khop_du
                            else ["Không có điểm khớp đủ mọi từ khoá, đã trả các điểm khớp một phần."]
                        )
                    )
                    or None,
                }
            )

        except TravelDbError as e:
            logger.error("browse_places đọc DB thất bại", error=str(e))
            return loi_db(e, input_context)
        except Exception as e:
            logger.error("browse_places thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không duyệt được địa điểm", exception=e, input_context=input_context
            )

    return browse_places
