"""Tool lập lộ trình một ngày có khung giờ mở cửa, thời gian tham quan và nghỉ trưa (Vietmap VRP)."""

import re
from datetime import date as Ngay
from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import BaseModel, Field

from ..services.travel_db import TravelDbError, travel_db
from ..services.vietmap_client import Diem, VietmapError, vietmap_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._db_common import THU_VI, khung_gio, mo_ta_gio
from ._vietmap_common import MO_TA_DIEM, NHAN_PHUONG_TIEN, gon_diem, link_google_maps, loi_vietmap, resolve_nhieu

logger = structlog.get_logger(__name__)

_GIO = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_MA_NGHI_TRUA = 9999
_TOI_DA_DIEM = 15
# VRP không nhận "truck" cho bài toán này; xe tải dùng hồ sơ ô tô.
_HO_SO = {"motorcycle": "motorcycle", "car": "car", "truck": "car"}
_TOA_DO = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*$")
_TIEN_TO_REF = ("auto:", "geocode:", "vm:")


class Stop(BaseModel):
    place: str = Field(
        description="Điểm đến: 'lat,lng', ref_id, place_id ('ChIJ...') lấy từ find_places, "
        "hoặc tên kèm tỉnh."
    )
    visit_minutes: int = Field(default=60, ge=0, le=600, description="Thời gian ở lại điểm này (phút).")
    open: str | None = Field(
        default=None, description="Giờ mở cửa 'HH:MM'. Bỏ trống và truyền `date` để tự lấy từ dữ liệu đã lưu."
    )
    close: str | None = Field(default=None, description="Giờ đóng cửa 'HH:MM'; phải tham quan xong trước giờ này.")
    must_visit: bool = Field(default=False, description="Ưu tiên giữ điểm này khi không xếp đủ.")


