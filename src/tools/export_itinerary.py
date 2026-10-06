"""Tool xuất lịch trình ra file lịch .ics (iPhone / Google Calendar / Outlook) và link Google Maps."""

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

import structlog
from fastmcp import FastMCP
from pydantic import BaseModel, Field

from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ..utils.text import bo_dau

logger = structlog.get_logger(__name__)

_GIO = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_VTIMEZONE = [
    "BEGIN:VTIMEZONE",
    "TZID:Asia/Ho_Chi_Minh",
    "BEGIN:STANDARD",
    "DTSTART:19700101T000000",
    "TZOFFSETFROM:+0700",
    "TZOFFSETTO:+0700",
    "TZNAME:ICT",
    "END:STANDARD",
    "END:VTIMEZONE",
]
_GMAPS_MODE = {"motorcycle": "two-wheeler", "car": "driving", "walking": "walking", "transit": "transit"}


class Activity(BaseModel):
    time: str = Field(description="Giờ bắt đầu 'HH:MM'.")
    end_time: str | None = Field(default=None, description="Giờ kết thúc 'HH:MM'; bỏ trống = tới hoạt động kế tiếp (tối đa 2 giờ).")
    title: str = Field(description="Tên hoạt động, VD 'Tham quan phố cổ'.")
    location: str | None = Field(default=None, description="Tên / địa chỉ nơi đến.")
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    note: str | None = Field(default=None, description="Ghi chú: giá vé, lưu ý, số điện thoại đặt chỗ...")


class Day(BaseModel):
    date: str = Field(description="Ngày 'YYYY-MM-DD'.")
    activities: list[Activity] = Field(description="Hoạt động trong ngày, theo thứ tự giờ.")


def _thoat(s: str) -> str:
    """Escape TEXT theo RFC 5545."""
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def _gap(dong: str) -> list[str]:
    """Gấp dòng dài hơn 75 byte (RFC 5545), không cắt giữa ký tự UTF-8."""
    ra, hien_tai, so_byte = [], "", 0
    for ky_tu in dong:
        b = len(ky_tu.encode("utf-8"))
        gioi_han = 75 if not ra else 74  # dòng tiếp có 1 khoảng trắng đầu
        if so_byte + b > gioi_han:
            ra.append(hien_tai)
            hien_tai, so_byte = "", 0
        hien_tai += ky_tu
        so_byte += b
    ra.append(hien_tai)
    return [ra[0]] + [" " + x for x in ra[1:]]


def _diem_maps(a: Activity) -> str | None:
    if a.lat is not None and a.lng is not None:
        return f"{a.lat},{a.lng}"
    return a.location


def link_tim(a: Activity) -> str | None:
    d = _diem_maps(a)
    return "https://www.google.com/maps/search/?" + urlencode({"api": "1", "query": d}) if d else None


def link_ngay(acts: list[Activity], mode: str) -> str | None:
    diem = [d for d in (_diem_maps(a) for a in acts) if d]
    if len(diem) < 2:
        return None
    q = {"api": "1", "origin": diem[0], "destination": diem[-1], "travelmode": _GMAPS_MODE.get(mode, "driving")}
    if len(diem) > 2:
        q["waypoints"] = "|".join(diem[1:-1][:9])
    return "https://www.google.com/maps/dir/?" + urlencode(q)


