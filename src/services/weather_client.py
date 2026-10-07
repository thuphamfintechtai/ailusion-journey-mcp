"""Client gọi WeatherAPI.com: dự báo, dự báo xa, lịch sử, biển, thiên văn, chất lượng không khí.

WeatherAPI báo lỗi bằng `{"error": {"code": ..., "message": ...}}`:

| code       | Nghĩa                          | Xử lý                                   |
|------------|--------------------------------|-----------------------------------------|
| 2006, 2008 | Key sai / bị khoá              | Cho key nghỉ dài, thử key kế tiếp       |
| 2007       | Hết quota tháng                | Cho key nghỉ QUOTA_COOLDOWN, thử key kế |
| 2009       | Gói của key không có endpoint  | Thử key kế (có thể gói cao hơn), không nghỉ; hết key -> WeatherPlanError |
| 1006       | Không tìm thấy địa điểm        | WeatherNotFound                          |
| khác 4xx   | Sai tham số                    | Raise luôn                               |

Tài liệu: https://www.weatherapi.com/docs/
"""

import json
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import httpx
import structlog

from ..config.settings import settings
from .key_pool import KeyPool

logger = structlog.get_logger(__name__)

_CACHE_TOI_DA = 500
_KEY_HONG = {2006, 2008}
_HET_QUOTA = {2007}
_KHONG_HO_TRO = {2009}


class WeatherError(Exception):
    def __init__(self, message: str, code: int | None = None, het_key: bool = False):
        super().__init__(message)
        self.code = code
        self.het_key = het_key


class WeatherNotFound(WeatherError):
    pass


class WeatherPlanError(WeatherError):
    """Gói của mọi key đều không hỗ trợ endpoint / khoảng ngày này."""


def _ma_loi(r: httpx.Response) -> tuple[int | None, str]:
    try:
        e = r.json().get("error") or {}
        return e.get("code"), str(e.get("message") or r.reason_phrase)
    except Exception:
        return None, r.reason_phrase or f"HTTP {r.status_code}"


class WeatherClient:
    def __init__(
        self,
        api_keys: list[str],
        base_url: str,
        timeout: int,
        *,
        cache_ttl: int = 1800,
        quota_cooldown: float = 6 * 3600,
        invalid_key_cooldown: float = 24 * 3600,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.pool = KeyPool(api_keys, "weatherapi", clock)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.quota_cooldown = quota_cooldown
        self.invalid_key_cooldown = invalid_key_cooldown
        self._clock = clock
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=self.timeout, headers={"User-Agent": "ailusion-journey-mcp/0.1"}
            )
        return self._client

    async def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        params = {k: v for k, v in params.items() if v is not None}
        khoa = path + json.dumps(params, sort_keys=True, ensure_ascii=False)
        if self.cache_ttl > 0 and (muc := self._cache.get(khoa)) and self._clock() - muc[0] <= self.cache_ttl:
            self._cache.move_to_end(khoa)
            return muc[1]

        if not self.pool.keys:
            raise WeatherError("Chưa cấu hình WEATHERAPI_API_KEYS", het_key=True)
        ung_vien = self.pool.candidates()
        if not ung_vien:
            raise WeatherError("Mọi key WeatherAPI đang tạm ngưng, thử lại sau", het_key=True)

        loi_cuoi: WeatherError | None = None
        do_key = True
        for idx, key in ung_vien:
            try:
                r = await self.client.get(path, params={**params, "key": key})
            except (httpx.TimeoutException, httpx.TransportError) as e:
                logger.warning("Gọi WeatherAPI lỗi mạng, thử key kế tiếp", key=KeyPool.mask(key), loi=type(e).__name__)
                loi_cuoi, do_key = WeatherError(f"Lỗi mạng khi gọi WeatherAPI: {type(e).__name__}"), False
                continue

            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:
                    # 200 nhưng không phải JSON: coi như lỗi phía server, thử key khác.
                    logger.warning("WeatherAPI trả dữ liệu không phải JSON", key=KeyPool.mask(key))
                    loi_cuoi, do_key = WeatherError("WeatherAPI trả dữ liệu không phải JSON", 200), False
                    continue
                self.pool.mark_ok(idx)
                if self.cache_ttl > 0:
                    self._cache[khoa] = (self._clock(), data)
                    while len(self._cache) > _CACHE_TOI_DA:
                        self._cache.popitem(last=False)
                return data

            code, thong_bao = _ma_loi(r)
            if code in _KEY_HONG:
                self.pool.cooldown(idx, self.invalid_key_cooldown, thong_bao)
            elif code in _HET_QUOTA:
                self.pool.cooldown(idx, self.quota_cooldown, thong_bao)
            elif code in _KHONG_HO_TRO:
                loi_cuoi, do_key = WeatherPlanError(thong_bao, code), False
                continue
            elif code == 1006:
                raise WeatherNotFound(thong_bao, code)
            elif r.status_code >= 500:
                logger.warning("WeatherAPI lỗi phía server, thử key kế tiếp", status=r.status_code)
                loi_cuoi, do_key = WeatherError(f"WeatherAPI lỗi HTTP {r.status_code}", code), False
                continue
            else:
                raise WeatherError(f"WeatherAPI từ chối tham số: {thong_bao}", code)
            loi_cuoi = WeatherError(thong_bao, code)

        loi_cuoi = loi_cuoi or WeatherError("Mọi key WeatherAPI đều bị từ chối")
        if not isinstance(loi_cuoi, WeatherPlanError):
            loi_cuoi.het_key = do_key
        raise loi_cuoi

    async def forecast(self, q: str, days: int, **kw: Any) -> dict[str, Any]:
        return await self.get("/forecast.json", {"q": q, "days": days, "lang": "vi", **kw})

    async def future(self, q: str, dt: str) -> dict[str, Any]:
        return await self.get("/future.json", {"q": q, "dt": dt, "lang": "vi"})

    async def history(self, q: str, dt: str) -> dict[str, Any]:
        return await self.get("/history.json", {"q": q, "dt": dt, "lang": "vi"})

    async def marine(self, q: str, days: int) -> dict[str, Any]:
        return await self.get("/marine.json", {"q": q, "days": days, "tides": "yes", "lang": "vi"})

    async def astronomy(self, q: str, dt: str) -> dict[str, Any]:
        return await self.get("/astronomy.json", {"q": q, "dt": dt})

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None


weather_client = WeatherClient(
    api_keys=settings.weatherapi_key_list,
    base_url=settings.weatherapi_base_url,
    timeout=settings.weatherapi_timeout,
    cache_ttl=settings.weatherapi_cache_ttl,
    quota_cooldown=settings.serpapi_quota_cooldown,
    invalid_key_cooldown=settings.serpapi_invalid_key_cooldown,
)
