"""Các nguồn dữ liệu mở không cần key: Open Topo Data (độ cao), Tranco (thứ hạng domain),
Wayback CDX (tuổi domain), GDACS (thiên tai toàn cầu).

Đều là dịch vụ công cộng miễn phí có giới hạn tần suất, nên:
- Open Topo Data: tối đa 1 request / giây, 100 điểm / request, 1.000 request / ngày.
- Tranco: tối đa 1 request / giây.
- Có cache dài cho dữ liệu ít đổi (thứ hạng, tuổi domain: 7 ngày; thiên tai: 30 phút).
"""

import asyncio
import time
from typing import Any

import httpx
import structlog

from ..config.settings import settings
from ..utils.ttl_cache import TtlCache

logger = structlog.get_logger(__name__)

_UA = {"User-Agent": "ailusion-journey-mcp/0.1 (travel planner)"}


class OpenDataError(Exception):
    pass


class _GioiHanTanSuat:
    """Bảo đảm cách nhau ít nhất `giay` giữa hai request tới cùng một dịch vụ."""

    def __init__(self, giay: float):
        self.giay = giay
        self._khoa = asyncio.Lock()
        self._lan_cuoi = 0.0

    async def __aenter__(self):
        await self._khoa.acquire()
        cho = self._lan_cuoi + self.giay - time.monotonic()
        if cho > 0:
            await asyncio.sleep(cho)

    async def __aexit__(self, *exc):
        self._lan_cuoi = time.monotonic()
        self._khoa.release()


class OpenDataClient:
    def __init__(self, timeout: int):
        self.timeout = timeout
        self._topo = _GioiHanTanSuat(1.05)
        self._tranco = _GioiHanTanSuat(1.05)
        # Wayback trả lỗi khi gọi dồn dập (thử 2026-10: 3 request song song đều hỏng).
        self._wayback = _GioiHanTanSuat(1.0)
        self.cache_lau = TtlCache(7 * 24 * 3600, max_items=5000)
        self.cache_ngan = TtlCache(1800, max_items=50)

    async def _get(self, dia_chi: str, params: dict[str, Any] | None = None) -> httpx.Response:
        # Tham số truy vấn đi trong dict riêng: Wayback có tham số tên `url`.
        async with httpx.AsyncClient(timeout=self.timeout, headers=_UA, follow_redirects=True) as c:
            return await c.get(dia_chi, params=params or None)

    # --- Open Topo Data -------------------------------------------------------

    async def elevations(self, points: list[tuple[float, float]], dataset: str = "srtm30m") -> list[float | None]:
        """Độ cao (m) cho tối đa 100 điểm (lat, lng)."""
        if len(points) > 100:
            raise OpenDataError("Open Topo Data nhận tối đa 100 điểm mỗi lần")
        vi_tri = "|".join(f"{lat:.6f},{lng:.6f}" for lat, lng in points)
        async with self._topo:
            try:
                r = await self._get(f"https://api.opentopodata.org/v1/{dataset}", {"locations": vi_tri})
            except httpx.HTTPError as e:
                raise OpenDataError(f"Không gọi được Open Topo Data: {type(e).__name__}") from e
        if r.status_code == 429:
            raise OpenDataError("Open Topo Data hết hạn mức (1.000 lượt / ngày), thử lại sau")
        if r.status_code >= 400:
            raise OpenDataError(f"Open Topo Data trả HTTP {r.status_code}")
        return [x.get("elevation") for x in r.json().get("results") or []]

    # --- Tranco + Wayback -----------------------------------------------------

    async def tranco_rank(self, domain: str) -> int | None:
        khoa = ("tranco", domain)
        if khoa in self.cache_lau:
            return self.cache_lau.get(khoa)
        async with self._tranco:
            try:
                r = await self._get(f"https://tranco-list.eu/api/ranks/domain/{domain}")
            except httpx.HTTPError as e:
                raise OpenDataError(f"Không gọi được Tranco: {type(e).__name__}") from e
        hang = None
        if r.status_code == 200:
            ds = r.json().get("ranks") or []
            hang = ds[0].get("rank") if ds else None
        elif r.status_code != 404:
            raise OpenDataError(f"Tranco trả HTTP {r.status_code}")
        self.cache_lau.set(khoa, hang)
        return hang

    async def wayback_first_seen(self, domain: str) -> str | None:
        """Ngày đầu tiên Internet Archive lưu domain (YYYY-MM-DD), None nếu chưa từng lưu."""
        khoa = ("wayback", domain)
        if khoa in self.cache_lau:
            return self.cache_lau.get(khoa)
        async with self._wayback:
            try:
                r = await self._get(
                    "https://web.archive.org/cdx/search/cdx",
                    {"url": domain, "limit": 1, "output": "json", "fl": "timestamp"},
                )
            except httpx.HTTPError as e:
                raise OpenDataError(f"Không gọi được Wayback: {type(e).__name__}") from e
        # Chỉ kết luận "chưa từng lưu" khi Wayback trả 200 hợp lệ; lỗi thì báo lỗi, không cache.
        if r.status_code != 200:
            raise OpenDataError(f"Wayback trả HTTP {r.status_code}")
        ngay = None
        try:
            dong = r.json() if r.text.strip() else []
        except ValueError as e:
            raise OpenDataError("Wayback trả dữ liệu không đọc được") from e
        if len(dong) > 1 and dong[1]:
            ts = dong[1][0]
            ngay = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}"
        self.cache_lau.set(khoa, ngay)
        return ngay

    # --- GDACS ----------------------------------------------------------------

    async def gdacs_events(self, **params: Any) -> list[dict[str, Any]]:
        khoa = ("gdacs", tuple(sorted(params.items())))
        if khoa in self.cache_ngan:
            return self.cache_ngan.get(khoa)
        try:
            r = await self._get("https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH", params)
        except httpx.HTTPError as e:
            raise OpenDataError(f"Không gọi được GDACS: {type(e).__name__}") from e
        if r.status_code == 204 or not r.content:
            ds: list[dict[str, Any]] = []
        elif r.status_code >= 400:
            raise OpenDataError(f"GDACS trả HTTP {r.status_code}")
        else:
            ds = r.json().get("features") or []
        self.cache_ngan.set(khoa, ds)
        return ds


open_data_client = OpenDataClient(timeout=settings.http_timeout)
