"""HTTP client dùng chung cho các service upstream.

Tool KHÔNG gọi httpx trực tiếp — gọi qua service ở đây để có sẵn
connection pool, timeout, retry và log thống nhất.
"""

from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config.settings import settings

logger = structlog.get_logger(__name__)


class HttpClient:
    """Wrapper quanh httpx.AsyncClient cho một service upstream.

    Client được tạo lazy ở lần gọi đầu và giữ lại cho các request sau,
    nên connection pool sống xuyên suốt vòng đời server.
    """

    def __init__(self, base_url: str, api_key: str = "", service_name: str = "upstream"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.service_name = service_name
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {"User-Agent": "ailusion-journey-mcp/0.1"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=settings.http_timeout,
                headers=headers,
                follow_redirects=True,
            )
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        reraise=True,
    )
    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Gọi upstream và trả về JSON đã parse.

        Raises:
            httpx.HTTPStatusError: khi upstream trả 4xx/5xx
            httpx.HTTPError: khi lỗi mạng / timeout sau khi hết retry
        """
        logger.debug(
            "Đang gọi upstream", service=self.service_name, method=method, path=path
        )
        response = await self.client.request(
            method, path, params=params, json=json, headers=headers
        )
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    async def get(self, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> Any:
        return await self.request("POST", path, **kwargs)

    async def health_check(self, path: str = "/health", timeout: float = 5.0) -> bool:
        """Ping upstream. Trả False thay vì raise — dùng cho health resource."""
        try:
            response = await self.client.get(path, timeout=timeout)
            return response.status_code < 500
        except Exception as e:
            logger.warning(
                "Health check upstream thất bại", service=self.service_name, error=str(e)
            )
            return False

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None


# Singleton cho từng upstream — import thẳng vào tool.
backend_client = HttpClient(
    base_url=settings.backend_base_url,
    api_key=settings.backend_api_key,
    service_name="ailusion-journey-be",
)

llm_client = HttpClient(
    base_url=settings.llm_base_url,
    api_key=settings.llm_api_key,
    service_name="ailusion-journey-llm",
)


async def close_http_clients() -> None:
    """Đóng mọi client khi server shutdown."""
    from .serpapi_client import serpapi_client
    from .tavily_client import tavily_client
    from .vietmap_client import vietmap_client
    from .weather_client import weather_client

    for client in (backend_client, llm_client, serpapi_client, vietmap_client, tavily_client, weather_client):
        await client.close()
