"""Tool đổi đơn vị hành chính trước <-> sau sáp nhập 1/7/2025 (thư viện vietnam-provinces, offline)."""

from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services import admin_units as au
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._serpapi_common import nhan_lua_chon

logger = structlog.get_logger(__name__)

_TOI_DA_UNG_VIEN = 5


def _ten_tinh(code: Any) -> str | None:
    p = au.province_by_code(int(code)) if code is not None else None
    return p.name if p else None


def _phuong_moi(w: Any) -> dict[str, Any]:
    return {"ward": w.name, "ward_code": int(w.code), "province": _ten_tinh(w.province_code), "province_code": int(w.province_code)}


def _phuong_cu(w: Any) -> dict[str, Any]:
    quan = au.legacy_district(int(w.district_code)) if getattr(w, "district_code", None) else None
    tinh = au.legacy_province(int(quan.province_code)) if quan else None
    return {
        "ward": w.name,
        "ward_code": int(w.code),
        "district": quan.name if quan else None,
        "province": tinh.name if tinh else None,
    }


def cu_sang_moi(ward: str | None, district: str | None, province: str | None) -> dict[str, Any]:
    tinh = au.legacy_find_province(province) if province else None
    if province and not tinh:
        return {"error": f"Không tìm thấy tỉnh cũ '{province}' (63 tỉnh / thành trước 1/7/2025)"}
    ma_tinh = int(tinh.code) if tinh else None
    quan = au.legacy_find_district(district, ma_tinh) if district else None
    if district and not quan:
        return {"error": f"Không tìm thấy quận / huyện cũ '{district}'"}

    if ward:
        trung = au.trung_ten(au.legacy_search_wards(ward, int(quan.code) if quan else None, ma_tinh), ward)
        if not trung:
            return {"error": f"Không tìm thấy phường / xã cũ '{ward}'"}
        if len(trung) > _TOI_DA_UNG_VIEN:
            return {
                "error": f"Có {len(trung)} phường / xã cũ tên '{ward}'; thêm `district` (quận / huyện cũ) hoặc `province`"
            }
        return {
            "results": [
                {"old": _phuong_cu(w), "new": [_phuong_moi(m) for m in au.new_from_legacy_ward(int(w.code))]}
                for w in trung
            ]
        }

    if quan:
        moi = au.new_from_legacy_district(int(quan.code))
        return {
            "old": {"district": quan.name, "province": tinh.name if tinh else _ten_tinh_cu(quan)},
            "new": {
                "province": _ten_tinh(moi[0].province_code) if moi else None,
                "wards": sorted(w.name for w in moi),
            },
            "note": "Quận / huyện đã bỏ từ 1/7/2025; đây là các phường / xã mới phủ lên địa bàn quận cũ.",
        }

    if tinh:
        moi = au.new_from_legacy_province(int(tinh.code))
        return {
            "old": {"province": tinh.name},
            "new": {"province": moi.name if moi else None, "province_code": int(moi.code) if moi else None},
        }

    return {"error": "Cần ít nhất một trong `ward`, `district`, `province`"}


def _ten_tinh_cu(quan: Any) -> str | None:
    t = au.legacy_province(int(quan.province_code))
    return t.name if t else None


def moi_sang_cu(ward: str | None, province: str | None) -> dict[str, Any]:
    tinh = au.find_province(province) if province else None
    if province and not tinh:
        return {"error": f"Không tìm thấy tỉnh / thành mới '{province}' (hiện có 34 tỉnh / thành)"}

    if not ward:
        if not tinh:
            return {"error": "Chiều mới -> cũ cần `ward` hoặc `province`"}
        return {
            "new": {"province": tinh.name},
            "merged_from_provinces": [p.name for p in tinh.get_legacy_sources()],
        }

    trung = au.trung_ten(au.search_wards(ward, int(tinh.code) if tinh else None), ward)
    if not trung:
        return {"error": f"Không tìm thấy phường / xã mới '{ward}'"}
    if len(trung) > _TOI_DA_UNG_VIEN:
        return {"error": f"Có {len(trung)} phường / xã mới tên '{ward}'; thêm `province`"}
    return {
        "results": [
            {
                "new": {"ward": w.name, "ward_code": int(w.code), "province": _ten_tinh(w.province_code)},
                "merged_from": [
                    {k: v for k, v in _phuong_cu(c).items() if k != "ward_code"} for c in au.legacy_sources(w)
                ],
            }
            for w in trung
        ]
    }


