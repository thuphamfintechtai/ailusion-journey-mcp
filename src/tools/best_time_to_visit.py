"""Tool gợi ý giờ / ngày vắng khách để ghé một địa điểm (giờ đông khách Google Maps đã lưu)."""

from datetime import date
from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.travel_db import TravelDbError, travel_db
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._db_common import NGUON_DB, THU_VI, doc_thu, khung_gio, loi_db, mo_ta_gio, muc_dong_theo_gio

logger = structlog.get_logger(__name__)


def _nhan(muc: float) -> str:
    if muc < 30:
        return "vắng"
    if muc < 60:
        return "vừa phải"
    if muc < 80:
        return "đông"
    return "rất đông"


def _gio_mo(open_hours: dict[str, Any] | None, thu: int, muc: dict[int, int]) -> list[int]:
    """Các giờ (0-23) đang mở. Không có giờ mở cửa thì coi giờ có mức đông > 0 là giờ mở."""
    khung = khung_gio(open_hours, thu)
    if khung is None:
        return sorted(g for g, v in muc.items() if v > 0)
    return sorted(g for g in muc if any(bd <= g * 60 < kt for bd, kt in khung))


def _phan_tich_ngay(p: dict[str, Any], thu: int) -> dict[str, Any] | None:
    muc = muc_dong_theo_gio(p.get("popular_times"), thu)
    gio = _gio_mo(p.get("open_hours"), thu, muc)
    if not gio or not any(muc.get(g) for g in gio):
        return None
    vang = sorted(gio, key=lambda g: (muc.get(g, 0), g))
    dinh = max(gio, key=lambda g: muc.get(g, 0))
    # Khung 2 giờ liền nhau vắng nhất, đủ cho một lượt tham quan thông thường.
    cap = [(g, muc.get(g, 0) + muc.get(g + 1, 0)) for g in gio if g + 1 in gio]
    khung_tot = min(cap, key=lambda x: x[1])[0] if cap else None
    tb = sum(muc.get(g, 0) for g in gio) / len(gio)
    return {
        "weekday": THU_VI[thu],
        "hours_that_day": mo_ta_gio(p.get("open_hours"), thu),
        "average_busyness": round(tb),
        "level": _nhan(tb),
        "best_2h_window": f"{khung_tot:02d}:00-{khung_tot + 2:02d}:00" if khung_tot is not None else None,
        "quietest_hours": [{"hour": f"{g:02d}:00", "busyness": muc.get(g, 0)} for g in vang[:3]],
        "peak_hour": {"hour": f"{dinh:02d}:00", "busyness": muc.get(dinh, 0), "level": _nhan(muc.get(dinh, 0))},
        "by_hour": {f"{g:02d}": muc.get(g, 0) for g in gio},
    }


def register_best_time_to_visit_tool(mcp: FastMCP):
    @mcp.tool(
        name="best_time_to_visit",
        title="Best Time To Visit (Giờ vắng nên ghé)",
        description=(
            "Gợi ý giờ và ngày trong tuần vắng khách nhất để ghé một địa điểm, dựa trên biểu đồ giờ "
            "đông khách (0-100) của Google Maps đã lưu, chỉ tính trong giờ mở cửa. Không tốn quota.\n\n"
            "Khi nào dùng: xếp giờ thăm điểm nổi tiếng (chùa, bảo tàng, quán nổi tiếng) để tránh đông, "
            "hoặc chọn ngày trong tuần cho điểm hay quá tải.\n"
            "Truyền `day` để xem chi tiết một ngày; bỏ trống để so sánh cả tuần.\n\n"
            "Trả về mỗi ngày: `best_2h_window`, `quietest_hours`, `peak_hour`, `average_busyness`; kèm "
            "`quietest_day` / `busiest_day`. Không có dữ liệu thì trả note, khi đó dùng get_place_details."
        ),
        tags={"journey", "db", "maps", "planning"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "postgres:gmaps_place.popular_times"},
    )
    async def best_time_to_visit(
        place: Annotated[str, Field(description="place_id ('ChIJ...') hoặc tên địa điểm, VD 'Văn Miếu Quốc Tử Giám'.")],
        destination: Annotated[
            str | None, Field(description="Điểm đến để phân biệt tên trùng, VD 'Hà Nội'.")
        ] = None,
        day: Annotated[
            str | None,
            Field(description="Ngày cụ thể 'YYYY-MM-DD' hoặc thứ ('Thứ Bảy', 'Chủ Nhật', 'saturday'). Bỏ trống = cả tuần."),
        ] = None,
    ) -> dict[str, Any]:
        input_context = {"place": place, "destination": destination, "day": day}
        try:
            if not place.strip():
                return format_validation_error("place", place, "không được để trống")
            cac_thu = list(range(7))
            if day and day.strip():
                try:
                    thu = date.fromisoformat(day.strip()).weekday()
                except ValueError:
                    thu = doc_thu(day)
                if thu is None:
                    return format_validation_error("day", day, "phải là 'YYYY-MM-DD' hoặc tên thứ, VD 'Thứ Bảy'")
                cac_thu = [thu]

            p = await travel_db.find_place(place, destination)
            if not p:
                return drop_empty(
                    {
                        "status": "success",
                        "place": place,
                        "note": "Không có địa điểm này trong dữ liệu đã lưu; dùng search_places rồi get_place_details.",
                    }
                )
            if not p.get("popular_times"):
                return drop_empty(
                    {
                        "status": "success",
                        "place": {"name": p.get("title"), "place_id": p.get("place_id")},
                        "other_matches": p.get("_other_matches"),
                        "note": "Địa điểm chưa có dữ liệu giờ đông khách; thử get_place_details.",
                        "data_source": NGUON_DB,
                    }
                )

            cac_ngay = [n for thu in cac_thu if (n := _phan_tich_ngay(p, thu))]
            tom_tat = None
            if len(cac_ngay) > 1:
                theo_tb = sorted(cac_ngay, key=lambda n: n["average_busyness"])
                tom_tat = {"quietest_day": theo_tb[0]["weekday"], "busiest_day": theo_tb[-1]["weekday"]}
                # So sánh cả tuần thì bỏ chi tiết từng giờ cho gọn.
                for n in cac_ngay:
                    n.pop("by_hour", None)

            return drop_empty(
                {
                    "status": "success",
                    "place": {"name": p.get("title"), "place_id": p.get("place_id"), "address": p.get("address")},
                    "other_matches": p.get("_other_matches"),
                    **(tom_tat or {}),
                    "days": cac_ngay,
                    "note": None if cac_ngay else "Địa điểm đóng cửa hoặc không có dữ liệu cho ngày đã chọn.",
                    "data_source": NGUON_DB,
                    "data_as_of": str(p.get("fetched_at") or "")[:10] or None,
                }
            )

        except TravelDbError as e:
            logger.error("best_time_to_visit đọc DB thất bại", error=str(e))
            return loi_db(e, input_context)
        except Exception as e:
            logger.error("best_time_to_visit thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không phân tích được giờ đông khách", exception=e, input_context=input_context
            )

    return best_time_to_visit
