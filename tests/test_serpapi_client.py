"""Test SerpApiClient: đổi key khi bị 401/429, không gọi SerpApi thật."""

import httpx
import pytest
import respx

from src.services.serpapi_client import SerpApiClient, SerpApiError

BASE = "https://serpapi.test"


def _client(keys: list[str]) -> SerpApiClient:
    return SerpApiClient(api_keys=keys, base_url=BASE, timeout=5)


@respx.mock
async def test_search_dung_key_dau_tien():
    route = respx.get(f"{BASE}/search.json").mock(
        return_value=httpx.Response(200, json={"properties": []})
    )
    data = await _client(["k1", "k2"]).search("google_hotels", q="Đà Nẵng", hl=None)

    assert data == {"properties": []}
    params = route.calls.last.request.url.params
    assert params["api_key"] == "k1"
    assert params["engine"] == "google_hotels"
    assert "hl" not in params  # tham số None bị bỏ


@respx.mock
async def test_doi_sang_key_sau_khi_het_quota():
    def tra_loi(request: httpx.Request) -> httpx.Response:
        if request.url.params["api_key"] == "k1":
            return httpx.Response(429, json={"error": "Your account has run out of searches."})
        return httpx.Response(200, json={"ok": True})

    respx.get(f"{BASE}/search.json").mock(side_effect=tra_loi)
    client = _client(["k1", "k2"])

    assert await client.search("google_maps", q="cafe") == {"ok": True}
    # Lần sau thử luôn key còn dùng được
    assert client._idx == 1


@respx.mock
async def test_moi_key_deu_hong_thi_raise():
    respx.get(f"{BASE}/search.json").mock(
        return_value=httpx.Response(401, json={"error": "Invalid API key."})
    )
    with pytest.raises(SerpApiError) as exc:
        await _client(["k1", "k2"]).search("google", q="x")

    assert exc.value.status_code == 401
    assert "k1" not in str(exc.value)


async def test_chua_cau_hinh_key():
    with pytest.raises(SerpApiError):
        await _client([]).search("google", q="x")


@respx.mock
async def test_account_bo_api_key():
    respx.get(f"{BASE}/account.json").mock(
        return_value=httpx.Response(200, json={"api_key": "k1", "plan_name": "Free Plan"})
    )
    assert await _client(["k1"]).account() == {"plan_name": "Free Plan"}
