"""Tool lập lộ trình một ngày có khung giờ mở cửa, thời gian tham quan và nghỉ trưa (Vietmap VRP)."""

import re
from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import BaseModel, Field

from ..services.vietmap_client import VietmapError, vietmap_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._vietmap_common import MO_TA_DIEM, NHAN_PHUONG_TIEN, gon_diem, link_google_maps, loi_vietmap, resolve_nhieu

logger = structlog.get_logger(__name__)

_GIO = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_MA_NGHI_TRUA = 9999
_TOI_DA_DIEM = 15
# VRP không nhận "truck" cho bài toán này; xe tải dùng hồ sơ ô tô.
_HO_SO = {"motorcycle": "motorcycle", "car": "car", "truck": "car"}


class Stop(BaseModel):
    place: str = Field(description="Điểm đến: 'lat,lng', ref_id, hoặc tên kèm tỉnh.")
    visit_minutes: int = Field(default=60, ge=0, le=600, description="Thời gian ở lại điểm này (phút).")
    open: str | None = Field(default=None, description="Giờ mở cửa 'HH:MM' (lấy từ get_place_details).")
    close: str | None = Field(default=None, description="Giờ đóng cửa 'HH:MM'; phải tham quan xong trước giờ này.")
    must_visit: bool = Field(default=False, description="Ưu tiên giữ điểm này khi không xếp đủ.")


