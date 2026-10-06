"""Test WeatherAPI client, chọn địa danh vùng (Vietmap) và 4 tool thời tiết. Không gọi API thật."""

from datetime import timedelta

import httpx
import pytest
import respx
from fastmcp import Client

from src.services.key_pool import KeyPool
from src.services.vietmap_client import VietmapError, vietmap_client
from src.services.weather_client import WeatherClient, WeatherError, WeatherPlanError, weather_client
from src.tools._weather_common import canh_bao_ngay, gio_24, hom_nay_vn

WB = weather_client.base_url
VB = vietmap_client.base_url
HOM_NAY = hom_nay_vn()


@pytest.fixture(autouse=True)
def _cau_hinh(monkeypatch):
    monkeypatch.setattr(weather_client, "pool", KeyPool(["w1", "w2"], "weatherapi"))
    weather_client._cache.clear()
    # Mặc định tắt Vietmap để tool gửi tên thẳng cho WeatherAPI; test riêng bật lại.
    monkeypatch.setattr(vietmap_client, "api_keys", [])
    vietmap_client._cache.clear()


def _loi(code: int, status: int = 400) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": f"loi {code}"}})


# --- client ------------------------------------------------------------------


@respx.mock
async def test_het_quota_2007_doi_key():
    def tra_loi(req):
        return _loi(2007, 403) if req.url.params["key"] == "w1" else httpx.Response(200, json={"ok": 1})

    respx.get(f"{WB}/current.json").mock(side_effect=tra_loi)
    c = WeatherClient(["w1", "w2"], WB, 5, cache_ttl=0)
    assert await c.get("/current.json", {"q": "Hanoi"}) == {"ok": 1}
    assert c.pool.status()[0]["dang_nghi"]


@respx.mock
async def test_goi_khong_ho_tro_2009_thu_key_khac_khong_cho_nghi():
    respx.get(f"{WB}/future.json").mock(return_value=_loi(2009, 403))
    c = WeatherClient(["w1", "w2"], WB, 5, cache_ttl=0)
    with pytest.raises(WeatherPlanError):
        await c.future("Hanoi", "2026-12-01")
    assert not any(k["dang_nghi"] for k in c.pool.status())


@respx.mock
async def test_key_sai_het_key():
    respx.get(f"{WB}/current.json").mock(return_value=_loi(2006, 401))
    with pytest.raises(WeatherError) as e:
        await WeatherClient(["w1"], WB, 5, cache_ttl=0).get("/current.json", {"q": "x"})
    assert e.value.het_key is True


def test_canh_bao_theo_nguong_kttv():
    assert "nắng nóng gay gắt (>=37°C)" in canh_bao_ngay({"maxtemp_c": 37.5})
    assert "rét hại (nhiệt độ trung bình <=13°C)" in canh_bao_ngay({"avgtemp_c": 12})
    assert any("mưa rất to" in c for c in canh_bao_ngay({"totalprecip_mm": 60}))
    assert canh_bao_ngay({"maxtemp_c": 30, "avgtemp_c": 25, "uv": 5}) == []
    assert gio_24("05:59 PM") == "17:59"


# --- chọn địa danh vùng ------------------------------------------------------


def _ac(*kq):
    return httpx.Response(200, json=list(kq))


def _r(name, display, tinh, ref):
    return {"name": name, "display": display, "ref_id": ref, "boundaries": [{"type": 0, "full_name": tinh}]}


@respx.mock
async def test_resolve_area_bo_phieu_theo_tinh(monkeypatch):
    monkeypatch.setattr(vietmap_client, "api_keys", ["v1"])

    def ac(req):
        if req.url.params.get("layers"):
            return _ac(
                _r("Xã Hội An", "Xã Hội An, Tỉnh An Giang", "Tỉnh An Giang", "auto:ag"),
                _r("Phường Hội An", "Phường Hội An, Thành phố Đà Nẵng", "Thành phố Đà Nẵng", "auto:dn"),
            )
        return _ac(
            _r("Xã Hội An", "Xã Hội An, Tỉnh An Giang", "Tỉnh An Giang", "auto:ag"),
            _r("Ikàlem-Hội An", "x", "Thành phố Đà Nẵng", "auto:1"),
            _r("Hội An Thalasso", "x", "Thành phố Đà Nẵng", "auto:2"),
        )

    respx.get(f"{VB}/autocomplete/v4").mock(side_effect=ac)
    place = respx.get(f"{VB}/place/v4").mock(return_value=httpx.Response(200, json={"lat": 15.88, "lng": 108.33}))
    d = await vietmap_client.resolve_area("Hội An")
    assert d.ref_id == "auto:dn" and place.calls.last.request.url.params["refid"] == "auto:dn"


