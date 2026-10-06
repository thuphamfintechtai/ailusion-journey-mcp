"""Kiểu dữ liệu chung của lịch Việt Nam."""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal, Protocol

# day_off: ngày nghỉ chính thức; working_day: đi làm bù (dù là cuối tuần);
# observance: ngày kỷ niệm không nghỉ; festival: lễ hội gắn với tỉnh/thành.
EventKind = Literal["day_off", "working_day", "observance", "festival"]


@dataclass(frozen=True)
class CalendarEvent:
    date: date
    name: str
    kind: EventKind
    source: str
    provinces: tuple[str, ...] = field(default=())
    crowd: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class Season:
    """Khoảng ngày cao điểm lặp lại (hè, Tết...), đã quy ra dương lịch."""

    start: date
    end: date
    name: str
    peak_level: int
    source: str
    note: str | None = None


@dataclass
class SourceResult:
    events: list[CalendarEvent] = field(default_factory=list)
    seasons: list[Season] = field(default_factory=list)
    # True nếu nguồn có đủ ngày nghỉ của năm đó; khi đó ngày nghỉ "dự kiến"
    # của nguồn ưu tiên thấp hơn bị bỏ (lễ hội, ngày kỷ niệm vẫn giữ).
    covers_day_offs: bool = False
    # Khoảng ngày nguồn này khẳng định chắc chắn (override): trong khoảng đó
    # mọi khẳng định nghỉ / không nghỉ của nguồn thấp hơn bị bỏ.
    authoritative_ranges: list[tuple[date, date]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class CalendarSource(Protocol):
    """Một nguồn lịch. Thêm nguồn mới: viết class theo Protocol này rồi đăng ký
    trong `SOURCE_FACTORIES` (sources.py) và thêm tên vào CALENDAR_SOURCES."""

    name: str
    priority: int  # cao hơn thắng khi mâu thuẫn

    async def load(self, year: int) -> SourceResult: ...
