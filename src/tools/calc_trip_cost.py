"""Tool cộng chi phí chuyến đi theo hạng mục, quy về VND, chia đầu người, cộng xăng / thuế / dự phòng."""

from datetime import date
from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import BaseModel, Field

from ..services.fx_client import FxError, fx_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._serpapi_common import doc_ngay

logger = structlog.get_logger(__name__)

# Nghị quyết giảm thuế GTGT 2% (10% -> 8%) có hiệu lực tới 31/12/2026; sau đó coi như 10%
# cho tới khi có quyết định mới — cập nhật hằng số này khi chính sách đổi.
_VAT_GIAM_DEN = date(2026, 12, 31)
_PHI_PHUC_VU = 0.05  # giá "++" của khách sạn / nhà hàng: 5% phí phục vụ rồi mới tính VAT
_TIEU_HAO_MAC_DINH = {"car": 7.0, "motorcycle": 2.5}  # lít / 100 km, ước lượng phổ biến


class CostItem(BaseModel):
    name: str = Field(description="Tên khoản, VD 'Khách sạn Hội An', 'Vé máy bay SGN-DAD'.")
    amount: float = Field(ge=0, description="Đơn giá.")
    currency: str = Field(default="VND", min_length=3, max_length=3, description="Mã tiền tệ ISO.")
    quantity: float = Field(default=1, ge=0, description="Số lượng (số vé, số phòng...).")
    per: Literal["total", "person", "night", "day"] = Field(
        default="total", description="Đơn giá tính theo: cả đoàn / mỗi người / mỗi đêm / mỗi ngày."
    )
    category: str = Field(default="khác", description="Nhóm: 'lưu trú', 'di chuyển', 'ăn uống', 'vé tham quan'...")
    tax: Literal["included", "vat", "plus_plus"] = Field(
        default="included", description="'included' giá đã gồm thuế; 'vat' chưa gồm VAT; 'plus_plus' giá ++ (5% phí phục vụ + VAT)."
    )


class Fuel(BaseModel):
    distance_km: float = Field(ge=0, description="Tổng quãng đường tự lái (lấy từ get_route).")
    vehicle: Literal["car", "motorcycle"] = Field(default="car", description="Loại xe.")
    liters_per_100km: float | None = Field(default=None, gt=0, description="Mức tiêu hao; bỏ trống dùng ước lượng.")
    price_per_liter_vnd: float | None = Field(default=None, gt=0, description="Giá xăng hiện tại (VND / lít).")


def _vat(ngay: date) -> float:
    return 0.08 if ngay <= _VAT_GIAM_DEN else 0.10


