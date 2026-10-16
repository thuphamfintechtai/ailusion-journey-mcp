"""Tool tìm điểm thay thế khi một điểm đóng cửa, quá đông hoặc không đáp ứng nhu cầu."""

from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.travel_db import TravelDbError, khoang_cach_km, travel_db
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._db_common import (
    NGUON_DB,
    diem_pho_bien,
    doc_thoi_diem,
    loi_db,
    muc_dong_luc,
    nhom_dia_diem,
    rating_hieu_chinh,
    truong_sao_tin_duoc,
    trang_thai_mo_cua,
)
from ._nhu_cau import NHU_CAU, NhuCau, danh_gia, them_nhan_nhu_cau

logger = structlog.get_logger(__name__)

_COT_DICH = "title, place_id, category, lat, lng, rating, review_count, open_hours, popular_times, attributes, attributes_not_available"
_COT_UNG_VIEN = "open_hours, popular_times, attributes, attributes_not_available"
# Số ứng viên kéo chi tiết (giờ mở cửa, giờ đông khách) từ DB: đủ để còn lại vài điểm sau khi lọc.
_SO_UNG_VIEN = 40


def register_find_alternatives_tool(mcp: FastMCP):
    @mcp.tool(
        name="find_alternatives",
        title="Find Alternatives (Tìm điểm thay thế)",
        description=(
            "Tìm điểm thay thế cho một địa điểm trong lịch trình: cùng loại (VD chùa thay chùa, quán "
            "phở thay quán phở; không đủ thì cùng nhóm tham quan / ăn uống / lưu trú / thư giãn), trong "
            "bán kính quanh điểm cũ, MỞ CỬA và đủ thời gian ở lại tại `at`, ít đông hơn, đáp ứng `needs`. "
            "Không tốn quota.\n\n"
            "Khi nào dùng: check_opening_hours báo đóng cửa, best_time_to_visit / compare_places báo "
            "quá đông, check_place_needs báo 'no' (VD không có lối vào xe lăn), hoặc người dùng muốn "
            "đổi điểm.\n"
            "Trả về `target` (tình trạng điểm cũ tại `at`) và `alternatives` kèm lý do: khoảng cách, "
            "mức đông, rating hiệu chỉnh, trạng thái mở cửa, nhu cầu."
        ),
        tags={"journey", "db", "maps", "planning"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "postgres:gmaps_place"},
    )
    async def find_alternatives(
        place: Annotated[str, Field(description="place_id hoặc tên điểm cần thay.")],
        at: Annotated[
            str | None, Field(description="Thời điểm dự định đến 'YYYY-MM-DD HH:MM'; có thì chỉ lấy điểm đang mở.")
        ] = None,
        stay_minutes: Annotated[int, Field(ge=0, le=720, description="Thời gian định ở lại, phút.")] = 60,
        radius_km: Annotated[float, Field(gt=0, le=30, description="Bán kính tìm quanh điểm cũ, km.")] = 3,
        needs: Annotated[
            list[NhuCau] | None,
            Field(max_length=8, description="Nhu cầu điểm thay thế phải đáp ứng (loại nơi ghi rõ là không có).", json_schema_extra=them_nhan_nhu_cau),
        ] = None,
        min_rating: Annotated[float | None, Field(ge=0, le=5, description="Rating tối thiểu.")] = None,
        destination: Annotated[str | None, Field(description="Điểm đến để phân biệt tên trùng.")] = None,
        limit: Annotated[int, Field(ge=1, le=10, description="Số điểm thay thế tối đa.")] = 5,
    ) -> dict[str, Any]:
        input_context = {"place": place, "at": at, "radius_km": radius_km, "needs": needs}
        try:
            if not place.strip():
                return format_validation_error("place", place, "không được để trống")
            luc = None
            if at and at.strip():
                luc = doc_thoi_diem(at)
                if luc is None:
                    return format_validation_error("at", at, "phải có dạng 'YYYY-MM-DD HH:MM'")
            nhu_cau = list(dict.fromkeys(needs or []))

            dich = (await travel_db.find_places([place], destination, columns=_COT_DICH))[0]
            if not dich:
                return drop_empty(
                    {"status": "success", "place": place, "note": "Không có địa điểm này trong dữ liệu đã lưu."}
                )
            if dich.get("lat") is None:
                return drop_empty({"status": "success", "place": place, "note": "Địa điểm thiếu toạ độ, không tìm quanh được."})

            loai, nhom = dich.get("category"), nhom_dia_diem(dich.get("category"))
            ung_vien = []
            for p in await travel_db.places():
                if p["cid"] == dich["_cid"] or p.get("lat") is None:
                    continue
                if min_rating is not None and (p.get("rating") or 0) < min_rating:
                    continue
                cung_loai = 2 if p.get("category") == loai else 1 if nhom and nhom_dia_diem(p.get("category")) == nhom else 0
                if not cung_loai:
                    continue
                kc = khoang_cach_km(dich["lat"], dich["lng"], p["lat"], p["lng"])
                if kc <= radius_km:
                    ung_vien.append((cung_loai, kc, p))
            ung_vien.sort(key=lambda x: (-x[0], -diem_pho_bien(x[2])))
            ung_vien = ung_vien[:_SO_UNG_VIEN]
            chi_tiet = await travel_db.details_by_cid([p["cid"] for *_, p in ung_vien], _COT_UNG_VIEN)

            muc_dich = muc_dong_luc(dich.get("popular_times"), luc) if luc else None
            dat: list[dict[str, Any]] = []
            loai_bo = {"closed": 0, "needs": 0}
            for cung_loai, kc, p in ung_vien:
                ct = chi_tiet.get(p["cid"], {})
                mo = trang_thai_mo_cua(ct.get("open_hours"), luc, stay_minutes) if luc else None
                if mo and mo["status"] == "closed":
                    loai_bo["closed"] += 1
                    continue
                if mo and mo["status"] == "open" and not mo.get("fits_stay"):
                    loai_bo["closed"] += 1
                    continue
                tt = {nc: danh_gia(nc, ct.get("attributes"), ct.get("attributes_not_available")) for nc in nhu_cau}
                if any(v == "no" for v in tt.values()):
                    loai_bo["needs"] += 1
                    continue
                muc = muc_dong_luc(ct.get("popular_times"), luc) if luc else None
                dat.append(
                    {
                        "name": p.get("title"),
                        "place_id": p.get("place_id"),
                        "category": p.get("category"),
                        "same_category": cung_loai == 2,
                        "distance_km": round(kc, 2),
                        "rating": p.get("rating") or None,
                        **truong_sao_tin_duoc(p),
                        "reviews": p.get("review_count"),
                        "open_status": mo.get("status") if mo else None,
                        "open_until": mo.get("open_until") if mo else None,
                        "busyness": muc,
                        "less_busy_than_target": (muc < muc_dich) if muc is not None and muc_dich is not None else None,
                        "needs_status": tt or None,
                        "address": p.get("address"),
                        "lat": p.get("lat"),
                        "lng": p.get("lng"),
                        "_xep": (
                            -cung_loai,
                            # Ưu tiên chắc chắn mở / đáp ứng nhu cầu hơn 'chưa rõ'.
                            0 if (mo is None or mo["status"] == "open") else 1,
                            sum(1 for v in tt.values() if v != "yes"),
                            (muc if muc is not None else 50) / 25,
                            -(rating_hieu_chinh(p) or 0),
                            kc,
                        ),
                    }
                )
            dat.sort(key=lambda d: d.pop("_xep"))

            mo_dich = trang_thai_mo_cua(dich.get("open_hours"), luc, stay_minutes) if luc else None
            return drop_empty(
                {
                    "status": "success",
                    "target": {
                        "name": dich.get("title"),
                        "place_id": dich.get("place_id"),
                        "category": loai,
                        "open_status": mo_dich.get("status") if mo_dich else None,
                        "busyness": muc_dich,
                        "needs_status": {
                            nc: danh_gia(nc, dich.get("attributes"), dich.get("attributes_not_available")) for nc in nhu_cau
                        }
                        or None,
                    },
                    "at": luc.strftime("%Y-%m-%d %H:%M") if luc else None,
                    "needs": {n: NHU_CAU[n]["nhan"] for n in nhu_cau} or None,
                    "candidates_checked": len(ung_vien),
                    "excluded": {k: v for k, v in loai_bo.items() if v} or None,
                    "alternatives": dat[:limit],
                    "note": None
                    if dat
                    else f"Không có điểm cùng loại phù hợp trong {radius_km} km; thử tăng radius_km hoặc bớt needs.",
                    "data_source": NGUON_DB,
                }
            )

        except TravelDbError as e:
            logger.error("find_alternatives đọc DB thất bại", error=str(e))
            return loi_db(e, input_context)
        except Exception as e:
            logger.error("find_alternatives thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không tìm được điểm thay thế", exception=e, input_context=input_context
            )

    return find_alternatives
