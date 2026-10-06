"""Client gọi Vietmap Maps API (tìm kiếm, toạ độ, chỉ đường, ma trận, TSP).

Giống SerpApiClient ở cách giữ key: key nằm ở query `apikey`, có nhiều key và
fallback khi key bị từ chối (401/403) hoặc hết hạn mức (429 / OVER_DAILY_LIMIT).
Khác ở hai điểm:
- Vietmap báo lỗi nghiệp vụ bằng HTTP 200 + `code` khác "OK" (route, matrix, tsp).
- Có cache trong bộ nhớ theo (endpoint, tham số) vì gói free chỉ miễn phí 2 tháng
  đầu; địa chỉ, toạ độ, đường đi giữa hai điểm hầu như không đổi trong 1 giờ.

Tài liệu: https://maps.vietmap.vn/docs/
"""

import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from ..config.settings import settings
from ..utils.text import bo_dau

logger = structlog.get_logger(__name__)

_TOA_DO = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
_TIEN_TO_REF = ("auto:", "geocode:", "vm:")
# code trong body JSON nghĩa là key hết hạn mức, đáng để thử key khác
_CODE_HET_HAN_MUC = {"OVER_DAILY_LIMIT", "OVER_QUERY_LIMIT"}
_CACHE_TOI_DA = 2000


