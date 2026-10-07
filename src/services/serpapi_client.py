"""Client gọi SerpApi (Google Hotels / Flights / Maps, TripAdvisor...).

Khác `HttpClient` ở hai điểm:
- SerpApi nhận key qua query `api_key`, không qua header Authorization.
- Có nhiều key (SERPAPI_API_KEYS) và cơ chế fallback giữa các key.

Cơ chế fallback
---------------
Mỗi lần gọi thử lần lượt các key đang dùng được, bắt đầu từ key thành công
gần nhất. Tuỳ lỗi mà key bị cho nghỉ (cooldown) khác nhau, để lần gọi sau
không tốn request vào key biết chắc là hỏng:

| Lỗi                                   | Xử lý                                   |
|---------------------------------------|-----------------------------------------|
| 401 / 403 (key sai, bị khoá)          | Nghỉ `SERPAPI_INVALID_KEY_COOLDOWN`     |
| 429 "run out of searches" (hết tháng) | Nghỉ `SERPAPI_QUOTA_COOLDOWN`           |
| 429 khác (chạm giới hạn theo giờ)     | Nghỉ `SERPAPI_RATE_LIMIT_COOLDOWN`      |
| 5xx, timeout, lỗi mạng                | Không nghỉ, thử key kế tiếp             |
| 4xx khác (tham số sai)                | Raise luôn, đổi key cũng không đỡ       |

Khi mọi key đều đang nghỉ thì raise ngay, không gọi SerpApi.

Tài liệu engine và các field trả về: xem docs/SERPAPI.md.
"""

import time
from collections.abc import Callable
from typing import Any

import httpx
import structlog

from ..config.settings import settings

logger = structlog.get_logger(__name__)

# Thông báo SerpApi dùng khi hết quota tháng (khác với chạm giới hạn giờ).
_DAU_HIEU_HET_QUOTA = ("run out of searches", "plan searches")
# SerpApi đôi khi trả 200 kèm "error" cho lỗi của key thay vì 401/429.
_DAU_HIEU_KEY_HONG = ("invalid api key", "api key is invalid", "account has been")


