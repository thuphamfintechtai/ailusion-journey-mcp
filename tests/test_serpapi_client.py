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


# --- Fallback và cooldown ---------------------------------------------------


class _Dong:
    """Đồng hồ giả để test cooldown không phải chờ thật."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _client_dong(keys: list[str], dong: _Dong) -> SerpApiClient:
    return SerpApiClient(
        api_keys=keys,
        base_url=BASE,
        timeout=5,
        rate_limit_cooldown=60,
        quota_cooldown=3600,
        invalid_key_cooldown=86400,
        clock=dong,
    )


@respx.mock
async def test_key_het_quota_bi_bo_qua_o_lan_goi_sau():
    da_goi: list[str] = []

    def tra_loi(request: httpx.Request) -> httpx.Response:
        key = request.url.params["api_key"]
        da_goi.append(key)
        if key == "k1":
            return httpx.Response(429, json={"error": "Your account has run out of searches."})
        return httpx.Response(200, json={"ok": True})

    respx.get(f"{BASE}/search.json").mock(side_effect=tra_loi)
    dong = _Dong()
    client = _client_dong(["k1", "k2"], dong)

    await client.search("google", q="a")
    client._idx = 0  # ép thử lại từ k1 để chắc rằng cooldown mới là thứ chặn
    await client.search("google", q="b")
    assert da_goi == ["k1", "k2", "k2"]

    # Hết cooldown thì k1 được thử lại
    dong.t += 3601
    client._idx = 0
    await client.search("google", q="c")
    assert da_goi[-2:] == ["k1", "k2"]


@respx.mock
async def test_gioi_han_gio_nghi_ngan_hon_het_quota():
    respx.get(f"{BASE}/search.json").mock(
        return_value=httpx.Response(429, json={"error": "Hourly throughput limit exceeded."})
    )
    dong = _Dong()
    client = _client_dong(["k1"], dong)

    with pytest.raises(SerpApiError) as exc:
        await client.search("google", q="x")
    assert exc.value.het_key is True
    assert client.key_status()[0]["con_nghi_giay"] == 60


@respx.mock
async def test_moi_key_dang_nghi_thi_khong_goi_serpapi():
    route = respx.get(f"{BASE}/search.json").mock(
        return_value=httpx.Response(401, json={"error": "Invalid API key."})
    )
    client = _client_dong(["k1", "k2"], _Dong())

    with pytest.raises(SerpApiError):
        await client.search("google", q="x")
    assert route.call_count == 2

    with pytest.raises(SerpApiError) as exc:
        await client.search("google", q="y")
    assert route.call_count == 2  # không tốn thêm request
    assert exc.value.het_key is True
    assert "tạm ngưng" in str(exc.value)


@respx.mock
async def test_loi_server_va_timeout_thu_key_ke_tiep_khong_cho_nghi():
    def tra_loi(request: httpx.Request) -> httpx.Response:
        key = request.url.params["api_key"]
        if key == "k1":
            raise httpx.ReadTimeout("cham", request=request)
        if key == "k2":
            return httpx.Response(503, json={"error": "Service unavailable"})
        return httpx.Response(200, json={"ok": True})

    respx.get(f"{BASE}/search.json").mock(side_effect=tra_loi)
    client = _client_dong(["k1", "k2", "k3"], _Dong())

    assert await client.search("google", q="x") == {"ok": True}
    assert client._idx == 2
    assert not any(k["dang_nghi"] for k in client.key_status())


@respx.mock
async def test_loi_mang_o_moi_key_khong_tinh_la_het_key():
    respx.get(f"{BASE}/search.json").mock(side_effect=httpx.ConnectError("mat mang"))
    with pytest.raises(SerpApiError) as exc:
        await _client_dong(["k1", "k2"], _Dong()).search("google", q="x")
    assert exc.value.het_key is False


@respx.mock
async def test_loi_tham_so_khong_doi_key():
    route = respx.get(f"{BASE}/search.json").mock(
        return_value=httpx.Response(400, json={"error": "Missing query `q` parameter."})
    )
    with pytest.raises(SerpApiError) as exc:
        await _client_dong(["k1", "k2"], _Dong()).search("google")
    assert exc.value.status_code == 400
    assert route.call_count == 1


@respx.mock
async def test_200_kem_loi_key_cung_doi_key():
    def tra_loi(request: httpx.Request) -> httpx.Response:
        if request.url.params["api_key"] == "k1":
            return httpx.Response(200, json={"error": "Invalid API key. Your API key should be here"})
        return httpx.Response(200, json={"ok": True})

    respx.get(f"{BASE}/search.json").mock(side_effect=tra_loi)
    client = _client_dong(["k1", "k2"], _Dong())
    assert await client.search("google", q="x") == {"ok": True}
    assert client.key_status()[0]["dang_nghi"] is True
