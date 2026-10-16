"""Sao tin được của một địa điểm: Bayes thực nghiệm, 2 số tiên nghiệm lấy từ dữ liệu.

    sao tin được = (C × μ + n × sao) ÷ (C + n)

μ (sao tiên nghiệm) và C (số review ảo) ước lượng riêng cho từng (điểm đến, loại hình) từ bảng
gmaps_place bằng `scripts_test/uoc_luong_rating_tien_nghiem.py` (DerSimonian–Laird 1986), lưu ở
`data/rating_prior.json`. Tra theo thứ tự: (điểm đến, loại hình) -> (toàn quốc, loại hình) ->
toàn bộ dữ liệu — script đã điền sẵn mức lùi cho mọi nhóm có trong DB.

Không có tiên nghiệm (thiếu file) thì lùi về Hoeffding (1963) như bước chấm của LLM cũ:
sao − 4 × √(ln(1/0,05) ÷ (2n)). Bước chấm (LLM) dùng đúng số ở đây (`rating_adjusted` trong
kết quả tool). Sắp "popular" của DB vẫn giữ công thức cũ (xem _db_common.diem_pho_bien).

Chỉ dùng để sắp thứ tự, không dùng làm ngưỡng loại bỏ.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog

from .travel_db import khoa_diem_den

logger = structlog.get_logger(__name__)

PRIOR_PATH = Path(__file__).resolve().parents[2] / "data" / "rating_prior.json"
TAT_CA = "*"
# Hoeffding khi không có tiên nghiệm: thang Google 1–5 sao (rộng 4), độ tin 95%.
HOEFFDING_RANGE = 4.0
HOEFFDING_RISK = 0.05


def _chuan_loai(category: str | None) -> str:
    return (category or "").strip()


@lru_cache(maxsize=1)
def bang_tien_nghiem(path: str = str(PRIOR_PATH)) -> dict[tuple[str, str], dict[str, Any]]:
    """{(khoá điểm đến | '*', loại hình | '*'): dòng tiên nghiệm}. Thiếu / hỏng file: {}."""
    try:
        nhom = json.loads(Path(path).read_text(encoding="utf-8"))["nhom"]
    except (OSError, ValueError, KeyError) as e:
        logger.warning("rating_prior_missing", path=path, error=str(e))
        return {}
    return {(x["destination"], _chuan_loai(x["category"])): x for x in nhom}


def tien_nghiem(destination: str | None, category: str | None) -> dict[str, Any] | None:
    """Dòng tiên nghiệm của (điểm đến, loại hình), lùi lên loại hình toàn quốc rồi toàn bộ."""
    bang = bang_tien_nghiem()
    if not bang:
        return None
    dest, loai = khoa_diem_den(destination) or TAT_CA, _chuan_loai(category)
    for khoa in ((dest, loai), (TAT_CA, loai), (TAT_CA, TAT_CA)):
        if khoa in bang:
            return bang[khoa]
    return None


def hoeffding(rating: float, so_review: int) -> float:
    margin = HOEFFDING_RANGE * math.sqrt(math.log(1 / HOEFFDING_RISK) / (2 * so_review))
    return round(rating - margin, 3)


def sao_tin_duoc(
    rating: float | None,
    so_review: int | None,
    destination: str | None = None,
    category: str | None = None,
) -> dict[str, Any] | None:
    """{'value', 'method': 'bayes' | 'hoeffding', 'prior_mean', 'prior_reviews', 'prior_level'}.
    Chưa có sao hoặc review: None."""
    if not rating or not so_review:
        return None
    tn = tien_nghiem(destination, category)
    if tn is None:
        return {"value": hoeffding(rating, so_review), "method": "hoeffding"}
    mu, c = tn["rating_tien_nghiem"], tn["so_review_tien_nghiem"]
    return {
        "value": round((c * mu + so_review * rating) / (c + so_review), 3),
        "method": "bayes",
        "prior_mean": mu,
        "prior_reviews": c,
        "prior_level": tn["muc"],
    }