def register_calc_trip_cost_tool(mcp: FastMCP):
    @mcp.tool(
        name="calc_trip_cost",
        title="Calc Trip Cost (Tính chi phí chuyến đi)",
        description=(
            "Cộng chi phí chuyến đi: từng khoản quy về VND (tỷ giá Vietcombank), nhân theo số "
            "người / đêm / ngày, cộng VAT cho giá chưa thuế hoặc giá '++', tiền xăng theo quãng "
            "đường, phí cầu đường, dự phòng; trả tổng, theo hạng mục và theo đầu người, so với "
            "ngân sách nếu có.\n\n"
            "Khi nào dùng: lập / so sánh phương án theo ngân sách. Lấy đơn giá từ search_hotels, "
            "search_flights, phí cầu đường từ get_route (toll_cost_vnd), quãng đường từ get_route.\n"
            "VAT: 8% cho chuyến tới 31/12/2026 (chính sách giảm thuế), sau đó 10%."
        ),
        tags={"journey", "money"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "tính toán + vietcombank"},
    )
    async def calc_trip_cost(
        items: Annotated[list[CostItem], Field(description="Các khoản chi.")],
        people: Annotated[int, Field(ge=1, le=100, description="Số người trong đoàn.")] = 1,
        nights: Annotated[int, Field(ge=0, le=60, description="Số đêm.")] = 0,
        days: Annotated[int | None, Field(ge=1, le=60, description="Số ngày; bỏ trống = nights + 1.")] = None,
        trip_date: Annotated[str | None, Field(description="Ngày đi YYYY-MM-DD, để chọn mức VAT.")] = None,
        tolls_vnd: Annotated[float, Field(ge=0, description="Tổng phí cầu đường (VND).")] = 0,
        fuel: Annotated[Fuel | None, Field(description="Tiền xăng cho chuyến tự lái.")] = None,
        contingency_pct: Annotated[float, Field(ge=0, le=50, description="Dự phòng phát sinh, % trên tổng.")] = 10,
        budget_vnd: Annotated[float | None, Field(ge=0, description="Ngân sách cả đoàn (VND) để so sánh.")] = None,
    ) -> dict[str, Any]:
        input_context = {"people": people, "nights": nights, "items": len(items)}
        try:
            try:
                ngay = doc_ngay(trip_date, "trip_date") if trip_date else date.today()
            except ValueError as e:
                return format_validation_error("trip_date", trip_date, str(e))
            so_ngay = days or nights + 1
            vat = _vat(ngay)
            nhan = {"total": 1, "person": people, "night": max(nights, 1), "day": so_ngay}

            chi_tiet: list[dict[str, Any]] = []
            theo_nhom: dict[str, float] = {}
            ghi_chu: list[str] = []
            for it in items:
                ty_gia = 1.0
                if it.currency.upper() != "VND":
                    ty_gia = (await fx_client.vnd_per_unit(it.currency.upper(), "sell"))["rate"]
                goc = it.amount * it.quantity * nhan[it.per] * ty_gia
                he_so_thue = {"included": 1.0, "vat": 1 + vat, "plus_plus": (1 + _PHI_PHUC_VU) * (1 + vat)}[it.tax]
                tien = goc * he_so_thue
                chi_tiet.append(
                    {
                        "name": it.name,
                        "category": it.category,
                        "vnd": round(tien),
                        "tax_added_vnd": round(tien - goc) or None,
                        "fx_rate": ty_gia if ty_gia != 1 else None,
                    }
                )
                theo_nhom[it.category] = theo_nhom.get(it.category, 0) + tien
            if per_nights := [it.name for it in items if it.per == "night" and nights == 0]:
                ghi_chu.append(f"nights=0 nên khoản tính theo đêm được nhân 1: {', '.join(per_nights)}")

            if tolls_vnd:
                theo_nhom["di chuyển"] = theo_nhom.get("di chuyển", 0) + tolls_vnd
                chi_tiet.append({"name": "Phí cầu đường", "category": "di chuyển", "vnd": round(tolls_vnd)})

            nhien_lieu = None
            if fuel:
                tieu_hao = fuel.liters_per_100km or _TIEU_HAO_MAC_DINH[fuel.vehicle]
                lit = fuel.distance_km * tieu_hao / 100
                nhien_lieu = {"liters": round(lit, 1), "liters_per_100km": tieu_hao}
                if fuel.price_per_liter_vnd:
                    tien_xang = lit * fuel.price_per_liter_vnd
                    nhien_lieu["vnd"] = round(tien_xang)
                    theo_nhom["di chuyển"] = theo_nhom.get("di chuyển", 0) + tien_xang
                    chi_tiet.append({"name": "Xăng", "category": "di chuyển", "vnd": round(tien_xang)})
                else:
                    ghi_chu.append("Chưa có giá xăng nên chỉ tính số lít; truyền fuel.price_per_liter_vnd để ra tiền.")

            tam_tinh = sum(theo_nhom.values())
            du_phong = tam_tinh * contingency_pct / 100
            tong = tam_tinh + du_phong
            so_sanh = None
            if budget_vnd is not None:
                chenh = budget_vnd - tong
                so_sanh = {
                    "budget_vnd": round(budget_vnd),
                    "remaining_vnd": round(chenh),
                    "within_budget": chenh >= 0,
                    "usage_pct": round(tong / budget_vnd * 100, 1) if budget_vnd else None,
                }

            return drop_empty(
                {
                    "status": "success",
                    "people": people,
                    "nights": nights,
                    "days": so_ngay,
                    "vat_rate": vat,
                    "items": chi_tiet,
                    "by_category_vnd": {k: round(v) for k, v in sorted(theo_nhom.items(), key=lambda x: -x[1])},
                    "fuel": nhien_lieu,
                    "subtotal_vnd": round(tam_tinh),
                    "contingency_vnd": round(du_phong),
                    "total_vnd": round(tong),
                    "per_person_vnd": round(tong / people),
                    "budget": so_sanh,
                    "notes": ghi_chu or None,
                }
            )
        except FxError as e:
            logger.error("calc_trip_cost lấy tỷ giá thất bại", error=str(e))
            return format_mcp_tool_error_response(str(e), exception=e, error_code=-3, input_context=input_context)
        except Exception as e:
            logger.error("calc_trip_cost thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response("Không tính được chi phí", exception=e, input_context=input_context)

    return calc_trip_cost