class SerpApiError(Exception):
    """Lỗi từ SerpApi, đã bỏ key khỏi message.

    `het_key=True` nghĩa là mọi key đều không dùng được (hết quota, sai key,
    đang nghỉ) — tool nên báo cho model biết là tạm thời không có dữ liệu,
    đừng gọi lại ngay.
    """

    def __init__(self, message: str, status_code: int | None = None, het_key: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.het_key = het_key


class SerpApiClient:
    def __init__(
        self,
        api_keys: list[str],
        base_url: str,
        timeout: int,
        *,
        rate_limit_cooldown: float = 900,
        quota_cooldown: float = 6 * 3600,
        invalid_key_cooldown: float = 24 * 3600,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.api_keys = api_keys
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.rate_limit_cooldown = rate_limit_cooldown
        self.quota_cooldown = quota_cooldown
        self.invalid_key_cooldown = invalid_key_cooldown
        self._clock = clock
        self._idx = 0
        # index key -> thời điểm (theo clock) key được dùng lại
        self._nghi_den: dict[int, float] = {}
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
        het_han = self._nghi_den.get(idx)
        if het_han is None:
            return False
        if self._clock() >= het_han:
            del self._nghi_den[idx]
            return False
        return True

    def _cho_nghi(self, idx: int, giay: float, ly_do: str) -> None:
        self._nghi_den[idx] = self._clock() + giay
        logger.warning(
            "Tạm ngưng dùng key SerpApi",
            key=self._mask(self.api_keys[idx]),
            ly_do=ly_do,
            nghi_giay=int(giay),
        )

    def key_status(self) -> list[dict[str, Any]]:
        """Trạng thái từng key (đã che) — dùng cho health/debug."""
        bay_gio = self._clock()
        return [
            {
                "key": self._mask(key),
                "dang_nghi": self._dang_nghi(i),
                "con_nghi_giay": max(0, int(self._nghi_den.get(i, bay_gio) - bay_gio)),
            }
            for i, key in enumerate(self.api_keys)
        ]

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        if not self.api_keys:
            raise SerpApiError("Chưa cấu hình SERPAPI_API_KEYS", het_key=True)

        thu_tu = [(self._idx + i) % len(self.api_keys) for i in range(len(self.api_keys))]
        ung_vien = [i for i in thu_tu if not self._dang_nghi(i)]
        if not ung_vien:
            som_nhat = min(self._nghi_den.values()) - self._clock()
            raise SerpApiError(
                f"Mọi key SerpApi đang tạm ngưng, thử lại sau khoảng {int(som_nhat // 60) + 1} phút",
                429,
                het_key=True,
            )

        loi_cuoi: SerpApiError | None = None
        loi_do_key = True  # mọi lần thất bại đều do key (không phải do mạng/5xx)
        for idx in ung_vien:
            key = self.api_keys[idx]
            try:
                response = await self.client.get(path, params={**params, "api_key": key})
            except (httpx.TimeoutException, httpx.TransportError) as e:
                logger.warning(
                    "Gọi SerpApi lỗi mạng, thử key kế tiếp",
                    key=self._mask(key),
                    loi=type(e).__name__,
                )
                loi_cuoi = SerpApiError(f"Lỗi mạng khi gọi SerpApi: {type(e).__name__}")
                loi_do_key = False
                continue

            thong_bao = _doc_loi(response)
            loai = _phan_loai_loi(response.status_code, thong_bao)

            if loai == "key_hong":
                self._cho_nghi(idx, self.invalid_key_cooldown, thong_bao)
            elif loai == "het_quota":
                self._cho_nghi(idx, self.quota_cooldown, thong_bao)
            elif loai == "qua_tai_gio":
                self._cho_nghi(idx, self.rate_limit_cooldown, thong_bao)
            elif loai == "loi_server":
                logger.warning(
                    "SerpApi lỗi phía server, thử key kế tiếp",
                    key=self._mask(key),
                    status=response.status_code,
                )
                loi_cuoi = SerpApiError(thong_bao, response.status_code)
                loi_do_key = False
                continue
            elif response.status_code >= 400:
                raise SerpApiError(thong_bao, response.status_code)
            else:
                # Ghi nhớ key còn dùng được để lần sau thử nó trước.
                self._idx = idx
                try:
                    data = response.json()
                except ValueError:
                    # 200 nhưng không phải JSON (trang lỗi HTML của proxy / CDN): coi như lỗi
                    # phía server để thử key khác, và để tool chạy được fallback.
                    logger.warning("SerpApi trả dữ liệu không phải JSON", key=self._mask(key))
                    loi_cuoi = SerpApiError("SerpApi trả dữ liệu không phải JSON", response.status_code)
                    loi_do_key = False
                    continue
                if isinstance(data, dict) and data.get("error"):
                    # SerpApi trả 200 + "error" khi không có kết quả cho truy vấn.
                    logger.info(
                        "SerpApi không có kết quả", engine=params.get("engine"), loi=data["error"]
                    )
                return data

            loi_cuoi = SerpApiError(thong_bao, response.status_code)

        if loi_cuoi is None:
            loi_cuoi = SerpApiError("Mọi key SerpApi đều bị từ chối")
        loi_cuoi.het_key = loi_do_key
        raise loi_cuoi

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
        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
    except Exception:
        pass
    return response.reason_phrase or f"HTTP {response.status_code}"


def _phan_loai_loi(status: int, thong_bao: str) -> str | None:
    """Xếp lỗi vào nhóm quyết định có đổi key / cho key nghỉ hay không."""
    tb = thong_bao.lower()
    if status in (401, 403) or any(d in tb for d in _DAU_HIEU_KEY_HONG):
        return "key_hong"
    if status == 429 or any(d in tb for d in _DAU_HIEU_HET_QUOTA):
        if any(d in tb for d in _DAU_HIEU_HET_QUOTA):
            return "het_quota"
        return "qua_tai_gio"
    if status >= 500:
        return "loi_server"
    return None


serpapi_client = SerpApiClient(
    api_keys=settings.serpapi_key_list,
    base_url=settings.serpapi_base_url,
    timeout=settings.serpapi_timeout,
    rate_limit_cooldown=settings.serpapi_rate_limit_cooldown,
    quota_cooldown=settings.serpapi_quota_cooldown,
    invalid_key_cooldown=settings.serpapi_invalid_key_cooldown,
)
