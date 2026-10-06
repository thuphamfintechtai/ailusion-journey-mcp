"""Tool tra danh mục đơn vị hành chính hiện hành (sau 1/7/2025), offline từ thư viện vietnam-provinces."""

from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services import admin_units as au
from ..utils.error_handler import format_mcp_tool_error_response
from ..utils.response_utils import drop_empty
from ..utils.text import bo_dau, ten_goc_co_dau, ten_goc_hanh_chinh

logger = structlog.get_logger(__name__)


def register_list_admin_units_tool(mcp: FastMCP):
    @mcp.tool(
        name="list_admin_units",
        title="List Admin Units (Tra đơn vị hành chính mới)",
        description=(
            "Tra danh mục đơn vị hành chính hiện hành (sau sáp nhập 1/7/2025, 34 tỉnh / thành, "
            "2 cấp tỉnh - phường / xã). Tra offline, không tốn quota.\n\n"
            "- Không truyền gì: danh sách 34 tỉnh / thành kèm loại (tỉnh / thành phố trung ương).\n"
            "- `province`: các phường / xã / đặc khu của tỉnh đó.\n"
            "- `search` (+ `province`): tìm phường / xã theo tên, kiểm tra một tên có còn tồn tại.\n\n"
            "Khi nào dùng: kiểm tra slot địa danh người dùng nhập có hợp lệ, liệt kê khu vực trong "
            "một tỉnh. Đổi tên cũ -> mới thì dùng convert_admin_unit."
        ),
        tags={"journey", "admin", "address"},
        meta={"version": "2.0.0", "author": "Ailusion team", "data_source": "vietnam-provinces (offline)"},
    )
    async def list_admin_units(
        province: Annotated[str | None, Field(description="Tỉnh / thành mới, VD 'Lâm Đồng', 'TP. Hồ Chí Minh'.")] = None,
        search: Annotated[str | None, Field(description="Tên phường / xã cần tìm, VD 'Hạ Long'.")] = None,
        limit: Annotated[int, Field(ge=1, le=200, description="Số dòng tối đa.")] = 50,
    ) -> dict[str, Any]:
        input_context = {"province": province, "search": search}
        try:
            tinh = None
            if province:
                tinh = au.find_province(province)
                if not tinh:
                    return format_mcp_tool_error_response(
                        f"Không tìm thấy tỉnh / thành mới '{province}' (hiện có 34 tỉnh / thành)",
                        error_code=-2,
                        input_context=input_context,
                    )

            if search:
                ds = au.search_wards(search, int(tinh.code) if tinh else None)
                # Gõ có dấu thì so có dấu: "Hạ Long" không trùng "Xã Hà Long".
                co_dau = bo_dau(search) != search.lower().strip()
                chuan = ten_goc_co_dau if co_dau else ten_goc_hanh_chinh
                q = chuan(search)
                ds.sort(key=lambda w: chuan(w.name) != q)  # trùng tên lên đầu
                return drop_empty(
                    {
                        "status": "success",
                        "level": "ward",
                        "total_found": len(ds),
                        "items": [
                            {
                                "name": w.name,
                                "code": int(w.code),
                                "type": w.division_type.value,
                                "province": (au.province_by_code(int(w.province_code)) or tinh).name,
                                "exact": chuan(w.name) == q or None,
                            }
                            for w in ds[:limit]
                        ],
                    }
                )

            if tinh:
                phuong = au.wards_of_province(int(tinh.code))
                return drop_empty(
                    {
                        "status": "success",
                        "level": "ward",
                        "province": tinh.name,
                        "total_found": len(phuong),
                        "items": [
                            {"name": w.name, "code": int(w.code), "type": w.division_type.value} for w in phuong[:limit]
                        ],
                        "note": f"Còn {len(phuong) - limit} đơn vị, tăng `limit` để xem thêm" if len(phuong) > limit else None,
                    }
                )

            tat_ca = au.provinces()
            return {
                "status": "success",
                "level": "province",
                "total_found": len(tat_ca),
                "items": [{"name": p.name, "code": int(p.code), "type": p.division_type.value} for p in tat_ca],
                "data_version": au.data_version(),
            }

        except Exception as e:
            logger.error("list_admin_units thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không tra được đơn vị hành chính", exception=e, input_context=input_context
            )

    return list_admin_units
