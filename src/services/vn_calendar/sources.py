"""Các nguồn lịch Việt Nam. Mỗi nguồn trả `SourceResult` cho một năm dương lịch.

| Nguồn        | Ưu tiên | Dữ liệu                                                       |
|--------------|---------|---------------------------------------------------------------|
| rules        | 10      | rules.json: luật lặp lại hằng năm (lễ dương/âm, lễ hội, mùa)   |
| google_ics   | 50      | Lịch ngày lễ VN công khai của Google, có nghỉ bù / làm bù     |
| overrides    | 100     | overrides/<năm>.json: quyết định riêng của Chính phủ từng năm |

Thêm nguồn: viết class có `name`, `priority`, `async load(year)` rồi thêm vào
`SOURCE_FACTORIES`, bật bằng biến CALENDAR_SOURCES.
"""

import json
import re
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import structlog
from lunar_vn import convert_lunar_to_solar

from ...config.settings import settings
from .models import CalendarEvent, CalendarSource, Season, SourceResult

logger = structlog.get_logger(__name__)

THU_MUC_DATA = Path(__file__).resolve().parents[2] / "data" / "calendar"


def _cac_ngay(bat_dau: date, so_ngay: int) -> list[date]:
    return [bat_dau + timedelta(days=i) for i in range(so_ngay)]


# --- rules -------------------------------------------------------------------


class RulesSource:
    """Tính ngày lễ từ luật trong rules.json — chạy offline, không cần cập nhật theo năm."""

    name = "rules"
    priority = 10

    def __init__(self, rules_file: Path):
        self.rules_file = rules_file
        self._rules: dict[str, Any] | None = None

    def _doc(self) -> dict[str, Any]:
        if self._rules is None:
            self._rules = json.loads(self.rules_file.read_text(encoding="utf-8"))
        return self._rules

    @staticmethod
    def _ngay_goc(lich: str, nam: int, thang: int, ngay: int) -> date | None:
        if lich == "solar":
            return date(nam, thang, ngay)
        try:
            return convert_lunar_to_solar(ngay, thang, nam)
        except ValueError:
            return None

    async def load(self, year: int) -> SourceResult:
        rules = self._doc()
        kq = SourceResult()

        for e in rules.get("events", []):
            # Ngày âm của năm âm Y-1 / Y / Y+1 đều có thể rơi vào năm dương Y.
            nam_xet = [year] if e["calendar"] == "solar" else [year - 1, year, year + 1]
            for nam in nam_xet:
                if e.get("from_year") and nam < e["from_year"]:
                    continue
                if e.get("to_year") and nam > e["to_year"]:
                    continue
                goc = self._ngay_goc(e["calendar"], nam, e["month"], e["day"])
                if goc is None:
                    continue
                bat_dau = goc + timedelta(days=e.get("offset_days", 0))
                for d in _cac_ngay(bat_dau, e.get("duration_days", 1)):
                    if d.year != year:
                        continue
                    kq.events.append(
                        CalendarEvent(
                            date=d,
                            name=e["name"],
                            kind=e["kind"],
                            source=self.name,
                            provinces=tuple(e.get("provinces", [])),
                            crowd=e.get("crowd"),
                            note=e.get("note"),
                        )
                    )

        for s in rules.get("seasons", []):
            for nam in ([year] if s["calendar"] == "solar" else [year - 1, year, year + 1]):
                tu, den = s["from"], s["to"]
                # Mùa vắt qua năm (VD 20 tháng Chạp -> 10 tháng Giêng) thì "to" thuộc năm sau.
                nam_den = nam + 1 if (den["month"], den["day"]) < (tu["month"], tu["day"]) else nam
                bd = self._ngay_goc(s["calendar"], nam, tu["month"], tu["day"])
                kt = self._ngay_goc(s["calendar"], nam_den, den["month"], den["day"])
                if bd is None or kt is None or kt.year < year or bd.year > year:
                    continue
                kq.seasons.append(
                    Season(bd, kt, s["name"], s["peak_level"], self.name, s.get("note"))
                )
        return kq


# --- google_ics --------------------------------------------------------------


