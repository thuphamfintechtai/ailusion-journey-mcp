"""Tool tìm địa điểm quanh một vị trí ở Việt Nam bằng Vietmap — thay search_places khi không cần rating."""

import asyncio
from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.vietmap_client import VietmapError, vietmap_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._vietmap_common import gon_diem, loi_vietmap

logger = structlog.get_logger(__name__)


def register_find_nearby_tool(mcp: FastMCP):
    @mcp.tool(
        name="find_nearby",
        title="Find Nearby (Tìm quanh đây)",
        description=(
            "Tìm địa điểm quanh một vị trí ở Việt Nam: quán ăn, quán chay, cà phê, khách sạn, "
            "bệnh viện, nhà thuốc, cây xăng, ATM, chợ, điểm tham quan... Dữ liệu Vietmap "
            "(9 triệu POI, địa chỉ theo đơn vị hành chính sau 1/7/2025).\n\n"
            "Khi nào dùng: cần danh sách ứng viên gần một điểm, khoảng cách, địa chỉ, toạ độ. "
            "Đây là lựa chọn MẶC ĐỊNH để tìm địa điểm vì không tốn quota SerpApi.\n"
            "Khi nào KHÔNG dùng: cần rating, số review, mức giá, giờ mở cửa -> dùng "
            "search_places / get_place_details (tốn quota) cho vài điểm trong danh sách ngắn.\n\n"
            "Vị trí tâm: truyền `lat`/`lng`, hoặc `near` là tên / địa chỉ (VD 'phố cổ Hội An').\n"
            "Trả về tối đa 10 điểm, `distance_km` tính từ tâm, `ref_id` dùng lại được cho "
            "get_route / distance_matrix / optimize_route."
        ),
        tags={"journey", "vietmap", "maps"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "vietmap:autocomplete/v4 + place/v4"},
    )
    async def find_nearby(
        query: Annotated[str, Field(description="Thứ cần tìm, VD 'quán chay', 'cà phê', 'cây xăng', 'nhà thuốc'.")],
        near: Annotated[
            str | None, Field(description="Tâm tìm kiếm dạng tên / địa chỉ, VD 'Chợ Bến Thành'. Bỏ trống nếu có lat/lng.")
        ] = None,
        lat: Annotated[float | None, Field(ge=8, le=24, description="Vĩ độ tâm tìm kiếm.")] = None,
        lng: Annotated[float | None, Field(ge=102, le=110, description="Kinh độ tâm tìm kiếm.")] = None,
        radius_m: Annotated[int, Field(ge=100, le=50000, description="Bán kính tìm, mét.")] = 2000,
        limit: Annotated[int, Field(ge=1, le=10, description="Số điểm tối đa.")] = 10,
        with_coordinates: Annotated[
            bool, Field(description="Lấy toạ độ từng điểm (mỗi điểm thêm 1 lượt gọi Vietmap).")
        ] = True,
    ) -> dict[str, Any]:
        input_context = {"query": query, "near": near, "lat": lat, "lng": lng}
        try:
            if not query.strip():
                return format_validation_error("query", query, "không được để trống")
            if (lat is None) != (lng is None):
                return format_validation_error("lat/lng", input_context, "phải truyền cả hai hoặc bỏ cả hai")
            if lat is None and not (near and near.strip()):
                return format_validation_error("near", near, "cần `near` hoặc `lat`/`lng`")

            tam = None
            if lat is None:
                tam = await vietmap_client.resolve(near)  # type: ignore[arg-type]
                lat, lng = tam.lat, tam.lng

            ket_qua = await vietmap_client.autocomplete(
                query.strip(),
                focus=f"{lat},{lng}",
                circle_center=f"{lat},{lng}",
                circle_radius=radius_m,
                layers="POI",
                display_type=1,
            )
            ket_qua = ket_qua[:limit]

            toa_do: list[dict[str, Any] | None] = [None] * len(ket_qua)
            if with_coordinates and ket_qua:
                chi_tiet = await asyncio.gather(
                    *(vietmap_client.place(r["ref_id"]) for r in ket_qua), return_exceptions=True
                )
                toa_do = [None if isinstance(c, BaseException) else c for c in chi_tiet]

            return drop_empty(
                {
                    "status": "success",
                    "query": query,
                    "center": gon_diem(tam) if tam else {"lat": lat, "lng": lng},
                    "radius_m": radius_m,
                    "total_found": len(ket_qua),
                    "places": [
                        {
                            "name": r.get("name"),
                            "address": r.get("address"),
                            "distance_km": round(r["distance"], 2) if r.get("distance") is not None else None,
                            "lat": (c or {}).get("lat"),
                            "lng": (c or {}).get("lng"),
                            "categories": r.get("categories") or None,
                            "ref_id": r.get("ref_id"),
                        }
                        for r, c in zip(ket_qua, toa_do)
                    ],
                    "note": None if ket_qua else "Không thấy địa điểm phù hợp, thử tăng radius_m hoặc đổi từ khoá",
                }
            )

        except VietmapError as e:
            logger.error("find_nearby gọi Vietmap thất bại", error=str(e))
            return loi_vietmap(e, input_context)
        except Exception as e:
            logger.error("find_nearby thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không tìm được địa điểm", exception=e, input_context=input_context
            )

    return find_nearby
