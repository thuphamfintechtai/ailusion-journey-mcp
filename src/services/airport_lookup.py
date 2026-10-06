"""Tra mã IATA sân bay Việt Nam từ bảng dựng sẵn — không gọi mạng, không tốn quota.

Tra được theo mã, tên sân bay, thành phố, tỉnh (cả tên trước và sau sáp nhập
1/7/2025) và điểm du lịch không có sân bay riêng (Hội An -> DAD, Hạ Long -> VDO).
Gõ có dấu hay không dấu đều được.

Sân bay nước ngoài không nằm ở đây; tool `find_airports` gọi SerpApi cho phần đó.
Bảng cần cập nhật khi có sân bay mới đưa vào khai thác (VD Long Thành, Phan Thiết).
"""

import re
import unicodedata
from dataclasses import dataclass, field

_MA_IATA = re.compile(r"^[A-Za-z]{3}$")


@dataclass(frozen=True)
class SanBay:
    code: str
    name: str
    city: str
    province: str  # tên tỉnh/thành sau sáp nhập 1/7/2025
    international: bool
    # Tên gọi khác của chính thành phố / tỉnh cũ (khớp mạnh)
    aliases: tuple[str, ...] = field(default=())
    # Điểm đến không có sân bay, thường bay tới sân bay này (khớp yếu hơn)
    serves: tuple[str, ...] = field(default=())


SAN_BAY_VN: tuple[SanBay, ...] = (
    SanBay("HAN", "Nội Bài", "Hà Nội", "Hà Nội", True,
           ("Ha Noi", "Hanoi", "HN"),
           ("Ninh Bình", "Tràng An", "Sa Pa", "Lào Cai", "Hà Giang", "Mộc Châu", "Mai Châu", "Tam Đảo", "Bắc Ninh")),
    SanBay("SGN", "Tân Sơn Nhất", "TP. Hồ Chí Minh", "TP. Hồ Chí Minh", True,
           ("Hồ Chí Minh", "Sài Gòn", "Saigon", "HCM", "TPHCM", "TP HCM", "Ho Chi Minh City"),
           ("Vũng Tàu", "Bà Rịa", "Phan Thiết", "Mũi Né", "Bình Thuận", "Tây Ninh", "Biên Hòa", "Đồng Nai", "Bình Dương", "Long Hải", "Hồ Tràm")),
    SanBay("DAD", "Đà Nẵng", "Đà Nẵng", "Đà Nẵng", True,
           ("Da Nang", "Danang"),
           ("Hội An", "Bà Nà", "Cù Lao Chàm", "Mỹ Sơn", "Quảng Nam")),
    SanBay("CXR", "Cam Ranh", "Nha Trang", "Khánh Hòa", True,
           ("Khánh Hòa", "Cam Ranh"),
           ("Phan Rang", "Ninh Thuận", "Ninh Chữ", "Bình Ba", "Vĩnh Hy")),
    SanBay("PQC", "Phú Quốc", "Phú Quốc", "An Giang", True,
           ("Phu Quoc",),
           ("Hòn Thơm", "Nam đảo Phú Quốc")),
    SanBay("DLI", "Liên Khương", "Đà Lạt", "Lâm Đồng", True,
           ("Dalat", "Lâm Đồng"),
           ("Bảo Lộc", "Đức Trọng")),
    SanBay("HUI", "Phú Bài", "Huế", "Huế", True,
           ("Hue", "Thừa Thiên Huế"),
           ("Lăng Cô", "Phong Điền")),
    SanBay("HPH", "Cát Bi", "Hải Phòng", "Hải Phòng", True,
           ("Hai Phong",),
           ("Cát Bà", "Đồ Sơn", "Hải Dương")),
    SanBay("VCA", "Cần Thơ", "Cần Thơ", "Cần Thơ", True,
           ("Can Tho",),
           ("Sóc Trăng", "Hậu Giang", "Vĩnh Long", "Bến Tre", "Châu Đốc")),
    SanBay("UIH", "Phù Cát", "Quy Nhơn", "Gia Lai", False,
           ("Bình Định", "Qui Nhon"),
           ("Kỳ Co", "Eo Gió")),
    SanBay("VDO", "Vân Đồn", "Vân Đồn", "Quảng Ninh", True,
           ("Quảng Ninh",),
           ("Hạ Long", "Vịnh Hạ Long", "Cô Tô", "Bái Tử Long", "Quan Lạn", "Móng Cái")),
    SanBay("VCS", "Côn Đảo", "Côn Đảo", "TP. Hồ Chí Minh", False,
           ("Con Dao",), ()),
    SanBay("BMV", "Buôn Ma Thuột", "Buôn Ma Thuột", "Đắk Lắk", False,
           ("Ban Me Thuot", "Đắk Lắk", "Dak Lak"),
           ("Buôn Đôn", "Hồ Lắk")),
    SanBay("VII", "Vinh", "Vinh", "Nghệ An", False,
           ("Nghệ An",),
           ("Cửa Lò", "Hà Tĩnh", "Kim Liên")),
    SanBay("THD", "Thọ Xuân", "Thanh Hóa", "Thanh Hóa", False,
           ("Thanh Hoa",),
           ("Sầm Sơn", "Pù Luông", "Hải Tiến")),
    SanBay("VDH", "Đồng Hới", "Đồng Hới", "Quảng Trị", False,
           ("Quảng Bình", "Dong Hoi"),
           ("Phong Nha", "Phong Nha Kẻ Bàng", "Sơn Đoòng", "Đông Hà")),
    SanBay("VCL", "Chu Lai", "Tam Kỳ", "Đà Nẵng", False,
           ("Chu Lai",),
           ("Quảng Ngãi", "Lý Sơn", "Sa Huỳnh")),
    SanBay("TBB", "Tuy Hòa", "Tuy Hòa", "Đắk Lắk", False,
           ("Phú Yên",),
           ("Gành Đá Đĩa", "Mũi Điện")),
    SanBay("PXU", "Pleiku", "Pleiku", "Gia Lai", False,
           ("Gia Lai",),
           ("Kon Tum", "Măng Đen")),
    SanBay("DIN", "Điện Biên Phủ", "Điện Biên Phủ", "Điện Biên", False,
           ("Điện Biên",), ("Mường Lay", "Lai Châu")),
    SanBay("CAH", "Cà Mau", "Cà Mau", "Cà Mau", False,
           ("Ca Mau",), ("Bạc Liêu", "Đất Mũi")),
    SanBay("VKG", "Rạch Giá", "Rạch Giá", "An Giang", False,
           ("Kiên Giang", "Rach Gia"), ("Nam Du", "Hòn Sơn")),
)