class VietmapError(Exception):
    """Lỗi từ Vietmap, đã bỏ key khỏi message. `het_key=True`: mọi key đều không dùng được."""

    def __init__(self, message: str, status_code: int | None = None, code: str | None = None, het_key: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.het_key = het_key


@dataclass(frozen=True)
class Diem:
    """Một điểm đã quy ra toạ độ."""

    lat: float
    lng: float
    label: str
    ref_id: str | None = None

    @property
    def latlng(self) -> str:
        return f"{self.lat},{self.lng}"


class VietmapClient:
    def __init__(
        self,
        api_keys: list[str],
        base_url: str,
        timeout: int,
        *,
        cache_ttl: int = 3600,
        rate_limit_cooldown: float = 900,
        invalid_key_cooldown: float = 24 * 3600,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.api_keys = api_keys
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.rate_limit_cooldown = rate_limit_cooldown
        self.invalid_key_cooldown = invalid_key_cooldown
        self._clock = clock
        self._idx = 0
        self._nghi_den: dict[int, float] = {}
        self._cache: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers={"User-Agent": "ailusion-journey-mcp/0.1"},
            )
        return self._client

    @staticmethod
    def _mask(key: str) -> str:
        return f"{key[:6]}..." if key else ""

    def _dang_nghi(self, idx: int) -> bool:
        het = self._nghi_den.get(idx)
        if het is None:
            return False
        if self._clock() >= het:
            del self._nghi_den[idx]
            return False
        return True

    def _cho_nghi(self, idx: int, giay: float, ly_do: str) -> None:
        self._nghi_den[idx] = self._clock() + giay
        logger.warning(
            "Tạm ngưng dùng key Vietmap", key=self._mask(self.api_keys[idx]), ly_do=ly_do, nghi_giay=int(giay)
        )

    def _doc_cache(self, khoa: tuple) -> Any | None:
        muc = self._cache.get(khoa)
        if muc is None:
            return None
        if self._clock() - muc[0] > self.cache_ttl:
            del self._cache[khoa]
            return None
        self._cache.move_to_end(khoa)
        return muc[1]

    def _ghi_cache(self, khoa: tuple, data: Any) -> None:
        self._cache[khoa] = (self._clock(), data)
        self._cache.move_to_end(khoa)
        while len(self._cache) > _CACHE_TOI_DA:
            self._cache.popitem(last=False)

    async def get(self, path: str, params: list[tuple[str, Any]]) -> Any:
        """GET có fallback key + cache. `params` là list cặp vì `point` lặp nhiều lần."""
        params = [(k, v) for k, v in params if v is not None]
        khoa = (path, tuple(params))
        if self.cache_ttl > 0 and (co := self._doc_cache(khoa)) is not None:
            return co

        if not self.api_keys:
            raise VietmapError("Chưa cấu hình VIETMAP_API_KEYS", het_key=True)
        thu_tu = [(self._idx + i) % len(self.api_keys) for i in range(len(self.api_keys))]
        ung_vien = [i for i in thu_tu if not self._dang_nghi(i)]
        if not ung_vien:
            raise VietmapError("Mọi key Vietmap đang tạm ngưng, thử lại sau", 429, het_key=True)

        loi_cuoi: VietmapError | None = None
        do_key = True
        for idx in ung_vien:
            key = self.api_keys[idx]
            try:
                r = await self.client.get(path, params=[*params, ("apikey", key)])
            except (httpx.TimeoutException, httpx.TransportError) as e:
                logger.warning("Gọi Vietmap lỗi mạng, thử key kế tiếp", key=self._mask(key), loi=type(e).__name__)
                loi_cuoi, do_key = VietmapError(f"Lỗi mạng khi gọi Vietmap: {type(e).__name__}"), False
                continue

            if r.status_code in (401, 403):
                self._cho_nghi(idx, self.invalid_key_cooldown, f"HTTP {r.status_code}")
                loi_cuoi = VietmapError("Key Vietmap bị từ chối", r.status_code)
                continue
            if r.status_code == 429:
                self._cho_nghi(idx, self.rate_limit_cooldown, "HTTP 429")
                loi_cuoi = VietmapError("Key Vietmap chạm giới hạn", 429)
                continue
            if r.status_code >= 500:
                logger.warning("Vietmap lỗi phía server, thử key kế tiếp", status=r.status_code)
                loi_cuoi, do_key = VietmapError(f"Vietmap lỗi HTTP {r.status_code}", r.status_code), False
                continue
            if r.status_code >= 400:
                raise VietmapError(f"Vietmap từ chối tham số (HTTP {r.status_code}): {r.text[:200]}", r.status_code)

            data = r.json()
            code = data.get("code") if isinstance(data, dict) else None
            if code in _CODE_HET_HAN_MUC:
                self._cho_nghi(idx, self.rate_limit_cooldown, code)
                loi_cuoi = VietmapError(f"Key Vietmap hết hạn mức ({code})", code=code)
                continue
            if code and code != "OK":
                raise VietmapError(f"Vietmap trả lỗi {code}: {data.get('messages')}", code=code)

            self._idx = idx
            if self.cache_ttl > 0:
                self._ghi_cache(khoa, data)
            return data

        loi_cuoi = loi_cuoi or VietmapError("Mọi key Vietmap đều bị từ chối")
        loi_cuoi.het_key = do_key
        raise loi_cuoi

    # --- API cấp cao ----------------------------------------------------------

    async def autocomplete(self, text: str, **kw: Any) -> list[dict[str, Any]]:
        return await self.get("/autocomplete/v4", [("text", text), *kw.items()]) or []

    async def search(self, text: str, **kw: Any) -> list[dict[str, Any]]:
        return await self.get("/search/v4", [("text", text), *kw.items()]) or []

    async def place(self, ref_id: str) -> dict[str, Any]:
        return await self.get("/place/v4", [("refid", ref_id)]) or {}

    async def reverse(self, lat: float, lng: float, display_type: int = 5) -> list[dict[str, Any]]:
        return await self.get("/reverse/v4", [("lat", lat), ("lng", lng), ("display_type", display_type)]) or []

    async def resolve(self, gia_tri: str) -> Diem:
        """Quy một điểm ra toạ độ: 'lat,lng', ref_id (auto:/geocode:/vm:) hoặc tên / địa chỉ."""
        gia_tri = gia_tri.strip()
        if m := _TOA_DO.match(gia_tri):
            return Diem(float(m.group(1)), float(m.group(2)), gia_tri)
        ref = gia_tri
        if not gia_tri.startswith(_TIEN_TO_REF):
            ref = await self._chon_ref(gia_tri)
        p = await self.place(ref)
        if p.get("lat") is None:
            raise VietmapError(f"Không lấy được toạ độ cho '{gia_tri}'", code="NOT_FOUND")
        return Diem(p["lat"], p["lng"], p.get("display") or gia_tri, ref)

    async def _chon_ref(self, ten: str) -> str:
        """Chọn ref_id cho một tên / địa chỉ.

        Thử thật (2026-10) cho thấy autocomplete/v4 xếp địa danh nổi tiếng lên đầu tốt
        hơn search/v4 (search trả "Khách sạn Sân bay Tân Sơn Nhất", "Hồ Hoàn Kiếm" ở
        Ô Chợ Dừa...). Nên: autocomplete trước, ưu tiên kết quả trùng tên, không có thì
        lấy kết quả đầu; autocomplete rỗng mới gọi search. Tên trùng nhau giữa các tỉnh
        thì cần người gọi ghi kèm tỉnh / phường.
        """
        q = bo_dau(ten)

        def chon(ds: list[dict[str, Any]]) -> dict[str, Any] | None:
            for r in ds:
                if bo_dau(r.get("name") or "") == q:
                    return r
            return ds[0] if ds else None

        if r := chon(await self.autocomplete(ten, display_type=1)):
            return r["ref_id"]
        if r := chon(await self.search(ten, display_type=1)):
            return r["ref_id"]
        raise VietmapError(f"Không tìm thấy địa điểm '{ten}'", code="NOT_FOUND")

    async def route(self, diem: list[Diem], vehicle: str, **kw: Any) -> dict[str, Any]:
        return await self.get(
            "/route/v4",
            [*(("point", d.latlng) for d in diem), ("vehicle", vehicle), ("points_encoded", "true"), *kw.items()],
        )

    async def matrix(self, diem: list[Diem], vehicle: str, sources: list[int], destinations: list[int]) -> dict[str, Any]:
        return await self.get(
            "/matrix/v4",
            [
                *(("point", d.latlng) for d in diem),
                ("vehicle", vehicle),
                ("sources", ";".join(map(str, sources))),
                ("destinations", ";".join(map(str, destinations))),
                ("annotation", "duration,distance"),
            ],
        )

    async def tsp(self, diem: list[Diem], vehicle: str, roundtrip: bool, end_at_last: bool) -> dict[str, Any]:
        return await self.get(
            "/tsp/v4",
            [
                *(("point", d.latlng) for d in diem),
                ("vehicle", vehicle),
                ("roundtrip", "true" if roundtrip else "false"),
                ("sources", "first"),
                ("destinations", "last" if (end_at_last and not roundtrip) else None),
                ("points_encoded", "false"),
            ],
        )

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None


vietmap_client = VietmapClient(
    api_keys=settings.vietmap_key_list,
    base_url=settings.vietmap_base_url,
    timeout=settings.vietmap_timeout,
    cache_ttl=settings.vietmap_cache_ttl,
    rate_limit_cooldown=settings.serpapi_rate_limit_cooldown,
    invalid_key_cooldown=settings.serpapi_invalid_key_cooldown,
)
