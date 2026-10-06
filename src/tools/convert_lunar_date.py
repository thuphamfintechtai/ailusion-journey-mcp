"""Tool đổi ngày âm lịch <-> dương lịch (múi giờ Việt Nam, UTC+7)."""

from datetime import date
from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from lunar_vn import can_chi, convert_lunar_to_solar, convert_solar_to_lunar, jd_from_date
from pydantic import Field

from ..services.vn_calendar import THU
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._serpapi_common import doc_ngay

logger = structlog.get_logger(__name__)


def _mo_ta(d: date) -> dict[str, Any]:
    al = convert_solar_to_lunar(d.day, d.month, d.year)
    return {
        "solar": d.isoformat(),
        "weekday": THU[d.weekday()],
        "lunar": {
            "day": al.day,
            "month": al.month,
            "year": al.year,
            "leap_month": al.leap or None,
            "text": f"{al.day}/{al.month}{' nhuận' if al.leap else ''} năm {can_chi.get_year_can_chi(al.year)}",
            "day_can_chi": can_chi.get_day_can_chi(jd_from_date(d.day, d.month, d.year)),
        },
    }


def register_convert_lunar_date_tool(mcp: FastMCP):
    @mcp.tool(
        name="convert_lunar_date",
        title="Convert Lunar Date (Đổi ngày âm - dương)",
        description=(
            "Đổi ngày giữa dương lịch và âm lịch Việt Nam (UTC+7, có tháng nhuận, can chi).\n\n"
            "- Dương -> âm: truyền `solar_date`.\n"
            "- Âm -> dương: truyền `lunar_day` + `lunar_month` (+ `lunar_year`, mặc định năm "
            "âm hiện tại). VD 'rằm tháng 7 năm nay' = lunar_day 15, lunar_month 7.\n"
            "Cần xem cả khoảng ngày có nghỉ lễ / lễ hội thì dùng vn_calendar. Không tốn quota."
        ),
        tags={"journey", "calendar"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "lunar-vn"},
    )
    async def convert_lunar_date(
        solar_date: Annotated[str | None, Field(description="Ngày dương lịch YYYY-MM-DD cần đổi sang âm.")] = None,
        lunar_day: Annotated[int | None, Field(ge=1, le=30, description="Ngày âm lịch (1-30).")] = None,
        lunar_month: Annotated[int | None, Field(ge=1, le=12, description="Tháng âm lịch (1-12).")] = None,
        lunar_year: Annotated[
            int | None, Field(ge=1901, le=2099, description="Năm âm lịch. Bỏ trống = năm âm hiện tại.")
        ] = None,
        leap_month: Annotated[bool, Field(description="True nếu là tháng nhuận.")] = False,
    ) -> dict[str, Any]:
        input_context = {
            "solar_date": solar_date,
            "lunar_day": lunar_day,
            "lunar_month": lunar_month,
            "lunar_year": lunar_year,
        }
        try:
            if solar_date:
                try:
                    d = doc_ngay(solar_date, "solar_date")
                except ValueError as e:
                    return format_mcp_tool_error_response(str(e), error_code=-2, input_context=input_context)
                return drop_empty({"status": "success", **_mo_ta(d)})

            if lunar_day is None or lunar_month is None:
                return format_validation_error(
                    "solar_date", solar_date, "cần solar_date, hoặc lunar_day + lunar_month"
                )
            hom_nay = date.today()
            nam = lunar_year or convert_solar_to_lunar(hom_nay.day, hom_nay.month, hom_nay.year).year
            try:
                d = convert_lunar_to_solar(lunar_day, lunar_month, nam, 1 if leap_month else 0)
            except ValueError as e:
                return format_validation_error(
                    "lunar_day", f"{lunar_day}/{lunar_month}/{nam}", f"ngày âm không tồn tại ({e})"
                )
            # Tháng âm có 29 ngày thì ngày 30 bị đẩy sang tháng sau: kiểm tra lại.
            al = convert_solar_to_lunar(d.day, d.month, d.year)
            if (al.day, al.month, al.leap) != (lunar_day, lunar_month, leap_month):
                return format_validation_error(
                    "lunar_day", f"{lunar_day}/{lunar_month}/{nam}", "tháng âm này không có ngày đó"
                )
            return drop_empty({"status": "success", **_mo_ta(d)})

        except Exception as e:
            logger.error("convert_lunar_date thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không đổi được ngày", exception=e, input_context=input_context
            )

    return convert_lunar_date
