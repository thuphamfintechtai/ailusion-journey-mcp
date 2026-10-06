"""Phần dùng chung cho các tool Vietmap. File bắt đầu bằng `_` nên không bị đăng ký thành tool."""

import asyncio
from typing import Any
from urllib.parse import urlencode

from ..services.vietmap_client import Diem, VietmapError, vietmap_client
from ..utils.error_handler import format_mcp_tool_error_response
from ._serpapi_common import MA_LOI_HET_QUOTA, nhan_lua_chon

PHUONG_TIEN = {"motorcycle": "xe máy", "car": "ô tô", "truck": "xe tải"}
NHAN_PHUONG_TIEN = nhan_lua_chon(PHUONG_TIEN)
MO_TA_DIEM = (
    "Mỗi điểm là một trong ba dạng: 'lat,lng' (VD '10.7725,106.698'), `ref_id` lấy từ "
    "find_nearby / geocode_vn, hoặc tên / địa chỉ ở Việt Nam (VD 'Chợ Bến Thành'). "
    "Tên / địa chỉ: GHI KÈM tỉnh hoặc phường (VD 'Hồ Hoàn Kiếm, Hà Nội') vì nhiều nơi "
    "trùng tên, và tốn thêm 2 lượt gọi; kiểm tra `points[].label` để chắc tool hiểu đúng "
    "điểm. Ưu tiên toạ độ hoặc ref_id khi đã có."
)
# Google Maps URLs: deep link không cần API key, dùng để người dùng mở chỉ đường.
_GMAPS_MODE = {"motorcycle": "two-wheeler", "car": "driving", "truck": "driving"}


async def resolve_nhieu(cac_diem: list[str]) -> list[Diem]:
    """Quy nhiều điểm ra toạ độ song song; lỗi điểm nào báo rõ điểm đó."""
    kq = await asyncio.gather(*(vietmap_client.resolve(d) for d in cac_diem), return_exceptions=True)
    for goc, r in zip(cac_diem, kq):
        if isinstance(r, BaseException):
            if isinstance(r, VietmapError) and r.code == "NOT_FOUND":
                raise VietmapError(f"Không tìm thấy điểm '{goc}', thử ghi rõ hơn (thêm phường / tỉnh)", code="NOT_FOUND")
            raise r
    return list(kq)  # type: ignore[arg-type]


def gon_diem(d: Diem) -> dict[str, Any]:
    return {"label": d.label, "lat": round(d.lat, 6), "lng": round(d.lng, 6), "ref_id": d.ref_id}


def link_google_maps(cac_diem: list[Diem], vehicle: str) -> str:
    """Deep link mở chỉ đường trên Google Maps (tối đa 9 điểm dừng giữa)."""
    q = {
        "api": "1",
        "origin": cac_diem[0].latlng,
        "destination": cac_diem[-1].latlng,
        "travelmode": _GMAPS_MODE.get(vehicle, "driving"),
    }
    if len(cac_diem) > 2:
        q["waypoints"] = "|".join(d.latlng for d in cac_diem[1:-1][:9])
    return "https://www.google.com/maps/dir/?" + urlencode(q)


def loi_vietmap(e: VietmapError, input_context: dict[str, Any]) -> dict[str, Any]:
    if e.het_key:
        return format_mcp_tool_error_response(
            error_message=(
                "Tạm thời hết lượt tra cứu bản đồ (Vietmap). Không gọi lại ngay; "
                "báo người dùng dữ liệu đường đi / địa điểm chưa sẵn sàng."
            ),
            exception=e,
            error_code=MA_LOI_HET_QUOTA,
            input_context=input_context,
        )
    if e.code == "NOT_FOUND":
        return format_mcp_tool_error_response(str(e), exception=e, error_code=-2, input_context=input_context)
    return format_mcp_tool_error_response(
        error_message=f"Không lấy được dữ liệu từ Vietmap: {e}",
        exception=e,
        error_code=-3,
        input_context=input_context,
    )
