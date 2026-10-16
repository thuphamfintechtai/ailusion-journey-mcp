"""Đọc dữ liệu du lịch đã cào sẵn trong Postgres (DB MLAI-AILUSION).

Các bảng (do ailusion-journey-data-collector ghi, ở đây chỉ đọc):
  - gmaps_place: địa điểm Google Maps (rating, review, giờ mở cửa, giờ đông khách)
  - event: lễ hội / sự kiện từ lehoivietnam.com.vn
  - weather_forecast, weather_current: thời tiết của các điểm đến phổ biến

Ba bảng ghi tên điểm đến ba kiểu ('ha-noi' / 'hanoi', 'Ha Noi', 'Hà Nội') nên mọi
so khớp đi qua `khoa_diem_den()`. DB không bật unaccent nên lọc chữ không dấu làm
ở Python trên bản rút gọn của gmaps_place giữ trong bộ nhớ (vài nghìn dòng).
"""

import asyncio
import json
import math
import re
import statistics
import time
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import asyncpg
import structlog

from ..config.settings import settings
from ..utils.text import bo_dau, go_co_dau, khop_tu, tach_tu, ten_goc_hanh_chinh, van_ban_de_khop
from ..utils.ttl_cache import TtlCache

logger = structlog.get_logger(__name__)


class TravelDbError(Exception):
    """DB chưa cấu hình, không kết nối được hoặc truy vấn lỗi."""


# Lỗi kết nối (khác lỗi câu SQL): DB đang sập / sai mật khẩu / hết connection. Gặp loại này thì
# cho DB nghỉ một lúc để các tool fallback không phải chờ đủ timeout ở mỗi lần gọi.
_LOI_KET_NOI = (
    OSError,
    asyncio.TimeoutError,
    asyncpg.InterfaceError,
    asyncpg.InvalidAuthorizationSpecificationError,
    asyncpg.CannotConnectNowError,
    asyncpg.TooManyConnectionsError,
)


# Tên gọi khác -> khoá chuẩn (bỏ dấu, viết liền).
_ALIAS = {
    "saigon": "hochiminh",
    "hcm": "hochiminh",
    "tphcm": "hochiminh",
    "hcmc": "hochiminh",
    "hn": "hanoi",
    "dn": "danang",
    "hp": "haiphong",
    "cotdohue": "hue",
}

# Tên hiển thị cho khoá điểm đến (DB lưu dạng slug 'ha-noi', 'hanoi').
_TEN_HIEN_THI = {
    "hanoi": "Hà Nội", "haiphong": "Hải Phòng", "danang": "Đà Nẵng", "hue": "Huế", "dalat": "Đà Lạt",
    "hochiminh": "TP. Hồ Chí Minh", "hoian": "Hội An", "nhatrang": "Nha Trang", "phuquoc": "Phú Quốc",
    "sapa": "Sa Pa", "halong": "Hạ Long", "ninhbinh": "Ninh Bình", "quynhon": "Quy Nhơn", "vungtau": "Vũng Tàu",
}


def ten_hien_thi(khoa: str, mac_dinh: str | None = None) -> str:
    return _TEN_HIEN_THI.get(khoa) or (mac_dinh or khoa).replace("-", " ").title()


def thuoc_diem_den(p: dict[str, Any], khoa: str) -> bool:
    """Địa điểm thuộc điểm đến `khoa`: cùng khoá và trong vùng, hoặc là nơi nhỏ trong tỉnh
    (`_noi`, VD Hội An trong dữ liệu Đà Nẵng — xem `_TINH_CUA`)."""
    return (p["_dest"] == khoa and not p.get("_ngoai_vung")) or khoa in p.get("_noi", ())


