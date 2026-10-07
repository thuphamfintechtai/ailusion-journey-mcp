"""Client gọi Tavily (search, extract) với nhiều key, fallback và cache trong bộ nhớ.

| Lỗi                         | Xử lý                                         |
|-----------------------------|-----------------------------------------------|
| 401 / 403                   | Key sai -> nghỉ SERPAPI_INVALID_KEY_COOLDOWN  |
| 432 / 433                   | Hết hạn mức gói / PayGo -> nghỉ QUOTA_COOLDOWN|
| 429                         | Gọi quá nhanh -> nghỉ theo Retry-After (>=60s)|
| 5xx, timeout, lỗi mạng      | Thử key kế tiếp, không cho nghỉ               |
| 400 / 422                   | Sai tham số -> raise luôn                     |

Tài liệu: https://docs.tavily.com/documentation/api-reference/endpoint/search
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


class TavilyError(Exception):
    def __init__(self, message: str, status_code: int | None = None, het_key: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.het_key = het_key


def _doc_loi(r: httpx.Response) -> str:
    try:
        d = r.json().get("detail")
        if isinstance(d, dict):
            return str(d.get("error") or d)
        return str(d or r.reason_phrase)
    except Exception:
        return r.reason_phrase or f"HTTP {r.status_code}"


class TavilyClient:
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
        self.pool = KeyPool(api_keys, "tavily", clock)
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

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        body = {k: v for k, v in body.items() if v is not None}
        khoa = path + json.dumps(body, sort_keys=True, ensure_ascii=False)
        if self.cache_ttl > 0 and (muc := self._cache.get(khoa)) and self._clock() - muc[0] <= self.cache_ttl:
            self._cache.move_to_end(khoa)
            return muc[1]

        if not self.pool.keys:
            raise TavilyError("Chưa cấu hình TAVILY_API_KEYS", het_key=True)
        ung_vien = self.pool.candidates()
        if not ung_vien:
            raise TavilyError("Mọi key Tavily đang tạm ngưng, thử lại sau", 429, het_key=True)

        loi_cuoi: TavilyError | None = None
        do_key = True
        for idx, key in ung_vien:
            try:
                r = await self.client.post(path, json=body, headers={"Authorization": f"Bearer {key}"})
            except (httpx.TimeoutException, httpx.TransportError) as e:
                logger.warning("Gọi Tavily lỗi mạng, thử key kế tiếp", key=KeyPool.mask(key), loi=type(e).__name__)
                loi_cuoi, do_key = TavilyError(f"Lỗi mạng khi gọi Tavily: {type(e).__name__}"), False
                continue

            if r.status_code in (401, 403):
                self.pool.cooldown(idx, self.invalid_key_cooldown, f"HTTP {r.status_code}")
            elif r.status_code in (432, 433):
                self.pool.cooldown(idx, self.quota_cooldown, _doc_loi(r))
            elif r.status_code == 429:
                try:
                    cho = max(60.0, float(r.headers.get("Retry-After", 60)))
                except ValueError:
                    cho = 60.0
                self.pool.cooldown(idx, cho, "HTTP 429")
            elif r.status_code >= 500:
                logger.warning("Tavily lỗi phía server, thử key kế tiếp", status=r.status_code)
                loi_cuoi, do_key = TavilyError(f"Tavily lỗi HTTP {r.status_code}", r.status_code), False
                continue
            elif r.status_code >= 400:
                raise TavilyError(f"Tavily từ chối tham số: {_doc_loi(r)}", r.status_code)
            else:
                try:
                    data = r.json()
                except ValueError:
                    # 200 nhưng không phải JSON: coi như lỗi phía server, thử key khác.
                    logger.warning("Tavily trả dữ liệu không phải JSON", status=r.status_code)
                    loi_cuoi, do_key = TavilyError("Tavily trả dữ liệu không phải JSON", r.status_code), False
                    continue
                self.pool.mark_ok(idx)
                if self.cache_ttl > 0:
                    self._cache[khoa] = (self._clock(), data)
                    while len(self._cache) > _CACHE_TOI_DA:
                        self._cache.popitem(last=False)
                return data
            loi_cuoi = TavilyError(_doc_loi(r), r.status_code)

        loi_cuoi = loi_cuoi or TavilyError("Mọi key Tavily đều bị từ chối")
        loi_cuoi.het_key = do_key
        raise loi_cuoi

    async def search(self, query: str, **kw: Any) -> dict[str, Any]:
        return await self.post("/search", {"query": query, **kw})

    async def extract(self, urls: list[str], **kw: Any) -> dict[str, Any]:
        return await self.post("/extract", {"urls": urls, **kw})

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None


tavily_client = TavilyClient(
    api_keys=settings.tavily_key_list,
    base_url=settings.tavily_base_url,
    timeout=settings.tavily_timeout,
    cache_ttl=settings.tavily_cache_ttl,
    quota_cooldown=settings.serpapi_quota_cooldown,
    invalid_key_cooldown=settings.serpapi_invalid_key_cooldown,
)
