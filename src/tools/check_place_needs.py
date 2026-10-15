"""Tool kiểm tra các điểm trong lịch trình có đáp ứng nhu cầu của người đi không."""

from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.travel_db import TravelDbError, travel_db
from ..utils.error_handler import format_mcp_tool_error_response
from ..utils.response_utils import drop_empty
from ._db_common import NGUON_DB, loi_db
from ._nhu_cau import NHU_CAU, NhuCau, danh_gia, luu_y, them_nhan_nhu_cau

logger = structlog.get_logger(__name__)


def register_check_place_needs_tool(mcp: FastMCP):
    @mcp.tool(
        name="check_place_needs",
        title="Check Place Needs (Kiểm tra điểm có đáp ứng nhu cầu)",
        description=(
            "Kiểm tra từng điểm trong danh sách (lịch trình, danh sách ngắn) có đáp ứng nhu cầu của "
            "người đi không: xe lăn, trẻ em, ăn chay, đỗ ô tô, thanh toán thẻ, mang chó, Wi-Fi... Mỗi "
            "nhu cầu trả 'yes' / 'no' (Google ghi rõ là KHÔNG có) / 'unknown' (chưa có thông tin). Kèm "
            "`heads_up`: điều cần biết trước khi tới dù không hỏi (chỉ nhận tiền mặt, phải đặt chỗ, "
            "phí vào cổng, thường phải xếp hàng). Không tốn quota.\n\n"
            "Bỏ trống `needs` để lấy mọi thông tin đã biết chắc (yes / no) của từng điểm.\n"
            "Khi nào dùng: rà lịch trình trước khi chốt cho người dùng xe lăn / có con nhỏ / ăn chay; "
            "điểm 'no' nên thay bằng find_alternatives. Tìm điểm mới theo nhu cầu -> find_places với needs."
        ),
        tags={"journey", "db", "maps", "accessibility", "planning"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "postgres:gmaps_place.attributes"},
    )
    async def check_place_needs(
        places: Annotated[
            list[str],
            Field(min_length=1, max_length=15, description="place_id hoặc tên địa điểm, VD ['Văn Miếu', 'ChIJ...']."),
        ],
        needs: Annotated[
            list[NhuCau] | None,
            Field(max_length=12, description="Nhu cầu cần kiểm tra. Bỏ trống = mọi điều đã biết chắc.", json_schema_extra=them_nhan_nhu_cau),
        ] = None,
        destination: Annotated[str | None, Field(description="Điểm đến để phân biệt tên trùng, VD 'Huế'.")] = None,
    ) -> dict[str, Any]:
        input_context = {"places": places, "needs": needs, "destination": destination}
        try:
            nhu_cau = list(dict.fromkeys(needs)) if needs else list(NHU_CAU)
            tim_thay = await travel_db.find_places(
                places, destination, columns="title, place_id, address, category, attributes, attributes_not_available"
            )
            ket_qua = []
            khong_dat: dict[str, list[str]] = {}
            for ten, p in zip(places, tim_thay):
                if not p:
                    ket_qua.append({"input": ten, "status": "not_found"})
                    continue
                trang_thai = {nc: danh_gia(nc, p.get("attributes"), p.get("attributes_not_available")) for nc in nhu_cau}
                if not needs:
                    # Không hỏi nhu cầu cụ thể: chỉ trả điều đã biết chắc cho gọn.
                    trang_thai = {k: v for k, v in trang_thai.items() if v != "unknown"}
                thieu = [nc for nc, v in trang_thai.items() if v == "no"]
                if needs and thieu:
                    khong_dat[p.get("title") or ten] = [NHU_CAU[n]["nhan"] for n in thieu]
                ket_qua.append(
                    {
                        "input": ten,
                        "name": p.get("title"),
                        "place_id": p.get("place_id"),
                        "category": p.get("category"),
                        "status": "found",
                        "needs_status": trang_thai,
                        "all_met": all(v == "yes" for v in trang_thai.values()) if needs else None,
                        "heads_up": luu_y(p.get("attributes")) or None,
                        "has_attribute_data": bool(p.get("attributes") or p.get("attributes_not_available")),
                        "other_matches": p.get("_other_matches"),
                    }
                )

            return drop_empty(
                {
                    "status": "success",
                    "needs": {n: NHU_CAU[n]["nhan"] for n in nhu_cau} if needs else None,
                    "results": ket_qua,
                    "not_met": khong_dat or None,
                    "note": "Điểm không đáp ứng (`not_met`) nên thay bằng find_alternatives với cùng `needs`."
                    if khong_dat
                    else None,
                    "data_source": NGUON_DB,
                }
            )

        except TravelDbError as e:
            logger.error("check_place_needs đọc DB thất bại", error=str(e))
            return loi_db(e, input_context)
        except Exception as e:
            logger.error("check_place_needs thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không kiểm tra được nhu cầu", exception=e, input_context=input_context
            )

    return check_place_needs
