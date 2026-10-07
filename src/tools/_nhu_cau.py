"""Danh mục nhu cầu của người đi -> thuộc tính Google Maps đã lưu (gmaps_place.attributes).

Google Maps chia thuộc tính theo nhóm ('Trẻ em', 'Bãi đỗ xe'...). Mỗi nhu cầu ở đây là:
  - `co`: các cặp (nhóm, giá trị) trong `attributes`, có MỘT trong số đó là đáp ứng
  - `khong`: các cặp trong `attributes_not_available` (Google ghi rõ là KHÔNG có)
  - `khong_neu_co`: cặp trong `attributes` mà có thì coi là KHÔNG đáp ứng (VD 'Chỉ nhận tiền mặt')
Không thấy cặp nào thì là 'unknown' — Google không có thông tin, không phải là không có.

File bắt đầu bằng `_` nên `auto_register` không coi đây là tool.
"""

from typing import Any, Literal

KXL = "Phù hợp cho người khuyết tật"

NHU_CAU: dict[str, dict[str, Any]] = {
    "wheelchair": {
        "nhan": "lối vào cho xe lăn",
        "co": [(KXL, "Lối vào cho xe lăn")],
        "khong": [(KXL, "Lối vào cho xe lăn")],
    },
    "wheelchair_toilet": {
        "nhan": "nhà vệ sinh cho xe lăn",
        "co": [(KXL, "Nhà vệ sinh cho xe lăn")],
        "khong": [(KXL, "Nhà vệ sinh cho xe lăn")],
    },
    "wheelchair_parking": {
        "nhan": "chỗ đỗ xe cho xe lăn",
        "co": [(KXL, "Chỗ đỗ xe cho xe lăn")],
        "khong": [(KXL, "Chỗ đỗ xe cho xe lăn")],
    },
    "hearing_assist": {
        "nhan": "hỗ trợ người khiếm thính",
        "co": [(KXL, "Vòng trợ thính"), (KXL, "Thiết bị hỗ trợ nghe"), (KXL, "Âm thanh truyền bằng công nghệ Auracast")],
        "khong": [(KXL, "Vòng trợ thính")],
    },
    "kids": {
        "nhan": "phù hợp trẻ em",
        "co": [
            ("Trẻ em", "Phù hợp cho trẻ em"),
            ("Trẻ em", "Hoạt động phù hợp với trẻ em"),
            ("Trẻ em", "Sân chơi"),
            ("Trẻ em", "Thực đơn dành cho trẻ em"),
            ("Khách hàng", "Dành cho gia đình"),
        ],
    },
    "high_chair": {"nhan": "ghế cao cho bé", "co": [("Trẻ em", "Ghế cao cho bé")]},
    "kids_menu": {"nhan": "thực đơn trẻ em", "co": [("Trẻ em", "Thực đơn dành cho trẻ em")]},
    "playground": {"nhan": "sân chơi", "co": [("Trẻ em", "Sân chơi")]},
    "baby_changing": {"nhan": "bàn thay tã / phòng cho con bú", "co": [("Trẻ em", "Có bàn thay tã, bỉm"), ("Trẻ em", "Phòng cho con bú")]},
    "vegetarian": {"nhan": "có món chay", "co": [("Dịch vụ", "Các món chay"), ("Dịch vụ", "Các món thuần chay")]},
    "vegan": {"nhan": "có món thuần chay", "co": [("Dịch vụ", "Các món thuần chay")]},
    "late_night_food": {"nhan": "đồ ăn khuya", "co": [("Dịch vụ", "Đồ ăn khuya")]},
    "breakfast": {"nhan": "phục vụ bữa sáng", "co": [("Lựa chọn ăn uống", "Bữa sáng"), ("Nổi tiếng về", "Bữa sáng")]},
    "brunch": {"nhan": "bữa nửa buổi", "co": [("Lựa chọn ăn uống", "Bữa nửa buổi")]},
    "alcohol": {"nhan": "có đồ uống có cồn", "co": [("Dịch vụ", "Bia"), ("Dịch vụ", "Rượu"), ("Dịch vụ", "Rượu vang"), ("Dịch vụ", "Rượu cốc tai")]},
    "private_room": {"nhan": "phòng ăn riêng", "co": [("Dịch vụ", "Phòng ăn riêng tư")]},
    "car_parking": {
        "nhan": "có chỗ đỗ ô tô",
        "co": [
            ("Bãi đỗ xe", "Bãi đỗ xe miễn phí"),
            ("Bãi đỗ xe", "Bãi đỗ xe có tính phí"),
            ("Bãi đỗ xe", "Chỗ đỗ xe trong khuôn viên"),
            ("Bãi đỗ xe", "Dịch vụ đỗ xe"),
            ("Bãi đỗ xe", "Gara đỗ xe miễn phí"),
            ("Bãi đỗ xe", "Gara đỗ xe có tính phí"),
            ("Bãi đỗ xe", "Đỗ xe miễn phí trên đường"),
            ("Bãi đỗ xe", "Đỗ xe có tính phí trên đường"),
        ],
        "khong": [("Bãi đỗ xe", "Chỗ đỗ xe")],
    },
    "free_parking": {
        "nhan": "đỗ xe miễn phí",
        "co": [("Bãi đỗ xe", "Bãi đỗ xe miễn phí"), ("Bãi đỗ xe", "Gara đỗ xe miễn phí"), ("Bãi đỗ xe", "Đỗ xe miễn phí trên đường")],
        "khong": [("Bãi đỗ xe", "Chỗ đỗ xe")],
    },
    "card_payment": {
        "nhan": "thanh toán thẻ",
        "co": [("Thanh toán", "Thẻ tín dụng"), ("Thanh toán", "Thẻ ghi nợ"), ("Thanh toán", "Thanh toán di động qua NFC")],
        "khong": [("Thanh toán", "Thẻ tín dụng")],
        "khong_neu_co": [("Thanh toán", "Chỉ nhận tiền mặt")],
    },
    "pets": {
        "nhan": "cho mang chó theo",
        "co": [("Thú cưng", "Cho phép mang theo chó"), ("Thú cưng", "Được mang chó vào trong"), ("Thú cưng", "Cho phép mang theo chó bên ngoài")],
    },
    "wifi": {"nhan": "Wi-Fi", "co": [("Tiện nghi", "Wi-Fi")], "khong": [("Tiện nghi", "Wi-Fi")]},
    "laptop_work": {"nhan": "hợp làm việc bằng laptop", "co": [("Nổi tiếng về", "Phù hợp để làm việc trên máy tính xách tay")]},
    "restroom": {
        "nhan": "có nhà vệ sinh",
        "co": [("Tiện nghi", "Nhà vệ sinh"), ("Tiện nghi", "Nhà vệ sinh công cộng"), ("Tiện nghi", "Nhà vệ sinh cho cả hai giới")],
        "khong": [("Tiện nghi", "Nhà vệ sinh")],
    },
    "luggage_storage": {"nhan": "giữ hộ hành lý", "co": [("Tiện nghi", "Giữ hộ hành lý")]},
    "outdoor_seating": {"nhan": "chỗ ngồi ngoài trời", "co": [("Các tùy chọn dịch vụ", "Chỗ ngồi ngoài trời")]},
    "takeaway": {"nhan": "mang đi", "co": [("Các tùy chọn dịch vụ", "Đồ ăn mang đi")], "khong": [("Các tùy chọn dịch vụ", "Đồ ăn mang đi")]},
    "delivery": {
        "nhan": "giao hàng",
        "co": [("Các tùy chọn dịch vụ", "Giao hàng"), ("Các tùy chọn dịch vụ", "Giao hàng gián tiếp")],
        "khong": [("Các tùy chọn dịch vụ", "Giao hàng")],
    },
    "reservations": {"nhan": "nhận đặt chỗ", "co": [("Lên kế hoạch", "Nhận đặt chỗ")], "khong": [("Lên kế hoạch", "Nhận đặt chỗ")]},
    "groups": {"nhan": "hợp đi nhóm đông", "co": [("Khách hàng", "Nhóm")]},
    "solo_dining": {"nhan": "hợp ăn một mình", "co": [("Nổi tiếng về", "Ăn tối một mình")]},
    "lgbtq_friendly": {
        "nhan": "thân thiện LGBTQ+",
        "co": [("Khách hàng", "Chào đón những người thuộc giới LGBTQ+"), ("Khách hàng", "Nơi an toàn cho người chuyển giới")],
    },
    "romantic": {"nhan": "không khí lãng mạn", "co": [("Bầu không khí", "Lãng mạn")]},
    "quiet": {"nhan": "yên tĩnh", "co": [("Bầu không khí", "Yên tĩnh")]},
    "cozy": {"nhan": "ấm cúng", "co": [("Bầu không khí", "Ấm cúng")]},
    "upscale": {"nhan": "cao cấp / sành điệu", "co": [("Bầu không khí", "Cao cấp"), ("Bầu không khí", "Sành điệu")]},
    "rooftop": {"nhan": "chỗ ngồi sân thượng", "co": [("Điểm nổi bật", "Chỗ ngồi trên sân thượng")]},
    "live_music": {"nhan": "nhạc sống / biểu diễn", "co": [("Điểm nổi bật", "Nhạc sống"), ("Điểm nổi bật", "Biểu diễn trực tiếp")]},
    "good_coffee": {"nhan": "cà phê ngon", "co": [("Điểm nổi bật", "Cà phê ngon")]},
    "sports_tv": {"nhan": "xem thể thao", "co": [("Điểm nổi bật", "Thể thao")]},
}

