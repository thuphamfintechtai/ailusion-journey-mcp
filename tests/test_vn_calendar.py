"""Test lịch Việt Nam: từng nguồn, quy tắc gộp, mức cao điểm và hai tool. Không gọi mạng."""

import json
from datetime import date, timedelta

import pytest
from fastmcp import Client

from src.services.vn_calendar import VnCalendarService
from src.services.vn_calendar.models import CalendarEvent, SourceResult
from src.services.vn_calendar.sources import THU_MUC_DATA, OverridesSource, RulesSource, _phan_tich_ics

ICS_MAU = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20260831\r\nDTEND;VALUE=DATE:20260901\r\n"
    "SUMMARY:Quốc khánh (ngày lễ bổ sung)\r\nDESCRIPTION:Ngày lễ\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20260902\r\nDTEND;VALUE=DATE:20260903\r\n"
    "SUMMARY:Quốc khánh\r\nDESCRIPTION:Ngày lễ\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20260822\r\nDTEND;VALUE=DATE:20260823\r\n"
    "SUMMARY:Ngày làm việc\r\nDESCRIPTION:Ngày lễ kỷ niệm\\nĐể ẩn\r\n  các ngày\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20261225\r\nDTEND;VALUE=DATE:20261226\r\n"
    "SUMMARY:Giáng sinh\r\nDESCRIPTION:Ngày lễ kỷ niệm\\nĐể ẩn\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


class NguonGia:
    """Nguồn giả trả sẵn danh sách event."""

    def __init__(self, name, priority, events, covers=False, ranges=()):
        self.name, self.priority = name, priority
        self._events, self._covers, self._ranges = events, covers, list(ranges)

    async def load(self, year):
        ev = [e for e in self._events if e.date.year == year]
        return SourceResult(
            events=ev,
            covers_day_offs=self._covers and bool(ev),
            authoritative_ranges=[r for r in self._ranges if r[0].year == year],
        )


def _rules():
    return RulesSource(THU_MUC_DATA / "rules.json")


# --- nguồn -------------------------------------------------------------------


async def test_rules_tinh_tet_va_gio_to_theo_am_lich():
    r = await _rules().load(2026)
    tet = sorted(e.date for e in r.events if e.name == "Tết Nguyên Đán")
    # Mùng 1 Tết Bính Ngọ = 17/2/2026; luật: giao thừa + mùng 1-4
    assert tet == [date(2026, 2, 16) + timedelta(days=i) for i in range(5)]
    gio_to = [e.date for e in r.events if e.name == "Giỗ Tổ Hùng Vương"]
    assert gio_to == [date(2026, 4, 26)]


async def test_rules_from_year():
    truoc = await _rules().load(2025)
    sau = await _rules().load(2026)
    assert not any(e.name == "Ngày Văn hóa Việt Nam" for e in truoc.events)
    assert any(e.date == date(2026, 11, 24) for e in sau.events if e.kind == "day_off")


async def test_rules_mua_vat_qua_nam():
    r = await _rules().load(2027)
    tet = [m for m in r.seasons if m.name == "Cao điểm đi lại dịp Tết"]
    # 20 tháng Chạp Bính Ngọ -> 10 tháng Giêng Đinh Mùi
    assert any(m.start == date(2027, 1, 27) and m.end == date(2027, 2, 15) for m in tet)


def test_phan_tich_ics():
    ev = _phan_tich_ics(ICS_MAU, "google_ics")
    loai = {(e.date, e.kind) for e in ev}
    assert (date(2026, 8, 31), "day_off") in loai
    assert (date(2026, 8, 22), "working_day") in loai
    assert (date(2026, 12, 25), "observance") in loai


async def test_overrides_doc_file(tmp_path):
    (tmp_path / "2030.json").write_text(
        json.dumps(
            {
                "year": 2030,
                "verified": False,
                "days_off": [{"from": "2030-02-01", "to": "2030-02-03", "name": "Nghỉ Tết"}],
                "working_days": [{"date": "2030-02-09", "name": "Làm bù"}],
            }
        ),
        encoding="utf-8",
    )
    r = await OverridesSource(tmp_path).load(2030)
    assert len([e for e in r.events if e.kind == "day_off"]) == 3
    assert r.authoritative_ranges[-1] == (date(2030, 2, 9), date(2030, 2, 9))
    assert r.events[0].note  # chưa xác minh thì có ghi chú
    assert (await OverridesSource(tmp_path).load(2031)).events == []


# --- gộp nguồn ---------------------------------------------------------------


async def test_nguon_cao_phu_nam_thi_bo_ngay_nghi_du_kien_cua_nguon_thap():
    thap = NguonGia("thap", 10, [CalendarEvent(date(2026, 9, 3), "Nghỉ dự kiến", "day_off", "thap")])
    cao = NguonGia(
        "cao", 50, [CalendarEvent(date(2026, 9, 2), "Quốc khánh", "day_off", "cao")], covers=True
    )
    kq = await VnCalendarService([thap, cao]).get_days(date(2026, 9, 2), date(2026, 9, 3))
    ngay = {d.date: d for d in kq["days"]}
    assert ngay[date(2026, 9, 2)].status == "nghi_le"
    assert ngay[date(2026, 9, 3)].day_off is False  # dự kiến của nguồn thấp bị bỏ


