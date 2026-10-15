"""Chỉ đường dự phòng bằng OSRM (OpenStreetMap, không cần khoá) khi Vietmap hết credit / lỗi.

Máy chủ công khai router.project-osrm.org chỉ có hồ sơ ô tô (`driving`), không có phí cầu đường;
đủ để vẽ đường xe chạy thật trên bản đồ và ước km / phút. Đặt OSRM_URL để dùng máy chủ riêng.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

OSRM_URL = os.getenv("OSRM_URL", "https://router.project-osrm.org").rstrip("/")
_TIMEOUT = 20.0


async def osrm_route(points: list[tuple[float, float]]) -> dict[str, Any] | None:
    """Đường đi qua các điểm (lat, lng) theo thứ tự -> {distance_m, duration_s, polyline}
    (polyline Google, 5 chữ số thập phân), None nếu OSRM không trả được."""
    coords = ";".join(f"{lng},{lat}" for lat, lng in points)
    url = f"{OSRM_URL}/route/v1/driving/{coords}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(url, params={"overview": "full", "geometries": "polyline"})
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("code") != "Ok" or not data.get("routes"):
        return None
    routes = data["routes"]
    best = routes[0]
    return {
        "distance_m": best.get("distance", 0.0),
        "duration_s": best.get("duration", 0.0),
        "polyline": best.get("geometry"),
    }