NhuCau = Literal[
    "wheelchair", "wheelchair_toilet", "wheelchair_parking", "hearing_assist",
    "kids", "high_chair", "kids_menu", "playground", "baby_changing",
    "vegetarian", "vegan", "late_night_food", "breakfast", "brunch", "alcohol", "private_room",
    "car_parking", "free_parking", "card_payment", "pets", "wifi", "laptop_work", "restroom",
    "luggage_storage", "outdoor_seating", "takeaway", "delivery", "reservations",
    "groups", "solo_dining", "lgbtq_friendly", "romantic", "quiet", "cozy", "upscale",
    "rooftop", "live_music", "good_coffee", "sports_tv",
]  # fmt: skip

# Thuộc tính cần báo trước cho người đi dù họ không hỏi (gây bất ngờ khi tới nơi).
_LUU_Y = {
    ("Thanh toán", "Chỉ nhận tiền mặt"): "Chỉ nhận tiền mặt",
    ("Thanh toán", "Phí vào cổng"): "Có thu phí vào cổng",
    ("Lên kế hoạch", "Bắt buộc đặt chỗ trước"): "Bắt buộc đặt chỗ trước",
    ("Lên kế hoạch", "Yêu cầu hẹn trước"): "Phải hẹn trước",
    ("Lên kế hoạch", "Bạn nên đặt vé trước"): "Nên đặt vé trước",
    ("Lên kế hoạch", "Nên đặt chỗ ăn tối trước"): "Nên đặt chỗ ăn tối trước",
    ("Lên kế hoạch", "Nên đặt chỗ ăn trưa trước"): "Nên đặt chỗ ăn trưa trước",
    ("Lên kế hoạch", "Thường phải đợi"): "Thường phải xếp hàng đợi",
    ("Lên kế hoạch", "Bắt buộc phải là thành viên"): "Chỉ dành cho thành viên",
}

