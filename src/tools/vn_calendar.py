"""Tool tra lịch Việt Nam cho một khoảng ngày: nghỉ lễ, nghỉ bù, âm lịch, lễ hội, cao điểm."""

from datetime import date
from typing import Annotated, Any

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.vn_calendar import THU, DayInfo, vn_calendar_service
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._serpapi_common import doc_ngay

logger = structlog.get_logger(__name__)

_TOI_DA_NGAY = 120
_HIEN_DU_KHI_TOI_DA = 14  # khoảng ngắn thì trả mọi ngày, dài thì chỉ ngày đáng chú ý
_TEN_TRANG_THAI = {"nghi_le": "nghỉ lễ", "lam_bu": "đi làm bù"}


def _gon_ngay(d: DayInfo) -> dict[str, Any]:
    return {
        "date": d.date.isoformat(),
        "weekday": THU[d.date.weekday()],
        "lunar": d.lunar,
        "day_off": d.day_off,
        "status": _TEN_TRANG_THAI.get(d.status or ""),
        "source": d.status_source if d.status else None,
        "events": [
            {
                "name": e.name,
                "kind": e.kind,
                "provinces": list(e.provinces) or None,
                "note": e.note,
            }
            for e in d.events
        ],
        "peak_level": d.peak_level,
        "peak_reasons": d.peak_reasons,
    }


def _dang_chu_y(d: DayInfo) -> bool:
    return bool(d.status or d.events or d.peak_level >= 2)


def register_vn_calendar_tool(mcp: FastMCP):
    @mcp.tool(
        name="vn_calendar",
        title="VN Calendar (Lịch Việt Nam)",
        description=(
            "Tra lịch Việt Nam cho một khoảng ngày: ngày nào nghỉ lễ / nghỉ bù / đi làm bù, "
            "ngày âm lịch, lễ hội truyền thống theo tỉnh, ngày kỷ niệm, và mức cao điểm "
            "đi lại (`peak_level` 0-3: 0 bình thường, 1 cuối tuần, 2 đông, 3 rất đông / "
            "vé khan hiếm) kèm lý do.\n\n"
            "Khi nào dùng: chọn ngày đi, cảnh báo giá vé/phòng tăng, biết quán xá đóng cửa "
            "dịp Tết, tránh hoặc tìm lễ hội. Truyền `province` để lấy lễ hội của nơi đến.\n"
            "Không tốn quota. Dữ liệu ghép từ luật lịch Việt Nam, lịch lễ công khai của "
            "Google và quyết định nghỉ lễ từng năm; `source` cho biết ngày nghỉ lấy từ đâu "
            "('rules' là ước tính theo luật, chưa có thông báo chính thức).\n\n"
            f"Khoảng tối đa {_TOI_DA_NGAY} ngày. Khoảng dài hơn {_HIEN_DU_KHI_TOI_DA} ngày "
            "chỉ trả các ngày đáng chú ý (nghỉ lễ, có sự kiện, cao điểm >= 2) trừ khi "
            "`all_days=true`."
        ),
        tags={"journey", "calendar"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "rules + google_ics + overrides"},
    )
    async def vn_calendar(
        start_date: Annotated[str, Field(description="Ngày bắt đầu, dạng YYYY-MM-DD.")],
        end_date: Annotated[
            str | None, Field(description="Ngày kết thúc (YYYY-MM-DD). Bỏ trống = chỉ xem start_date.")
        ] = None,
        province: Annotated[
            str | None,
            Field(description="Tỉnh/thành nơi đến để lọc lễ hội, VD 'Ninh Bình', 'Hà Nội'. Tên cũ hay mới đều được."),
        ] = None,
        include_observances: Annotated[
            bool, Field(description="Kèm ngày kỷ niệm không nghỉ (Trung Thu, Vu Lan, 8/3, Giáng sinh...).")
        ] = True,
        all_days: Annotated[bool, Field(description="Trả mọi ngày kể cả ngày bình thường.")] = False,
    ) -> dict[str, Any]:
        input_context = {"start_date": start_date, "end_date": end_date, "province": province}
        try:
            try:
                bd = doc_ngay(start_date, "start_date")
                kt = doc_ngay(end_date, "end_date") if end_date else bd
            except ValueError as e:
                return format_mcp_tool_error_response(str(e), error_code=-2, input_context=input_context)
            if kt < bd:
                return format_validation_error("end_date", end_date, "trước start_date")
            so_ngay = (kt - bd).days + 1
            if so_ngay > _TOI_DA_NGAY:
                return format_validation_error("end_date", end_date, f"khoảng ngày vượt {_TOI_DA_NGAY} ngày")
            if not (1901 <= bd.year and kt.year <= 2099):
                return format_validation_error("start_date", start_date, "chỉ hỗ trợ năm 1901-2099")

            kq = await vn_calendar_service.get_days(
                bd, kt, province=province, include_observances=include_observances
            )
            cac_ngay: list[DayInfo] = kq["days"]
            hien = cac_ngay if (all_days or so_ngay <= _HIEN_DU_KHI_TOI_DA) else [d for d in cac_ngay if _dang_chu_y(d)]

            return drop_empty(
                {
                    "status": "success",
                    "range": {"from": bd.isoformat(), "to": kt.isoformat(), "days": so_ngay},
                    "province": province,
                    "summary": {
                        "days_off": sum(d.day_off for d in cac_ngay),
                        "holidays": sorted({e.name for d in cac_ngay for e in d.events if e.kind == "day_off"}),
                        "max_peak_level": max((d.peak_level for d in cac_ngay), default=0),
                    },
                    "day_off_blocks": [
                        {
                            "from": k["from"].isoformat(),
                            "to": k["to"].isoformat(),
                            "days": k["days"],
                            "holidays": k["holidays"],
                        }
                        for k in kq["day_off_blocks"]
                    ],
                    "seasons": [
                        {**m, "from": m["from"].isoformat(), "to": m["to"].isoformat()} for m in kq["seasons"]
                    ],
                    "days": [_gon_ngay(d) for d in hien],
                    "today": date.today().isoformat(),
                    "warnings": kq["warnings"],
                }
            )

        except Exception as e:
            logger.error("vn_calendar thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không tra được lịch", exception=e, input_context=input_context
            )

    return vn_calendar
