"""Phần lõi tìm chỗ ở trong dữ liệu đã lưu: dùng cho find_stays và fallback của search_hotels.

Giá là giá phòng / đêm Google Maps hiện lúc cào (một mức, không theo ngày / số người), chỉ
để so sánh và lọc ngân sách. File bắt đầu bằng `_` nên `auto_register` không coi đây là tool.
"""

from typing import Any

from ..services.travel_db import travel_db
from ._db_common import (
    diem_pho_bien,
    gia_phong,
    la_cho_o,
    loai_cho_o,
    loc_dia_diem,
    moi_nhat,
    phan_vi,
    rating_hieu_chinh,
    truong_sao_tin_duoc,
)
from ._nhu_cau import NHU_CAU, bieu_thuc_so_yes, dieu_kien_sql, trang_thai_nhu_cau

GHI_CHU_GIA = (
    "Giá là giá phòng / đêm Google Maps hiện lúc cào (`data_as_of`), không theo ngày nhận phòng "
    "hay số người; dùng để so sánh / lọc ngân sách, giá thật cần kiểm tra khi đặt (search_hotels)."
)


async def tim_cho_o(
    *,
    destination_key: str | None,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float | None = None,
    query: str | None = None,
    kind: str | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    min_rating: float | None = None,
    min_reviews: int | None = None,
    needs: list[str] | None = None,
    strict_needs: bool = False,
    sort_by: str = "popular",
    limit: int = 10,
) -> dict[str, Any]:
    """Lọc chỗ ở theo vùng / loại / giá / rating / nhu cầu. Trả {stays, total_found, price_stats, ...}."""
    ung_vien, _ = loc_dia_diem(
        await travel_db.places(),
        query=query,
        destination_key=destination_key,
        lat=lat,
        lng=lng,
        radius_km=radius_km,
        min_rating=min_rating,
        min_reviews=min_reviews,
        sort_by="distance" if sort_by == "distance" else "popular",
    )
    ung_vien = [p for p in ung_vien if la_cho_o(p.get("category"))]
    if kind and kind != "any":
        ung_vien = [p for p in ung_vien if loai_cho_o(p.get("category")) == kind]

    # Mặt bằng giá của khu vực / loại đã chọn, tính TRƯỚC khi lọc ngân sách để biết ngân sách
    # đặt ra là thấp hay cao so với thị trường.
    thong_ke = phan_vi([g for p in ung_vien if (g := gia_phong(p))])

    if min_price is not None or max_price is not None:
        ung_vien = [
            p
            for p in ung_vien
            if (g := gia_phong(p)) is not None
            and (min_price is None or g >= min_price)
            and (max_price is None or g <= max_price)
        ]

    so_yes: dict[str, int] = {}
    if needs and ung_vien:
        dk, ts = dieu_kien_sql(needs, 2, chac_chan=strict_needs)
        bt, ts_bt = bieu_thuc_so_yes(needs, 2 + len(ts)) if not strict_needs else ("0", [])
        dat = await travel_db.cids_matching([p["cid"] for p in ung_vien], dk, ts, bt, ts_bt)
        ung_vien = [p for p in ung_vien if p["cid"] in dat]
        so_yes = dat

    if sort_by == "price_low":
        ung_vien.sort(key=lambda p: (gia_phong(p) is None, gia_phong(p) or 0))
    elif sort_by == "rating":
        ung_vien.sort(key=lambda p: -(rating_hieu_chinh(p) or 0))
    elif sort_by == "value":
        # Điểm đáng tiền: rating hiệu chỉnh trên mỗi triệu đồng, chỉ xét chỗ có giá.
        co_gia = [p for p in ung_vien if gia_phong(p)]
        co_gia.sort(key=lambda p: -((rating_hieu_chinh(p) or 0) / (gia_phong(p) / 1e6) ** 0.5))
        ung_vien = co_gia
    elif sort_by != "distance":
        ung_vien.sort(key=lambda p: -diem_pho_bien(p))
    if needs and not strict_needs:
        # Nơi chắc chắn đáp ứng nhiều nhu cầu hơn lên trước, giữ thứ tự sort_by trong cùng mức.
        ung_vien.sort(key=lambda p: -so_yes.get(p["cid"], 0))

    chon = ung_vien[:limit]
    ket_qua = [gon_cho_o(p) for p in chon]
    if needs and chon:
        chi_tiet = await travel_db.details_by_cid([p["cid"] for p in chon], "attributes, attributes_not_available")
        for r, p in zip(ket_qua, chon):
            r["needs_status"] = trang_thai_nhu_cau(needs, chi_tiet.get(p["cid"], {}))
    return {
        "total_found": len(ung_vien),
        "stays": ket_qua,
        "price_stats_vnd": thong_ke,
        "data_as_of": moi_nhat(ung_vien),
        "price_note": GHI_CHU_GIA,
        "needs_applied": [NHU_CAU[n]["nhan"] for n in needs] if needs else None,
    }


def gon_cho_o(p: dict[str, Any]) -> dict[str, Any]:
    kc = p.get("_distance_km")
    return {
        "name": p.get("title"),
        "place_id": p.get("place_id"),
        "kind": loai_cho_o(p.get("category")),
        "category": p.get("category"),
        "price_per_night_vnd": gia_phong(p),
        "rating": p.get("rating") or None,
        **truong_sao_tin_duoc(p),
        "reviews": p.get("review_count"),
        "address": p.get("address"),
        "lat": p.get("lat"),
        "lng": p.get("lng"),
        "distance_km": round(kc, 2) if kc is not None else None,
        "phone": p.get("phone"),
        "website": p.get("website"),
    }

