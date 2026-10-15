"""Tìm địa điểm đã lưu, tùy chọn lọc đồng thời theo nhu cầu."""

from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.travel_db import TravelDbError, travel_db
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._db_common import NGUON_DB, chon_diem_den, gon_dia_diem, goi_y_diem_den, loc_dia_diem, loi_db, moi_nhat
from ._nhu_cau import NHU_CAU, NhuCau, bieu_thuc_so_yes, dieu_kien_sql, luu_y, them_nhan_nhu_cau, trang_thai_nhu_cau
from ._serpapi_common import nhan_lua_chon

logger = structlog.get_logger(__name__)


def register_find_places_tool(mcp: FastMCP):
    @mcp.tool(
        name="find_places",
        title="Find Places (Tìm địa điểm đã lưu)",
        description=(
            "Tìm POI trong PostgreSQL theo điểm đến hoặc tọa độ, từ khóa, loại, giá, rating, "
            "số review, bán kính và xe lăn. Có `needs` thì lọc thêm các nhu cầu như ăn chay, "
            "đỗ xe, trẻ em, Wi-Fi; mỗi nơi trả `needs_status` và `heads_up`. Không có `needs` "
            "thì chỉ đọc dữ liệu địa điểm, không truy vấn thuộc tính chi tiết. Giá và thuộc tính "
            "là dữ liệu đã lưu, xem `data_as_of`; không tốn quota API."
        ),
        tags={"journey", "db", "maps", "preferences"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "postgres:gmaps_place"},
    )
    async def find_places(
        destination: Annotated[str | None, Field(description="Điểm đến; bỏ trống nếu dùng lat/lng.")] = None,
        query: Annotated[str | None, Field(description="Từ khóa trong tên, loại hoặc mô tả.")] = None,
        category: Annotated[str | None, Field(description="Loại địa điểm Google Maps, VD 'Nhà hàng'.")] = None,
        lat: Annotated[float | None, Field(ge=-90, le=90, description="Vĩ độ tâm, đi cùng lng.")] = None,
        lng: Annotated[float | None, Field(ge=-180, le=180, description="Kinh độ tâm, đi cùng lat.")] = None,
        radius_km: Annotated[float, Field(gt=0, le=50, description="Bán kính quanh lat/lng, km.")] = 3,
        min_rating: Annotated[float | None, Field(ge=0, le=5, description="Rating tối thiểu.")] = None,
        min_reviews: Annotated[int | None, Field(ge=0, description="Số review tối thiểu.")] = None,
        wheelchair: Annotated[bool, Field(description="Chỉ lấy điểm có thông tin tiếp cận xe lăn.")] = False,
        min_price_vnd: Annotated[int | None, Field(ge=0, description="Giá tối thiểu, VND.")] = None,
        max_price_vnd: Annotated[int | None, Field(ge=0, description="Giá tối đa, VND.")] = None,
        strict_price: Annotated[bool, Field(description="Loại cả nơi thiếu giá khi lọc giá.")] = False,
        needs: Annotated[
            list[NhuCau] | None,
            Field(min_length=1, max_length=8, description="Các nhu cầu phải đáp ứng cùng lúc.", json_schema_extra=them_nhan_nhu_cau),
        ] = None,
        strict_needs: Annotated[
            bool, Field(description="True: chỉ lấy nơi chắc chắn đáp ứng needs; False: giữ nơi chưa rõ.")
        ] = True,
        sort_by: Annotated[
            Literal["popular", "rating", "distance"],
            Field(
                description="popular, rating hoặc distance; distance cần lat/lng.",
                json_schema_extra=nhan_lua_chon({"popular": "phổ biến", "rating": "rating cao", "distance": "gần nhất"}),
            ),
        ] = "popular",
        limit: Annotated[int, Field(ge=1, le=30, description="Số địa điểm tối đa.")] = 10,
    ) -> dict[str, Any]:
        context = {"destination": destination, "query": query, "category": category, "lat": lat, "lng": lng, "needs": needs}
        try:
            if (lat is None) != (lng is None):
                return format_validation_error("lat/lng", context, "phải truyền cả hai hoặc bỏ cả hai")
            if lat is None and not (destination and destination.strip()):
                return format_validation_error("destination", destination, "cần destination hoặc lat/lng")
            if sort_by == "distance" and lat is None:
                return format_validation_error("sort_by", sort_by, "distance cần lat/lng")
            if min_price_vnd is not None and max_price_vnd is not None and min_price_vnd > max_price_vnd:
                return format_validation_error("min_price_vnd", min_price_vnd, "phải nhỏ hơn hoặc bằng max_price_vnd")

            destination_key, destination_notes = await chon_diem_den(destination, co_toa_do=lat is not None)
            if lat is None and not destination_key:
                return drop_empty({
                    "status": "success", "destination": destination, "places": [],
                    "available_destinations": await goi_y_diem_den(),
                    "note": "Chưa có dữ liệu địa điểm đã lưu cho điểm đến này.",
                })

            candidates, query_complete = loc_dia_diem(
                await travel_db.places(),
                query=query, destination_key=destination_key,
                lat=lat, lng=lng, radius_km=radius_km if lat is not None else None,
                category=category, min_rating=min_rating, min_reviews=min_reviews,
                wheelchair=wheelchair, sort_by=sort_by,
                min_price_vnd=min_price_vnd, max_price_vnd=max_price_vnd,
                strict_price=strict_price,
            )
            needs_unique = list(dict.fromkeys(needs or []))
            checked_count = len(candidates)
            matched_scores: dict[str, int] = {}
            if needs_unique and candidates:
                sql, params = dieu_kien_sql(needs_unique, 2, chac_chan=strict_needs)
                score_sql, score_params = (
                    bieu_thuc_so_yes(needs_unique, 2 + len(params)) if not strict_needs else ("0", [])
                )
                matched_scores = await travel_db.cids_matching(
                    [place["cid"] for place in candidates], sql, params, score_sql, score_params
                )
                candidates = [place for place in candidates if place["cid"] in matched_scores]
                if not strict_needs:
                    candidates.sort(key=lambda place: -matched_scores[place["cid"]])
            elif needs_unique:
                candidates = []

            selected = candidates[:limit]
            places = [gon_dia_diem(place) for place in selected]
            if needs_unique and selected:
                details = await travel_db.details_by_cid(
                    [place["cid"] for place in selected], "attributes, attributes_not_available"
                )
                for item, place in zip(places, selected):
                    detail = details.get(place["cid"], {})
                    item["needs_status"] = trang_thai_nhu_cau(needs_unique, detail)
                    item["heads_up"] = luu_y(detail.get("attributes")) or None

            notes = list(destination_notes)
            if needs_unique and not candidates:
                if strict_needs:
                    notes.append(
                        f"Không có nơi nào chắc chắn đáp ứng đủ {len(needs_unique)} nhu cầu trong "
                        f"{checked_count} ứng viên. Thử bớt nhu cầu hoặc strict_needs=false."
                    )
                else:
                    notes.append(
                        f"Mọi ứng viên ({checked_count}) đều bị ghi rõ là không đáp ứng ít nhất một nhu cầu."
                    )
            elif not candidates:
                notes.append("Không có địa điểm khớp điều kiện; nới bộ lọc hoặc dùng search_places.")
            elif not query_complete:
                notes.append("Không có điểm khớp đủ mọi từ khoá, đã trả các điểm khớp một phần.")
            return drop_empty({
                "status": "success", "destination": destination,
                "needs": {need: NHU_CAU[need]["nhan"] for need in needs_unique} if needs_unique else None,
                "candidates_checked": checked_count if needs_unique else None,
                "total_found": len(candidates), "places": places,
                "note": " ".join(notes) or None,
                "data_source": NGUON_DB, "data_as_of": moi_nhat(candidates),
            })
        except TravelDbError as exc:
            logger.error("find_places đọc DB thất bại", error=str(exc))
            return loi_db(exc, context)
        except Exception as exc:
            logger.error("find_places thất bại", error=str(exc), exc_info=True)
            return format_mcp_tool_error_response("Không tìm được địa điểm", exception=exc, input_context=context)

    return find_places