@respx.mock
async def test_resolve_area_hoa_thi_bao_trung_ten(monkeypatch):
    monkeypatch.setattr(vietmap_client, "api_keys", ["v1"])
    respx.get(f"{VB}/autocomplete/v4").mock(
        return_value=_ac(
            _r("Xã Cô Tô", "Xã Cô Tô, Tỉnh An Giang", "Tỉnh An Giang", "auto:ag"),
            _r("Đặc khu Cô Tô", "Đặc khu Cô Tô, Tỉnh Quảng Ninh", "Tỉnh Quảng Ninh", "auto:qn"),
        )
    )
    with pytest.raises(VietmapError) as e:
        await vietmap_client.resolve_area("Cô Tô")
    assert e.value.code == "AMBIGUOUS"


@respx.mock
async def test_resolve_area_co_dau_khong_khop_ten_gan_giong(monkeypatch):
    """'Hạ Long' không được khớp xã 'Hà Long'; autocomplete bỏ sót thì dùng search/v4."""
    monkeypatch.setattr(vietmap_client, "api_keys", ["v1"])
    respx.get(f"{VB}/autocomplete/v4").mock(
        return_value=_ac(_r("Xã Hà Long", "Xã Hà Long, Tỉnh Thanh Hóa", "Tỉnh Thanh Hóa", "auto:th"))
    )
    respx.get(f"{VB}/search/v4").mock(
        return_value=_ac(_r("Phường Hạ Long", "Phường Hạ Long, Tỉnh Quảng Ninh", "Tỉnh Quảng Ninh", "geocode:qn"))
    )
    respx.get(f"{VB}/place/v4").mock(return_value=httpx.Response(200, json={"lat": 20.9, "lng": 107.1}))
    d = await vietmap_client.resolve_area("Hạ Long")
    assert d.ref_id == "geocode:qn"


@respx.mock
async def test_resolve_area_goi_y_tinh_sau_dau_phay(monkeypatch):
    monkeypatch.setattr(vietmap_client, "api_keys", ["v1"])
    respx.get(f"{VB}/autocomplete/v4").mock(
        return_value=_ac(
            _r("Xã Cô Tô", "Xã Cô Tô, Tỉnh An Giang", "Tỉnh An Giang", "auto:ag"),
            _r("Đặc khu Cô Tô", "Đặc khu Cô Tô, Tỉnh Quảng Ninh", "Tỉnh Quảng Ninh", "auto:qn"),
        )
    )
    respx.get(f"{VB}/place/v4").mock(return_value=httpx.Response(200, json={"lat": 20.97, "lng": 107.76}))
    assert (await vietmap_client.resolve_area("Cô Tô, Quảng Ninh")).ref_id == "auto:qn"


# --- tool --------------------------------------------------------------------


async def _goi(ten, args):
    from src.fastmcp_server import mcp

    async with Client(mcp) as c:
        r = await c.call_tool(ten, args, raise_on_error=False)
    return r.structured_content


def _ngay(d, **day):
    return {
        "date": d.isoformat(),
        "day": {"maxtemp_c": 30, "mintemp_c": 22, "avgtemp_c": 26, "condition": {"text": "Nắng"}, **day},
        "astro": {"sunrise": "05:40 AM", "sunset": "05:35 PM"},
        "hour": [{"time": f"{d.isoformat()} {h:02d}:00", "temp_c": 25, "chance_of_rain": 10} for h in range(24)],
    }


@respx.mock
async def test_weather_forecast_ten_khong_dau_va_canh_bao():
    route = respx.get(f"{WB}/forecast.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "location": {"name": "Da Lat", "country": "Vietnam", "lat": 11.9, "lon": 108.4},
                "forecast": {"forecastday": [_ngay(HOM_NAY), _ngay(HOM_NAY + timedelta(1), maxtemp_c=36)]},
                "alerts": {"alert": []},
            },
        )
    )
    kq = await _goi(
        "weather_forecast",
        {"location": "Đà Lạt", "start_date": (HOM_NAY + timedelta(1)).isoformat(), "hourly": True},
    )

    assert route.calls.last.request.url.params["q"] == "da lat"
    ngay = kq["days"]
    assert len(ngay) == 1 and ngay[0]["source"] == "forecast" and ngay[0]["sunrise"] == "05:40"
    assert "nắng nóng (>=35°C)" in ngay[0]["warnings"]
    assert [h["time"] for h in ngay[0]["hours"]] == ["06:00", "09:00", "12:00", "15:00", "18:00", "21:00"]


@respx.mock
async def test_weather_forecast_goi_free_chi_tra_3_ngay():
    respx.get(f"{WB}/forecast.json").mock(
        return_value=httpx.Response(
            200,
            json={"location": {"name": "X"}, "forecast": {"forecastday": [_ngay(HOM_NAY + timedelta(i)) for i in range(3)]}},
        )
    )
    kq = await _goi(
        "weather_forecast",
        {"location": "Hue", "start_date": HOM_NAY.isoformat(), "end_date": (HOM_NAY + timedelta(5)).isoformat()},
    )
    assert len(kq["days"]) == 3
    assert any("chỉ trả 3 ngày" in n for n in kq["notes"])


