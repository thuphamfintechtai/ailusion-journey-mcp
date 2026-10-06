"""Tool tìm chuyến bay kèm giá vé (Google Flights qua SerpApi)."""

import re
from datetime import date
from typing import Annotated, Any, Literal

import structlog
from fastmcp import FastMCP
from pydantic import Field

from ..services.airport_lookup import doi_ra_ma_iata
from ..services.serpapi_client import SerpApiError, serpapi_client
from ..utils.error_handler import format_mcp_tool_error_response, format_validation_error
from ..utils.response_utils import drop_empty
from ._serpapi_common import doc_ngay, khong_co_ket_qua, loi_serpapi, tham_so_vn, nhan_lua_chon

logger = structlog.get_logger(__name__)

_TRAVEL_CLASS = {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}
_STOPS = {"any": 0, "nonstop": 1, "max_1_stop": 2, "max_2_stops": 3}
_MA_SAN_BAY = re.compile(r"^[A-Z]{3}(,[A-Z]{3})*$")


def _gon_chuyen(f: dict[str, Any], tot_nhat: bool) -> dict[str, Any]:
    return {
        "best": tot_nhat or None,
        "price_vnd": f.get("price"),
        "total_duration_min": f.get("total_duration"),
        "type": f.get("type"),
        "legs": [
            {
                "from": (c.get("departure_airport") or {}).get("id"),
                "depart": (c.get("departure_airport") or {}).get("time"),
                "to": (c.get("arrival_airport") or {}).get("id"),
                "arrive": (c.get("arrival_airport") or {}).get("time"),
                "airline": c.get("airline"),
                "flight_number": c.get("flight_number"),
                "duration_min": c.get("duration"),
                "overnight": c.get("overnight"),
            }
            for c in (f.get("flights") or [])
        ],
        "layovers": [
            {"airport": lv.get("id"), "duration_min": lv.get("duration"), "overnight": lv.get("overnight")}
            for lv in (f.get("layovers") or [])
        ],
        "departure_token": f.get("departure_token"),
    }


