"""Tool tra toạ độ và chuẩn hoá địa chỉ Việt Nam (cũ <-> mới sau sáp nhập 1/7/2025) bằng Vietmap."""

from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.travel_db import TravelDbError, khoang_cach_km, travel_db
from ..services.vietmap_client import VietmapError, diem_tu_db, vietmap_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._vietmap_common import loi_vietmap

logger = structlog.get_logger(__name__)


def _hanh_chinh(r: dict[str, Any]) -> dict[str, Any]:
    """Rút phường / quận / tỉnh từ `boundaries` (type 2 = phường xã, 1 = quận huyện, 0 = tỉnh)."""
    cap = {b.get("type"): b.get("full_name") for b in r.get("boundaries") or []}
    return {"ward": cap.get(2), "district": cap.get(1), "province": cap.get(0)}


def _gon(r: dict[str, Any], toa_do: dict[str, Any] | None) -> dict[str, Any]:
    cu = r.get("data_old") or {}
    return {
        "name": r.get("name") or None,
        "address_new": r.get("display"),
        "admin_new": _hanh_chinh(r),
        "address_old": cu.get("display"),
        "admin_old": _hanh_chinh(cu) if cu else None,
        "lat": (toa_do or {}).get("lat") or r.get("lat"),
        "lng": (toa_do or {}).get("lng") or r.get("lng"),
        "ref_id": r.get("ref_id"),
    }


async def _tu_db(address: str | None, lat: float | None, lng: float | None) -> dict[str, Any] | None:
    """Vietmap lỗi (VD hết credit HTTP 423): tra trong dữ liệu đã lưu.
    Theo địa chỉ: địa điểm trùng tên, không có thì tâm điểm đến. Ngược (toạ độ): địa chỉ của
    địa điểm đã lưu gần nhất, kèm khoảng cách tới đó."""
    if lat is not None and lng is not None:
        try:
            ds = [p for p in await travel_db.places() if p.get("lat") is not None and p.get("address")]
        except TravelDbError:
            return None
        if not ds:
            return None
        gan = min(ds, key=lambda p: khoang_cach_km(lat, lng, p["lat"], p["lng"]))
        km = khoang_cach_km(lat, lng, gan["lat"], gan["lng"])
        return {
            "status": "success",
            "mode": "reverse",
            "source": "ailusion_db",
            "results": [{"name": gan.get("title"), "address_new": gan.get("address"), "lat": lat, "lng": lng}],
            "note": f"Vietmap lỗi; địa chỉ của chỗ đã lưu gần nhất ({gan.get('title')}, cách {km:.2f} km).",
        }
    d = await diem_tu_db(address or "")
    if d is None:
        return None
    return {
        "status": "success",
        "mode": "forward",
        "query": address,
        "source": "ailusion_db",
        "results": [{"name": d.label, "lat": round(d.lat, 6), "lng": round(d.lng, 6)}],
        "note": f"Vietmap lỗi; toạ độ theo dữ liệu đã lưu: {d.label}.",
    }


def register_geocode_vn_tool(mcp: FastMCP):
    @mcp.tool(
        name="geocode_vn",
        title="Geocode VN (Toạ độ & chuẩn hoá địa chỉ)",
        description=(
            "Tra toạ độ và chuẩn hoá địa chỉ ở Việt Nam, trả CẢ địa chỉ theo đơn vị hành "
            "chính mới (sau sáp nhập 1/7/2025, 2 cấp phường - tỉnh) LẪN địa chỉ cũ (3 cấp).\n\n"
            "- Địa chỉ / tên địa điểm -> toạ độ + địa chỉ mới + địa chỉ cũ: truyền `address`.\n"
            "- Toạ độ -> địa chỉ: truyền `lat` + `lng`.\n\n"
            "Khi nào dùng: chuẩn hoá địa chỉ người dùng gõ (thường là địa chỉ cũ) trước khi "
            "so khớp hay dò mâu thuẫn; lấy toạ độ cho một điểm; biết một toạ độ thuộc phường "
            "/ tỉnh nào. VD '12 Lý Tự Trọng, P. Bến Nghé, Q.1' -> 'Phường Sài Gòn, TP.HCM'."
        ),
        tags={"journey", "vietmap", "maps"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "vietmap:search/v4 + place/v4 + reverse/v4"},
    )
    async def geocode_vn(
        address: Annotated[
            str | None, Field(description="Địa chỉ hoặc tên địa điểm, cũ hay mới đều được.")
        ] = None,
        lat: Annotated[float | None, Field(ge=8, le=24, description="Vĩ độ (khi tra ngược toạ độ -> địa chỉ).")] = None,
        lng: Annotated[float | None, Field(ge=102, le=110, description="Kinh độ, đi cùng `lat`.")] = None,
        limit: Annotated[int, Field(ge=1, le=5, description="Số kết quả tối đa khi tra theo địa chỉ.")] = 1,
    ) -> dict[str, Any]:
        input_context = {"address": address, "lat": lat, "lng": lng}
        try:
            if (lat is None) != (lng is None):
                return format_validation_error("lat/lng", input_context, "phải truyền cả hai hoặc bỏ cả hai")

            if lat is not None:
                kq = await vietmap_client.reverse(lat, lng, display_type=5)  # type: ignore[arg-type]
                return drop_empty(
                    {
                        "status": "success",
                        "mode": "reverse",
                        "results": [_gon(r, None) for r in kq[:1]],
                        "note": None if kq else "Không xác định được địa chỉ cho toạ độ này",
                    }
                )

            if not (address and address.strip()):
                return format_validation_error("address", address, "cần `address` hoặc `lat`/`lng`")

            kq = (await vietmap_client.search(address.strip(), display_type=5))[:limit]
            ket_qua = []
            for r in kq:
                chi_tiet = await vietmap_client.place(r["ref_id"])
                ket_qua.append(_gon(r, chi_tiet))
            return drop_empty(
                {
                    "status": "success",
                    "mode": "forward",
                    "query": address,
                    "results": ket_qua,
                    "note": None if ket_qua else "Không tìm thấy địa chỉ, thử ghi đầy đủ hơn (số nhà, đường, phường, tỉnh)",
                }
            )

        except VietmapError as e:
            logger.error("geocode_vn gọi Vietmap thất bại", error=str(e))
            if kq := await _tu_db(address.strip() if address else None, lat, lng):
                return kq
            return loi_vietmap(e, input_context)
        except Exception as e:
            logger.error("geocode_vn thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không tra được địa chỉ", exception=e, input_context=input_context
            )

    return geocode_vn
