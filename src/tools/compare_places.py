"""Tool so sánh nhiều địa điểm cạnh nhau và xuất sẵn đầu vào cho score_options."""

import math
from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.travel_db import TravelDbError, khoang_cach_km, travel_db
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._db_common import (
    NGUON_DB,
    doc_thoi_diem,
    gia_uoc_tinh,
    loi_db,
    muc_dong_luc,
    rating_hieu_chinh,
    trang_thai_mo_cua,
    ty_le_sao,
)
from ._nhu_cau import NhuCau, danh_gia, luu_y, them_nhan_nhu_cau

logger = structlog.get_logger(__name__)

_COT = (
    "title, place_id, category, address, rating, review_count, reviews_per_rating, price_range, summary, "
    "lat, lng, open_hours, popular_times, attributes, attributes_not_available"
)

# Tiêu chí cho score_options: (khoá, hướng, trọng số mặc định)
_TIEU_CHI = [
    ("rating_adjusted", "max", 3),
    ("one_star_pct", "min", 1),
    ("price_vnd", "min", 2),
    ("busyness", "min", 1),
    ("distance_km", "min", 2),
    ("needs_met", "max", 3),
]


def register_compare_places_tool(mcp: FastMCP):
    @mcp.tool(
        name="compare_places",
        title="Compare Places (So sánh địa điểm)",
        description=(
            "So sánh 2-10 địa điểm cạnh nhau từ dữ liệu đã lưu: rating hiệu chỉnh theo số review, tỷ "
            "lệ 1 sao, giá (giá phòng / đêm cho chỗ ở, khoảng giá / người cho quán), mức đông và trạng "
            "thái mở cửa tại thời điểm `at`, khoảng cách từ điểm xuất phát, mức đáp ứng `needs`. Không "
            "tốn quota.\n\n"
            "Trả thêm `score_options_input` (options + criteria) để gọi thẳng score_options xếp hạng "
            "TOPSIS; chỉ gồm tiêu chí mà MỌI điểm đều có số liệu.\n"
            "Khi nào dùng: chọn giữa vài quán / khách sạn / điểm tham quan ứng viên, cần lý do định lượng."
        ),
        tags={"journey", "db", "maps", "planning"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "postgres:gmaps_place"},
    )
    async def compare_places(
        places: Annotated[list[str], Field(min_length=2, max_length=10, description="place_id hoặc tên các điểm cần so.")],
        at: Annotated[
            str | None, Field(description="Thời điểm dự định đến 'YYYY-MM-DD HH:MM' để so mức đông / mở cửa.")
        ] = None,
        stay_minutes: Annotated[int, Field(ge=0, le=720, description="Thời gian định ở lại, phút (dùng với `at`).")] = 60,
        origin_lat: Annotated[float | None, Field(ge=-90, le=90, description="Vĩ độ điểm xuất phát (khách sạn...).")] = None,
        origin_lng: Annotated[float | None, Field(ge=-180, le=180, description="Kinh độ điểm xuất phát.")] = None,
        needs: Annotated[
            list[NhuCau] | None,
            Field(max_length=8, description="Nhu cầu để tính `needs_met`.", json_schema_extra=them_nhan_nhu_cau),
        ] = None,
        destination: Annotated[str | None, Field(description="Điểm đến để phân biệt tên trùng.")] = None,
    ) -> dict[str, Any]:
        input_context = {"places": places, "at": at, "destination": destination}
        try:
            if (origin_lat is None) != (origin_lng is None):
                return format_validation_error("origin_lat/origin_lng", input_context, "phải truyền cả hai hoặc bỏ cả hai")
            luc = None
            if at and at.strip():
                luc = doc_thoi_diem(at)
                if luc is None:
                    return format_validation_error("at", at, "phải có dạng 'YYYY-MM-DD HH:MM'")
            nhu_cau = list(dict.fromkeys(needs or []))

            tim_thay = await travel_db.find_places(places, destination, columns=_COT)
            hang: list[dict[str, Any]] = []
            khong_thay = []
            da_co: set[str] = set()
            for ten, p in zip(places, tim_thay):
                if not p:
                    khong_thay.append(ten)
                    continue
                if p.get("place_id") in da_co:
                    continue
                da_co.add(p.get("place_id"))
                sao = ty_le_sao(p.get("reviews_per_rating"))
                gia = gia_uoc_tinh(p)
                mo = trang_thai_mo_cua(p.get("open_hours"), luc, stay_minutes) if luc else None
                kc = (
                    khoang_cach_km(origin_lat, origin_lng, p["lat"], p["lng"])
                    if origin_lat is not None and p.get("lat") is not None
                    else None
                )
                tt_nhu_cau = {nc: danh_gia(nc, p.get("attributes"), p.get("attributes_not_available")) for nc in nhu_cau}
                hang.append(
                    {
                        "name": p.get("title"),
                        "place_id": p.get("place_id"),
                        "category": p.get("category"),
                        "rating": p.get("rating") or None,
                        "reviews": p.get("review_count"),
                        "rating_adjusted": rating_hieu_chinh(p.get("rating"), p.get("review_count")),
                        "one_star_pct": round(sao["1"] * 100, 1) if sao else None,
                        "price": gia,
                        "open_status": mo.get("status") if mo else None,
                        "fits_stay": mo.get("fits_stay") if mo else None,
                        "busyness": muc_dong_luc(p.get("popular_times"), luc) if luc else None,
                        "distance_km": round(kc, 2) if kc is not None else None,
                        "needs_status": tt_nhu_cau or None,
                        "needs_met": sum(1 for v in tt_nhu_cau.values() if v == "yes") if nhu_cau else None,
                        "heads_up": luu_y(p.get("attributes")) or None,
                    }
                )

            if len(hang) < 2:
                return drop_empty(
                    {
                        "status": "success",
                        "compared": hang,
                        "not_found": khong_thay or None,
                        "note": "Cần ít nhất 2 điểm tìm được trong dữ liệu đã lưu để so sánh.",
                    }
                )

            # Chỉ đưa vào score_options tiêu chí mà mọi điểm đều có số (TOPSIS cần ma trận đủ).
            def gia_tri(h: dict[str, Any], k: str) -> float | None:
                v = (h.get("price") or {}).get("vnd") if k == "price_vnd" else h.get(k)
                return float(v) if isinstance(v, int | float) and not math.isnan(v) else None

            dung, bo = [], []
            for k, huong, w in _TIEU_CHI:
                if all(gia_tri(h, k) is not None for h in hang):
                    dung.append({"name": k, "direction": huong, "weight": w})
                elif any(gia_tri(h, k) is not None for h in hang):
                    bo.append(k)
            loai_gia = {(h.get("price") or {}).get("kind") for h in hang} - {None}
            if "price_vnd" in [c["name"] for c in dung] and len(loai_gia) > 1:
                # Giá phòng / đêm và giá / người không cùng thang, so sánh vô nghĩa.
                dung = [c for c in dung if c["name"] != "price_vnd"]
                bo.append("price_vnd")
            dau_vao = None
            if dung:
                dau_vao = {
                    "options": [{"name": h["name"], "metrics": {c["name"]: gia_tri(h, c["name"]) for c in dung}} for h in hang],
                    "criteria": dung,
                }

            return drop_empty(
                {
                    "status": "success",
                    "at": luc.strftime("%Y-%m-%d %H:%M") if luc else None,
                    "compared": hang,
                    "not_found": khong_thay or None,
                    "score_options_input": dau_vao,
                    "criteria_skipped": bo or None,
                    "note": "`criteria_skipped`: tiêu chí thiếu số liệu ở một số điểm (hoặc giá khác loại) nên "
                    "không đưa vào score_options."
                    if bo
                    else None,
                    "data_source": NGUON_DB,
                }
            )

        except TravelDbError as e:
            logger.error("compare_places đọc DB thất bại", error=str(e))
            return loi_db(e, input_context)
        except Exception as e:
            logger.error("compare_places thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không so sánh được địa điểm", exception=e, input_context=input_context
            )

    return compare_places
