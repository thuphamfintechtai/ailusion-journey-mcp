"""Tool lấy chi tiết một địa điểm Google Maps: giờ mở cửa, giờ đông khách, tiếp cận, thời gian ở lại."""

import re
from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.serpapi_client import SerpApiError, serpapi_client
from ..services.travel_db import travel_db
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._db_common import NGUON_DB, THU_VI, fallback_db, muc_dong_theo_gio
from ._serpapi_common import khong_co_ket_qua, loi_serpapi, tham_so_vn

logger = structlog.get_logger(__name__)


SO_ANH_TOI_DA = 6


def anh_serpapi(p: dict[str, Any]) -> list[str] | None:
    """Tối đa 6 URL ảnh của place_results SerpApi: `images[].thumbnail`; SerpApi nay thường không
    trả `images` nữa thì lấy ảnh khách chụp trong `user_reviews.most_relevant[].images` (lần lượt
    mỗi review một ảnh cho đa dạng). Chỉ lấy link ảnh, không lấy tên người viết."""
    ds = [a.get("thumbnail") for a in p.get("images") or [] if isinstance(a, dict) and a.get("thumbnail")]
    if len(ds) < SO_ANH_TOI_DA:
        theo_review = [
            [a.get("thumbnail") for a in r.get("images") or [] if isinstance(a, dict) and a.get("thumbnail")]
            for r in (p.get("user_reviews") or {}).get("most_relevant") or []
            if isinstance(r, dict)
        ]
        for vong in range(max((len(x) for x in theo_review), default=0)):
            for anh in theo_review:
                if vong < len(anh) and anh[vong] not in ds:
                    ds.append(anh[vong])
            if len(ds) >= SO_ANH_TOI_DA:
                break
    return ds[:SO_ANH_TOI_DA] or None


def _gon_gio_dong_khach(popular_times: dict[str, Any] | None) -> dict[str, Any] | None:
    """Rút `popular_times` về {thứ: [[giờ, mức đông 0-100], ...]} bỏ giờ đóng cửa."""
    if not popular_times:
        return None
    theo_ngay = {}
    for ngay, cac_gio in (popular_times.get("graph_results") or {}).items():
        diem = [
            [g.get("time"), g.get("busyness_score")]
            for g in cac_gio
            if g.get("busyness_score")
        ]
        if diem:
            theo_ngay[ngay] = diem
    return drop_empty(
        {
            "live": (popular_times.get("live_hash") or {}).get("info"),
            "by_day": theo_ngay or None,
        }
    ) or None


# "People typically spend 45 min to 1.5 hr here" / "Mọi người thường dành 1-2 giờ ở đây"
_SO_DON_VI = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:-|–|to|đến|tới)?\s*(\d+(?:[.,]\d+)?)?\s*"
    r"(min|mins|minutes|phút|hr|hrs|hour|hours|giờ|tiếng|h)\b",
    re.IGNORECASE,
)
_GIO = ("hr", "hrs", "hour", "hours", "giờ", "tiếng", "h")


def thoi_gian_o_lai(text: str | None) -> dict[str, int] | None:
    """Câu "Mọi người thường dành …" của Google -> {"low": phút, "high": phút}.

    - "45 min to 1.5 hr" -> 45, 90 · "1-2 giờ" -> 60, 120 · "up to 1 hr" / "tối đa 1 giờ" -> 0..60
      được ghi low = high = 60 (Google chỉ cho mốc trên) · "15 phút" -> 15, 15.
    Không đọc được số nào thì None.
    """
    if not text:
        return None
    phut: list[int] = []
    for a, b, don_vi in _SO_DON_VI.findall(text):
        he_so = 60 if don_vi.lower() in _GIO else 1
        for so in (a, b):
            if so:
                phut.append(round(float(so.replace(",", ".")) * he_so))
    if not phut:
        return None
    return {"low": min(phut), "high": max(phut)}