def _giay(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 3600 + m * 60


def _hhmm(giay: float) -> str:
    giay = int(giay)
    return f"{giay // 3600:02d}:{giay % 3600 // 60:02d}"


def register_plan_day_route_tool(mcp: FastMCP):
    @mcp.tool(
        name="plan_day_route",
        title="Plan Day Route (Lập lộ trình trong ngày)",
        description=(
            "Lập lộ trình một ngày: chọn thứ tự đi các điểm sao cho kịp giờ mở cửa / đóng cửa của "
            "từng điểm, đủ thời gian tham quan, có giờ nghỉ trưa, trong khung giờ đi chơi của đoàn. "
            "Trả giờ đến / giờ đi từng điểm, thời gian di chuyển, thời gian chờ, và các điểm KHÔNG "
            "xếp kịp (kèm lý do) để đề xuất bỏ hoặc dời sang ngày khác.\n\n"
            "Khác optimize_route (chỉ tối ưu quãng đường, không biết giờ mở cửa). Lấy giờ mở cửa "
            "bằng get_place_details trước khi gọi.\n"
            "Chi phí: số điểm transaction Vietmap mỗi lần gọi.\n\n" + MO_TA_DIEM
        ),
        tags={"journey", "vietmap", "routing", "planning"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "vietmap:vrp"},
    )
    async def plan_day_route(
        start: Annotated[str, Field(description="Nơi xuất phát (thường là khách sạn).")],
        stops: Annotated[list[Stop], Field(min_length=1, max_length=_TOI_DA_DIEM, description="Các điểm muốn đi.")],
        day_start: Annotated[str, Field(description="Giờ bắt đầu đi chơi 'HH:MM'.")] = "08:00",
        day_end: Annotated[str, Field(description="Giờ phải về tới nơi kết thúc 'HH:MM'.")] = "18:00",
        end: Annotated[str | None, Field(description="Nơi kết thúc; bỏ trống = quay về `start`.")] = None,
        lunch_break: Annotated[bool, Field(description="Chừa 1 giờ nghỉ trưa trong khung 11:00-13:30.")] = True,
        vehicle: Annotated[
            Literal["motorcycle", "car", "truck"], Field(description="Phương tiện.", json_schema_extra=NHAN_PHUONG_TIEN)
        ] = "car",
    ) -> dict[str, Any]:
        input_context = {"start": start, "stops": [s.place for s in stops], "day_start": day_start, "day_end": day_end}
        try:
            for ten, gio in [("day_start", day_start), ("day_end", day_end)] + [
                (f"stops[{i}].{k}", v) for i, s in enumerate(stops) for k, v in (("open", s.open), ("close", s.close)) if v
            ]:
                if not _GIO.match(gio):
                    return format_validation_error(ten, gio, "phải có dạng HH:MM")
            bd, kt = _giay(day_start), _giay(day_end)
            if kt <= bd:
                return format_validation_error("day_end", day_end, "phải sau day_start")

            diem = await resolve_nhieu([start, *(s.place for s in stops), *([end] if end else [])])
            diem_dau, cac_diem = diem[0], diem[1 : 1 + len(stops)]
            diem_cuoi = diem[-1] if end else diem_dau

            jobs = []
            ly_do_truoc: dict[int, str] = {}
            for i, (s, d) in enumerate(zip(stops, cac_diem), start=1):
                job: dict[str, Any] = {"id": i, "location": [d.lng, d.lat], "service": s.visit_minutes * 60}
                if s.open or s.close:
                    mo = _giay(s.open) if s.open else bd
                    # Phải BẮT ĐẦU tham quan đủ sớm để xong trước giờ đóng cửa.
                    dong = (_giay(s.close) if s.close else kt) - s.visit_minutes * 60
                    if dong < mo:
                        ly_do_truoc[i] = "thời gian tham quan dài hơn khung giờ mở cửa"
                    job["time_windows"] = [[mo, max(dong, mo)]]
                if s.must_visit:
                    job["priority"] = 10
                jobs.append(job)

            xe: dict[str, Any] = {
                "id": 1,
                "profile": _HO_SO[vehicle],
                "start": [diem_dau.lng, diem_dau.lat],
                "end": [diem_cuoi.lng, diem_cuoi.lat],
                "time_window": [bd, kt],
            }
            if lunch_break:
                xe["breaks"] = [{"id": _MA_NGHI_TRUA, "time_windows": [[11 * 3600, 12 * 3600 + 30 * 60]], "service": 3600}]

            data = await vietmap_client.vrp([xe], jobs)
            routes = data.get("routes") or []
            buoc_ra: list[dict[str, Any]] = []
            thu_tu_diem = [diem_dau]
            if routes:
                truoc_tg, truoc_qd = 0.0, 0.0
                for b in routes[0].get("steps") or []:
                    loai = b.get("type")
                    den = b.get("arrival", 0)
                    di_chuyen = (b.get("duration", 0) - truoc_tg) / 60
                    quang_duong = (b.get("distance", 0) - truoc_qd) / 1000
                    truoc_tg, truoc_qd = b.get("duration", 0), b.get("distance", 0)
                    cho = b.get("waiting_time", 0)
                    o_lai = b.get("service", 0)
                    muc: dict[str, Any] = {
                        "arrive": _hhmm(den),
                        "start": _hhmm(den + cho) if cho else None,
                        "leave": _hhmm(den + cho + o_lai) if loai != "end" else None,
                        "travel_min": round(di_chuyen, 1) if di_chuyen else None,
                        "distance_km": round(quang_duong, 2) if quang_duong else None,
                        "wait_min": round(cho / 60, 1) if cho else None,
                    }
                    if loai == "start":
                        buoc_ra.append({"type": "start", "place": gon_diem(diem_dau)["label"], "leave": _hhmm(den)})
                    elif loai == "job":
                        idx = b["id"] - 1
                        thu_tu_diem.append(cac_diem[idx])
                        buoc_ra.append({"type": "visit", "stop_index": idx, "place": cac_diem[idx].label, **muc})
                    elif loai == "break":
                        buoc_ra.append({"type": "lunch", "place": "Nghỉ trưa (gần điểm vừa rời)", **muc})
                    elif loai == "end":
                        buoc_ra.append({"type": "end", "place": diem_cuoi.label, **muc})
            thu_tu_diem.append(diem_cuoi)

            khong_xep = []
            for u in data.get("unassigned") or []:
                idx = u["id"] - 1
                if 0 <= idx < len(stops):
                    khong_xep.append(
                        {
                            "stop_index": idx,
                            "place": cac_diem[idx].label,
                            "reason": ly_do_truoc.get(u["id"])
                            or "không xếp kịp trong khung giờ mở cửa / khung giờ đi chơi của ngày",
                        }
                    )

            tong = data.get("summary") or {}
            return drop_empty(
                {
                    "status": "success",
                    "vehicle": vehicle,
                    "schedule": buoc_ra,
                    "unassigned": khong_xep or None,
                    "total_travel_min": round(tong.get("duration", 0) / 60, 1),
                    "total_distance_km": round(tong.get("distance", 0) / 1000, 2),
                    "total_wait_min": round(tong.get("waiting_time", 0) / 60, 1) or None,
                    "google_maps_url": link_google_maps(thu_tu_diem, vehicle) if len(thu_tu_diem) > 2 else None,
                    "points": [gon_diem(d) for d in diem],
                    "note": (
                        "Giờ là ước tính theo đường đi Vietmap, chưa tính kẹt xe giờ cao điểm. Giờ xuất phát "
                        "là giờ vừa kịp lịch; đi sớm hơn chỉ thêm thời gian chờ ở điểm có giờ mở cửa."
                    ),
                }
            )

        except VietmapError as e:
            logger.error("plan_day_route gọi Vietmap thất bại", error=str(e))
            return loi_vietmap(e, input_context)
        except Exception as e:
            logger.error("plan_day_route thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response("Không lập được lộ trình", exception=e, input_context=input_context)

    return plan_day_route