def register_convert_admin_unit_tool(mcp: FastMCP):
    @mcp.tool(
        name="convert_admin_unit",
        title="Convert Admin Unit (Đổi đơn vị hành chính cũ - mới)",
        description=(
            "Đổi đơn vị hành chính theo danh mục chính thức giữa trước và sau sáp nhập 1/7/2025 "
            "(63 -> 34 tỉnh, bỏ cấp quận / huyện). Tra offline, không tốn quota, trả về tức thì.\n\n"
            "- old_to_new: phường / xã cũ (+ quận / huyện cũ, tỉnh cũ) -> phường / xã mới và tỉnh "
            "mới. Chỉ truyền quận cũ -> các phường mới phủ địa bàn đó. Chỉ truyền tỉnh cũ -> tỉnh mới "
            "(VD 'Bà Rịa - Vũng Tàu' -> 'TP. Hồ Chí Minh').\n"
            "- new_to_old: phường / xã mới (+ tỉnh mới) -> các phường / xã cũ đã gộp thành nó. Chỉ "
            "truyền tỉnh mới -> các tỉnh cũ đã gộp thành nó.\n\n"
            "Khi nào dùng: chuẩn hoá địa chỉ cũ trong review / bài viết trước khi so khớp, giải thích "
            "cho người dùng địa danh cũ giờ thuộc đâu. Có số nhà + tên đường thì dùng geocode_vn.\n"
            "Tên phổ biến ('Phường 1', 'Xã Tân Hòa') trùng ở nhiều nơi: luôn truyền thêm quận cũ / tỉnh."
        ),
        tags={"journey", "admin", "address"},
        meta={"version": "2.0.0", "author": "Ailusion team", "data_source": "vietnam-provinces (offline)"},
    )
    async def convert_admin_unit(
        direction: Annotated[
            Literal["old_to_new", "new_to_old"],
            Field(
                description="Chiều đổi.",
                json_schema_extra=nhan_lua_chon({"old_to_new": "cũ sang mới", "new_to_old": "mới sang cũ"}),
            ),
        ] = "old_to_new",
        ward: Annotated[str | None, Field(description="Phường / xã / thị trấn / đặc khu, VD 'Phường Bến Nghé', 'P. 1'.")] = None,
        district: Annotated[
            str | None, Field(description="Quận / huyện / thị xã CŨ (chỉ dùng cho old_to_new), VD 'Quận 3'.")
        ] = None,
        province: Annotated[str | None, Field(description="Tỉnh / thành, VD 'TP. Hồ Chí Minh', 'Bà Rịa - Vũng Tàu'.")] = None,
    ) -> dict[str, Any]:
        input_context = {"direction": direction, "ward": ward, "district": district, "province": province}
        try:
            if direction == "new_to_old" and district:
                return format_validation_error("district", district, "cấp quận / huyện đã bỏ, không dùng cho new_to_old")
            kq = cu_sang_moi(ward, district, province) if direction == "old_to_new" else moi_sang_cu(ward, province)
            if "error" in kq:
                return format_mcp_tool_error_response(kq["error"], error_code=-2, input_context=input_context)
            return drop_empty({"status": "success", "direction": direction, **kq, "data_version": au.data_version()})

        except Exception as e:
            logger.error("convert_admin_unit thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không đổi được đơn vị hành chính", exception=e, input_context=input_context
            )

    return convert_admin_unit
