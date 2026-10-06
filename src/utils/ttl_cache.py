"""Cache trong bộ nhớ có hạn dùng (TTL) và giới hạn số mục (bỏ mục cũ nhất).

Dùng cho các client gọi nguồn miễn phí có giới hạn tần suất (Vietcombank 1 lần / 5 phút,
Tranco 1 lần / giây...). Mất khi restart server, đủ cho mục đích giảm lượt gọi.
"""

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any

_THIEU = object()


class TtlCache:
    def __init__(self, ttl: float, max_items: int = 1000, clock: Callable[[], float] = time.monotonic):
        self.ttl = ttl
        self.max_items = max_items
        self._clock = clock
        self._data: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()

    def get(self, key: Hashable, default: Any = None) -> Any:
        muc = self._data.get(key, _THIEU)
        if muc is _THIEU:
            return default
        luc, gia_tri = muc  # type: ignore[misc]
        if self._clock() - luc > self.ttl:
            del self._data[key]
            return default
        self._data.move_to_end(key)
        return gia_tri

    def __contains__(self, key: Hashable) -> bool:
        return self.get(key, _THIEU) is not _THIEU

    def set(self, key: Hashable, value: Any) -> None:
        self._data[key] = (self._clock(), value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_items:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()
