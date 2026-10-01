"""Client gọi SerpApi (Google Hotels / Flights / Maps, TripAdvisor...).

Khác `HttpClient` ở hai điểm:
- SerpApi nhận key qua query `api_key`, không qua header Authorization.
- Có nhiều key (SERPAPI_API_KEYS). Khi key đang dùng bị từ chối (401) hoặc
  hết quota / chạm giới hạn theo giờ (429) thì chuyển sang key kế tiếp.

Tài liệu engine và các field trả về: xem docs/SERPAPI.md.
"""

from typing import Any

import httpx
import structlog

from ..config.settings import settings

logger = structlog.get_logger(__name__)

# Mã lỗi SerpApi trả khi key không dùng được nữa, đáng để thử key khác.
_MA_LOI_DOI_KEY = {401, 429}


class SerpApiError(Exception):
    """Lỗi từ SerpApi, đã bỏ key khỏi message."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class SerpApiClient:
    def __init__(self, api_keys: list[str], base_url: str, timeout: int):
        self.api_keys = api_keys
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._idx = 0
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

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        if not self.api_keys:
            raise SerpApiError("Chưa cấu hình SERPAPI_API_KEYS")

        loi_cuoi: SerpApiError | None = None
        for lan in range(len(self.api_keys)):
            idx = (self._idx + lan) % len(self.api_keys)
            key = self.api_keys[idx]
            response = await self.client.get(path, params={**params, "api_key": key})

            if response.status_code in _MA_LOI_DOI_KEY:
                thong_bao = _doc_loi(response)
                logger.warning(
                    "Key SerpApi bị từ chối, chuyển key khác",
                    key=self._mask(key),
                    status=response.status_code,
                    loi=thong_bao,
                )
                loi_cuoi = SerpApiError(thong_bao, response.status_code)
                continue

            if response.status_code >= 400:
                raise SerpApiError(_doc_loi(response), response.status_code)

            # Ghi nhớ key còn dùng được để lần sau thử nó trước.
            self._idx = idx
            data = response.json()
            if isinstance(data, dict) and data.get("error"):
                # SerpApi trả 200 + "error" khi không có kết quả cho truy vấn.
                logger.info("SerpApi không có kết quả", engine=params.get("engine"), loi=data["error"])
            return data

        raise loi_cuoi or SerpApiError("Mọi key SerpApi đều bị từ chối")

    async def search(self, engine: str, **params: Any) -> dict[str, Any]:
        """Gọi /search.json. Truy vấn trùng tham số trong 1 giờ lấy từ cache, không tốn quota."""
        params = {k: v for k, v in params.items() if v is not None}
        return await self._get("/search.json", {"engine": engine, **params})

    async def account(self, key: str | None = None) -> dict[str, Any]:
        """Gọi Account API (miễn phí, không tính quota). Bỏ `api_key` khỏi kết quả."""
        if key is None:
            data = await self._get("/account.json", {})
        else:
            response = await self.client.get("/account.json", params={"api_key": key})
            if response.status_code >= 400:
                raise SerpApiError(_doc_loi(response), response.status_code)
            data = response.json()
        data.pop("api_key", None)
        return data

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None


def _doc_loi(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error") or response.reason_phrase)
    except Exception:
        return response.reason_phrase or f"HTTP {response.status_code}"


serpapi_client = SerpApiClient(
    api_keys=settings.serpapi_key_list,
    base_url=settings.serpapi_base_url,
    timeout=settings.serpapi_timeout,
)