def _giay(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 3600 + m * 60


def _hhmm(giay: float) -> str:
    giay = int(giay)
    return f"{giay // 3600:02d}:{giay % 3600 // 60:02d}"


def _la_ma_db(s: str) -> bool:
    """place_id / cid / data_id của dữ liệu Google Maps đã lưu; Vietmap không hiểu các mã này."""
    s = s.strip()
    return s.startswith(("ChIJ", "0x")) or s.isdigit()


def _la_ten(s: str) -> bool:
    """Tên / địa chỉ (không phải toạ độ, ref_id hay mã DB): mới cần tra giờ mở cửa theo tên."""
    s = s.strip()
    return not (_TOA_DO.match(s) or s.startswith(_TIEN_TO_REF) or _la_ma_db(s))


def _khung_job(khung: list[tuple[int, int]], visit_minutes: int) -> list[list[int]]:
    """Khung mở cửa (phút từ 0h) -> time_windows cho giờ BẮT ĐẦU tham quan (giây), đủ thời gian ở lại."""
    ra = []
    for bd, kt in khung:
        mo, dong = bd * 60, min(kt, 1440) * 60 - visit_minutes * 60
        if dong >= mo:
            ra.append([mo, dong])
    return ra


def register_plan_day_route_tool(mcp: FastMCP):
    @mcp.tool(
        name="plan_day_route",
        title="Plan Day Route (Lập lộ trình trong ngày)",
        description=(
            "Lập lộ trình một ngày: chọn thứ tự đi các điểm sao cho kịp giờ mở cửa / đóng cửa của "
            "từng điểm, đủ thời gian tham quan, có giờ nghỉ trưa, trong khung giờ đi chơi của đoàn. "
            "Trả giờ đến / giờ đi từng điểm, thời gian di chuyển, thời gian chờ, và các điểm KHÔNG "
            "xếp kịp (kèm lý do) để đề xuất bỏ hoặc dời sang ngày khác.\n\n"
            "Truyền `date` để tool "
            "tự lấy giờ mở cửa đã lưu đúng thứ (miễn phí) cho điểm chưa có open / close; điểm là place_id "
            "(từ find_places) dùng luôn toạ độ đã lưu. Chỉ gọi get_place_details "
            "cho điểm trong `opening_hours_used` thiếu `hours_that_day`.\n"
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
        date: Annotated[
            str | None,
            Field(
                description="Ngày đi 'YYYY-MM-DD'. Có thì điểm nào chưa truyền open / close sẽ tự lấy giờ mở cửa "
                "đúng thứ từ dữ liệu Google Maps đã lưu; điểm đóng cửa ngày đó được đưa vào `unassigned`."
            ),
        ] = None,
        destination: Annotated[
            str | None, Field(description="Điểm đến để phân biệt tên trùng khi tra dữ liệu đã lưu, VD 'Đà Lạt'.")
        ] = None,
    ) -> dict[str, Any]:
        input_context = {
            "start": start,
            "stops": [s.place for s in stops],
            "day_start": day_start,
            "day_end": day_end,
            "date": date,
        }
        try:
            for ten, gio in [("day_start", day_start), ("day_end", day_end)] + [
                (f"stops[{i}].{k}", v) for i, s in enumerate(stops) for k, v in (("open", s.open), ("close", s.close)) if v
            ]:
                if not _GIO.match(gio):
                    return format_validation_error(ten, gio, "phải có dạng HH:MM")
            bd, kt = _giay(day_start), _giay(day_end)
            if kt <= bd:
                return format_validation_error("day_end", day_end, "phải sau day_start")

            ngay = None
            if date and date.strip():
                try:
                    ngay = Ngay.fromisoformat(date.strip())
                except ValueError:
                    return format_validation_error("date", date, "phải có dạng YYYY-MM-DD")

            # Tra dữ liệu Google Maps đã lưu: place_id luôn phải tra (Vietmap không hiểu), tên chỉ tra khi
            # cần giờ mở cửa. Điểm tra được dùng luôn toạ độ đã lưu, khỏi tốn lượt Vietmap.
            can_tra = [
                i
                for i, s in enumerate(stops)
                if _la_ma_db(s.place) or (ngay and not (s.open or s.close) and _la_ten(s.place))
            ]
            tu_db: dict[int, dict[str, Any]] = {}
            ghi_chu: list[str] = []
            if can_tra and travel_db.enabled:
                try:
                    tim = await travel_db.find_places(
                        [stops[i].place for i in can_tra], destination, columns="title, place_id, lat, lng, open_hours"
                    )
                except TravelDbError as e:
                    logger.warning("plan_day_route không đọc được dữ liệu đã lưu", error=str(e))
                    tim = [None] * len(can_tra)
                    ghi_chu.append("Không đọc được dữ liệu đã lưu nên chưa tự lấy giờ mở cửa.")
                for i, p in zip(can_tra, tim):
                    if not p or p.get("lat") is None or p.get("lng") is None:
                        continue
                    if p.get("_other_matches") and not _la_ma_db(stops[i].place):
                        ghi_chu.append(
                            f"'{stops[i].place}' trùng nhiều nơi trong dữ liệu đã lưu nên chưa lấy giờ mở cửa; truyền place_id."
                        )
                        continue
                    tu_db[i] = p
            thieu = [stops[i].place for i in can_tra if _la_ma_db(stops[i].place) and i not in tu_db]
            if thieu:
                return format_validation_error(
                    "stops", thieu, "không có trong dữ liệu đã lưu hoặc thiếu toạ độ; truyền 'lat,lng' hoặc tên kèm tỉnh"
                )

            dau_vao = [start, *(s.place for s in stops), *([end] if end else [])]
            for i, p in tu_db.items():
                dau_vao[1 + i] = f"{p['lat']},{p['lng']}"
            diem = await resolve_nhieu(dau_vao)
            for i, p in tu_db.items():
                diem[1 + i] = Diem(diem[1 + i].lat, diem[1 + i].lng, p.get("title") or stops[i].place)
            diem_dau, cac_diem = diem[0], diem[1 : 1 + len(stops)]
            diem_cuoi = diem[-1] if end else diem_dau

            jobs = []
            ly_do_truoc: dict[int, str] = {}
            dong_cua: dict[int, str] = {}
            gio_da_lay: list[dict[str, Any]] = []
            for i, (s, d) in enumerate(zip(stops, cac_diem), start=1):
                job: dict[str, Any] = {"id": i, "location": [d.lng, d.lat], "service": s.visit_minutes * 60}
                if s.open or s.close:
                    mo = _giay(s.open) if s.open else bd
                    # Phải BẮT ĐẦU tham quan đủ sớm để xong trước giờ đóng cửa.
                    dong = (_giay(s.close) if s.close else kt) - s.visit_minutes * 60
                    if dong < mo:
                        ly_do_truoc[i] = "thời gian tham quan dài hơn khung giờ mở cửa"
                    job["time_windows"] = [[mo, max(dong, mo)]]
                elif ngay and (i - 1) in tu_db:
                    gio = tu_db[i - 1].get("open_hours")
                    thu = ngay.weekday()
                    khung = khung_gio(gio, thu)
                    gio_da_lay.append(
                        {"stop_index": i - 1, "place": d.label, "weekday": THU_VI[thu], "hours_that_day": mo_ta_gio(gio, thu)}
                    )
                    if khung == []:
                        dong_cua[i] = f"đóng cửa vào {THU_VI[thu]} (theo giờ mở cửa đã lưu)"
                        continue
                    if khung and khung != [(0, 1440)]:
                        cua_so = _khung_job(khung, s.visit_minutes)
                        if not cua_so:
                            ly_do_truoc[i] = "thời gian tham quan dài hơn khung giờ mở cửa"
                            cua_so = [[khung[0][0] * 60, khung[0][0] * 60]]
                        job["time_windows"] = cua_so
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

            # Mọi điểm đều đóng cửa ngày đó thì không cần gọi VRP.
            data = await vietmap_client.vrp([xe], jobs) if jobs else {}
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
            for i, ly_do in dong_cua.items():
                khong_xep.append({"stop_index": i - 1, "place": cac_diem[i - 1].label, "reason": ly_do})
            khong_xep.sort(key=lambda x: x["stop_index"])

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
                    "opening_hours_used": gio_da_lay or None,
                    "note": " ".join(
                        [
                            "Giờ là ước tính theo đường đi Vietmap, chưa tính kẹt xe giờ cao điểm. Giờ xuất phát "
                            "là giờ vừa kịp lịch; đi sớm hơn chỉ thêm thời gian chờ ở điểm có giờ mở cửa.",
                            *(
                                [
                                    "Giờ mở cửa trong `opening_hours_used` lấy từ dữ liệu Google Maps đã lưu, "
                                    "không tính ngày lễ đóng cửa bất thường."
                                ]
                                if gio_da_lay
                                else []
                            ),
                            *ghi_chu,
                        ]
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