def register_export_itinerary_tool(mcp: FastMCP):
    @mcp.tool(
        name="export_itinerary",
        title="Export Itinerary (Xuất lịch trình)",
        description=(
            "Xuất lịch trình đã chốt ra nội dung file lịch `.ics` (nhập được vào lịch iPhone, "
            "Google Calendar, Outlook; giờ Việt Nam), kèm link Google Maps cho từng điểm và link "
            "chỉ đường cả ngày (không cần API key).\n\n"
            "Khi nào dùng: bước cuối, sau khi người dùng chốt phương án. Trả `ics_content` để "
            "backend lưu thành file `ics_filename` cho người dùng tải.\n"
            "Không tốn quota. Chưa xuất PDF."
        ),
        tags={"journey", "export"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "nội bộ (RFC 5545, Google Maps URLs)"},
    )
    async def export_itinerary(
        title: Annotated[str, Field(description="Tên chuyến đi, VD 'Đà Lạt 3N2Đ cùng gia đình'.")],
        days: Annotated[list[Day], Field(min_length=1, max_length=30, description="Các ngày của lịch trình.")],
        travel_mode: Annotated[
            Literal["motorcycle", "car", "walking", "transit"], Field(description="Phương tiện cho link chỉ đường.")
        ] = "car",
        reminder_minutes: Annotated[
            int | None, Field(ge=0, le=1440, description="Nhắc trước bao nhiêu phút; bỏ trống = không nhắc.")
        ] = 30,
    ) -> dict[str, Any]:
        input_context = {"title": title, "days": len(days)}
        try:
            dong = [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//Ailusion Journey//MCP//VI",
                "CALSCALE:GREGORIAN",
                "METHOD:PUBLISH",
                f"X-WR-CALNAME:{_thoat(title)}",
                "X-WR-TIMEZONE:Asia/Ho_Chi_Minh",
                *_VTIMEZONE,
            ]
            dau_thoi_gian = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            tom_tat = []
            so_su_kien = 0
            for ngay in days:
                try:
                    d = datetime.strptime(ngay.date, "%Y-%m-%d")
                except ValueError:
                    return format_validation_error("days.date", ngay.date, "phải có dạng YYYY-MM-DD")
                hd = sorted(ngay.activities, key=lambda a: a.time)
                for i, a in enumerate(hd):
                    for ten, gio in (("time", a.time), ("end_time", a.end_time)):
                        if gio and not _GIO.match(gio):
                            return format_validation_error(f"activities.{ten}", gio, "phải có dạng HH:MM")
                    h, m = map(int, a.time.split(":"))
                    bat_dau = d.replace(hour=h, minute=m)
                    if a.end_time:
                        h2, m2 = map(int, a.end_time.split(":"))
                        ket_thuc = d.replace(hour=h2, minute=m2)
                    elif i + 1 < len(hd):
                        h2, m2 = map(int, hd[i + 1].time.split(":"))
                        ket_thuc = min(d.replace(hour=h2, minute=m2), bat_dau + timedelta(hours=2))
                    else:
                        ket_thuc = bat_dau + timedelta(hours=1)
                    if ket_thuc <= bat_dau:
                        ket_thuc = bat_dau + timedelta(minutes=30)

                    mo_ta = "\n".join(x for x in (a.note, link_tim(a)) if x)
                    su_kien = [
                        "BEGIN:VEVENT",
                        f"UID:{uuid.uuid4()}@ailusion-journey",
                        f"DTSTAMP:{dau_thoi_gian}",
                        f"DTSTART;TZID=Asia/Ho_Chi_Minh:{bat_dau:%Y%m%dT%H%M%S}",
                        f"DTEND;TZID=Asia/Ho_Chi_Minh:{ket_thuc:%Y%m%dT%H%M%S}",
                        f"SUMMARY:{_thoat(a.title)}",
                    ]
                    if a.location:
                        su_kien.append(f"LOCATION:{_thoat(a.location)}")
                    if a.lat is not None and a.lng is not None:
                        su_kien.append(f"GEO:{a.lat};{a.lng}")
                    if mo_ta:
                        su_kien.append(f"DESCRIPTION:{_thoat(mo_ta)}")
                    if reminder_minutes is not None:
                        su_kien += [
                            "BEGIN:VALARM",
                            "ACTION:DISPLAY",
                            f"DESCRIPTION:{_thoat(a.title)}",
                            f"TRIGGER:-PT{reminder_minutes}M",
                            "END:VALARM",
                        ]
                    su_kien.append("END:VEVENT")
                    dong += su_kien
                    so_su_kien += 1

                tom_tat.append(
                    {
                        "date": ngay.date,
                        "route_url": link_ngay(hd, travel_mode),
                        "places": [
                            {"time": a.time, "title": a.title, "maps_url": link_tim(a)} for a in hd
                        ],
                    }
                )
            dong.append("END:VCALENDAR")
            noi_dung = "\r\n".join(x for d_ in dong for x in _gap(d_)) + "\r\n"
            ten_file = re.sub(r"[^a-z0-9]+", "-", bo_dau(title)).strip("-")[:60] or "lich-trinh"

            return drop_empty(
                {
                    "status": "success",
                    "ics_filename": f"{ten_file}.ics",
                    "ics_content": noi_dung,
                    "events": so_su_kien,
                    "days": tom_tat,
                }
            )
        except Exception as e:
            logger.error("export_itinerary thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response("Không xuất được lịch trình", exception=e, input_context=input_context)

    return export_itinerary
