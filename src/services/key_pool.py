"""Quản lý nhiều API key của một dịch vụ: thứ tự thử, cho key lỗi nghỉ, nhớ key còn tốt.

Dùng cho client mới (Tavily). SerpApiClient và VietmapClient đang tự giữ logic
tương tự; khi sửa hai client đó thì chuyển sang dùng class này.
"""

import time
from collections.abc import Callable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class KeyPool:
    def __init__(self, keys: list[str], service: str, clock: Callable[[], float] = time.monotonic):
        self.keys = keys
        self.service = service
        self._clock = clock
        self._idx = 0
        self._nghi_den: dict[int, float] = {}

    @staticmethod
    def mask(key: str) -> str:
        return f"{key[:6]}..." if key else ""

    def _dang_nghi(self, idx: int) -> bool:
        het = self._nghi_den.get(idx)
        if het is None:
            return False
        if self._clock() >= het:
            del self._nghi_den[idx]
            return False
        return True

    def candidates(self) -> list[tuple[int, str]]:
        """Các key đang dùng được, bắt đầu từ key thành công gần nhất."""
        if not self.keys:
            return []
        thu_tu = [(self._idx + i) % len(self.keys) for i in range(len(self.keys))]
        return [(i, self.keys[i]) for i in thu_tu if not self._dang_nghi(i)]

    def cooldown(self, idx: int, giay: float, ly_do: str) -> None:
        self._nghi_den[idx] = self._clock() + giay
        logger.warning(
            "Tạm ngưng dùng key", dich_vu=self.service, key=self.mask(self.keys[idx]), ly_do=ly_do, nghi_giay=int(giay)
        )

    def mark_ok(self, idx: int) -> None:
        self._idx = idx

    def status(self) -> list[dict[str, Any]]:
        bay_gio = self._clock()
        return [
            {
                "key": self.mask(k),
                "dang_nghi": self._dang_nghi(i),
                "con_nghi_giay": max(0, int(self._nghi_den.get(i, bay_gio) - bay_gio)),
            }
            for i, k in enumerate(self.keys)
        ]

    def reset(self) -> None:
        self._idx = 0
        self._nghi_den.clear()