def _tim_time_spent(du_lieu: Any) -> str | None:
    """Chuỗi `time_spent` ở bất kỳ tầng nào của popular_times đã lưu (cấu trúc tuỳ lần cào)."""
    if isinstance(du_lieu, dict):
        if isinstance(du_lieu.get("time_spent"), str):
            return du_lieu["time_spent"]
        du_lieu = list(du_lieu.values())
    if isinstance(du_lieu, list):
        for x in du_lieu:
            if (kq := _tim_time_spent(x)) is not None:
                return kq
    return None


async def _ghi_anh(id_: str, thumbnail: str | None, images: list[str] | None) -> None:
    """Lưu ảnh vừa lấy từ SerpApi vào DB để lần sau đọc DB là đủ ảnh. Lỗi chỉ ghi log."""
    if not travel_db.enabled or not (thumbnail or images):
        return
    try:
        if images:
            await travel_db.save_images(id_, images)
        if thumbnail:
            await travel_db.save_thumbnail(id_, thumbnail)
    except Exception as e:  # noqa: BLE001 — ghi ảnh hỏng không làm hỏng kết quả
        logger.warning("get_place_details không ghi được ảnh vào DB", error=str(e))


async def _tu_db(place_id: str) -> dict[str, Any] | None:
    p = await travel_db.find_place(place_id)
    if not p:
        return None
    dong_khach = {
        THU_VI[thu]: [[f"{g:02d}:00", v] for g, v in sorted(muc.items()) if v]
        for thu in range(7)
        if (muc := muc_dong_theo_gio(p.get("popular_times"), thu))
    }
    return {
        "status": "success",
        "place": {
            "name": p.get("title"),
            "place_id": p.get("place_id"),
            "data_id": p.get("data_id"),
            # rating 0 với 0 review là chưa có đánh giá, không phải bị chấm 0 sao.
            "rating": p.get("rating") or None,
            "reviews": p.get("review_count"),
            "reviews_per_rating": p.get("reviews_per_rating"),
            "price": p.get("price_range"),
            "type": p.get("category"),
            "address": p.get("address"),
            "gps": {"latitude": p["lat"], "longitude": p["lng"]} if p.get("lat") is not None else None,
            "phone": p.get("phone"),
            "website": p.get("website"),
            "maps_url": p.get("maps_url"),
            "hours": p.get("open_hours"),
            "popular_times": {"by_day": dong_khach} if dong_khach else None,
            "time_spent": _tim_time_spent(p.get("popular_times")),
            "stay_min": thoi_gian_o_lai(_tim_time_spent(p.get("popular_times"))),
            "attributes": p.get("attributes"),
            "attributes_not_available": p.get("attributes_not_available"),
            "description": p.get("description") or p.get("summary"),
            "thumbnail": p.get("thumbnail"),
            "images": [u for u in p.get("images") or [] if isinstance(u, str) and u][:SO_ANH_TOI_DA] or None,
        },
        "other_matches": p.get("_other_matches"),
        "data_as_of": str(p.get("fetched_at") or "")[:10] or None,
    }