def _phan_tich_ics(noi_dung: str, nguon: str) -> list[CalendarEvent]:
    """Đọc VEVENT dạng ngày (VALUE=DATE) từ file ICS — không cần thư viện ngoài."""
    noi_dung = re.sub(r"\r?\n[ \t]", "", noi_dung)  # gộp dòng gấp (RFC 5545)
    events: list[CalendarEvent] = []
    for khoi in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", noi_dung, re.S):
        bd = re.search(r"DTSTART;VALUE=DATE:(\d{8})", khoi)
        kt = re.search(r"DTEND;VALUE=DATE:(\d{8})", khoi)
        ten = re.search(r"^SUMMARY:(.*)$", khoi, re.M)
        mo_ta = re.search(r"^DESCRIPTION:(.*)$", khoi, re.M)
        if not bd or not ten:
            continue
        ngay_bd = datetime.strptime(bd.group(1), "%Y%m%d").date()
        ngay_kt = datetime.strptime(kt.group(1), "%Y%m%d").date() if kt else ngay_bd + timedelta(days=1)
        ten_s = ten.group(1).strip().replace("\\,", ",")
        mo_ta_s = mo_ta.group(1) if mo_ta else ""

        if "ngày làm việc" in ten_s.lower():
            loai = "working_day"
        elif mo_ta_s.startswith("Ngày lễ kỷ niệm"):
            loai = "observance"
        else:
            loai = "day_off"

        for d in _cac_ngay(ngay_bd, max(1, (ngay_kt - ngay_bd).days)):
            events.append(CalendarEvent(date=d, name=ten_s, kind=loai, source=nguon))
    return events


class GoogleIcsSource:
    """Lịch ngày lễ VN công khai của Google Calendar (không cần key), cache trong bộ nhớ."""

    name = "google_ics"
    priority = 50

    def __init__(self, url: str, ttl: int, timeout: float):
        self.url = url
        self.ttl = ttl
        self.timeout = timeout
        self._cache: tuple[float, list[CalendarEvent]] | None = None

    async def _tai(self) -> list[CalendarEvent]:
        if self._cache and time.monotonic() - self._cache[0] < self.ttl:
            return self._cache[1]
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            r = await client.get(self.url)
            r.raise_for_status()
        events = _phan_tich_ics(r.text, self.name)
        self._cache = (time.monotonic(), events)
        logger.info("Đã tải lịch Google", so_su_kien=len(events))
        return events

    async def load(self, year: int) -> SourceResult:
        try:
            tat_ca = await self._tai()
        except Exception as e:
            logger.warning("Không tải được lịch Google", error=str(e))
            if self._cache:  # dùng bản cũ còn hơn không có
                tat_ca = self._cache[1]
            else:
                return SourceResult(warnings=[f"Không tải được lịch Google ({type(e).__name__})"])
        events = [e for e in tat_ca if e.date.year == year]
        return SourceResult(
            events=events,
            covers_day_offs=any(e.kind == "day_off" for e in events),
        )


# --- overrides ---------------------------------------------------------------


class OverridesSource:
    """Đọc overrides/<năm>.json: quyết định riêng của từng năm, ưu tiên cao nhất."""

    name = "overrides"
    priority = 100

    def __init__(self, thu_muc: Path):
        self.thu_muc = thu_muc

    async def load(self, year: int) -> SourceResult:
        kq = SourceResult()
        file = self.thu_muc / f"{year}.json"
        if not file.exists():
            return kq
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.error("File override lịch hỏng", file=str(file), error=str(e))
            return SourceResult(warnings=[f"File {file.name} không đọc được"])

        ghi_chu = None if data.get("verified", True) else "Chưa đối chiếu văn bản gốc"
        for khoang in data.get("days_off", []):
            bd, kt = date.fromisoformat(khoang["from"]), date.fromisoformat(khoang["to"])
            kq.authoritative_ranges.append((bd, kt))
            for d in _cac_ngay(bd, (kt - bd).days + 1):
                kq.events.append(CalendarEvent(d, khoang["name"], "day_off", self.name, note=ghi_chu))
        for ngay in data.get("working_days", []):
            d = date.fromisoformat(ngay["date"])
            kq.authoritative_ranges.append((d, d))
            kq.events.append(CalendarEvent(d, ngay.get("name", "Đi làm bù"), "working_day", self.name, note=ghi_chu))
        return kq


# --- đăng ký -----------------------------------------------------------------

SOURCE_FACTORIES: dict[str, Callable[[], CalendarSource]] = {
    "rules": lambda: RulesSource(Path(settings.calendar_rules_file or THU_MUC_DATA / "rules.json")),
    "google_ics": lambda: GoogleIcsSource(
        settings.calendar_ics_url, settings.calendar_ics_ttl, settings.http_timeout
    ),
    "overrides": lambda: OverridesSource(Path(settings.calendar_overrides_dir or THU_MUC_DATA / "overrides")),
}


def tao_nguon(ten_nguon: list[str]) -> list[CalendarSource]:
    nguon = []
    for ten in ten_nguon:
        if ten not in SOURCE_FACTORIES:
            logger.warning("Bỏ qua nguồn lịch không biết", nguon=ten, co_san=list(SOURCE_FACTORIES))
            continue
        nguon.append(SOURCE_FACTORIES[ten]())
    return nguon