TrangThai = Literal["yes", "no", "unknown"]


def _co(bang: dict[str, Any] | None, nhom: str, gia_tri: str) -> bool:
    ds = (bang or {}).get(nhom)
    return isinstance(ds, list) and gia_tri in ds


def danh_gia(nhu_cau: str, attributes: dict[str, Any] | None, not_available: dict[str, Any] | None) -> TrangThai:
    """Địa điểm có đáp ứng nhu cầu không: 'yes' / 'no' / 'unknown' (Google không có thông tin)."""
    spec = NHU_CAU[nhu_cau]
    if any(_co(attributes, n, v) for n, v in spec.get("khong_neu_co", [])):
        return "no"
    if any(_co(attributes, n, v) for n, v in spec["co"]):
        return "yes"
    if any(_co(not_available, n, v) for n, v in spec.get("khong", [])):
        return "no"
    return "unknown"


def luu_y(attributes: dict[str, Any] | None) -> list[str]:
    """Các điều cần biết trước khi tới (chỉ nhận tiền mặt, phải đặt chỗ, phí vào cổng...)."""
    return [nhan for (n, v), nhan in _LUU_Y.items() if _co(attributes, n, v)]


def dieu_kien_sql(
    nhu_cau: list[str], bat_dau: int, chac_chan: bool = True
) -> tuple[str, list[dict[str, list[str]]]]:
    """Điều kiện SQL cho MỌI nhu cầu, dùng toán tử jsonb @> trên attributes / attributes_not_available.

    `chac_chan=True`: phải có bằng chứng đáp ứng ('yes'). `False`: chỉ loại nơi Google ghi rõ là
    KHÔNG có ('no'), giữ nơi chưa có thông tin — cần cho chỗ ở vì chỉ ~10% có dữ liệu tiện nghi.
    Trả (chuỗi điều kiện, tham số), tham số đánh số từ $`bat_dau`. Lọc ở DB để không phải kéo
    cột attributes của hàng nghìn địa điểm qua mạng.
    """
    if not chac_chan:
        return _dieu_kien_khong_bi_loai(nhu_cau, bat_dau)
    dk, ts = [], []
    for nc in nhu_cau:
        spec = NHU_CAU[nc]
        hoac = []
        for n, v in spec["co"]:
            ts.append({n: [v]})
            hoac.append(f"attributes @> ${bat_dau + len(ts) - 1}::jsonb")
        for n, v in spec.get("khong_neu_co", []):
            ts.append({n: [v]})
            hoac_khong = f"NOT coalesce(attributes @> ${bat_dau + len(ts) - 1}::jsonb, false)"
            hoac = [f"(({' OR '.join(hoac)}) AND {hoac_khong})"]
        dk.append("(" + " OR ".join(hoac) + ")")
    return " AND ".join(dk) or "TRUE", ts