async def test_override_thang_trong_khoang_chac_chan():
    giua = NguonGia(
        "giua", 50, [CalendarEvent(date(2027, 2, 11), "Tết", "day_off", "giua")], covers=True
    )
    cao = NguonGia(
        "cao",
        100,
        [CalendarEvent(date(2027, 2, 4), "Nghỉ Tết", "day_off", "cao")],
        ranges=[(date(2027, 2, 4), date(2027, 2, 4)), (date(2027, 2, 11), date(2027, 2, 11))],
    )
    kq = await VnCalendarService([giua, cao]).get_days(date(2027, 2, 4), date(2027, 2, 11))
    ngay = {d.date: d for d in kq["days"]}
    assert ngay[date(2027, 2, 4)].status_source == "cao"
    assert ngay[date(2027, 2, 11)].day_off is False  # override khẳng định không nghỉ


async def test_lam_bu_cuoi_tuan_khong_phai_ngay_nghi():
    nguon = NguonGia("g", 50, [CalendarEvent(date(2026, 8, 22), "Ngày làm việc", "working_day", "g")])
    kq = await VnCalendarService([nguon]).get_days(date(2026, 8, 22), date(2026, 8, 23))
    ngay = {d.date: d for d in kq["days"]}
    assert ngay[date(2026, 8, 22)].day_off is False and ngay[date(2026, 8, 22)].status == "lam_bu"
    assert ngay[date(2026, 8, 23)].day_off is True  # Chủ nhật


async def test_cao_diem_ky_nghi_dai():
    ev = [CalendarEvent(date(2026, 8, 31) + timedelta(days=i), "Quốc khánh", "day_off", "g") for i in range(3)]
    kq = await VnCalendarService([NguonGia("g", 50, ev)]).get_days(date(2026, 8, 27), date(2026, 9, 3))
    ngay = {d.date: d for d in kq["days"]}
    # T7 29/8 -> T4 2/9 là khối 5 ngày
    assert kq["day_off_blocks"][0]["days"] == 5
    assert ngay[date(2026, 8, 28)].peak_level == 2  # ngày trước kỳ nghỉ
    assert ngay[date(2026, 8, 30)].peak_level == 3
    assert "cao điểm chiều về" in ngay[date(2026, 9, 2)].peak_reasons
    assert ngay[date(2026, 9, 3)].peak_level == 0


async def test_le_hoi_loc_theo_tinh():
    svc = VnCalendarService([_rules()])
    # 13 tháng Giêng Đinh Mùi = 18/2/2027 (Hội Lim, Bắc Ninh)
    ninh = await svc.get_days(date(2027, 2, 18), date(2027, 2, 18), province="Bac Ninh")
    hcm = await svc.get_days(date(2027, 2, 18), date(2027, 2, 18), province="TP. Hồ Chí Minh")
    assert any(e.name == "Hội Lim" for e in ninh["days"][0].events)
    assert "lễ hội Hội Lim" in ninh["days"][0].peak_reasons
    assert not any(e.name == "Hội Lim" for e in hcm["days"][0].events)


# --- tool --------------------------------------------------------------------


@pytest.fixture
def _lich_offline(monkeypatch, tmp_path):
    import src.tools.vn_calendar as mod

    monkeypatch.setattr(mod, "vn_calendar_service", VnCalendarService([_rules(), OverridesSource(tmp_path)]))


async def _goi(ten, args):
    from src.fastmcp_server import mcp

    async with Client(mcp) as c:
        r = await c.call_tool(ten, args, raise_on_error=False)
    return r.structured_content


async def test_tool_vn_calendar_khoang_dai_chi_tra_ngay_dang_chu_y(_lich_offline):
    kq = await _goi("vn_calendar", {"start_date": "2026-02-01", "end_date": "2026-03-31"})
    assert kq["status"] == "success"
    assert "Tết Nguyên Đán" in kq["summary"]["holidays"]
    assert len(kq["days"]) < kq["range"]["days"]
    tet = next(d for d in kq["days"] if d["date"] == "2026-02-17")
    assert tet["lunar"].startswith("1/1 ") and tet["source"] == "rules"


async def test_tool_vn_calendar_qua_gioi_han(_lich_offline):
    kq = await _goi("vn_calendar", {"start_date": "2026-01-01", "end_date": "2026-12-31"})
    assert kq["status"] == "error"


async def test_tool_convert_lunar_date_hai_chieu():
    am = await _goi("convert_lunar_date", {"solar_date": "2026-02-17"})
    assert am["lunar"]["text"] == "1/1 năm Bính Ngọ"
    duong = await _goi("convert_lunar_date", {"lunar_day": 15, "lunar_month": 8, "lunar_year": 2026})
    assert duong["solar"] == "2026-09-25"


async def test_tool_convert_lunar_date_ngay_khong_ton_tai():
    kq = await _goi("convert_lunar_date", {"lunar_day": 30, "lunar_month": 2, "lunar_year": 2026})
    # Tháng 2 âm Bính Ngọ chỉ có 29 ngày
    assert kq["status"] == "error"