# Điểm đến -> tỉnh sau sáp nhập 1/7/2025, để lọc lễ hội theo `event.address_region`. Cũng là các
# "nơi nhỏ" mà DB cào chung với tỉnh (Hội An trong 'danang', Phú Quốc trong 'angiang'…): tìm theo
# nơi nhỏ thì lấy chỗ của tỉnh có tên nơi đó trong địa chỉ (`_noi`, `thuoc_diem_den`).
_TINH_CUA = {
    "dalat": "lamdong",
    "muine": "lamdong",
    "phanthiet": "lamdong",
    "sapa": "laocai",
    "hoian": "danang",
    "halong": "quangninh",
    "phuquoc": "angiang",
    "nhatrang": "khanhhoa",
    "vungtau": "hochiminh",
    "quynhon": "gialai",
    "catba": "haiphong",
    "tamcoc": "ninhbinh",
    "trangan": "ninhbinh",
}

# Bỏ các cột nặng (reviews, popular_times, maps_url...) vì đường tới DB chậm; chi tiết lấy
# riêng bằng place_full(). Cờ xe lăn tính sẵn trong SQL thay vì kéo cả `attributes` về.
# Địa điểm cách tâm điểm đến quá xa là cào nhầm (VD câu 'đặc sản Hải Phòng' ra quán ở TP.HCM).
# 80 km đủ rộng cho Cát Bà (Hải Phòng), Bà Nà / Hội An (Đà Nẵng), Lăng Cô (Huế).
_BAN_KINH_VUNG_KM = 80

# Từ đi kèm tên nơi không mang nghĩa ('Ho Chi Minh City', 'TP Huế').
_TU_PHU_DIA_DANH = {"city", "thanh", "pho", "tp", "tinh", "province", "vietnam", "viet", "nam"}

_COT_RUT_GON = (
    "cid, place_id, destination, seed_query, best_match, title, category, address, lat, lng, "
    "rating, review_count, price_range, left(summary, 200) AS summary, website, phone, "
    "thumbnail, attributes::text ILIKE '%xe lăn%' AS wheelchair, fetched_at"
)


def khoa_diem_den(ten: str | None) -> str:
    """'Thành phố Hồ Chí Minh' / 'ha-noi' / 'Ha Noi' -> 'hochiminh' / 'hanoi' / 'hanoi'."""
    if not ten:
        return ""
    k = ten_goc_hanh_chinh(ten.replace("-", " ")).replace(" ", "")
    return _ALIAS.get(k, k)


def tim_khoa_trong(text: str | None, tap_khoa: set[str] | dict[str, Any]) -> list[str]:
    """Các khoá trong `tap_khoa` xuất hiện trong `text` theo ranh giới từ, khoá dài trước.

    'Khuê Trung' không chứa 'hue' (phải đúng nguyên cụm từ); 'Ho Chi Minh City' chứa 'hochiminh';
    'Hà Nội - Hải Phòng' chứa cả 'hanoi' và 'haiphong'.
    """
    if not text:
        return []
    k = khoa_diem_den(text)
    if k in tap_khoa:
        return [k]
    toks = [bo_dau(t) for t in tach_tu(text)]
    tim: list[tuple[int, int, str]] = []
    for i in range(len(toks)):
        for j in range(i + 1, min(i + 5, len(toks)) + 1):
            cum = "".join(toks[i:j])
            cum = _ALIAS.get(cum, cum)
            if cum in tap_khoa:
                tim.append((i, j, cum))
    # Bỏ cụm nằm lọt trong cụm dài hơn đã chọn, giữ thứ tự xuất hiện.
    tim.sort(key=lambda x: (-(x[1] - x[0]), x[0]))
    chon: list[tuple[int, int, str]] = []
    for i, j, cum in tim:
        if all(j <= a or i >= b for a, b, _ in chon) and cum not in [c for *_, c in chon]:
            chon.append((i, j, cum))
    return [c for *_, c in sorted(chon)]


def bo_dia_danh(text: str, khoa: str) -> str:
    """Bỏ cụm từ là tên nơi `khoa` và từ phụ đi kèm khỏi `text`: 'Văn Miếu, Hà Nội' -> 'Văn Miếu'."""
    goc = tach_tu(text)
    toks = [bo_dau(t) for t in goc]
    for i in range(len(toks)):
        for j in range(i + 1, min(i + 5, len(toks)) + 1):
            cum = "".join(toks[i:j])
            if _ALIAS.get(cum, cum) == khoa:
                con = goc[:i] + goc[j:]
                while con and bo_dau(con[-1]) in _TU_PHU_DIA_DANH:
                    con.pop()
                return " ".join(con)
    return text