def _dieu_kien_khong_bi_loai(nhu_cau: list[str], bat_dau: int) -> tuple[str, list[dict[str, list[str]]]]:
    dk, ts = [], []
    for nc in nhu_cau:
        spec = NHU_CAU[nc]
        if spec.get("khong"):
            co = []
            for n, v in spec["co"]:
                ts.append({n: [v]})
                co.append(f"coalesce(attributes @> ${bat_dau + len(ts) - 1}::jsonb, false)")
        # Nhu cầu không có dữ liệu 'không có' (VD trẻ em) thì không bao giờ bị loại ở chế độ này;
        # chỉ thêm tham số khi thật sự dùng, thừa tham số là Postgres báo lỗi.
        khong = []
        for n, v in spec.get("khong", []):
            ts.append({n: [v]})
            khong.append(f"coalesce(attributes_not_available @> ${bat_dau + len(ts) - 1}::jsonb, false)")
        # Cùng thứ tự ưu tiên với danh_gia(): khong_neu_co (VD 'Chỉ nhận tiền mặt') luôn là 'no';
        # sau đó có bằng chứng 'có' thì giữ; cuối cùng mới loại theo attributes_not_available.
        if khong:
            dk.append(f"(({' OR '.join(co)}) OR NOT ({' OR '.join(khong)}))")
        for n, v in spec.get("khong_neu_co", []):
            ts.append({n: [v]})
            dk.append(f"NOT coalesce(attributes @> ${bat_dau + len(ts) - 1}::jsonb, false)")
    return " AND ".join(dk) or "TRUE", ts


def bieu_thuc_so_yes(nhu_cau: list[str], bat_dau: int) -> tuple[str, list[dict[str, list[str]]]]:
    """Biểu thức SQL đếm số nhu cầu CHẮC CHẮN đáp ứng ('yes') của một dòng, để xếp hạng khi lọc
    không chắc chắn (nơi đáp ứng 5/8 nhu cầu phải đứng trước nơi 0/8 dù kém phổ biến hơn)."""
    phan, ts = [], []
    for nc in nhu_cau:
        dk, t = dieu_kien_sql([nc], bat_dau + len(ts), chac_chan=True)
        ts += t
        phan.append(f"(coalesce({dk}, false))::int")
    return " + ".join(phan) or "0", ts


def trang_thai_nhu_cau(nhu_cau: list[str], p: dict[str, Any]) -> dict[str, TrangThai]:
    return {nc: danh_gia(nc, p.get("attributes"), p.get("attributes_not_available")) for nc in nhu_cau}


def nhan_nhu_cau() -> dict[str, str]:
    """{khoá: nhãn tiếng Việt} để hiện trong dropdown của client."""
    return {k: v["nhan"] for k, v in NHU_CAU.items()}


def them_nhan_nhu_cau(schema: dict[str, Any]) -> None:
    """json_schema_extra cho tham số kiểu list[NhuCau] | None: gắn `enumNames` song ngữ vào chỗ
    có `enum` (nằm sâu trong anyOf / items) để client hiện 'kids (phù hợp trẻ em)'."""
    nhan = nhan_nhu_cau()

    def di(nut: Any) -> None:
        if isinstance(nut, dict):
            if "enum" in nut:
                nut["enumNames"] = [f"{v} ({nhan.get(v, v)})" for v in nut["enum"]]
            for con in nut.values():
                di(con)
        elif isinstance(nut, list):
            for con in nut:
                di(con)

    di(schema)