_DIEM = {"code": 100, "city": 90, "name": 85, "alias": 80, "province": 60, "serves": 50, "chua": 30}


def bo_dau(s: str) -> str:
    """'Đà Nẵng' -> 'da nang': bỏ dấu, chữ thường, gom khoảng trắng."""
    s = s.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return s.strip()


def _bo_tien_to(s: str) -> str:
    """Bỏ các tiền tố hay gõ kèm: 'sân bay', 'tp', 'thành phố', 'tỉnh'."""
    return re.sub(r"^(san bay|sb|thanh pho|tp|tinh|airport)\s+", "", s).strip()


def tra_san_bay_vn(query: str) -> list[tuple[SanBay, int, str]]:
    """Trả [(sân bay, điểm, khớp theo gì)] giảm dần theo điểm. Rỗng nếu không thấy."""
    q = _bo_tien_to(bo_dau(query))
    if not q:
        return []

    ket_qua: dict[str, tuple[SanBay, int, str]] = {}

    def ghi(sb: SanBay, loai: str) -> None:
        diem = _DIEM[loai]
        if sb.code not in ket_qua or ket_qua[sb.code][1] < diem:
            ket_qua[sb.code] = (sb, diem, loai)

    for sb in SAN_BAY_VN:
        nhom = [
            ("code", (sb.code,)),
            ("city", (sb.city,)),
            ("name", (sb.name,)),
            ("alias", sb.aliases),
            ("province", (sb.province,)),
            ("serves", sb.serves),
        ]
        for loai, ten_list in nhom:
            for ten in ten_list:
                t = _bo_tien_to(bo_dau(ten))
                if q == t:
                    ghi(sb, loai)
                elif len(q) >= 4 and loai != "code" and (q in t or t in q):
                    ghi(sb, "chua")

    return sorted(ket_qua.values(), key=lambda x: (-x[1], x[0].code))


def doi_ra_ma_iata(gia_tri: str) -> str | None:
    """Đổi input của người dùng thành mã IATA.

    - Đã là mã 3 chữ (hoặc nhiều mã nối dấu phẩy): viết hoa rồi trả lại.
    - Tên thành phố / điểm đến ở Việt Nam: trả mã nếu có đúng một kết quả điểm cao nhất.
    - Không xác định được (nước ngoài, mơ hồ): trả None.
    """
    gon = gia_tri.strip()
    cac_ma = [m.strip() for m in gon.split(",")]
    if len(cac_ma) > 1 and all(_MA_IATA.match(m) for m in cac_ma):
        return ",".join(m.upper() for m in cac_ma)

    ket_qua = tra_san_bay_vn(gon)
    if _MA_IATA.match(gon):
        # 3 chữ cái: ưu tiên mã sân bay VN, rồi tên viết tắt VN ('HCM', 'Hue'),
        # còn lại coi là mã IATA nước ngoài.
        khop = [sb for sb, _, loai in ket_qua if loai in ("code", "city", "alias")]
        return khop[0].code if khop else gon.upper()


    if not ket_qua:
        return None
    cao_nhat = ket_qua[0][1]
    dong_hang = [sb for sb, diem, _ in ket_qua if diem == cao_nhat]
    if len(dong_hang) != 1 or cao_nhat < _DIEM["serves"]:
        return None
    return dong_hang[0].code