def register_get_place_details_tool(mcp: FastMCP):
    @mcp.tool(
        name="get_place_details",
        title="Get Place Details (Chi tiết địa điểm)",
        description=(
            "Lấy chi tiết một địa điểm Google Maps theo `place_id` (lấy từ search_places): "
            "giờ mở cửa từng ngày, giờ đông khách theo giờ (0-100), thời gian khách thường ở lại "
            "(`time_spent` nguyên văn Google + `stay_min` {low, high} phút), mức giá, số điện thoại, "
            "website, và nhóm thuộc tính `attributes` (tiếp cận xe lăn, có món chay, hợp "
            "trẻ em, chỗ đỗ xe, thanh toán...) khi Google có dữ liệu.\n\n"
            "Khi nào dùng: kiểm tra một điểm có mở vào giờ dự định không, tránh giờ đông, "
            "kiểm tra ràng buộc tiếp cận / ăn kiêng.\n"
            "Mỗi lần gọi tốn 1 lượt quota — chỉ gọi cho điểm đã lọt vào danh sách ngắn. "
            "Khi SerpApi lỗi / hết quota tool tự trả dữ liệu đã lưu (`data_source`='ailusion_db').\n"
            "`source`='auto': dùng dữ liệu đã lưu nếu đã có thời gian ở lại (`stay_min`), thiếu mới gọi "
            "SerpApi; 'db': chỉ đọc DB (không tốn quota)."
        ),
        tags={"journey", "serpapi", "maps"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "serpapi:google_maps"},
    )
    async def get_place_details(
        place_id: Annotated[
            str, Field(description="Google place_id, VD 'ChIJ...'. Lấy từ kết quả search_places.")
        ],
        source: Annotated[
            Literal["live", "auto", "db"],
            Field(description="'auto' (mặc định) đọc DB trước, thiếu mới gọi SerpApi; 'db' chỉ đọc DB; 'live' luôn gọi SerpApi."),
        ] = "auto",
    ) -> dict[str, Any]:
        input_context = {"place_id": place_id}
        try:
            if not place_id.strip():
                return format_validation_error("place_id", place_id, "không được để trống")

            if source != "live" and travel_db.enabled:
                da_luu = await _tu_db(place_id.strip())
                pl = (da_luu or {}).get("place") or {}
                if da_luu and (source == "db" or pl.get("stay_min")):
                    return drop_empty({**da_luu, "data_source": NGUON_DB})
                if source == "db":
                    return {"status": "success", "place_id": place_id, "place": None, "note": "DB chưa có chỗ này"}

            data = await serpapi_client.search(
                "google_maps", type="place", place_id=place_id.strip(), **tham_so_vn()
            )

            ghi_chu = khong_co_ket_qua(data)
            p = data.get("place_results") or {}
            if not p:
                return drop_empty(
                    {"status": "success", "place_id": place_id, "place": None, "note": ghi_chu or "Không tìm thấy địa điểm"}
                )

            anh = anh_serpapi(p)
            await _ghi_anh(place_id.strip(), p.get("thumbnail"), anh)

            return drop_empty(
                {
                    "status": "success",
                    "place": {
                        "name": p.get("title"),
                        "place_id": p.get("place_id") or place_id,
                        "data_id": p.get("data_id"),
                        "rating": p.get("rating"),
                        "reviews": p.get("reviews"),
                        "price": p.get("price"),
                        "type": p.get("type"),
                        "address": p.get("address"),
                        "gps": p.get("gps_coordinates"),
                        "phone": p.get("phone"),
                        "website": p.get("website"),
                        "open_state": p.get("open_state"),
                        "hours": p.get("hours") or p.get("operating_hours"),
                        "popular_times": _gon_gio_dong_khach(p.get("popular_times")),
                        # Dòng "Mọi người thường dành …": chuỗi gốc + số phút ít / lâu đã đọc.
                        "time_spent": (p.get("popular_times") or {}).get("live_hash", {}).get("time_spent"),
                        "stay_min": thoi_gian_o_lai(
                            ((p.get("popular_times") or {}).get("live_hash") or {}).get("time_spent")
                        ),
                        "attributes": p.get("extensions"),
                        "description": p.get("description"),
                        "thumbnail": p.get("thumbnail"),
                        "images": anh,
                        "review_topics": [
                            {"keyword": t.get("keyword"), "mentions": t.get("mentions")}
                            for t in (p.get("user_reviews") or {}).get("summary") or []
                        ][:10],
                    },
                    "note": ghi_chu,
                }
            )

        except SerpApiError as e:
            logger.error("get_place_details gọi SerpApi thất bại", error=str(e))
            return await fallback_db(
                "get_place_details", loi_serpapi(e, input_context), lambda: _tu_db(place_id.strip())
            )
        except Exception as e:
            logger.error("get_place_details thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không lấy được chi tiết địa điểm", exception=e, input_context=input_context
            )

    return get_place_details
