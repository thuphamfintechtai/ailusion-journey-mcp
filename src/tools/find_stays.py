"""Tool tìm chỗ ở theo ngân sách / đêm trong dữ liệu Google Maps đã lưu."""

from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.travel_db import TravelDbError, travel_db
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._cho_o import tim_cho_o
from ._db_common import NGUON_DB, chon_diem_den, goi_y_diem_den, loi_db
from ._nhu_cau import NhuCau, them_nhan_nhu_cau
from ._serpapi_common import nhan_lua_chon

logger = structlog.get_logger(__name__)


def register_find_stays_tool(mcp: FastMCP):
    @mcp.tool(
        name="find_stays",
        title="Find Stays (Tìm chỗ ở theo ngân sách)",
        description=(
            "Tìm khách sạn, resort, căn hộ, biệt thự, homestay, nhà trọ đã lưu theo NGÂN SÁCH / ĐÊM, "
            "loại chỗ ở, rating, bán kính quanh một điểm và nhu cầu (xe lăn, đỗ ô tô, mang chó, trẻ em, "
            "Wi-Fi...). Kèm `price_stats_vnd` (p25 / trung vị / p75) của khu vực để biết ngân sách "
            "đặt ra là thấp hay cao so với mặt bằng. Không tốn quota.\n\n"
            "Khi nào dùng: lọc nhanh danh sách chỗ ở hợp túi tiền, so sánh mặt bằng giá giữa các "
            "điểm đến, chọn chỗ ở gần lịch trình.\n"
            "Khi nào KHÔNG dùng: cần giá đúng ngày nhận / trả phòng, theo số người, so giá nhiều "
            "nguồn đặt phòng -> search_hotels (tốn quota). Giá ở đây là giá tham khảo lúc cào.\n\n"
            "`sort_by`: 'popular' (rating theo số review), 'price_low', 'rating' (rating hiệu chỉnh), "
            "'value' (đáng tiền: rating trên mỗi mức giá), 'distance' (cần lat/lng)."
        ),
        tags={"journey", "db", "lodging", "budget"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "postgres:gmaps_place.summary"},
    )
    async def find_stays(
        destination: Annotated[
            str | None, Field(description="Điểm đến, VD 'Đà Nẵng', 'Hải Phòng'. Bỏ trống nếu dùng lat/lng.")
        ] = None,
        max_price_vnd: Annotated[int | None, Field(ge=0, description="Giá tối đa / đêm, VND. VD 800000.")] = None,
        min_price_vnd: Annotated[int | None, Field(ge=0, description="Giá tối thiểu / đêm, VND.")] = None,
        kind: Annotated[
            Literal["any", "hotel", "resort", "apartment", "villa", "homestay", "hostel"],
            Field(
                description="Loại chỗ ở.",
                json_schema_extra=nhan_lua_chon(
                    {
                        "any": "mọi loại",
                        "hotel": "khách sạn",
                        "resort": "resort / nghỉ dưỡng",
                        "apartment": "căn hộ",
                        "villa": "biệt thự",
                        "homestay": "homestay / nhà dân",
                        "hostel": "nhà trọ / nhà nghỉ",
                    }
                ),
            ),
        ] = "any",
        query: Annotated[str | None, Field(description="Từ khoá trong tên, VD 'Mường Thanh', 'view biển'.")] = None,
        lat: Annotated[float | None, Field(ge=-90, le=90, description="Vĩ độ tâm (VD điểm tham quan chính).")] = None,
        lng: Annotated[float | None, Field(ge=-180, le=180, description="Kinh độ tâm.")] = None,
        radius_km: Annotated[float, Field(gt=0, le=50, description="Bán kính quanh lat/lng, km.")] = 3,
        min_rating: Annotated[float | None, Field(ge=0, le=5, description="Rating tối thiểu.")] = None,
        min_reviews: Annotated[int | None, Field(ge=0, description="Số review tối thiểu.")] = None,
        needs: Annotated[
            list[NhuCau] | None,
            Field(
                max_length=8,
                description="Nhu cầu phải đáp ứng, VD ['wheelchair', 'car_parking', 'pets'].",
                json_schema_extra=them_nhan_nhu_cau,
            ),
        ] = None,
        strict_needs: Annotated[
            bool,
            Field(
                description="true = chỉ lấy chỗ CHẮC CHẮN đáp ứng `needs`; false (mặc định) = chỉ loại chỗ "
                "Google ghi rõ là không có, giữ chỗ chưa có thông tin (chỉ ~10% chỗ ở có dữ liệu tiện nghi). "
                "Xem `needs_status` (yes / no / unknown) của từng chỗ."
            ),
        ] = False,
        sort_by: Annotated[
            Literal["popular", "price_low", "rating", "value", "distance"],
            Field(
                description="Thứ tự kết quả.",
                json_schema_extra=nhan_lua_chon(
                    {
                        "popular": "phổ biến",
                        "price_low": "giá thấp trước",
                        "rating": "rating cao",
                        "value": "đáng tiền",
                        "distance": "gần nhất",
                    }
                ),
            ),
        ] = "popular",
        limit: Annotated[int, Field(ge=1, le=30, description="Số chỗ ở tối đa.")] = 10,
    ) -> dict[str, Any]:
        input_context = {"destination": destination, "max_price_vnd": max_price_vnd, "kind": kind, "lat": lat, "lng": lng}
        try:
            if (lat is None) != (lng is None):
                return format_validation_error("lat/lng", input_context, "phải truyền cả hai hoặc bỏ cả hai")
            if lat is None and not (destination and destination.strip()):
                return format_validation_error("destination", destination, "cần `destination` hoặc `lat`/`lng`")
            if sort_by == "distance" and lat is None:
                return format_validation_error("sort_by", sort_by, "'distance' cần `lat`/`lng`")
            if min_price_vnd is not None and max_price_vnd is not None and min_price_vnd > max_price_vnd:
                return format_validation_error("min_price_vnd", min_price_vnd, "lớn hơn max_price_vnd")

            dest, ghi_chu_dest = await chon_diem_den(destination, co_toa_do=lat is not None)
            if lat is None:
                if not dest:
                    return drop_empty(
                        {
                            "status": "success",
                            "destination": destination,
                            "stays": [],
                            "available_destinations": await goi_y_diem_den(),
                            "note": "Chưa có dữ liệu chỗ ở đã lưu cho điểm đến này; dùng search_hotels.",
                        }
                    )

            kq = await tim_cho_o(
                destination_key=dest,
                lat=lat,
                lng=lng,
                radius_km=radius_km if lat is not None else None,
                query=query,
                kind=kind,
                min_price=min_price_vnd,
                max_price=max_price_vnd,
                min_rating=min_rating,
                min_reviews=min_reviews,
                needs=list(needs) if needs else None,
                strict_needs=strict_needs,
                sort_by=sort_by,
                limit=limit,
            )
            ghi_chu = list(ghi_chu_dest)
            tk = kq.get("price_stats_vnd")
            if not kq["stays"]:
                ghi_chu.append("Không có chỗ ở khớp điều kiện.")
                if max_price_vnd is not None and tk and max_price_vnd < tk["p25"]:
                    ghi_chu.append(f"Ngân sách thấp hơn 75% chỗ ở trong khu vực (p25 = {tk['p25']:,} VND / đêm).")
            return drop_empty(
                {
                    "status": "success",
                    "destination": destination,
                    **kq,
                    "note": " ".join(ghi_chu) or None,
                    "data_source": NGUON_DB,
                }
            )

        except TravelDbError as e:
            logger.error("find_stays đọc DB thất bại", error=str(e))
            return loi_db(e, input_context)
        except Exception as e:
            logger.error("find_stays thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không tìm được chỗ ở", exception=e, input_context=input_context
            )

    return find_stays
