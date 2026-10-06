"""Gộp các nguồn lịch thành thông tin từng ngày: nghỉ hay làm, âm lịch, sự kiện, mức cao điểm.

Quy tắc gộp ngày nghỉ (duyệt nguồn từ ưu tiên cao xuống thấp):
1. Ngày nằm trong `authoritative_ranges` của một nguồn -> nguồn đó quyết định.
2. Nguồn có khẳng định nghỉ / làm bù cho ngày đó -> lấy, trừ khi một nguồn
   ưu tiên cao hơn đã `covers_day_offs` cho năm đó (nguồn thấp chỉ là dự kiến).
3. Không nguồn nào nói gì -> Thứ 7, Chủ nhật là nghỉ.
Lễ hội và ngày kỷ niệm thì gộp từ mọi nguồn, bỏ trùng tên.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from lunar_vn import can_chi, convert_solar_to_lunar

from ...utils.text import bo_dau
from .models import CalendarEvent, CalendarSource, Season, SourceResult

THU = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
_DEM_RONG = 10  # ngày mở rộng hai đầu để tính đúng khối nghỉ cắt ngang khoảng hỏi


@dataclass
class DayInfo:
    date: date
    lunar: str
    day_off: bool
    status: str | None  # "nghi_le" | "lam_bu" | None
    status_source: str | None
    events: list[CalendarEvent] = field(default_factory=list)
    peak_level: int = 0
    peak_reasons: list[str] = field(default_factory=list)


def chuoi_am_lich(d: date) -> str:
    al = convert_solar_to_lunar(d.day, d.month, d.year)
    nhuan = " nhuận" if al.leap else ""
    return f"{al.day}/{al.month}{nhuan} {can_chi.get_year_can_chi(al.year)}"


def _khop_tinh(tinh_hoi: str | None, cac_tinh: tuple[str, ...]) -> bool:
    if not tinh_hoi:
        return True
    q = bo_dau(tinh_hoi)
    return any(q == bo_dau(t) or q in bo_dau(t) or bo_dau(t) in q for t in cac_tinh)


class VnCalendarService:
    def __init__(self, sources: list[CalendarSource]):
        self.sources = sorted(sources, key=lambda s: -s.priority)

    async def _tai(self, nam_list: list[int]) -> tuple[dict[tuple[str, int], SourceResult], list[str]]:
        viec = {(s.name, n): s.load(n) for s in self.sources for n in nam_list}
        kq = await asyncio.gather(*viec.values(), return_exceptions=True)
        du_lieu: dict[tuple[str, int], SourceResult] = {}
        canh_bao: list[str] = []
        for khoa, r in zip(viec, kq):
            if isinstance(r, BaseException):
                canh_bao.append(f"Nguồn {khoa[0]} lỗi năm {khoa[1]}: {type(r).__name__}")
                du_lieu[khoa] = SourceResult()
            else:
                du_lieu[khoa] = r
                canh_bao += r.warnings
        return du_lieu, sorted(set(canh_bao))

    def _trang_thai(
        self, d: date, du_lieu: dict[tuple[str, int], SourceResult]
    ) -> tuple[str | None, str | None, list[CalendarEvent]]:
        """Trả (trạng thái, nguồn quyết định, các event nghỉ/làm bù của nguồn đó)."""
        da_phu = False
        for s in self.sources:
            r = du_lieu[(s.name, d.year)]
            cua_ngay = [e for e in r.events if e.date == d and e.kind in ("day_off", "working_day")]
            chac_chan = any(bd <= d <= kt for bd, kt in r.authoritative_ranges)
            if chac_chan or (cua_ngay and not da_phu):
                if any(e.kind == "day_off" for e in cua_ngay):
                    return "nghi_le", s.name, cua_ngay
                if any(e.kind == "working_day" for e in cua_ngay):
                    return "lam_bu", s.name, cua_ngay
                return None, s.name, []
            da_phu = da_phu or r.covers_day_offs
        return None, None, []

    async def get_days(
        self,
        start: date,
        end: date,
        province: str | None = None,
        include_observances: bool = True,
    ) -> dict[str, Any]:
        tu, den = start - timedelta(days=_DEM_RONG), end + timedelta(days=_DEM_RONG)
        du_lieu, canh_bao = await self._tai(list(range(tu.year, den.year + 1)))

        moi_event = [e for r in du_lieu.values() for e in r.events]
        moi_mua: list[Season] = [m for r in du_lieu.values() for m in r.seasons]

        ngay: dict[date, DayInfo] = {}
        d = tu
        while d <= den:
            trang_thai, nguon, ev_nghi = self._trang_thai(d, du_lieu)
            nghi = trang_thai == "nghi_le" or (d.weekday() >= 5 and trang_thai != "lam_bu")

            khac: dict[str, CalendarEvent] = {}
            for e in moi_event:
                if e.date != d or e.kind in ("day_off", "working_day"):
                    continue
                if e.kind == "observance" and not include_observances:
                    continue
                if e.kind == "festival" and not _khop_tinh(province, e.provinces):
                    continue
                khac.setdefault(bo_dau(e.name), e)
            ten_nghi: dict[str, CalendarEvent] = {}
            for e in ev_nghi:
                ten_nghi.setdefault(bo_dau(e.name), e)

            ngay[d] = DayInfo(
                date=d,
                lunar=chuoi_am_lich(d),
                day_off=nghi,
                status=trang_thai,
                status_source=nguon,
                events=list(ten_nghi.values()) + [e for k, e in khac.items() if k not in ten_nghi],
            )
            d += timedelta(days=1)

        khoi_nghi = self._tinh_cao_diem(ngay, moi_mua, province)

        trong_khoang = [ngay[x] for x in sorted(ngay) if start <= x <= end]
        khoi_trong_khoang = [k for k in khoi_nghi if k["to"] >= start and k["from"] <= end]
        mua = sorted(
            {(m.start, m.end, m.name, m.peak_level, m.note) for m in moi_mua if m.end >= start and m.start <= end}
        )
        return {
            "days": trong_khoang,
            "day_off_blocks": khoi_trong_khoang,
            "seasons": [
                {"name": n, "from": a, "to": b, "peak_level": lv, "note": nt} for a, b, n, lv, nt in mua
            ],
            "sources": [s.name for s in self.sources],
            "warnings": canh_bao,
        }

    @staticmethod
    def _tinh_cao_diem(
        ngay: dict[date, DayInfo], mua: list[Season], province: str | None
    ) -> list[dict[str, Any]]:
        def nang(info: DayInfo, muc: int, ly_do: str) -> None:
            info.peak_level = max(info.peak_level, muc)
            if ly_do not in info.peak_reasons:
                info.peak_reasons.append(ly_do)

        cac_ngay = sorted(ngay)
        for d in cac_ngay:
            if ngay[d].day_off:
                nang(ngay[d], 1, "ngày nghỉ")

        # Khối ngày nghỉ liên tiếp có ngày lễ, dài >= 3 ngày: cả khối cao điểm.
        khoi: list[dict[str, Any]] = []
        i = 0
        while i < len(cac_ngay):
            if not ngay[cac_ngay[i]].day_off:
                i += 1
                continue
            j = i
            while j + 1 < len(cac_ngay) and ngay[cac_ngay[j + 1]].day_off:
                j += 1
            dai = j - i + 1
            le = [e.name for d in cac_ngay[i : j + 1] for e in ngay[d].events if e.kind == "day_off"]
            le = list(dict.fromkeys(le))
            if le and dai >= 3:
                ten = f"kỳ nghỉ {dai} ngày ({', '.join(le[:2])})"
                for d in cac_ngay[i : j + 1]:
                    nang(ngay[d], 3, ten)
                nang(ngay[cac_ngay[j]], 3, "cao điểm chiều về")
                truoc = cac_ngay[i] - timedelta(days=1)
                if truoc in ngay:
                    nang(ngay[truoc], 2, "ngày trước kỳ nghỉ, cao điểm chiều đi")
            if le:
                khoi.append({"from": cac_ngay[i], "to": cac_ngay[j], "days": dai, "holidays": le})
            i = j + 1

        for m in mua:
            for d in cac_ngay:
                if m.start <= d <= m.end:
                    nang(ngay[d], m.peak_level, m.name)

        # Lễ hội đông người chỉ nâng mức khi đang hỏi đúng tỉnh đó.
        if province:
            for d in cac_ngay:
                for e in ngay[d].events:
                    if e.kind == "festival" and e.crowd == "cao":
                        nang(ngay[d], 2, f"lễ hội {e.name}")
        return khoi
