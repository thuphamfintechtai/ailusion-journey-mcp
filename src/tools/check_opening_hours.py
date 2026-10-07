"""Tool kiểm tra nhiều địa điểm có mở cửa vào một thời điểm không (giờ mở cửa Google Maps đã lưu)."""

from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.travel_db import TravelDbError, travel_db
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._db_common import NGUON_DB, THU_VI, doc_thoi_diem, loi_db, mo_ta_gio, trang_thai_mo_cua

logger = structlog.get_logger(__name__)



def register_check_opening_hours_tool(mcp: FastMCP):
    @mcp.tool(
        name="check_opening_hours",
        title="Check Opening Hours (Kiểm tra giờ mở cửa)",
        description=(
            "Kiểm tra một lúc nhiều địa điểm có mở cửa vào thời điểm dự định không, còn bao lâu thì "
            "đóng, và có đủ thời gian ở lại `stay_minutes` không. Dựa trên giờ mở cửa Google Maps đã "
            "lưu (không tốn quota).\n\n"
            "Khi nào dùng: rà lịch trình trước khi chốt (mỗi điểm ứng với giờ đến dự kiến), hoặc lọc "
            "ứng viên còn mở buổi tối.\n"
            "Nếu `status`='not_found' hoặc 'unknown' (chưa có dữ liệu) -> gọi get_place_details cho "
            "điểm đó.\n\n"
            "Trả về mỗi điểm: `status` open / closed / unknown / not_found, `hours_that_day`, "
            "`open_until`, `closes_in_min`, `fits_stay`, `next_open_today`. Giờ là giờ Việt Nam (có "
            "offset như '+00:00' thì tự đổi); "
            "không tính ngày lễ đóng cửa bất thường."
        ),
        tags={"journey", "db", "maps", "planning"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "postgres:gmaps_place.open_hours"},
    )
    async def check_opening_hours(
        places: Annotated[
            list[str],
            Field(
                min_length=1,
                max_length=15,
                description="Danh sách place_id ('ChIJ...') hoặc tên địa điểm, VD ['Chùa Trấn Quốc', 'ChIJ...'].",
            ),
        ],
        at: Annotated[str, Field(description="Thời điểm dự định đến, 'YYYY-MM-DD HH:MM' (giờ địa phương).")],
        stay_minutes: Annotated[int, Field(ge=0, le=720, description="Thời gian định ở lại, phút.")] = 60,
        destination: Annotated[
            str | None, Field(description="Điểm đến, giúp phân biệt tên trùng giữa các thành phố. VD 'Huế'.")
        ] = None,
    ) -> dict[str, Any]:
        input_context = {"places": places, "at": at, "destination": destination}
        try:
            luc = doc_thoi_diem(at)
            if luc is None:
                return format_validation_error("at", at, "phải có dạng 'YYYY-MM-DD HH:MM'")

            tim_thay = await travel_db.find_places(places, destination, columns="title, place_id, address, open_hours")
            ket_qua = []
            for ten, p in zip(places, tim_thay):
                if not p:
                    ket_qua.append({"input": ten, "status": "not_found"})
                    continue
                tt = trang_thai_mo_cua(p.get("open_hours"), luc, stay_minutes)
                ket_qua.append(
                    {
                        "input": ten,
                        "name": p.get("title"),
                        "place_id": p.get("place_id"),
                        "address": p.get("address") if p.get("_other_matches") else None,
                        **tt,
                        "hours_that_day": mo_ta_gio(p.get("open_hours"), luc.weekday()),
                        "other_matches": p.get("_other_matches"),
                    }
                )

            return drop_empty(
                {
                    "status": "success",
                    "at": luc.strftime("%Y-%m-%d %H:%M"),
                    "weekday": THU_VI[luc.weekday()],
                    "stay_minutes": stay_minutes,
                    "results": ket_qua,
                    "note": "Có điểm trùng tên ở nhiều nơi (xem `other_matches`); truyền `destination` "
                    "hoặc place_id để chọn đúng."
                    if any(r.get("other_matches") for r in ket_qua)
                    else None,
                    "data_source": NGUON_DB,
                }
            )

        except TravelDbError as e:
            logger.error("check_opening_hours đọc DB thất bại", error=str(e))
            return loi_db(e, input_context)
        except Exception as e:
            logger.error("check_opening_hours thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không kiểm tra được giờ mở cửa", exception=e, input_context=input_context
            )

    return check_opening_hours