def register_search_flights_tool(mcp: FastMCP):
    @mcp.tool(
        name="search_flights",
        title="Search Flights (Tìm chuyến bay)",
        description=(
            "Tìm chuyến bay và giá vé (VND) giữa hai sân bay, gồm cả bay nội địa Việt Nam. "
            "Dữ liệu từ Google Flights.\n\n"
            "Khi nào dùng: cần giá vé, giờ bay, số điểm dừng cho một ngày cụ thể.\n"
            "Khứ hồi: truyền `return_date`. Kết quả lần đầu là chặng đi, giá là tổng khứ hồi. "
            "Muốn xem chặng về của một chuyến đi, gọi lại với cùng tham số và "
            "`departure_token` của chuyến đó.\n\n"
            "Điểm đi / đến: truyền mã IATA, hoặc tên thành phố / điểm đến ở Việt Nam "
            "(tool tự đổi ra mã, VD 'Đà Lạt' -> DLI, 'Hội An' -> DAD). Sân bay nước ngoài "
            "hoặc khi chưa chắc: gọi find_airports trước, đừng tự đoán mã.\n"
            "Mỗi lần gọi tốn 1 lượt quota.\n"
            "Trả về: `flights` (thời lượng tính bằng phút) và `price_insights`."
        ),
        tags={"journey", "serpapi", "price"},
        meta={"version": "1.0.0", "author": "Ailusion team", "data_source": "serpapi:google_flights"},
    )
    async def search_flights(
        departure_id: Annotated[
            str,
            Field(
                description=(
                    "Sân bay đi: mã IATA (VD 'SGN', nhiều mã nối dấu phẩy) hoặc tên thành phố "
                    "ở Việt Nam (VD 'Sài Gòn'). Nước ngoài: lấy mã từ find_airports."
                )
            ),
        ],
        arrival_id: Annotated[
            str,
            Field(
                description=(
                    "Sân bay đến: mã IATA (VD 'DAD') hoặc tên thành phố / điểm đến ở Việt Nam "
                    "(VD 'Hội An'). Nước ngoài: lấy mã từ find_airports."
                )
            ),
        ],
        outbound_date: Annotated[str, Field(description="Ngày bay đi, dạng YYYY-MM-DD.")],
        return_date: Annotated[
            str | None, Field(description="Ngày bay về (YYYY-MM-DD). Bỏ trống nếu một chiều.")
        ] = None,
        adults: Annotated[int, Field(ge=1, le=9, description="Số người lớn.")] = 1,
        children: Annotated[int, Field(ge=0, le=8, description="Số trẻ em 2-11 tuổi.")] = 0,
        infants_on_lap: Annotated[
            int, Field(ge=0, le=4, description="Số em bé dưới 2 tuổi ngồi cùng người lớn.")
        ] = 0,
        travel_class: Annotated[
            Literal["economy", "premium_economy", "business", "first"],
            Field(
                description="Hạng ghế.",
                json_schema_extra=nhan_lua_chon(
                    {
                        "economy": "phổ thông",
                        "premium_economy": "phổ thông đặc biệt",
                        "business": "thương gia",
                        "first": "hạng nhất",
                    }
                ),
            ),
        ] = "economy",
        stops: Annotated[
            Literal["any", "nonstop", "max_1_stop", "max_2_stops"],
            Field(
                description="Giới hạn số điểm dừng.",
                json_schema_extra=nhan_lua_chon(
                    {
                        "any": "không giới hạn",
                        "nonstop": "bay thẳng",
                        "max_1_stop": "tối đa 1 điểm dừng",
                        "max_2_stops": "tối đa 2 điểm dừng",
                    }
                ),
            ),
        ] = "any",
        max_price: Annotated[
            int | None, Field(ge=0, description="Giá vé tối đa cho cả nhóm, VND.")
        ] = None,
        departure_token: Annotated[
            str | None,
            Field(description="Token của chuyến đi đã chọn, để lấy danh sách chặng về (khứ hồi)."),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=20, description="Số lựa chọn tối đa trả về.")] = 8,
    ) -> dict[str, Any]:
        input_context = {
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "outbound_date": outbound_date,
            "return_date": return_date,
        }
        try:
            di = doi_ra_ma_iata(departure_id)
            den = doi_ra_ma_iata(arrival_id)
            for ten, ma, goc in (("departure_id", di, departure_id), ("arrival_id", den, arrival_id)):
                if not ma or not _MA_SAN_BAY.match(ma):
                    return format_validation_error(
                        ten,
                        goc,
                        "không xác định được sân bay. Gọi find_airports để lấy mã IATA rồi thử lại",
                    )
            if di == den:
                return format_validation_error("arrival_id", den, "trùng sân bay đi")
            try:
                ngay_di = doc_ngay(outbound_date, "outbound_date")
                ngay_ve = doc_ngay(return_date, "return_date") if return_date else None
            except ValueError as e:
                return format_mcp_tool_error_response(str(e), error_code=-2, input_context=input_context)
            if ngay_di < date.today():
                return format_validation_error("outbound_date", outbound_date, "đã ở quá khứ")
            if ngay_ve and ngay_ve < ngay_di:
                return format_validation_error("return_date", return_date, "trước ngày bay đi")
            if infants_on_lap > adults:
                return format_validation_error(
                    "infants_on_lap", infants_on_lap, "không được nhiều hơn số người lớn"
                )

            data = await serpapi_client.search(
                "google_flights",
                departure_id=di,
                arrival_id=den,
                outbound_date=ngay_di.isoformat(),
                return_date=ngay_ve.isoformat() if ngay_ve else None,
                type=1 if ngay_ve else 2,
                adults=adults,
                children=children or None,
                infants_on_lap=infants_on_lap or None,
                travel_class=_TRAVEL_CLASS[travel_class],
                stops=_STOPS[stops] or None,
                max_price=max_price,
                departure_token=departure_token,
                **tham_so_vn(currency=True),
            )

            ghi_chu = khong_co_ket_qua(data)
            chuyen = [_gon_chuyen(f, True) for f in data.get("best_flights") or []]
            chuyen += [_gon_chuyen(f, False) for f in data.get("other_flights") or []]
            insights = data.get("price_insights") or {}

            return drop_empty(
                {
                    "status": "success",
                    "route": f"{di} -> {den}",
                    "trip_type": "round_trip" if ngay_ve else "one_way",
                    "leg": "return" if departure_token else "outbound",
                    "currency": "VND",
                    "total_found": len(chuyen),
                    "flights": chuyen[:limit],
                    "price_insights": drop_empty(
                        {
                            "lowest_price_vnd": insights.get("lowest_price"),
                            "price_level": insights.get("price_level"),
                            "typical_price_range_vnd": insights.get("typical_price_range"),
                        }
                    )
                    or None,
                    "note": ghi_chu,
                }
            )

        except SerpApiError as e:
            logger.error("search_flights gọi SerpApi thất bại", error=str(e))
            return loi_serpapi(e, input_context)
        except Exception as e:
            logger.error("search_flights thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(
                error_message="Không tìm được chuyến bay", exception=e, input_context=input_context
            )

    return search_flights
