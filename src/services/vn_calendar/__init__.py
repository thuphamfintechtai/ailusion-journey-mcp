"""Lịch Việt Nam: âm lịch, ngày nghỉ (có nghỉ bù), lễ hội, mức cao điểm.

Dữ liệu ghép từ nhiều nguồn có thứ tự ưu tiên, xem sources.py và service.py.
"""

from ...config.settings import settings
from .service import THU, DayInfo, VnCalendarService, chuoi_am_lich
from .sources import SOURCE_FACTORIES, tao_nguon

vn_calendar_service = VnCalendarService(
    tao_nguon([s.strip() for s in settings.calendar_sources.split(",") if s.strip()])
)

__all__ = [
    "SOURCE_FACTORIES",
    "THU",
    "DayInfo",
    "VnCalendarService",
    "chuoi_am_lich",
    "tao_nguon",
    "vn_calendar_service",
]
