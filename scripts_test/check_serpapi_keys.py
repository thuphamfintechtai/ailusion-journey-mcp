"""Kiểm tra từng key trong SERPAPI_API_KEYS bằng Account API.

Account API miễn phí, không trừ quota. Script in plan, quota còn lại và
giới hạn theo giờ của từng key (key chỉ hiện 6 ký tự đầu).

Chạy từ thư mục gốc repo:
    python -m scripts_test.check_serpapi_keys
"""

import asyncio
import sys

import httpx

from src.config.settings import settings
from src.services.serpapi_client import SerpApiError, serpapi_client

TRUONG_IN = [
    ("plan_name", "Plan"),
    ("account_status", "Trạng thái"),
    ("searches_per_month", "Quota / tháng"),
    ("this_month_usage", "Đã dùng tháng này"),
    ("plan_searches_left", "Còn lại trong plan"),
    ("extra_credits", "Extra credits"),
    ("total_searches_left", "Tổng còn lại"),
    ("account_rate_limit_per_hour", "Giới hạn / giờ"),
    ("this_hour_searches", "Đã dùng giờ này"),
    ("plan_renewal_date", "Ngày gia hạn"),
]


async def main() -> int:
    keys = settings.serpapi_key_list
    if not keys:
        print("Chưa cấu hình SERPAPI_API_KEYS trong .env", file=sys.stderr)
        return 1

    tong_con_lai = 0
    try:
        for i, key in enumerate(keys, 1):
            print(f"\n[{i}] key {key[:6]}...")
            try:
                data = await serpapi_client.account(key)
            except (SerpApiError, httpx.HTTPError) as e:
                print(f"    LỖI: {e}")
                continue
            for field, nhan in TRUONG_IN:
                print(f"    {nhan:<20} {data.get(field)}")
            tong_con_lai += int(data.get("total_searches_left") or 0)
    finally:
        await serpapi_client.close()

    print(f"\nTổng số search còn lại của {len(keys)} key: {tong_con_lai}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
