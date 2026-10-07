"""Tool lấy review Google Maps của một địa điểm (Google Maps Reviews qua SerpApi)."""

from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.serpapi_client import SerpApiError, serpapi_client
from ..services.travel_db import travel_db
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ..utils.text import tach_tu
from ._db_common import fallback_db, khop_du_tu_khoa
from ._serpapi_common import khong_co_ket_qua, loi_serpapi, nhan_lua_chon

logger = structlog.get_logger(__name__)

_SORT_BY = {
    "relevant": "qualityScore",
    "newest": "newestFirst",
    "highest": "ratingHigh",
    "lowest": "ratingLow",
}
_DO_DAI_TOI_DA = 600  # ký tự mỗi review, đủ để trích khía cạnh mà không phình token


def _gon_review(r: dict[str, Any]) -> dict[str, Any]:
    noi_dung = r.get("snippet") or (r.get("extracted_snippet") or {}).get("original") or ""
    return {
        "rating": r.get("rating"),
        "date": r.get("iso_date") or r.get("date"),
        "text": noi_dung[:_DO_DAI_TOI_DA],
        "likes": r.get("likes") or None,
        "details": r.get("details"),
        "owner_replied": bool(r.get("response")) or None,
    }


async def _tu_db(place_id: str, sort_by: str, query: str | None) -> dict[str, Any] | None:
    """Review đã lưu (vài review mỗi điểm) và phân bố số sao."""
    p = await travel_db.find_place(place_id)
    if not p:
        return None
    reviews = [r for r in p.get("reviews") or [] if isinstance(r, dict)]
    toks = tach_tu(query)
    if toks:
        reviews = [r for r in reviews if khop_du_tu_khoa(toks, r.get("text"))]
    if sort_by == "newest":
        reviews.sort(key=lambda r: r.get("date") or "", reverse=True)
    elif sort_by in ("highest", "lowest"):
        reviews.sort(key=lambda r: r.get("rating") or 0, reverse=sort_by == "highest")
    return {
        "status": "success",
        "place": drop_empty(
            {"name": p.get("title"), "rating": p.get("rating"), "reviews": p.get("review_count"), "address": p.get("address")}
        ),
        "reviews_per_rating": p.get("reviews_per_rating"),
        "returned": len(reviews),
        "reviews": [
            {"rating": r.get("rating"), "date": r.get("date"), "text": (r.get("text") or "")[:_DO_DAI_TOI_DA]}
            for r in reviews
        ],
        "note": "Chỉ có vài review đã lưu cho mỗi địa điểm, không phân trang và không có `topics`.",
        "data_as_of": str(p.get("fetched_at") or "")[:10] or None,
    }


def register_get_place_reviews_tool(mcp: FastMCP):
    @mcp.tool(
        name="get_place_reviews",
        title="Get Place Reviews (Review địa điểm)",
        description=(
            "Lấy review Google Maps của một địa điểm theo `place_id`: rating, ngày, nội dung, "
            "và `topics` (cụm từ được nhắc nhiều kèm số lượt, VD 'đỗ xe', 'phục vụ').\n\n"
            "Khi nào dùng: kiểm chứng trải nghiệm thật (chặt chém, ồn, chỗ cho bé, đường "
            "dốc, đỗ ô tô...), hoặc lọc review theo chủ đề bằng `query`.\n"
            "Mỗi trang ~8-20 review và tốn 1 lượt quota; mặc định chỉ lấy 1 trang. Khi SerpApi lỗi / "
            "hết quota tool tự trả vài review đã lưu (`data_source`='ailusion_db')."
        ),
        tags={"journey", "serpapi", "reviews"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "serpapi:google_maps_reviews"},
    )
    async def get_place_reviews(
        place_id: Annotated[str, Field(description="Google place_id lấy từ search_places.")],
        sort_by: Annotated[
            Literal["relevant", "newest", "highest", "lowest"],
            Field(
                description="Thứ tự review. 'newest' để xem tình trạng gần đây, 'lowest' để tìm vấn đề.",
                json_schema_extra=nhan_lua_chon(
                    {
                        "relevant": "liên quan nhất",
                        "newest": "mới nhất",
                        "highest": "điểm cao nhất",
                        "lowest": "điểm thấp nhất",
                    }
                ),
            ),
        ] = "relevant",
        query: Annotated[
            str | None,
            Field(description="Chỉ lấy review nhắc tới cụm từ này, VD 'đỗ xe', 'trẻ em', 'xe lăn'."),
        ] = None,
        pages: Annotated[
            int, Field(ge=1, le=3, description="Số trang review (mỗi trang tốn 1 lượt quota).")
        ] = 1,
    ) -> dict[str, Any]:
        input_context = {"place_id": place_id, "query": query}
        try:
            if not place_id.strip():
                return format_validation_error("place_id", place_id, "không được để trống")

            reviews: list[dict[str, Any]] = []
            topics: list[dict[str, Any]] = []
            place_info: dict[str, Any] = {}
            ghi_chu = None
            token = None
            for trang in range(pages):
                data = await serpapi_client.search(
                    "google_maps_reviews",
                    place_id=place_id.strip(),
                    hl="vi",
                    sort_by=_SORT_BY[sort_by],
                    query=query.strip() if query else None,
                    next_page_token=token,
                    num=20 if token else None,  # SerpApi chỉ nhận `num` từ trang 2
                )
                ghi_chu = khong_co_ket_qua(data)
                if trang == 0:
                    place_info = data.get("place_info") or {}
                    topics = [
                        {"keyword": t.get("keyword"), "mentions": t.get("mentions")}
                        for t in data.get("topics") or []
                    ]
                reviews += [_gon_review(r) for r in data.get("reviews") or []]
                token = (data.get("serpapi_pagination") or {}).get("next_page_token")
                if not token:
                    break

            return drop_empty(
                {
                    "status": "success",
                    "place": drop_empty(
                        {
                            "name": place_info.get("title"),
                            "rating": place_info.get("rating"),
                            "reviews": place_info.get("reviews"),
                            "address": place_info.get("address"),
                        }
                    )
                    or None,
                    "topics": topics[:15],
                    "returned": len(reviews),
                    "reviews": reviews,
                    "note": ghi_chu,
                }
            )

        except SerpApiError as e:
            logger.error("get_place_reviews gọi SerpApi thất bại", error=str(e))
            return await fallback_db(
                "get_place_reviews", loi_serpapi(e, input_context), lambda: _tu_db(place_id.strip(), sort_by, query)
            )
        except Exception as e:
            logger.error("get_place_reviews thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không lấy được review", exception=e, input_context=input_context
            )

    return get_place_reviews
