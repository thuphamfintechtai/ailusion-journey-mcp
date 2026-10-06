"""Client đọc Google News RSS (tìm tin theo từ khoá, không cần key).

ĐIỀU KHOẢN: dòng copyright trong feed ghi "made available solely for the purpose of rendering
Google News results within a personal feed reader for personal, non-commercial use. Any other
use of the feed is expressly prohibited." Tức là KHÔNG được dùng trong sản phẩm thương mại.
Chủ dự án đã quyết định thêm tool (2026-10) với công tắc GOOGLE_NEWS_RSS_ENABLED; phải tắt
(hoặc thay bằng nguồn có giấy phép như NewsData.io) trước khi thương mại hoá.
"""

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import structlog

from ..config.settings import settings
from ..utils.ttl_cache import TtlCache

logger = structlog.get_logger(__name__)

_URL = "https://news.google.com/rss/search"


class GoogleNewsError(Exception):
    pass


def phan_tich_rss(xml: str) -> list[dict[str, Any]]:
    goc = ET.fromstring(xml)
    tin = []
    for it in goc.iter("item"):
        nguon = it.find("source")
        ten_bao = (nguon.text or "").strip() if nguon is not None else None
        tieu_de = (it.findtext("title") or "").strip()
        # Google gắn " - Tên báo" vào cuối tiêu đề; bỏ đi vì đã có trường source.
        if ten_bao and tieu_de.endswith(f" - {ten_bao}"):
            tieu_de = tieu_de[: -len(ten_bao) - 3]
        ngay = it.findtext("pubDate")
        try:
            iso = parsedate_to_datetime(ngay).isoformat() if ngay else None
        except (TypeError, ValueError):
            iso = None
        tin.append(
            {
                "title": tieu_de,
                "source": ten_bao,
                "source_url": nguon.get("url") if nguon is not None else None,
                "published": iso,
                "link": (it.findtext("link") or "").strip(),
            }
        )
    return tin


class GoogleNewsClient:
    def __init__(self, timeout: int, cache_ttl: int = 900):
        self.timeout = timeout
        self.cache = TtlCache(cache_ttl, max_items=200)

    async def search(self, query: str) -> list[dict[str, Any]]:
        if (co := self.cache.get(query)) is not None:
            return co
        params = {"q": query, "hl": "vi", "gl": "VN", "ceid": "VN:vi"}
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, headers={"User-Agent": "Mozilla/5.0 (ailusion-journey-mcp)"}, follow_redirects=True
            ) as c:
                r = await c.get(_URL, params=params)
        except httpx.HTTPError as e:
            raise GoogleNewsError(f"Không gọi được Google News: {type(e).__name__}") from e
        if r.status_code != 200:
            raise GoogleNewsError(f"Google News trả HTTP {r.status_code}")
        try:
            tin = phan_tich_rss(r.text)
        except ET.ParseError as e:
            raise GoogleNewsError("Google News trả dữ liệu không đọc được") from e
        self.cache.set(query, tin)
        return tin


google_news_client = GoogleNewsClient(timeout=settings.http_timeout)
