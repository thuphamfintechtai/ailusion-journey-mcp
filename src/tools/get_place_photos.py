"""Tool lấy ảnh đại diện cho nhiều địa điểm: đọc DB trước, thiếu thì tra SerpApi rồi ghi lại DB."""

import asyncio
from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.serpapi_client import SerpApiError, serpapi_client
from ..services.travel_db import TravelDbError, travel_db
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ._serpapi_common import tham_so_vn

logger = structlog.get_logger(__name__)

# Mỗi lần tra tốn 1 lượt quota: giới hạn số lần tra / lần gọi và số lần tra song song.
SO_LAN_TRA_TOI_DA = 12
SO_TRA_SONG_SONG = 4


def _tham_so_tra(id_: str, dong: dict[str, Any] | None) -> dict[str, str] | None:
    """Tham số SerpApi google_maps type=place cho một id; None nếu không tra được (chỉ có tên)."""
    place_id = id_ if id_.startswith("ChIJ") else (dong or {}).get("place_id")
    if place_id:
        return {"place_id": place_id}
    cid = id_ if id_.isdigit() else (dong or {}).get("cid")
    return {"data_cid": cid} if cid else None


async def _tu_db(ids: list[str], destination: str | None) -> list[dict[str, Any] | None]:
    if not travel_db.enabled:
        return [None] * len(ids)
    try:
        return await travel_db.find_places(ids, destination, columns="thumbnail, place_id, cid")
    except TravelDbError as e:
        logger.warning("get_place_photos không đọc được DB", error=str(e))
        return [None] * len(ids)


async def _tra_serpapi(tham_so: dict[str, str], khoa_cho: asyncio.Semaphore) -> str | None:
    async with khoa_cho:
        try:
            data = await serpapi_client.search("google_maps", type="place", **tham_so, **tham_so_vn())
        except SerpApiError as e:
            logger.warning("get_place_photos tra SerpApi thất bại", error=str(e))
            return None
    return (data.get("place_results") or {}).get("thumbnail") or None


async def _ghi_db(id_: str, url: str) -> None:
    try:
        await travel_db.save_thumbnail(id_, url)
    except TravelDbError as e:
        logger.warning("get_place_photos không ghi được ảnh vào DB", error=str(e))


def register_get_place_photos_tool(mcp: FastMCP):
    @mcp.tool(
        name="get_place_photos",
        title="Get Place Photos (Ảnh địa điểm)",
        description=(
            "Lấy ảnh đại diện (URL) cho nhiều địa điểm một lần, để hiển thị lịch trình.\n\n"
            "`places`: place_id ('ChIJ...') lấy từ browse_places / find_places_by_needs / search_places; "
            "cũng nhận cid, data_id ('0x...') hoặc tên địa điểm (nên kèm `destination`).\n"
            "Đọc dữ liệu đã lưu trước; điểm chưa có ảnh thì tra SerpApi (tối đa 12 điểm / lần gọi, "
            "mỗi điểm tốn 1 lượt quota) rồi lưu lại cho lần sau. `source`='db' chỉ đọc DB (không tốn quota).\n"
            "Trả `photos` {id: url hoặc null}, `from_db`, `from_serpapi`."
        ),
        tags={"journey", "serpapi", "maps"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "ailusion_db+serpapi:google_maps"},
    )
    async def get_place_photos(
        places: Annotated[
            list[str],
            Field(min_length=1, max_length=60, description="Danh sách place_id (hoặc cid / data_id / tên)."),
        ],
        destination: Annotated[
            str | None, Field(description="Điểm đến, giúp khớp đúng khi truyền tên địa điểm.")
        ] = None,
        source: Annotated[
            Literal["auto", "db"],
            Field(description="'auto' (mặc định) đọc DB, thiếu ảnh mới tra SerpApi; 'db' chỉ đọc DB."),
        ] = "auto",
    ) -> dict[str, Any]:
        input_context = {"places": places, "destination": destination}
        try:
            ids = list(dict.fromkeys(s.strip() for s in places if s and s.strip()))
            if not ids:
                return format_validation_error("places", places, "không được để trống")

            dong_db = dict(zip(ids, await _tu_db(ids, destination)))
            anh: dict[str, str | None] = {i: (dong_db[i] or {}).get("thumbnail") or None for i in ids}
            tu_db = sum(1 for v in anh.values() if v)

            tu_serpapi = 0
            if source == "auto" and serpapi_client.api_keys:
                can_tra = [
                    (i, ts) for i in ids if not anh[i] and (ts := _tham_so_tra(i, dong_db[i]))
                ][:SO_LAN_TRA_TOI_DA]
                khoa_cho = asyncio.Semaphore(SO_TRA_SONG_SONG)
                ket_qua = await asyncio.gather(*(_tra_serpapi(ts, khoa_cho) for _, ts in can_tra))
                for (i, _), url in zip(can_tra, ket_qua):
                    if not url:
                        continue
                    anh[i] = url
                    tu_serpapi += 1
                    if travel_db.enabled:
                        # Ghi theo cid của dòng DB nếu đã khớp (kể cả khi khớp theo tên), không thì theo id.
                        await _ghi_db((dong_db[i] or {}).get("cid") or i, url)

            return {"status": "success", "photos": anh, "from_db": tu_db, "from_serpapi": tu_serpapi}

        except Exception as e:
            logger.error("get_place_photos thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không lấy được ảnh địa điểm", exception=e, input_context=input_context
            )

    return get_place_photos
