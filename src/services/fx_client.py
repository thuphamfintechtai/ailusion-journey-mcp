"""Tỷ giá: Vietcombank (chính, VND với ~20 ngoại tệ) + fawazahmed0 currency-api (dự phòng).

- Vietcombank XML không cần key nhưng ghi rõ "Only one request every 5 minutes!", nên cache
  tối thiểu 5 phút (FX_CACHE_TTL, mặc định 30 phút). Có giá mua tiền mặt (Buy), mua chuyển
  khoản (Transfer), bán (Sell) — là số tiền thật người dùng trả / nhận ở ngân hàng.
- fawazahmed0 (jsDelivr, cập nhật ngày, tỷ giá trung bình thị trường) cho tiền Vietcombank
  không niêm yết (VD THB đôi khi thiếu chiều mua, IDR, PHP...).
"""

import asyncio
import xml.etree.ElementTree as ET
from typing import Any

import httpx
import structlog

from ..config.settings import settings
from ..utils.ttl_cache import TtlCache

logger = structlog.get_logger(__name__)

_VCB_URL = "https://portal.vietcombank.com.vn/Usercontrols/TVPortal.TyGia/pXML.aspx"
_DU_PHONG_URL = "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/vnd.json"


class FxError(Exception):
    pass


def _so(s: str | None) -> float | None:
    s = (s or "").strip().replace(",", "")
    try:
        return float(s) if s and s != "-" else None
    except ValueError:
        return None


def phan_tich_vcb(xml: str) -> dict[str, Any]:
    goc = ET.fromstring(xml)
    ty_gia = {}
    for e in goc.findall("Exrate"):
        ma = (e.get("CurrencyCode") or "").strip().upper()
        if ma:
            ty_gia[ma] = {
                "name": (e.get("CurrencyName") or "").strip(),
                "buy": _so(e.get("Buy")),
                "transfer": _so(e.get("Transfer")),
                "sell": _so(e.get("Sell")),
            }
    return {"updated": (goc.findtext("DateTime") or "").strip(), "rates": ty_gia}


class FxClient:
    def __init__(self, timeout: int, cache_ttl: int):
        self.timeout = timeout
        # Không cho TTL dưới 5 phút: điều kiện sử dụng của Vietcombank.
        self.cache = TtlCache(max(cache_ttl, 300), max_items=4)
        self._khoa = asyncio.Lock()

    async def _tai(self, url: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": "ailusion-journey-mcp/0.1"}) as c:
            r = await c.get(url)
            r.raise_for_status()
            return r

    async def vietcombank(self) -> dict[str, Any]:
        async with self._khoa:  # nhiều tool gọi cùng lúc thì chỉ một request ra ngoài
            if (co := self.cache.get("vcb")) is not None:
                return co
            try:
                data = phan_tich_vcb((await self._tai(_VCB_URL)).text)
            except (httpx.HTTPError, ET.ParseError) as e:
                raise FxError(f"Không lấy được tỷ giá Vietcombank: {type(e).__name__}") from e
            self.cache.set("vcb", data)
            return data

    async def du_phong(self) -> dict[str, Any]:
        """{'date': ..., 'vnd': {'usd': 0.000038, ...}} — số ngoại tệ đổi được từ 1 VND."""
        if (co := self.cache.get("du_phong")) is not None:
            return co
        try:
            data = (await self._tai(_DU_PHONG_URL)).json()
        except (httpx.HTTPError, ValueError) as e:
            raise FxError(f"Không lấy được tỷ giá dự phòng: {type(e).__name__}") from e
        self.cache.set("du_phong", data)
        return data

    async def vnd_per_unit(self, currency: str, rate_type: str = "transfer") -> dict[str, Any]:
        """Bao nhiêu VND cho 1 đơn vị `currency`, kèm nguồn."""
        ma = currency.upper()
        if ma == "VND":
            return {"rate": 1.0, "source": "VND", "rate_type": None, "updated": None}
        try:
            vcb = await self.vietcombank()
            dong = vcb["rates"].get(ma)
            gia = dong.get(rate_type) if dong else None
            if gia is None and dong:
                gia = dong.get("transfer") or dong.get("sell")
            if gia:
                return {"rate": gia, "source": "vietcombank", "rate_type": rate_type, "updated": vcb["updated"]}
        except FxError as e:
            logger.warning("Vietcombank lỗi, dùng tỷ giá dự phòng", error=str(e))
        dp = await self.du_phong()
        ti_le = (dp.get("vnd") or {}).get(ma.lower())
        if not ti_le:
            raise FxError(f"Không có tỷ giá cho {ma}")
        return {"rate": 1 / ti_le, "source": "fawazahmed0 (tỷ giá trung bình thị trường)", "rate_type": "mid", "updated": dp.get("date")}


fx_client = FxClient(timeout=settings.http_timeout, cache_ttl=settings.fx_cache_ttl)
