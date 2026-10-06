"""Tool chấm điểm và xếp hạng phương án theo nhiều tiêu chí (TOPSIS hoặc tổng trọng số)."""

import math
from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import BaseModel, Field

from ..utils.error_handler import format_mcp_tool_error_response
from ..utils.response_utils import drop_empty
from ._serpapi_common import nhan_lua_chon

logger = structlog.get_logger(__name__)


class Option(BaseModel):
    name: str = Field(description="Tên phương án, VD 'Phương án A - Hội An 3N2Đ'.")
    metrics: dict[str, float] = Field(description="Giá trị từng tiêu chí, VD {'cost_vnd': 6500000, 'travel_min': 240}.")


class Criterion(BaseModel):
    name: str = Field(description="Tên tiêu chí, khớp khoá trong `metrics`.")
    direction: Literal["max", "min"] = Field(description="'max' càng cao càng tốt (rating), 'min' càng thấp càng tốt (chi phí).")
    weight: float = Field(default=1, ge=0, description="Trọng số mặc định.")


def _topsis(ma_tran: list[list[float]], trong_so: list[float], huong: list[str]) -> list[float]:
    so_cot = len(trong_so)
    chuan = []
    for j in range(so_cot):
        do_dai = math.sqrt(sum(r[j] ** 2 for r in ma_tran)) or 1.0
        chuan.append([r[j] / do_dai * trong_so[j] for r in ma_tran])
    tot = [max(c) if huong[j] == "max" else min(c) for j, c in enumerate(chuan)]
    xau = [min(c) if huong[j] == "max" else max(c) for j, c in enumerate(chuan)]
    diem = []
    for i in range(len(ma_tran)):
        d_tot = math.sqrt(sum((chuan[j][i] - tot[j]) ** 2 for j in range(so_cot)))
        d_xau = math.sqrt(sum((chuan[j][i] - xau[j]) ** 2 for j in range(so_cot)))
        diem.append(d_xau / (d_tot + d_xau) if d_tot + d_xau else 1.0)
    return diem


def _trong_so_tuyen_tinh(ma_tran: list[list[float]], trong_so: list[float], huong: list[str]) -> list[float]:
    diem = [0.0] * len(ma_tran)
    for j in range(len(trong_so)):
        cot = [r[j] for r in ma_tran]
        lo, hi = min(cot), max(cot)
        for i, v in enumerate(cot):
            chuan = 1.0 if hi == lo else (v - lo) / (hi - lo)
            diem[i] += trong_so[j] * (chuan if huong[j] == "max" else 1 - chuan)
    return diem


def register_score_options_tool(mcp: FastMCP):
    @mcp.tool(
        name="score_options",
        title="Score Options (Chấm điểm phương án)",
        description=(
            "Xếp hạng các phương án chuyến đi theo nhiều tiêu chí (chi phí, thời gian di chuyển, "
            "rating, rủi ro thời tiết...) bằng TOPSIS hoặc tổng trọng số. Truyền nhiều bộ trọng "
            "số trong `weight_sets` (VD 'tiết kiệm', 'thoải mái', 'cân bằng') để xem phương án "
            "nào thắng ổn định qua các ưu tiên.\n\n"
            "Khi nào dùng: bước chọn phương án, cần lý do định lượng thay vì cảm tính. Chuẩn hoá "
            "đơn vị là việc của tool, chỉ cần khai `direction` đúng (chi phí = 'min').\n"
            "Không tốn quota."
        ),
        tags={"journey", "planning"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "tính toán"},
    )
    async def score_options(
        options: Annotated[list[Option], Field(min_length=2, max_length=20, description="Các phương án.")],
        criteria: Annotated[list[Criterion], Field(min_length=1, max_length=15, description="Các tiêu chí.")],
        weight_sets: Annotated[
            dict[str, dict[str, float]] | None,
            Field(description="Nhiều bộ trọng số, VD {'tiết kiệm': {'cost_vnd': 3, 'rating': 1}}. Thiếu tiêu chí = dùng weight mặc định."),
        ] = None,
        method: Annotated[
            Literal["topsis", "weighted_sum"],
            Field(
                description="Phương pháp.",
                json_schema_extra=nhan_lua_chon({"topsis": "TOPSIS", "weighted_sum": "tổng trọng số"}),
            ),
        ] = "topsis",
    ) -> dict[str, Any]:
        input_context = {"options": [o.name for o in options], "criteria": [c.name for c in criteria]}
        try:
            thieu = [f"{o.name}: {c.name}" for o in options for c in criteria if c.name not in o.metrics]
            if thieu:
                return format_mcp_tool_error_response(
                    "Thiếu giá trị tiêu chí: " + "; ".join(thieu[:10]), error_code=-2, input_context=input_context
                )
            ma_tran = [[float(o.metrics[c.name]) for c in criteria] for o in options]
            huong = [c.direction for c in criteria]
            bo = weight_sets or {"mặc định": {c.name: c.weight for c in criteria}}

            xep_hang: dict[str, list[dict[str, Any]]] = {}
            thang: dict[str, str] = {}
            for ten_bo, ts in bo.items():
                w = [float(ts.get(c.name, c.weight)) for c in criteria]
                tong_w = sum(w) or 1.0
                w = [x / tong_w for x in w]
                diem = (_topsis if method == "topsis" else _trong_so_tuyen_tinh)(ma_tran, w, huong)
                thu_tu = sorted(range(len(options)), key=lambda i: -diem[i])
                xep_hang[ten_bo] = [
                    {"rank": r + 1, "option": options[i].name, "score": round(diem[i], 4)} for r, i in enumerate(thu_tu)
                ]
                thang[ten_bo] = options[thu_tu[0]].name

            tot_nhat = {}
            for j, c in enumerate(criteria):
                cot = [r[j] for r in ma_tran]
                gia_tri = max(cot) if c.direction == "max" else min(cot)
                tot_nhat[c.name] = [o.name for o, v in zip(options, cot) if v == gia_tri]

            on_dinh = len(set(thang.values())) == 1
            return drop_empty(
                {
                    "status": "success",
                    "method": method,
                    "rankings": xep_hang,
                    "winner_by_weight_set": thang,
                    "robust_winner": next(iter(thang.values())) if on_dinh else None,
                    "best_per_criterion": tot_nhat,
                    "note": None
                    if on_dinh
                    else "Phương án thắng đổi theo bộ trọng số: hỏi người dùng ưu tiên điều gì trước khi chốt.",
                }
            )
        except Exception as e:
            logger.error("score_options thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response("Không chấm được phương án", exception=e, input_context=input_context)

    return score_options
