"""Tool hồ sơ độ cao và độ dốc dọc tuyến đường (Vietmap route + Open Topo Data SRTM 30 m)."""

import math
from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.open_data_client import OpenDataError, open_data_client
from ..services.vietmap_client import VietmapError, vietmap_client
from ..utils.error_handler import format_mcp_tool_error_response
from ..utils.response_utils import drop_empty
from ._vietmap_common import MO_TA_DIEM, NHAN_PHUONG_TIEN, gon_diem, loi_vietmap, resolve_nhieu

logger = structlog.get_logger(__name__)

_SO_MAU = 100  # Open Topo Data nhận tối đa 100 điểm / request
_DOAN_DOC = 200.0  # tính độ dốc trên đoạn >= 200 m: SRTM sai số vài mét, đoạn ngắn sẽ ra dốc ảo
# Ngưỡng tham khảo: 5% là giới hạn thoải mái cho xe lăn / xe đẩy, 8% xe máy chở 2 người cần
# về số thấp, 12% trở lên là đèo rất dốc.
_NGUONG = ((12.0, "rất dốc"), (8.0, "dốc"), (5.0, "hơi dốc"))


def _khoang_cach(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Haversine (m) giữa hai điểm (lat, lng)."""
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def lay_mau(toa_do: list[tuple[float, float]], so_mau: int) -> list[tuple[float, float, float]]:
    """Lấy `so_mau` điểm cách đều theo quãng đường dọc tuyến -> [(km, lat, lng)]."""
    tich_luy = [0.0]
    for a, b in zip(toa_do, toa_do[1:]):
        tich_luy.append(tich_luy[-1] + _khoang_cach(a, b))
    tong = tich_luy[-1]
    if tong == 0:
        return [(0.0, *toa_do[0])]
    ra, j = [], 0
    for k in range(so_mau):
        muc_tieu = tong * k / (so_mau - 1)
        while j < len(tich_luy) - 2 and tich_luy[j + 1] < muc_tieu:
            j += 1
        doan = tich_luy[j + 1] - tich_luy[j] or 1.0
        t = min(max((muc_tieu - tich_luy[j]) / doan, 0.0), 1.0)
        lat = toa_do[j][0] + t * (toa_do[j + 1][0] - toa_do[j][0])
        lng = toa_do[j][1] + t * (toa_do[j + 1][1] - toa_do[j][1])
        ra.append((muc_tieu / 1000, lat, lng))
    return ra


def phan_tich(km: list[float], cao: list[float]) -> dict[str, Any]:
    leo = sum(max(b - a, 0) for a, b in zip(cao, cao[1:]))
    xuong = sum(max(a - b, 0) for a, b in zip(cao, cao[1:]))
    doan_doc: list[dict[str, Any]] = []
    doc_max = 0.0
    i = 0
    while i < len(km) - 1:
        j = i + 1
        while j < len(km) - 1 and (km[j] - km[i]) * 1000 < _DOAN_DOC:
            j += 1
        dai = (km[j] - km[i]) * 1000
        if dai <= 0:
            break
        doc = (cao[j] - cao[i]) / dai * 100
        doc_max = max(doc_max, abs(doc))
        muc = next((ten for nguong, ten in _NGUONG if abs(doc) >= nguong), None)
        if muc:
            huong = "lên" if doc > 0 else "xuống"
            truoc = doan_doc[-1] if doan_doc else None
            if truoc and truoc["direction"] == huong and abs(truoc["to_km"] - km[i]) < 1e-6:
                truoc["to_km"] = round(km[j], 2)
                truoc["max_grade_pct"] = max(truoc["max_grade_pct"], round(abs(doc), 1))
                truoc["level"] = next(t for n, t in _NGUONG if truoc["max_grade_pct"] >= n)
            else:
                doan_doc.append(
                    {
                        "from_km": round(km[i], 2),
                        "to_km": round(km[j], 2),
                        "direction": huong,
                        "max_grade_pct": round(abs(doc), 1),
                        "level": muc,
                    }
                )
        i = j
    return {
        "min_elevation_m": round(min(cao)),
        "max_elevation_m": round(max(cao)),
        "total_ascent_m": round(leo),
        "total_descent_m": round(xuong),
        "max_grade_pct": round(doc_max, 1),
        "steep_segments": doan_doc,
    }


def _loi_khuyen(doc_max: float, vehicle: str) -> list[str]:
    ra = []
    if doc_max >= 12:
        ra.append("Có đoạn rất dốc (>=12%): xe máy chở 2 người / xe số yếu nên về số thấp, đổ đèo phanh bằng số.")
    elif doc_max >= 8:
        ra.append("Có đoạn dốc (>=8%): xe máy chở nặng cần về số thấp; đi bộ sẽ mệt.")
    if doc_max >= 5:
        ra.append("Không hợp xe lăn / xe đẩy trẻ em ở các đoạn >=5% nếu đi bộ.")
    if vehicle == "car" and doc_max >= 12:
        ra.append("Ô tô số tự động nên dùng chế độ L / S khi xuống dốc dài.")
    return ra


def register_elevation_profile_tool(mcp: FastMCP):
    @mcp.tool(
        name="elevation_profile",
        title="Elevation Profile (Độ dốc tuyến đường)",
        description=(
            "Hồ sơ độ cao dọc một tuyến đường ở Việt Nam: độ cao thấp / cao nhất, tổng leo / đổ "
            "dốc, độ dốc lớn nhất và các đoạn dốc (km bắt đầu - kết thúc, lên / xuống, mức hơi dốc "
            "/ dốc / rất dốc), kèm lời khuyên cho xe máy, ô tô, xe lăn, xe đẩy.\n\n"
            "Khi nào dùng: chuyến đèo (Hà Giang, Tà Xùa, đèo Prenn, Khánh Lê), đoàn có người già "
            "/ xe đẩy / xe lăn, đi xe máy chở nặng. Độ dốc tính trên đoạn >= 200 m từ dữ liệu "
            "SRTM 30 m nên là ước lượng; dốc ngắn cục bộ (bậc thang, dốc hẻm) không thấy được.\n"
            "Chi phí: 1 lượt Vietmap + 1 lượt Open Topo Data (miễn phí, 1.000 lượt / ngày).\n\n" + MO_TA_DIEM
        ),
        tags={"journey", "routing", "accessibility"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "vietmap:route/v4 + opentopodata:srtm30m"},
    )
    async def elevation_profile(
        points: Annotated[list[str], Field(min_length=2, max_length=10, description="Các điểm theo thứ tự đi.")],
        vehicle: Annotated[
            Literal["motorcycle", "car", "truck"], Field(description="Phương tiện.", json_schema_extra=NHAN_PHUONG_TIEN)
        ] = "motorcycle",
        profile_points: Annotated[
            int, Field(ge=5, le=100, description="Số điểm độ cao trả về trong `profile` (để vẽ biểu đồ).")
        ] = 20,
    ) -> dict[str, Any]:
        input_context = {"points": points, "vehicle": vehicle}
        try:
            diem = await resolve_nhieu(points)
            data = await vietmap_client.get(
                "/route/v4",
                [*(("point", d.latlng) for d in diem), ("vehicle", vehicle), ("points_encoded", "false")],
            )
            paths = data.get("paths") or []
            hinh = ((paths[0].get("points") or {}).get("coordinates") or []) if paths else []
            if len(hinh) < 2:
                return drop_empty({"status": "success", "note": "Không tìm được tuyến đường giữa các điểm"})
            toa_do = [(lat, lng) for lng, lat in hinh]

            mau = lay_mau(toa_do, _SO_MAU)
            cao = await open_data_client.elevations([(lat, lng) for _, lat, lng in mau])
            hop_le = [(k, c) for (k, _, _), c in zip(mau, cao) if c is not None]
            if len(hop_le) < 2:
                return drop_empty({"status": "success", "note": "Không có dữ liệu độ cao cho tuyến này"})
            km = [k for k, _ in hop_le]
            cao_m = [c for _, c in hop_le]
            tom_tat = phan_tich(km, cao_m)
            buoc = max(1, len(km) // profile_points)

            return drop_empty(
                {
                    "status": "success",
                    "vehicle": vehicle,
                    "points": [gon_diem(d) for d in diem],
                    "distance_km": round(paths[0].get("distance", 0) / 1000, 2),
                    **tom_tat,
                    "advice": _loi_khuyen(tom_tat["max_grade_pct"], vehicle),
                    "profile": [{"km": round(k, 2), "elevation_m": round(c)} for k, c in list(zip(km, cao_m))[::buoc]],
                    "note": "Độ dốc ước lượng từ SRTM 30 m trên đoạn >= 200 m; dốc ngắn cục bộ không thể hiện.",
                }
            )

        except VietmapError as e:
            logger.error("elevation_profile gọi Vietmap thất bại", error=str(e))
            return loi_vietmap(e, input_context)
        except OpenDataError as e:
            logger.error("elevation_profile gọi Open Topo Data thất bại", error=str(e))
            return format_mcp_tool_error_response(str(e), exception=e, error_code=-3, input_context=input_context)
        except Exception as e:
            logger.error("elevation_profile thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response("Không tính được độ dốc", exception=e, input_context=input_context)

    return elevation_profile
