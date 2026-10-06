"""Test Tavily client (fallback key, cache) và 4 tool tìm kiếm. Không gọi Tavily thật."""

import json
from datetime import date, timedelta

import httpx
import pytest
import respx
from fastmcp import Client

from src.services.key_pool import KeyPool
from src.services.tavily_client import TavilyClient, TavilyError, tavily_client
from src.tools._tavily_common import loai_nguon

BASE = tavily_client.base_url


def _client(keys, **kw):
    return TavilyClient(api_keys=keys, base_url=BASE, timeout=5, **kw)


@pytest.fixture(autouse=True)
def _key_gia(monkeypatch):
    monkeypatch.setattr(tavily_client, "pool", KeyPool(["k1", "k2"], "tavily"))
    tavily_client._cache.clear()


def _key(req) -> str:
    return req.headers["Authorization"].removeprefix("Bearer ")


# --- client ------------------------------------------------------------------


@respx.mock
async def test_het_han_muc_432_doi_key():
    def tra_loi(req):
        if _key(req) == "k1":
            return httpx.Response(432, json={"detail": {"error": "exceeds your plan's set usage limit"}})
        return httpx.Response(200, json={"results": []})

    respx.post(f"{BASE}/search").mock(side_effect=tra_loi)
    c = _client(["k1", "k2"], cache_ttl=0)
    assert await c.search("x") == {"results": []}
    trang_thai = c.pool.status()
    assert trang_thai[0]["dang_nghi"] and trang_thai[0]["con_nghi_giay"] > 3600


@respx.mock
async def test_429_nghi_theo_retry_after():
    respx.post(f"{BASE}/search").mock(return_value=httpx.Response(429, headers={"Retry-After": "120"}, json={}))
    c = _client(["k1"], cache_ttl=0, clock=lambda: 1000.0)  # đồng hồ đứng yên để số giây chính xác
    with pytest.raises(TavilyError) as e:
        await c.search("x")
    assert e.value.het_key is True
    assert c.pool.status()[0]["con_nghi_giay"] == 120


@respx.mock
async def test_sai_tham_so_khong_doi_key():
    route = respx.post(f"{BASE}/search").mock(return_value=httpx.Response(400, json={"detail": {"error": "bad"}}))
    with pytest.raises(TavilyError) as e:
        await _client(["k1", "k2"], cache_ttl=0).search("x")
    assert e.value.status_code == 400 and route.call_count == 1


@respx.mock
async def test_cache_va_bo_tham_so_none():
    route = respx.post(f"{BASE}/search").mock(return_value=httpx.Response(200, json={"results": []}))
    c = _client(["k1"], cache_ttl=60)
    await c.search("x", time_range=None)
    await c.search("x", time_range=None)
    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content) == {"query": "x"}
    assert "k1" not in next(iter(c._cache))


def test_loai_nguon():
    assert loai_nguon("https://www.vnexpress.net/abc") == "bao_chi"
    assert loai_nguon("https://baolamdong.vn/x") == "bao_chi"
    assert loai_nguon("https://dalat.lamdong.gov.vn/x") == "chinh_thong"
    assert loai_nguon("https://danago.vn/x") == "khac"


# --- tool --------------------------------------------------------------------


async def _goi(ten, args):
    from src.fastmcp_server import mcp

    async with Client(mcp) as c:
        r = await c.call_tool(ten, args, raise_on_error=False)
    return r.structured_content


@respx.mock
async def test_web_search_mac_dinh_uu_tien_vn():
    route = respx.post(f"{BASE}/search").mock(
        return_value=httpx.Response(
            200, json={"results": [{"title": "A", "url": "https://tuoitre.vn/a", "content": "đoạn", "score": 0.81234}]}
        )
    )
    kq = await _goi("web_search", {"query": "giá vé Bà Nà", "time_range": "month"})

    assert kq["results"][0]["source_type"] == "bao_chi" and kq["results"][0]["score"] == 0.812
    body = json.loads(route.calls.last.request.content)
    assert body["country"] == "vietnam" and body["time_range"] == "month" and body["search_depth"] == "basic"
    assert "include_raw_content" not in body


@respx.mock
async def test_local_news_sap_moi_nhat_truoc():
    route = respx.post(f"{BASE}/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"title": "cũ", "url": "https://vnexpress.net/1", "published_date": "Fri, 11 Sep 2026 15:55:22 GMT"},
                    {"title": "không ngày", "url": "https://x.com/2"},
                    {"title": "mới", "url": "https://tuoitre.vn/3", "published_date": "Wed, 23 Sep 2026 10:01:00 GMT"},
                ]
            },
        )
    )
    kq = await _goi("local_news", {"location": "Đà Lạt", "days": 10})

    assert [t["title"] for t in kq["news"]] == ["mới", "cũ", "không ngày"]
    body = json.loads(route.calls.last.request.content)
    assert body["topic"] == "news" and body["include_domains_mode"] == "prefer"
    assert body["start_date"] == (date.today() - timedelta(days=10)).isoformat()


@respx.mock
async def test_fetch_page_cat_noi_dung_va_bao_loi():
    route = respx.post(f"{BASE}/extract").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [{"url": "https://a.vn/x", "raw_content": "x" * 900}],
                "failed_results": [{"url": "https://b.vn", "error": "timeout"}],
            },
        )
    )
    kq = await _goi("fetch_page", {"urls": ["https://a.vn/x", "https://b.vn"], "focus": "giá vé", "max_chars": 500})

    assert len(kq["pages"][0]["content"]) == 500 and kq["pages"][0]["truncated"] is True
    assert kq["failed"][0]["url"] == "https://b.vn"
    body = json.loads(route.calls.last.request.content)
    assert body["query"] == "giá vé" and body["chunks_per_source"] == 5


async def test_fetch_page_url_sai():
    kq = await _goi("fetch_page", {"urls": ["vnexpress.net/abc"]})
    assert kq["status"] == "error"


@respx.mock
async def test_search_events_tao_cau_theo_thang():
    route = respx.post(f"{BASE}/search").mock(
        return_value=httpx.Response(200, json={"answer": "Có lễ hội", "results": [{"title": "A", "url": "https://a.vn"}]})
    )
    kq = await _goi(
        "search_events", {"location": "Đà Nẵng", "start_date": "2026-11-28", "end_date": "2026-12-02", "kind": "concert"}
    )

    assert kq["answer"] == "Có lễ hội" and kq["answer_note"]
    assert kq["query_used"] == "lịch concert Đà Nẵng tháng 11/2026 tháng 12/2026"
    assert json.loads(route.calls.last.request.content)["include_answer"] == "basic"


@respx.mock
async def test_het_key_tra_ma_loi_rieng():
    respx.post(f"{BASE}/search").mock(return_value=httpx.Response(401, json={"detail": {"error": "Unauthorized"}}))
    kq = await _goi("web_search", {"query": "x"})
    assert kq["error_code"] == -4
