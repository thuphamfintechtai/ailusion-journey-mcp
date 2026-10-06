"""Tool tìm khách sạn / homestay kèm giá theo đêm (Google Hotels qua SerpApi)."""

from datetime import date
from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.serpapi_client import SerpApiError, serpapi_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._serpapi_common import doc_ngay, khong_co_ket_qua, loi_serpapi, tham_so_vn, nhan_lua_chon

logger = structlog.get_logger(__name__)

_SORT_BY = {"relevance": None, "lowest_price": 3, "highest_rating": 8, "most_reviewed": 13}


def _gon_khach_san(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": p.get("name"),
        "type": p.get("type"),
        "hotel_class": p.get("extracted_hotel_class") or p.get("hotel_class"),
        "rating": p.get("overall_rating"),
        "reviews": p.get("reviews"),
        "location_rating": p.get("location_rating"),
        "price_per_night_vnd": (p.get("rate_per_night") or {}).get("extracted_lowest"),
        "total_price_vnd": (p.get("total_rate") or {}).get("extracted_lowest"),
        "prices_by_source": [
            {
                "source": s.get("source"),
                "price_per_night_vnd": (s.get("rate_per_night") or {}).get("extracted_lowest"),
            }
            for s in (p.get("prices") or [])[:3]
        ],
        "gps": p.get("gps_coordinates"),
        "check_in_time": p.get("check_in_time"),
        "check_out_time": p.get("check_out_time"),
        "amenities": (p.get("amenities") or [])[:12],
        "nearby_places": [
            {
                "name": n.get("name"),
                "transport": [
                    f"{t.get('type')}: {t.get('duration')}" for t in (n.get("transportations") or [])
                ],
            }
            for n in (p.get("nearby_places") or [])[:4]
        ],
        "link": p.get("link"),
        "property_token": p.get("property_token"),
    }


def register_search_hotels_tool(mcp: FastMCP):
    @mcp.tool(
        name="search_hotels",
        title="Search Hotels (Tìm khách sạn)",
        description=(
            "Tìm khách sạn, resort, homestay ở một điểm đến kèm giá theo đêm (VND), "
            "hạng sao, rating, tiện nghi và thời gian tới các điểm lân cận. Dữ liệu từ "
            "Google Hotels, giá gộp từ nhiều trang đặt phòng.\n\n"
            "Khi nào dùng: cần giá phòng thật cho một khoảng ngày cụ thể, so sánh chỗ ở "
            "theo ngân sách hoặc tiện nghi (hồ bơi, chỗ đỗ xe, thân thiện trẻ em...).\n"
            "Khi nào KHÔNG dùng: tìm quán ăn / điểm tham quan (dùng search_places).\n\n"
            "Mỗi lần gọi tốn 1 lượt quota, nên gọi một lần với đủ bộ lọc thay vì gọi lặp.\n"
            "Trả về: danh sách `hotels`, giá tính bằng VND."
        ),
        tags={"journey", "serpapi", "price"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "serpapi:google_hotels"},
    )
    async def search_hotels(
        destination: Annotated[
            str,
            Field(description="Thành phố, khu vực hoặc tên khách sạn. VD: 'Hội An', 'phố cổ Hội An'."),
        ],
        check_in_date: Annotated[str, Field(description="Ngày nhận phòng, dạng YYYY-MM-DD.")],
        check_out_date: Annotated[
            str, Field(description="Ngày trả phòng, dạng YYYY-MM-DD, phải sau ngày nhận phòng.")
        ],
        adults: Annotated[int, Field(ge=1, le=20, description="Số người lớn.")] = 2,
        children_ages: Annotated[
            list[int] | None,
            Field(description="Tuổi từng trẻ em (1-17), VD [4, 8]. Bỏ trống nếu không có trẻ em."),
        ] = None,
        min_price: Annotated[
            int | None, Field(ge=0, description="Giá tối thiểu mỗi đêm, VND.")
        ] = None,
        max_price: Annotated[
            int | None, Field(ge=0, description="Giá tối đa mỗi đêm, VND. VD 1500000.")
        ] = None,
        hotel_class: Annotated[
            list[Literal[2, 3, 4, 5]] | None,
            Field(description="Lọc hạng sao, VD [4, 5]."),
        ] = None,
        sort_by: Annotated[
            Literal["relevance", "lowest_price", "highest_rating", "most_reviewed"],
            Field(
                description="Cách sắp xếp kết quả.",
                json_schema_extra=nhan_lua_chon(
                    {
                        "relevance": "phù hợp nhất",
                        "lowest_price": "giá thấp nhất",
                        "highest_rating": "đánh giá cao nhất",
                        "most_reviewed": "nhiều review nhất",
                    }
                ),
            ),
        ] = "relevance",
        vacation_rentals: Annotated[
            bool, Field(description="True để tìm căn hộ / homestay / nhà nguyên căn thay vì khách sạn.")
        ] = False,
        limit: Annotated[int, Field(ge=1, le=20, description="Số chỗ ở tối đa trả về.")] = 10,
    ) -> dict[str, Any]:
        input_context = {
            "destination": destination,
            "check_in_date": check_in_date,
            "check_out_date": check_out_date,
        }
        try:
            if not destination.strip():
                return format_validation_error("destination", destination, "không được để trống")
            try:
                ngay_vao = doc_ngay(check_in_date, "check_in_date")
                ngay_ra = doc_ngay(check_out_date, "check_out_date")
            except ValueError as e:
                return format_mcp_tool_error_response(str(e), error_code=-2, input_context=input_context)
            if ngay_ra <= ngay_vao:
                return format_validation_error(
                    "check_out_date", check_out_date, "phải sau check_in_date"
                )
            if ngay_vao < date.today():
                return format_validation_error("check_in_date", check_in_date, "đã ở quá khứ")
            if min_price and max_price and min_price > max_price:
                return format_validation_error("min_price", min_price, "lớn hơn max_price")

            tre_em = children_ages or []
            data = await serpapi_client.search(
                "google_hotels",
                q=destination.strip(),
                check_in_date=ngay_vao.isoformat(),
                check_out_date=ngay_ra.isoformat(),
                adults=adults,
                children=len(tre_em) or None,
                children_ages=",".join(str(t) for t in tre_em) or None,
                min_price=min_price,
                max_price=max_price,
                hotel_class=",".join(str(h) for h in sorted(set(hotel_class))) if hotel_class else None,
                sort_by=_SORT_BY[sort_by],
                vacation_rentals="true" if vacation_rentals else None,
                **tham_so_vn(currency=True),
            )

            ghi_chu = khong_co_ket_qua(data)
            danh_sach = data.get("properties") or []
            return drop_empty(
                {
                    "status": "success",
                    "destination": destination,
                    "nights": (ngay_ra - ngay_vao).days,
                    "currency": "VND",
                    "total_found": len(danh_sach),
                    "hotels": [_gon_khach_san(p) for p in danh_sach[:limit]],
                    "note": ghi_chu,
                }
            )

        except SerpApiError as e:
            logger.error("search_hotels gọi SerpApi thất bại", error=str(e))
            return loi_serpapi(e, input_context)
        except Exception as e:
            logger.error("search_hotels thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không tìm được khách sạn", exception=e, input_context=input_context
            )

    return search_hotels
