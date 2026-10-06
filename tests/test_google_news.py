"""Test tool google_news (Google News RSS). Không gọi Google thật."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import respx
from fastmcp import Client

from src.services.google_news_client import google_news_client, phan_tich_rss


def _rss(*items: tuple[str, str, str, datetime]) -> str:
    the = "".join(
        f"<item><title>{t} - {b}</title><link>https://news.google.com/rss/articles/x{i}</link>"
        f"<pubDate>{format_datetime(d)}</pubDate><source url=\"{u}\">{b}</source></item>"
        for i, (t, b, u, d) in enumerate(items)
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{the}</channel></rss>'


def test_phan_tich_rss_bo_ten_bao_khoi_tieu_de():
    d = datetime(2026, 9, 23, 10, 1, tzinfo=timezone.utc)
    tin = phan_tich_rss(_rss(("Đèo Gia Bắc sạt lở", "Báo Tuổi Trẻ", "https://tuoitre.vn", d)))
    assert tin[0]["title"] == "Đèo Gia Bắc sạt lở" and tin[0]["source"] == "Báo Tuổi Trẻ"
    assert tin[0]["published"].startswith("2026-09-23T10:01")


@respx.mock
async def test_google_news_loc_tin_cu_va_sap_xep():
    google_news_client.cache.clear()
    bay_gio = datetime.now(timezone.utc)
    route = respx.get("https://news.google.com/rss/search").mock(
        return_value=httpx.Response(
            200,
            text=_rss(
                ("Tin cũ", "VnExpress", "https://vnexpress.net", bay_gio - timedelta(days=20)),
                ("Tin hôm qua", "Laodong.vn", "https://laodong.vn", bay_gio - timedelta(days=1)),
                ("Tin hôm nay", "Blog X", "https://blogx.com", bay_gio - timedelta(hours=2)),
            ),
        )
    )
    from src.fastmcp_server import mcp

    async with Client(mcp) as c:
        kq = (await c.call_tool("google_news", {"query": "sạt lở đèo", "location": "Đà Lạt", "days": 7})).structured_content

    p = route.calls.last.request.url.params
    assert p["q"] == "sạt lở đèo Đà Lạt when:7d" and p["gl"] == "VN" and p["hl"] == "vi"
    assert [t["title"] for t in kq["news"]] == ["Tin hôm nay", "Tin hôm qua"]
    assert kq["news"][1]["source_type"] == "bao_chi" and kq["news"][0]["source_type"] == "khac"


async def test_tat_bang_cau_hinh(monkeypatch):
    from fastmcp import FastMCP

    from src.config.settings import settings
    from src.tools.google_news import register_google_news_tool

    monkeypatch.setattr(settings, "google_news_rss_enabled", False)
    m = FastMCP("x")
    assert register_google_news_tool(m) is None
    assert "google_news" not in {t.name for t in await m.list_tools()}