@respx.mock
async def test_weather_forecast_ngay_xa_bi_chan_van_tra_ghi_chu():
    respx.get(f"{WB}/future.json").mock(return_value=_loi(2009, 403))
    ngay = (HOM_NAY + timedelta(40)).isoformat()
    kq = await _goi("weather_forecast", {"location": "Hue", "start_date": ngay})
    assert kq["status"] == "success" and kq["days"] == []
    assert any(ngay in n for n in kq["notes"])
    assert not any("source=future" in n for n in kq["notes"])


async def test_weather_forecast_qua_300_ngay():
    kq = await _goi("weather_forecast", {"location": "Hue", "start_date": (HOM_NAY + timedelta(400)).isoformat()})
    assert kq["status"] == "error"


@respx.mock
async def test_air_quality_gio_o_nhiem_nhat():
    respx.get(f"{WB}/forecast.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "location": {"name": "Hanoi"},
                "current": {"air_quality": {"us-epa-index": 3, "pm2_5": 42.44}},
                "forecast": {
                    "forecastday": [
                        {
                            "date": HOM_NAY.isoformat(),
                            "day": {"air_quality": {"us-epa-index": 4, "pm2_5": 65.6}},
                            "hour": [
                                {"time": "x 07:00", "air_quality": {"pm2_5": 30}},
                                {"time": "x 23:00", "air_quality": {"pm2_5": 165.3}},
                            ],
                        }
                    ]
                },
            },
        )
    )
    kq = await _goi("air_quality", {"location": "Hà Nội"})
    assert kq["current"]["level"] == "Kém cho nhóm nhạy cảm" and kq["current"]["pm2_5"] == 42.4
    assert kq["days"][0]["level"] == "Kém" and kq["days"][0]["worst_hour"] == "23:00"


@respx.mock
async def test_marine_canh_bao_song_cao_va_thuy_trieu():
    gio = [{"time": f"x {h:02d}:00", "sig_ht_mt": 2.4 if h == 12 else 0.8, "gust_kph": 30, "water_temp_c": 28} for h in range(24)]
    respx.get(f"{WB}/marine.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "location": {"name": "Con Dao"},
                "forecast": {
                    "forecastday": [
                        {
                            "date": HOM_NAY.isoformat(),
                            "day": {"tides": [{"tide": [{"tide_time": "x 10:45", "tide_type": "LOW", "tide_height_mt": 0.25}]}]},
                            "hour": gio,
                        }
                    ]
                },
            },
        )
    )
    kq = await _goi("marine_forecast", {"lat": 8.68, "lng": 106.6, "days": 1})
    d = kq["days"][0]
    assert d["max_wave_m"] == 2.4 and d["tides"][0]["type"] == "nước ròng"
    assert any("sóng cao" in w for w in d["warnings"])


@respx.mock
async def test_sun_and_sky_goi_y_san_may():
    ngay = HOM_NAY + timedelta(1)
    respx.get(f"{WB}/astronomy.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "location": {"name": "Ta Xua"},
                "astronomy": {"astro": {"sunrise": "05:55 AM", "sunset": "05:40 PM", "moon_phase": "Full Moon", "moon_illumination": "98"}},
            },
        )
    )
    gio = [
        {"time": f"x {h:02d}:00", "humidity": 95 if h < 8 else 70, "wind_kph": 5, "cloud": 20, "chance_of_rain": 0}
        for h in range(24)
    ]
    respx.get(f"{WB}/forecast.json").mock(
        return_value=httpx.Response(200, json={"forecast": {"forecastday": [{"date": ngay.isoformat(), "hour": gio}]}})
    )
    kq = await _goi("sun_and_sky", {"location": "Tà Xùa", "date": ngay.isoformat()})

    assert kq["sunrise"] == "05:55" and kq["golden_hour_evening"] == "16:40 - 17:40"
    assert kq["moon_phase"] == "Trăng tròn" and kq["moon_illumination_pct"] == 98
    assert kq["suggestions"]["cloud_hunting"].startswith("khá thuận")
    assert kq["suggestions"]["stargazing"].startswith("trung bình")  # trời quang nhưng trăng tròn


@respx.mock
async def test_ten_trung_tinh_tra_loi_thay_vi_doan(monkeypatch):
    monkeypatch.setattr(vietmap_client, "api_keys", ["v1"])
    respx.get(f"{VB}/autocomplete/v4").mock(
        return_value=_ac(
            _r("Xã Cô Tô", "Xã Cô Tô, Tỉnh An Giang", "Tỉnh An Giang", "auto:ag"),
            _r("Đặc khu Cô Tô", "Đặc khu Cô Tô, Tỉnh Quảng Ninh", "Tỉnh Quảng Ninh", "auto:qn"),
        )
    )
    weather = respx.get(f"{WB}/forecast.json")
    kq = await _goi("weather_forecast", {"location": "Cô Tô", "start_date": HOM_NAY.isoformat()})
    assert kq["status"] == "error" and "trùng tên" in kq["error"]
    assert weather.call_count == 0
