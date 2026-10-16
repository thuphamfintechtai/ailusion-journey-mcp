"""Tool đọc sâu rating của một địa điểm: độ tin cậy, phân cực, độ mới, review xấu."""

from datetime import date
from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.travel_db import TravelDbError, travel_db
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._db_common import NGUON_DB, loi_db, rating_hieu_chinh, ty_le_sao

logger = structlog.get_logger(__name__)

_COT = "title, place_id, category, destination, address, rating, review_count, reviews_per_rating, reviews, fetched_at"


def _do_tin_cay(n: int) -> str:
    if n < 30:
        return "thấp (dưới 30 review)"
    if n < 300:
        return "vừa (30-300 review)"
    return "cao (trên 300 review)"


async def _thu_hang_cung_loai(p: dict[str, Any], diem: float | None) -> dict[str, Any] | None:
    """Rating hiệu chỉnh đứng thứ mấy phần trăm trong cùng loại, cùng điểm đến."""
    if diem is None:
        return None
    ds = await travel_db.places()
    dau = next((x for x in ds if x["cid"] == p.get("_cid")), None)
    if not dau:
        return None
    cung = [
        r
        for x in ds
        if x["_dest"] == dau["_dest"]
        and x.get("category") == p.get("category")
        and not x.get("_ngoai_vung")
        and (r := rating_hieu_chinh(x)) is not None
    ]
    if len(cung) < 5:
        return None
    thap_hon = sum(1 for r in cung if r < diem)
    return {"category": p.get("category"), "peers": len(cung), "percentile": round(thap_hon / len(cung) * 100)}


def register_rating_insights_tool(mcp: FastMCP):
    @mcp.tool(
        name="rating_insights",
        title="Rating Insights (Đọc sâu rating)",
        description=(
            "Đọc sâu rating Google Maps đã lưu của một địa điểm thay vì chỉ nhìn con số trung bình: "
            "phân bố 1-5 sao, rating hiệu chỉnh theo số review, độ tin cậy, có PHÂN CỰC không (nhiều "
            "5 sao lẫn nhiều 1 sao: trải nghiệm không ổn định), xếp hạng so với cùng loại ở cùng điểm "
            "đến, độ mới của review, và trích các review điểm thấp. Không tốn quota.\n\n"
            "Khi nào dùng: điểm có rating đẹp nhưng cần biết có đáng tin không trước khi đưa vào lịch "
            "trình; giải thích vì sao chọn / loại một điểm. Đọc nhiều review hơn -> get_place_reviews."
        ),
        tags={"journey", "db", "reviews"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "postgres:gmaps_place.reviews_per_rating"},
    )
    async def rating_insights(
        place: Annotated[str, Field(description="place_id ('ChIJ...') hoặc tên địa điểm.")],
        destination: Annotated[str | None, Field(description="Điểm đến để phân biệt tên trùng.")] = None,
    ) -> dict[str, Any]:
        input_context = {"place": place, "destination": destination}
        try:
            if not place.strip():
                return format_validation_error("place", place, "không được để trống")
            p = (await travel_db.find_places([place], destination, columns=_COT))[0]
            if not p:
                return drop_empty(
                    {"status": "success", "place": place, "note": "Không có địa điểm này trong dữ liệu đã lưu."}
                )

            n = p.get("review_count") or 0
            sao = ty_le_sao(p.get("reviews_per_rating"))
            diem = rating_hieu_chinh(p)
            nhan_xet: list[str] = []
            phan_cuc = False
            if sao:
                xau = sao["1"] + sao["2"]
                if sao["1"] >= 0.10 and sao["5"] >= 0.55:
                    phan_cuc = True
                    nhan_xet.append(
                        f"Phân cực: {sao['5']:.0%} 5 sao nhưng {sao['1']:.0%} 1 sao, trải nghiệm không ổn định, nên đọc review xấu."
                    )
                elif xau >= 0.15:
                    nhan_xet.append(f"Tỷ lệ đánh giá xấu cao: {xau:.0%} là 1-2 sao.")
                elif sao["5"] >= 0.8 and n >= 100:
                    nhan_xet.append(f"Đánh giá đồng đều tốt: {sao['5']:.0%} là 5 sao.")
            if n and n < 30:
                nhan_xet.append("Ít review, rating chưa đủ tin cậy.")

            reviews = [r for r in p.get("reviews") or [] if isinstance(r, dict)]
            ngay = sorted((r.get("date") for r in reviews if r.get("date")), reverse=True)
            moi_nhat = ngay[0] if ngay else None
            so_ngay = None
            if moi_nhat:
                try:
                    so_ngay = (date.today() - date.fromisoformat(moi_nhat[:10])).days
                except ValueError:
                    so_ngay = None
            if so_ngay is not None and so_ngay > 365:
                nhan_xet.append(f"Review mới nhất đã cách đây {so_ngay} ngày, thông tin có thể cũ.")
            xau_nhat = sorted((r for r in reviews if (r.get("rating") or 5) <= 3), key=lambda r: r.get("rating") or 0)

            hang = await _thu_hang_cung_loai(p, diem)
            if hang and hang["percentile"] >= 90:
                nhan_xet.append(f"Thuộc top {100 - hang['percentile']}% {hang['category']} cùng điểm đến.")

            return drop_empty(
                {
                    "status": "success",
                    "place": {"name": p.get("title"), "place_id": p.get("place_id"), "category": p.get("category")},
                    "rating": p.get("rating") or None,
                    "reviews": n,
                    "rating_adjusted": diem,
                    "confidence": _do_tin_cay(n),
                    "star_distribution_pct": {k: round(v * 100, 1) for k, v in sao.items()} if sao else None,
                    "polarized": phan_cuc,
                    "rank_in_category": hang,
                    "latest_review_date": moi_nhat,
                    "days_since_latest_review": so_ngay,
                    "low_star_reviews": [
                        {"rating": r.get("rating"), "date": r.get("date"), "text": (r.get("text") or "")[:300]}
                        for r in xau_nhat[:3]
                    ]
                    or None,
                    "insights": nhan_xet or None,
                    "note": "Review đã lưu chỉ 5-8 bài mỗi điểm; phân bố sao tính trên toàn bộ review Google.",
                    "other_matches": p.get("_other_matches"),
                    "data_source": NGUON_DB,
                    "data_as_of": str(p.get("fetched_at") or "")[:10] or None,
                }
            )

        except TravelDbError as e:
            logger.error("rating_insights đọc DB thất bại", error=str(e))
            return loi_db(e, input_context)
        except Exception as e:
            logger.error("rating_insights thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không phân tích được rating", exception=e, input_context=input_context
            )

    return rating_insights
