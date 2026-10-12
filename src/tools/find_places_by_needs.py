"""Tool tìm địa điểm đáp ứng nhu cầu cụ thể (trẻ em, xe lăn, ăn chay, đỗ ô tô, mang chó...)."""

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


def register_find_places_by_needs_tool(mcp: FastMCP):
    @mcp.tool(
        name="find_places_by_needs",
        title="Find Places By Needs (Tìm địa điểm theo nhu cầu)",
        description=(
            "Tìm quán ăn, cà phê, điểm tham quan, spa... đáp ứng ĐỒNG THỜI các nhu cầu thực tế của "
            "người đi, dựa trên thuộc tính Google Maps đã lưu. Nhu cầu gồm: tiếp cận (xe lăn, nhà vệ "
            "sinh xe lăn, hỗ trợ khiếm thính), trẻ em (phù hợp trẻ em, ghế cao, sân chơi, thực đơn trẻ "
            "em), ăn uống (chay, thuần chay, đồ ăn khuya, bữa sáng), đi lại (đỗ ô tô, đỗ xe miễn phí), "
            "thanh toán thẻ, mang chó, Wi-Fi, làm việc laptop, không khí (lãng mạn, yên tĩnh, ấm cúng, "
            "cao cấp), rooftop, nhạc sống, thân thiện LGBTQ+... Lọc thêm được theo khoảng giá "
            "(`max_price_vnd` / `min_price_vnd`). Không tốn quota.\n\n"
            "Khi nào dùng: người dùng nêu ràng buộc cụ thể ('đi với bé 2 tuổi, cần chỗ đỗ ô tô', "
            "'quán chay lãng mạn', 'cà phê làm việc có Wi-Fi'). Kiểm tra một danh sách điểm đã chọn "
            "-> check_place_needs.\n\n"
            "Mỗi kết quả có `needs_status` (yes / no / unknown) và `heads_up` (chỉ nhận tiền mặt, phải "
            "đặt chỗ, phí vào cổng...). Google chỉ có thuộc tính cho khoảng nửa số địa điểm, nên "
            "`strict_needs=true` (mặc định) có thể bỏ sót; đặt false để giữ cả nơi chưa rõ."
        ),
        tags={"journey", "db", "maps", "accessibility", "preferences"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "postgres:gmaps_place.attributes"},
    )
    async def find_places_by_needs(
        needs: Annotated[
            list[NhuCau],
            Field(min_length=1, max_length=8, description="Các nhu cầu phải đáp ứng cùng lúc.", json_schema_extra=them_nhan_nhu_cau),
        ],
        destination: Annotated[str | None, Field(description="Điểm đến, VD 'Hà Nội'. Bỏ trống nếu dùng lat/lng.")] = None,
        category: Annotated[
            str | None, Field(description="Loại địa điểm theo Google Maps, VD 'Nhà hàng', 'Quán cà phê', 'Công viên'.")
        ] = None,
        query: Annotated[str | None, Field(description="Từ khoá trong tên / loại / mô tả, VD 'hải sản', 'bún chả'.")] = None,
        lat: Annotated[float | None, Field(ge=-90, le=90, description="Vĩ độ tâm.")] = None,
        lng: Annotated[float | None, Field(ge=-180, le=180, description="Kinh độ tâm.")] = None,
        radius_km: Annotated[float, Field(gt=0, le=50, description="Bán kính quanh lat/lng, km.")] = 3,
        min_rating: Annotated[float | None, Field(ge=0, le=5, description="Rating tối thiểu.")] = None,
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
        strict_needs: Annotated[
            bool, Field(description="true = chỉ lấy nơi CHẮC CHẮN đáp ứng; false = chỉ loại nơi Google ghi rõ là không có.")
        ] = True,
        sort_by: Annotated[
            Literal["popular", "rating", "distance"],
            Field(
                description="'distance' cần lat/lng.",
                json_schema_extra=nhan_lua_chon({"popular": "phổ biến", "rating": "rating cao", "distance": "gần nhất"}),
            ),
        ] = "popular",
        limit: Annotated[int, Field(ge=1, le=30, description="Số địa điểm tối đa.")] = 10,
    ) -> dict[str, Any]:
        input_context = {"needs": needs, "destination": destination, "category": category, "lat": lat, "lng": lng}
        try:
            if (lat is None) != (lng is None):
                return format_validation_error("lat/lng", input_context, "phải truyền cả hai hoặc bỏ cả hai")
            if lat is None and not (destination and destination.strip()):
                return format_validation_error("destination", destination, "cần `destination` hoặc `lat`/`lng`")
            if sort_by == "distance" and lat is None:
                return format_validation_error("sort_by", sort_by, "'distance' cần `lat`/`lng`")
            if max_price_vnd is not None and min_price_vnd is not None and min_price_vnd > max_price_vnd:
                return format_validation_error("min_price_vnd", min_price_vnd, "phải nhỏ hơn hoặc bằng `max_price_vnd`")
            nhu_cau = list(dict.fromkeys(needs))

            dest, ghi_chu_dest = await chon_diem_den(destination, co_toa_do=lat is not None)
            if lat is None:
                if not dest:
                    return drop_empty(
                        {
                            "status": "success",
                            "destination": destination,
                            "places": [],
                            "available_destinations": await goi_y_diem_den(),
                            "note": "Chưa có dữ liệu đã lưu cho điểm đến này.",
                        }
                    )

            ung_vien, _ = loc_dia_diem(
                await travel_db.places(),
                query=query,
                destination_key=dest,
                lat=lat,
                lng=lng,
                radius_km=radius_km if lat is not None else None,
                category=category,
                min_rating=min_rating,
                sort_by=sort_by,
                max_price_vnd=max_price_vnd,
                min_price_vnd=min_price_vnd,
                strict_price=strict_price,
            )
            dk, ts = dieu_kien_sql(nhu_cau, 2, chac_chan=strict_needs)
            bt, ts_bt = bieu_thuc_so_yes(nhu_cau, 2 + len(ts)) if not strict_needs else ("0", [])
            dat = await travel_db.cids_matching([p["cid"] for p in ung_vien], dk, ts, bt, ts_bt)
            ket_qua = [p for p in ung_vien if p["cid"] in dat]
            # Lọc không chắc chắn: nơi đáp ứng chắc nhiều nhu cầu hơn lên trước (sort ổn định, giữ
            # thứ tự sort_by trong cùng mức).
            ket_qua.sort(key=lambda p: -dat[p["cid"]])

            chon = ket_qua[:limit]
            chi_tiet = await travel_db.details_by_cid([p["cid"] for p in chon], "attributes, attributes_not_available")
            dia_diem = []
            for p in chon:
                ct = chi_tiet.get(p["cid"], {})
                dia_diem.append(
                    {
                        **gon_dia_diem(p),
                        "needs_status": trang_thai_nhu_cau(nhu_cau, ct),
                        "heads_up": luu_y(ct.get("attributes")) or None,
                    }
                )

            ghi_chu = " ".join(ghi_chu_dest) or None
            if not ket_qua:
                ghi_chu = (
                    f"Không có nơi nào chắc chắn đáp ứng đủ {len(nhu_cau)} nhu cầu trong {len(ung_vien)} ứng viên. "
                    "Thử bớt nhu cầu, hoặc strict_needs=false để giữ nơi chưa có thông tin."
                    if strict_needs
                    else f"Mọi ứng viên ({len(ung_vien)}) đều bị Google ghi rõ là không đáp ứng ít nhất một nhu cầu."
                )
            return drop_empty(
                {
                    "status": "success",
                    "destination": destination,
                    "needs": {n: NHU_CAU[n]["nhan"] for n in nhu_cau},
                    "candidates_checked": len(ung_vien),
                    "total_found": len(ket_qua),
                    "places": dia_diem,
                    "note": ghi_chu,
                    "data_source": NGUON_DB,
                    "data_as_of": moi_nhat(ket_qua),
                }
            )

        except TravelDbError as e:
            logger.error("find_places_by_needs đọc DB thất bại", error=str(e))
            return loi_db(e, input_context)
        except Exception as e:
            logger.error("find_places_by_needs thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không tìm được địa điểm theo nhu cầu", exception=e, input_context=input_context
            )

    return find_places_by_needs