def khoa_tinh(ten: str | None) -> str:
    k = khoa_diem_den(ten)
    return _TINH_CUA.get(k, k)


def khoang_cach_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Haversine, đủ chính xác cho bán kính vài chục km."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _gia_tri(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    return v


def _sach(v: Any) -> Any:
    """Chuẩn NFC và bỏ ký tự điều khiển (Postgres từ chối \\x00) của chuỗi đầu vào."""
    if isinstance(v, str):
        return "".join(c for c in unicodedata.normalize("NFC", v) if unicodedata.category(c) != "Cc")
    if isinstance(v, list):
        return [_sach(x) for x in v]
    return v


def _dong(r: asyncpg.Record) -> dict[str, Any]:
    return {k: _gia_tri(v) for k, v in r.items()}


async def _khoi_tao_ket_noi(conn: asyncpg.Connection) -> None:
    for kieu in ("jsonb", "json"):
        await conn.set_type_codec(kieu, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


class TravelDb:
    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None
        self._cache = TtlCache(ttl=settings.pg_cache_ttl, max_items=16)
        self._nghi_den = 0.0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._khoa_pool = asyncio.Lock()
        # Khoá riêng cho việc nạp cache: nhiều request cùng lúc khi cache trống chỉ tải một lần.
        self._khoa_cache = asyncio.Lock()

    def _theo_loop(self) -> None:
        """Lock và pool asyncpg gắn với event loop tạo ra chúng; loop đổi (test, chạy lại server
        trong cùng process) thì tạo mới thay vì dùng lại và lỗi 'bound to a different event loop'."""
        loop = asyncio.get_running_loop()
        if loop is not self._loop:
            self._loop = loop
            self._khoa_pool = asyncio.Lock()
            self._khoa_cache = asyncio.Lock()
            self._pool = None

    @property
    def enabled(self) -> bool:
        return bool(settings.pg_host and settings.pg_data_database)

    async def _lay_pool(self) -> asyncpg.Pool:
        if not self.enabled:
            raise TravelDbError("Chưa cấu hình PG_HOST / PG_DATA_DATABASE")
        self._theo_loop()
        async with self._khoa_pool:
            if self._pool is None:
                self._pool = await asyncpg.create_pool(
                    host=settings.pg_host,
                    port=settings.pg_port,
                    user=settings.pg_user,
                    password=settings.pg_password,
                    database=settings.pg_data_database,
                    min_size=0,
                    max_size=settings.pg_pool_max,
                    timeout=settings.pg_timeout,
                    # Truy vấn danh sách rút gọn kéo ~1 MB, đường tới DB chậm nên cho rộng hơn connect.
                    command_timeout=settings.pg_timeout * 3,
                    init=_khoi_tao_ket_noi,
                )
        return self._pool

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        args = tuple(_sach(a) for a in args)
        if time.monotonic() < self._nghi_den:
            raise TravelDbError("Postgres vừa lỗi kết nối, tạm ngưng thử lại")
        # Lỗi lúc lấy kết nối mới là DB sập / sai cấu hình -> cho DB nghỉ. Một câu truy vấn chậm
        # bị timeout thì chỉ câu đó lỗi, không được làm các tool khác mất DB.
        try:
            pool = await self._lay_pool()
            ket_noi = await pool.acquire(timeout=settings.pg_timeout)
        except TravelDbError:
            raise
        except (*_LOI_KET_NOI, asyncpg.PostgresError) as e:
            self._nghi_den = time.monotonic() + settings.pg_retry_after
            # Pool có thể đang giữ kết nối hỏng / cấu hình cũ: bỏ đi để lần sau tạo mới.
            pool_cu, self._pool = self._pool, None
            if pool_cu is not None:
                pool_cu.terminate()
            logger.error("Không kết nối được Postgres", error=repr(e), nghi_giay=settings.pg_retry_after)
            raise TravelDbError(f"Không kết nối được DB dữ liệu đã lưu: {type(e).__name__}") from e
        try:
            return [_dong(r) for r in await ket_noi.fetch(sql, *args, timeout=settings.pg_timeout * 3)]
        except asyncio.TimeoutError as e:
            logger.error("Truy vấn Postgres quá thời gian", sql=sql[:80])
            raise TravelDbError("Truy vấn dữ liệu đã lưu quá thời gian") from e
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as e:
            logger.error("Truy vấn Postgres thất bại", error=str(e))
            raise TravelDbError(f"Không đọc được dữ liệu đã lưu: {e}") from e
        finally:
            await pool.release(ket_noi)

    async def _cache_hoac_tai(self, khoa: str, tai) -> Any:
        ds = self._cache.get(khoa)
        if ds is not None:
            return ds
        self._theo_loop()
        async with self._khoa_cache:
            ds = self._cache.get(khoa)  # request trước có thể vừa nạp xong
            if ds is None:
                ds = await tai()
                self._cache.set(khoa, ds)
        return ds

    async def close(self) -> None:
        self._nghi_den = 0.0
        if self._pool is not None:
            # Pool của loop khác không đóng được từ loop này, chỉ bỏ tham chiếu.
            if self._loop is asyncio.get_running_loop():
                await self._pool.close()
            self._pool = None

    # --- gmaps_place ---------------------------------------------------------

    async def places(self) -> list[dict[str, Any]]:
        """Bản rút gọn mọi địa điểm, kèm `_dest` (khoá điểm đến) và `_text` (bỏ dấu) để lọc."""

        async def tai() -> list[dict[str, Any]]:
            ds = await self.fetch(f"SELECT {_COT_RUT_GON} FROM gmaps_place")
            for p in ds:
                p["_dest"] = khoa_diem_den(p.get("destination"))
                dia_chi = bo_dau(p.get("address") or "").replace(" ", "")
                p["_noi"] = tuple(
                    noi for noi, tinh in _TINH_CUA.items() if tinh == p["_dest"] and noi in dia_chi
                )
                # seed_query là câu người cào gõ ('các nhà hàng tốt nhất ở Hà Nội'), không mô tả
                # địa điểm nên không đưa vào văn bản để khớp, trừ khi Google trả đúng một kết quả.
                truong = ["title", "category", "summary"] + (["seed_query"] if p.get("best_match") else [])
                p["_text_cd"], p["_text"] = van_ban_de_khop(" ".join(str(p.get(k) or "") for k in truong))
            # Tâm điểm đến = trung vị toạ độ (trung bình bị kéo lệch bởi chính các điểm cào nhầm).
            theo_dest: dict[str, list[dict[str, Any]]] = {}
            for p in ds:
                if p.get("lat") is not None:
                    theo_dest.setdefault(p["_dest"], []).append(p)
            for nhom in theo_dest.values():
                lat = statistics.median(p["lat"] for p in nhom)
                lng = statistics.median(p["lng"] for p in nhom)
                for p in nhom:
                    p["_ngoai_vung"] = khoang_cach_km(lat, lng, p["lat"], p["lng"]) > _BAN_KINH_VUNG_KM
            so_ngoai = sum(1 for p in ds if p.get("_ngoai_vung"))
            if so_ngoai:
                logger.warning("Có địa điểm nằm ngoài vùng điểm đến, bỏ qua khi lọc theo điểm đến", so_dia_diem=so_ngoai)
            return ds

        return await self._cache_hoac_tai("places", tai)

    async def destinations(self) -> dict[str, dict[str, Any]]:
        """{khoá: {name, count, lat, lng}}: tâm = trung vị toạ độ, chỉ đếm địa điểm trong vùng.
        Tính lại khi `places()` nạp bản mới (giữ theo đúng danh sách đang cache)."""
        tat_ca = await self.places()
        da_tinh = getattr(self, "_diem_den_tinh_san", None)
        if da_tinh is not None and da_tinh[0] is tat_ca:
            return da_tinh[1]
        nhom: dict[str, list[dict[str, Any]]] = {}
        for p in tat_ca:
            if not p.get("_ngoai_vung"):
                nhom.setdefault(p["_dest"], []).append(p)
        ket_qua = {}
        for k, ds in nhom.items():
            co_toa_do = [p for p in ds if p.get("lat") is not None]
            ket_qua[k] = {
                "name": ten_hien_thi(k, ds[0]["destination"]),
                "count": len(ds),
                "lat": statistics.median(p["lat"] for p in co_toa_do) if co_toa_do else None,
                "lng": statistics.median(p["lng"] for p in co_toa_do) if co_toa_do else None,
            }
        # Nơi nhỏ cào chung với tỉnh (Hội An, Phú Quốc, Sa Pa…): thành điểm đến riêng, gồm cả chỗ
        # đã có khoá riêng (Đà Lạt: 'dalat' + chỗ trong 'lamdong' có Đà Lạt trong địa chỉ).
        for noi in _TINH_CUA:
            ds = [p for p in tat_ca if thuoc_diem_den(p, noi)]
            co_toa_do = [p for p in ds if p.get("lat") is not None]
            if co_toa_do:
                ket_qua[noi] = {
                    "name": ten_hien_thi(noi, ds[0]["destination"]),
                    "count": len(ds),
                    "lat": statistics.median(p["lat"] for p in co_toa_do),
                    "lng": statistics.median(p["lng"] for p in co_toa_do),
                }
        self._diem_den_tinh_san = (tat_ca, ket_qua)
        return ket_qua

    async def find_destination(self, text: str | None) -> str | None:
        """Khoá điểm đến có dữ liệu xuất hiện trong `text` (VD 'quán chay Hà Nội' -> 'hanoi').
        Có nhiều nơi thì lấy cụm dài nhất; cần biết đủ thì dùng find_destinations."""
        ds = await self.find_destinations(text)
        return max(ds, key=len) if ds else None

    async def find_destinations(self, text: str | None) -> list[str]:
        return tim_khoa_trong(text, await self.destinations())

    async def place_full(self, cid: str) -> dict[str, Any] | None:
        rows = await self.fetch("SELECT * FROM gmaps_place WHERE cid = $1", cid)
        return rows[0] if rows else None

    async def _ung_vien_theo_ten(self, ten: str, dest: str | None) -> list[tuple[float, int, dict[str, Any]]]:
        """(điểm, số review, địa điểm) theo mức khớp tên, tốt nhất trước.

        4 = đúng tên (cả dấu), 3 = đúng tên khi bỏ dấu, 2 = đúng câu tìm của người cào
        (`seed_query`, chỉ khi Google trả đúng một kết quả), 1.5 = tên BẮT ĐẦU bằng cụm từ cần tìm
        ('Văn Miếu' -> 'Văn Miếu – Quốc Tử Giám'), 1 = tên chứa nguyên cụm từ ('Khách sạn gần Văn
        Miếu'), 0.5 = tên chứa đủ các từ nhưng không liền nhau ('Lăng Tự Đức' -> 'Lăng vua Tự Đức').
        Khớp đúng tên luôn hơn khớp qua seed_query: tìm 'Phở Thìn Lò Đúc' phải ra quán tên đó
        trước quán 'Phở Thìn' được cào bằng câu 'Phở Thìn Lò Đúc'.
        """
        tim_cd = " ".join(tach_tu(ten))
        tim_kd = bo_dau(ten)
        if not tim_kd:
            return []
        ung_vien = []
        for p in await self.places():
            if dest and not thuoc_diem_den(p, dest):
                continue
            tieu_de_cd = " ".join(tach_tu(p.get("title")))
            tieu_de_kd = bo_dau(p.get("title") or "")
            if not tieu_de_kd:
                continue
            hat_giong = bo_dau((p.get("seed_query") or "").split(",")[0]) if p.get("best_match") else ""
            if tim_cd == tieu_de_cd:
                diem = 4
            elif tim_kd == tieu_de_kd:
                diem = 3
            elif hat_giong and tim_kd == hat_giong:
                diem = 2
            elif f"{tieu_de_kd} ".startswith(f"{tim_kd} "):
                diem = 1.5
            elif f" {tim_kd} " in f" {tieu_de_kd} ":
                diem = 1
            elif len(tu_tim := tim_kd.split()) >= 2 and set(tu_tim) <= set(tieu_de_kd.split()):
                diem = 0.5
            else:
                continue
            ung_vien.append((diem, p.get("review_count") or 0, p))
        ung_vien.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return ung_vien

    @staticmethod
    def _cung_hang(ung_vien: list[tuple[float, int, dict[str, Any]]]) -> list[dict[str, Any]]:
        """Các địa điểm khác cùng mức khớp với ứng viên tốt nhất (chi nhánh, trùng tên)."""
        if not ung_vien:
            return []
        diem_dau, _, dau = ung_vien[0]
        return [
            {"name": p.get("title"), "address": p.get("address"), "place_id": p.get("place_id")}
            for d, _, p in ung_vien[1:]
            if d == diem_dau and p["cid"] != dau["cid"]
        ][:3]

    async def _diem_den_bat_buoc(self, destination: str | None) -> tuple[bool, str | None]:
        """(hợp lệ, khoá). Có truyền destination mà DB chưa có thì không hợp lệ: đừng khớp nhầm
        địa điểm cùng tên ở thành phố khác."""
        if not (destination and destination.strip()):
            return True, None
        dest = await self.find_destination(destination)
        return dest is not None, dest

    async def find_place(self, ten_hoac_id: str, destination: str | None = None) -> dict[str, Any] | None:
        """Tìm một địa điểm theo place_id ('ChIJ...'), cid, data_id ('0x...') hoặc tên.

        Kết quả theo tên có thêm `_other_matches` khi còn địa điểm khác khớp ngang (chi nhánh).
        """
        return (await self.find_places([ten_hoac_id], destination))[0]

    async def find_places(
        self, ds_ten: list[str], destination: str | None = None, columns: str = "*"
    ) -> list[dict[str, Any] | None]:
        """Như find_place cho nhiều địa điểm, nhưng chỉ tốn một truy vấn chi tiết cho cả danh sách."""
        hop_le, dest = await self._diem_den_bat_buoc(destination)
        cid_theo_vi_tri: list[str | None] = []
        id_can_tra: list[str] = []
        cung_hang: dict[int, list[dict[str, Any]]] = {}
        for i, ten in enumerate(ds_ten):
            s = (ten or "").strip()
            if s.startswith("ChIJ") or s.startswith("0x") or s.isdigit():
                cid_theo_vi_tri.append(None)
                id_can_tra.append(s)
                continue
            uv = await self._ung_vien_theo_ten(s, dest) if hop_le else []
            if not uv and hop_le:
                # 'Văn Miếu, Hà Nội' / 'Cầu Rồng (Đà Nẵng)': tách tên nơi ra làm điểm đến rồi tìm lại.
                for khoa in await self.find_destinations(s):
                    if dest and khoa != dest:
                        continue
                    ten_rut_gon = bo_dia_danh(s, khoa)
                    if ten_rut_gon and ten_rut_gon != s:
                        uv = await self._ung_vien_theo_ten(ten_rut_gon, khoa)
                    if uv:
                        break
            cid_theo_vi_tri.append(uv[0][2]["cid"] if uv else None)
            cung_hang[i] = self._cung_hang(uv)

        cids = [c for c in cid_theo_vi_tri if c]
        if not cids and not id_can_tra:
            return [None] * len(ds_ten)
        rows = await self.fetch(
            f"SELECT {columns}, cid AS _cid, place_id AS _pid, data_id AS _did FROM gmaps_place "
            "WHERE cid = ANY($1::text[]) OR cid = ANY($2::text[]) OR place_id = ANY($2::text[]) "
            "OR data_id = ANY($2::text[])",
            cids,
            id_can_tra,
        )
        theo_khoa: dict[str, dict[str, Any]] = {}
        for r in rows:
            for k in ("_cid", "_pid", "_did"):
                if r.get(k):
                    theo_khoa.setdefault(r[k], r)

        ket_qua: list[dict[str, Any] | None] = []
        for i, ten in enumerate(ds_ten):
            khoa = cid_theo_vi_tri[i] or (ten or "").strip()
            r = theo_khoa.get(khoa)
            if r is not None and cung_hang.get(i):
                r = {**r, "_other_matches": cung_hang[i]}
            ket_qua.append(r)
        return ket_qua

    async def save_thumbnail(self, id_: str, url: str) -> int:
        """Ghi ảnh đại diện cho địa điểm (theo place_id hoặc cid) chưa có ảnh. Trả số dòng đã ghi."""
        rows = await self.fetch(
            "UPDATE gmaps_place SET thumbnail = $1, updated_at = now() "
            "WHERE (place_id = $2 OR cid = $2) AND thumbnail IS NULL RETURNING cid",
            url,
            id_,
        )
        # Sửa luôn bản rút gọn đang cache để find_places không phải chờ hết TTL mới thấy ảnh.
        for p in self._cache.get("places") or []:
            if id_ in (p.get("place_id"), p.get("cid")) and not p.get("thumbnail"):
                p["thumbnail"] = url
        return len(rows)

    async def save_images(self, id_: str, urls: list[str]) -> int:
        """Ghi tối đa 6 URL ảnh cho địa điểm (place_id hoặc cid) chưa có hoặc có dưới 3 ảnh."""
        ds = list(dict.fromkeys(u for u in urls if isinstance(u, str) and u))[:6]
        if not ds:
            return 0
        rows = await self.fetch(
            "UPDATE gmaps_place SET images = $1::jsonb, updated_at = now() "
            "WHERE (place_id = $2 OR cid = $2) AND (images IS NULL OR jsonb_array_length(images) < 3) "
            "RETURNING cid",
            ds,
            id_,
        )
        for p in self._cache.get("places") or []:
            if "images" in p and id_ in (p.get("place_id"), p.get("cid")) and len(p.get("images") or []) < 3:
                p["images"] = ds
        return len(rows)

    async def details_by_cid(self, cids: list[str], columns: str) -> dict[str, dict[str, Any]]:
        """{cid: các cột yêu cầu} cho nhiều địa điểm trong một truy vấn."""
        if not cids:
            return {}
        rows = await self.fetch(f"SELECT cid AS _cid, {columns} FROM gmaps_place WHERE cid = ANY($1::text[])", list(cids))
        return {r["_cid"]: r for r in rows}

    async def cids_matching(
        self, cids: list[str], dieu_kien: str, tham_so: list[Any], diem: str = "0", tham_so_diem: list[Any] | None = None
    ) -> dict[str, int]:
        """{cid: điểm} của các `cids` thoả điều kiện SQL trên cột attributes (xem
        tools/_nhu_cau.dieu_kien_sql). Tham số điều kiện đánh số từ $2, của `diem` nối tiếp sau."""
        if not cids:
            return {}
        rows = await self.fetch(
            f"SELECT cid, {diem} AS _diem FROM gmaps_place WHERE cid = ANY($1::text[]) AND {dieu_kien}",
            list(cids),
            *tham_so,
            *(tham_so_diem or []),
        )
        return {r["cid"]: int(r["_diem"] or 0) for r in rows}

    # --- event ---------------------------------------------------------------

    async def _event_regions(self) -> list[str]:

        async def tai() -> list[str]:
            sql = "SELECT DISTINCT address_region FROM event WHERE address_region IS NOT NULL"
            return [r["address_region"] for r in await self.fetch(sql)]

        return await self._cache_hoac_tai("event_regions", tai)

    async def festivals(
        self, location: str, tu: date, den: date, keyword: str | None = None, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Sự kiện diễn ra giao với [tu, den] ở tỉnh của `location`, hoặc nhắc tên `location`."""
        vung_theo_khoa: dict[str, list[str]] = {}
        for r in await self._event_regions():
            vung_theo_khoa.setdefault(khoa_diem_den(r), []).append(r)
        # 'Hà Nội, Huế' hay 'Hội An' (thuộc Đà Nẵng): tìm mọi tên nơi trong chuỗi rồi đổi ra tỉnh.
        khoa_can = {khoa_tinh(location), khoa_diem_den(location)}
        khoa_can |= {_TINH_CUA.get(k, k) for k in tim_khoa_trong(location, set(vung_theo_khoa) | set(_TINH_CUA))}
        vung = [r for k in khoa_can for r in vung_theo_khoa.get(k, [])]
        # Escape ký tự đại diện của ILIKE: location '%' hay '_' không được khớp mọi sự kiện.
        tu_khoa = "%" + re.sub(r"([\\%_])", r"\\\1", location.strip()) + "%"
        rows = await self.fetch(
            """
            SELECT event_id, url, name, start_date, end_date, date_display, location_name,
                   street_address, address_region, organizer, price, price_currency,
                   left(description, 400) AS description, highlights, tags, fetched_at
            FROM event
            WHERE start_date <= $2 AND coalesce(end_date, start_date) >= $1
              AND (address_region = ANY($3::text[]) OR location_name ILIKE $4 OR admin_areas::text ILIKE $4
               OR name ILIKE $4)
            -- Sự kiện nhắc đúng tên nơi (VD 'Hội An') lên trước sự kiện chỉ cùng tỉnh.
            ORDER BY (location_name ILIKE $4 OR admin_areas::text ILIKE $4 OR name ILIKE $4) DESC, start_date
            LIMIT 200
            """,
            tu,
            den,
            vung,
            tu_khoa,
        )
        toks = tach_tu(keyword)
        giu_dau = go_co_dau(toks)
        if toks:

            def khop(r: dict[str, Any]) -> bool:
                the = " ".join(str(t.get("name") if isinstance(t, dict) else t) for t in r.get("tags") or [])
                co, khong = van_ban_de_khop(
                    " ".join([r.get("name") or "", r.get("description") or "", the, " ".join(map(str, r.get("highlights") or []))])
                )
                return all(khop_tu(t, co, khong, giu_dau) for t in toks)

            rows = [r for r in rows if khop(r)]
        # Nguồn đăng trùng một sự kiện nhiều lần (cùng tên, ngày, tỉnh): chỉ giữ bản đầu.
        da_co: set[tuple[Any, ...]] = set()
        gon: list[dict[str, Any]] = []
        for r in rows:
            khoa = (bo_dau(r.get("name") or ""), r.get("start_date"), r.get("address_region"))
            if khoa not in da_co:
                da_co.add(khoa)
                gon.append(r)
        return gon[:limit]

    # --- weather -------------------------------------------------------------

    async def weather(
        self, location: str | None, lat: float | None, lng: float | None, tu: date, den: date
    ) -> dict[str, Any] | None:
        """Dự báo đã lưu của điểm đến khớp tên, hoặc gần toạ độ nhất (trong 40 km)."""
        hien_tai = await self.fetch(
            "SELECT DISTINCT ON (destination) * FROM weather_current ORDER BY destination, observed_at DESC"
        )
        chon = None
        if location:
            theo_khoa = {khoa_diem_den(h["destination"]): h for h in hien_tai}
            khoa = tim_khoa_trong(location.split(",")[0], theo_khoa) or tim_khoa_trong(location, theo_khoa)
            chon = theo_khoa[khoa[0]] if khoa else None
        if chon is None and lat is not None and lng is not None:
            gan = sorted(
                (khoang_cach_km(lat, lng, h["lat"], h["lon"]), h) for h in hien_tai if h.get("lat") is not None
            )
            if gan and gan[0][0] <= 40:
                chon = gan[0][1]
        if chon is None:
            return None
        ngay = await self.fetch(
            "SELECT * FROM weather_forecast WHERE destination = $1 AND forecast_date BETWEEN $2 AND $3 ORDER BY forecast_date",
            chon["destination"],
            tu,
            den,
        )
        return {"current": chon, "days": ngay}

    async def weather_destinations(self) -> list[str]:
        return [r["destination"] for r in await self.fetch("SELECT DISTINCT destination FROM weather_forecast ORDER BY 1")]


travel_db = TravelDb()
