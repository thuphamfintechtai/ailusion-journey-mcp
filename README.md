# ailusion-journey-mcp

MCP server của **Ailusion Journey** — lập kế hoạch du lịch cá nhân hoá theo
ngân sách, thời gian, sở thích, thời tiết, phương tiện, mật độ di chuyển và
khả năng tiếp cận.

Server này là lớp **giao diện MCP**: khai báo tool / resource / prompt cho LLM
gọi, rồi uỷ thác việc lấy dữ liệu và tính toán xuống `ailusion-journey-be` và
`ailusion-journey-llm`.

## Chạy nhanh

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # sửa BACKEND_BASE_URL, LLM_BASE_URL, SERPAPI_API_KEYS

python main.py                # HTTP  -> http://localhost:8002/mcp
MCP_TRANSPORT=stdio python main.py   # stdio -> dùng trong Claude Code/Desktop
```

Kiểm tra:

```bash
curl localhost:8002/health
pytest
```

## Chạy server và MCP Inspector

MCP Inspector là công cụ chính thức để xem và gọi thử tool/resource mà không
cần client thật. Cần Node.js (dùng `npx`, không phải cài gì thêm).

Cách nhanh nhất — một lệnh chạy cả server lẫn Inspector web, Inspector đã
cấu hình sẵn `http://localhost:8002/mcp` và tự mở trình duyệt, Ctrl+C tắt cả hai:

```bash
bash scripts_test/dev.sh
```

Muốn chạy từng phần thì làm theo các bước dưới.

### 1. Chạy server

```bash
source venv/bin/activate
python main.py
```

Server lên ở `http://localhost:8002/mcp`, health ở `http://localhost:8002/health`.

Nếu port đang bị chiếm:

```bash
lsof -ti:8002 | xargs -r kill -9
```

### 2. Inspector - chế độ CLI (nhanh, hợp để script và CI)

```bash
# Liệt kê tool
npx -y @modelcontextprotocol/inspector --cli http://localhost:8002/mcp \
  --transport http --method tools/list

# Liệt kê resource
npx -y @modelcontextprotocol/inspector --cli http://localhost:8002/mcp \
  --transport http --method resources/list

# Đọc một resource
npx -y @modelcontextprotocol/inspector --cli http://localhost:8002/mcp \
  --transport http --method resources/read --uri "system://health"

# Gọi một tool (đổi tool-name và tham số theo tool của bạn)
npx -y @modelcontextprotocol/inspector --cli http://localhost:8002/mcp \
  --transport http --method tools/call \
  --tool-name build_itinerary --tool-arg destination="Đà Nẵng" --tool-arg days=3
```

### 3. Inspector - chế độ giao diện web

```bash
npx -y @modelcontextprotocol/inspector
```

Lệnh này in ra URL kèm token, dạng:

```
http://127.0.0.1:6274?MCP_INSPECTOR_API_TOKEN=<token>
```

Mở URL đó, rồi trong giao diện chọn:

| Trường | Giá trị |
|---|---|
| Transport Type | `Streamable HTTP` |
| URL | `http://localhost:8002/mcp` |

Bấm **Connect**. Tab `Tools` / `Resources` / `Prompts` sẽ hiện những gì
`auto_register` đã nạp.

Inspector dùng port `6274` (web) và `6275` (sandbox; bản cũ dùng `6277` làm proxy). Dọn khi cần:

```bash
lsof -ti:6274,6275,6277 | xargs kill -9
```

### 4. Test bằng chính Claude Code

Cách sát thực tế nhất, vì đây đúng là cách tool sẽ được gọi:

```bash
# Server HTTP đang chạy sẵn
claude mcp add --transport http ailusion-journey http://localhost:8002/mcp

# Hoặc để Claude Code tự khởi động server qua stdio
claude mcp add ailusion-journey -e MCP_TRANSPORT=stdio -- \
  /đường/dẫn/ailusion-journey-mcp/venv/bin/python main.py

claude mcp list          # kiểm tra kết nối
```

Gỡ ra: `claude mcp remove ailusion-journey`.

## SerpApi

Dữ liệu khách sạn, vé máy bay, địa điểm, chỉ đường và review lấy qua
[SerpApi](https://serpapi.com). Khai báo key trong `.env`:

```env
SERPAPI_API_KEYS=<key_1>,<key_2>   # nhiều key cách nhau bởi dấu phẩy
```

Client `src/services/serpapi_client.py` tự chuyển sang key kế tiếp khi key
hiện tại hết quota, bị từ chối, timeout hoặc SerpApi lỗi 5xx; key lỗi được cho
nghỉ một thời gian để không tốn request (chi tiết ở docs/SERPAPI.md mục 1).

Tool đang dùng SerpApi:

| Tool | Engine | Dùng cho |
|---|---|---|
| `search_hotels` | `google_hotels` | Khách sạn / homestay, giá theo đêm (VND) |
| `search_flights` | `google_flights` | Vé máy bay một chiều / khứ hồi |
| `find_airports` | bảng nội bộ VN, `google_flights_autocomplete` | Tra mã IATA từ tên thành phố / điểm đến |
| `search_places` | `google_maps` | Tìm quán ăn, điểm tham quan, cây xăng... |
| `get_place_details` | `google_maps` (`type=place`) | Giờ mở cửa, giờ đông khách, tiếp cận, `thumbnail` + tối đa 6 `images` |
| `get_place_photos` | `gmaps_place.thumbnail` + `google_maps` (`type=place`) | Ảnh đại diện cho nhiều điểm: đọc DB trước, thiếu thì tra SerpApi (tối đa 12 điểm / lần) và ghi lại DB |
| `get_place_reviews` | `google_maps_reviews` | Review và chủ đề được nhắc nhiều |

Tool dùng Vietmap (bản đồ Việt Nam, cấu hình `VIETMAP_API_KEYS`, fallback và
cache giống SerpApi, code ở `src/services/vietmap_client.py`). Đây là lựa chọn
mặc định cho tìm địa điểm / toạ độ / đường đi; SerpApi chỉ dùng khi cần giá,
rating, review, giờ mở cửa:

| Tool | Endpoint | Dùng cho |
|---|---|---|
| `find_nearby` | `autocomplete/v4` + `place/v4` | Tìm quán, khách sạn, cây xăng... quanh một điểm |
| `geocode_vn` | `search/v4`, `reverse/v4` | Toạ độ, chuẩn hoá địa chỉ cũ <-> mới sau sáp nhập |
| `get_route` | `route/v4` | Chỉ đường xe máy / ô tô / xe tải, phí cầu đường |
| `distance_matrix` | `matrix/v4` | Thời gian, quãng đường giữa nhiều điểm |
| `plan_day_route` | `vrp` | Lộ trình trong ngày theo giờ mở cửa, thời gian tham quan, nghỉ trưa; truyền `date` thì tự lấy giờ mở cửa đã lưu, nhận cả place_id |
| `elevation_profile` | `route/v4` + Open Topo Data | Độ cao, độ dốc dọc tuyến (đèo, xe máy, xe lăn / xe đẩy) |

Tool dùng Tavily (tìm kiếm web, cấu hình `TAVILY_API_KEYS`, gói free 1.000
credit/tháng, code ở `src/services/tavily_client.py`). Mỗi kết quả có
`source_type`: `chinh_thong` (.gov.vn), `bao_chi` (báo có giấy phép) hoặc `khac`:

| Tool | Endpoint | Dùng cho |
|---|---|---|
| `web_search` | `search` | Kiểm chứng thông tin, tìm kinh nghiệm, phản biện |
| `fetch_page` | `extract` | Đọc một trang đã biết URL, lọc đoạn liên quan bằng `focus` |
| `search_events` | `search` | Concert, festival, pháo hoa có ngày cụ thể |

Tool dùng WeatherAPI.com (cấu hình `WEATHERAPI_API_KEYS`, code ở
`src/services/weather_client.py`). Tên địa danh được Vietmap đổi ra toạ độ vùng
trước (bộ tra tên của WeatherAPI hay nhầm địa danh nhỏ ở Việt Nam); tên trùng
giữa nhiều tỉnh thì tool báo lỗi kèm ứng viên thay vì đoán:

| Tool | Endpoint | Dùng cho |
|---|---|---|
| `weather_forecast` | `forecast` / `future` / `history` | Thời tiết theo ngày, cảnh báo theo ngưỡng KTTV (nắng nóng, rét đậm, mưa to, UV) |
| `air_quality` | `forecast?aqi=yes` | Mức AQI, PM2.5, giờ ô nhiễm nhất, lời khuyên cho trẻ nhỏ / người già |
| `marine_forecast` | `marine` | Sóng, gió giật, nhiệt độ nước, thuỷ triều cho chuyến đi đảo |
| `sun_and_sky` | `astronomy` + `forecast` | Giờ mặt trời / mặt trăng, giờ vàng, gợi ý săn mây và ngắm sao |

Tool không dùng SerpApi (không tốn quota):

| Tool | Dùng cho |
|---|---|
| `parse_money` | Đọc số tiền trong câu: '500k', '2tr5', '3 củ', '$50', '2-3 triệu/người' |
| `convert_currency` | Đổi tiền theo tỷ giá Vietcombank (cache >= 5 phút), dự phòng tỷ giá thị trường |
| `score_options` | Xếp hạng phương án bằng TOPSIS / tổng trọng số, nhiều bộ trọng số |
| `export_itinerary` | Xuất lịch trình ra file `.ics` và link Google Maps |
| `mask_pii` | Che SĐT, email, CCCD, số tài khoản, số thẻ, biển số trước khi lưu / ghi log |
| `source_trust` | Chấm tin cậy domain: loại nguồn, hạng Tranco, tuổi domain (Wayback) |
| `disaster_alerts` | Cảnh báo thiên tai gần Việt Nam và Biển Đông (GDACS) |
| `google_news` | Tin mới theo từ khoá (Google News RSS). **Chỉ dùng phi thương mại**, tắt bằng `GOOGLE_NEWS_RSS_ENABLED=false` |
| `convert_admin_unit` | Đổi phường / quận / tỉnh cũ <-> mới sau sáp nhập 1/7/2025 (thư viện `vietnam-provinces`, offline, GPL-3.0) |
| `list_admin_units` | Danh mục 34 tỉnh / thành và phường / xã hiện hành, kiểm tra tên có tồn tại |
| `vn_calendar` | Ngày nghỉ lễ / nghỉ bù, âm lịch, lễ hội theo tỉnh, mức cao điểm. Cách mở rộng: [docs/VN_CALENDAR.md](docs/VN_CALENDAR.md) |
| `convert_lunar_date` | Đổi ngày âm - dương lịch |

Tool đọc dữ liệu đã cào sẵn trong Postgres (DB `MLAI-AILUSION`, cấu hình `PG_HOST`,
`PG_USER`, `PG_PASSWORD`, `PG_DATA_DATABASE`; code ở `src/services/travel_db.py`), không tốn quota:

| Tool | Bảng | Dùng cho |
|---|---|---|
| `browse_places` | `gmaps_place` | Duyệt địa điểm theo điểm đến / loại / rating / bán kính / xe lăn / khoảng giá; trả kèm `price_vnd`, `rating_adjusted` và `thumbnail` |
| `check_opening_hours` | `gmaps_place.open_hours` | Nhiều điểm có mở vào giờ dự định không, đủ thời gian ở lại không |
| `best_time_to_visit` | `gmaps_place.popular_times` | Giờ / ngày vắng khách nhất trong giờ mở cửa |
| `find_festivals` | `event` | Lễ hội, sự kiện theo tỉnh (sau sáp nhập) và khoảng ngày |
| `destination_snapshot` | cả 3 nhóm bảng | Tổng quan điểm đến: thời tiết, top địa điểm theo nhóm, lễ hội |
| `find_stays` | `gmaps_place.summary` | Chỗ ở theo ngân sách / đêm (giá tham khảo lúc cào), loại, rating, bán kính, mặt bằng giá p25 / trung vị / p75 |
| `find_places_by_needs` | `gmaps_place.attributes` | Địa điểm đáp ứng cùng lúc nhiều nhu cầu: xe lăn, trẻ em, ăn chay, đỗ ô tô, mang chó, Wi-Fi, lãng mạn, rooftop... (39 nhu cầu), lọc thêm theo khoảng giá |
| `check_place_needs` | `attributes` + `attributes_not_available` | Rà từng điểm trong lịch trình: có / không / chưa rõ cho mỗi nhu cầu, kèm lưu ý (chỉ nhận tiền mặt, phải đặt chỗ, phí vào cổng) |
| `compare_places` | `gmaps_place` | So sánh 2-10 điểm (rating hiệu chỉnh, % 1 sao, giá, mức đông, khoảng cách, nhu cầu), xuất sẵn đầu vào cho `score_options` |
| `rating_insights` | `reviews_per_rating` + `reviews` | Rating có đáng tin không: phân bố sao, phân cực, xếp hạng trong cùng loại, độ mới, review điểm thấp |
| `find_alternatives` | `gmaps_place` | Điểm thay thế cùng loại ở gần khi điểm cũ đóng cửa / quá đông / không đáp ứng nhu cầu |

Cùng dữ liệu này làm **fallback**: khi API ngoài lỗi hoặc hết quota, `search_places`,
`get_place_details`, `get_place_reviews` (SerpApi), `find_nearby` (Vietmap), `search_events`
(Tavily), `search_hotels` (SerpApi, trả chỗ ở với giá tham khảo) và `weather_forecast` (WeatherAPI) tự trả dữ liệu đã lưu kèm `data_source: ailusion_db`,
`fallback_reason`, `data_as_of`. DB không có dữ liệu khớp thì vẫn trả lỗi gốc. Tắt bằng
`PG_FALLBACK_ENABLED=false`.

Kiểm tra quota từng key (miễn phí):

```bash
python -m scripts_test.check_serpapi_keys
```

Dashboard có gì, quota tính ra sao, engine nào dùng cho ràng buộc nào và các
field trả về: xem [docs/SERPAPI.md](docs/SERPAPI.md).

## Cấu trúc

```
main.py                  Entry point, chọn transport (http | stdio | sse)
src/
├── fastmcp_server.py    Tạo FastMCP instance, lifespan, /health, gọi auto_register
├── auto_register.py     Quét tools/ resources/ prompts/ tìm hàm register_*
├── config/settings.py   Settings đọc từ .env (pydantic-settings)
├── tools/               MỖI TOOL = 1 FILE — chỗ viết code chính
│   └── _template.py     Khuôn mẫu (prefix _ nên không bị đăng ký)
├── resources/           Resource MCP (health_check đã có sẵn)
├── prompts/             Prompt template
├── services/            Gọi upstream: http_client.py (backend_client, llm_client),
│                        serpapi_client.py (SerpApi, xoay vòng nhiều key)
├── models/              Pydantic schema dùng chung cho nhiều tool
└── utils/               logging, error_handler, response_utils
tests/                   Smoke test + test SerpApiClient
scripts_test/            Script thử tay (dev.sh, check_serpapi_keys.py)
docs/SERPAPI.md          Dữ liệu SerpApi: dashboard, quota, engine, field
```

## Thêm một tool

1. `cp src/tools/_template.py src/tools/build_itinerary.py`
2. Đổi tên hàm thành `register_build_itinerary_tool(mcp)`, đổi `name=` trong
   `@mcp.tool(...)` thành `build_itinerary`
3. Viết logic, gọi upstream qua `src/services/`
4. Restart server — `auto_register` tự nhận

Không phải sửa `fastmcp_server.py` hay `tools/__init__.py`.

Chi tiết quy ước: xem [DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md).

## Repo liên quan

| Repo | Vai trò |
|---|---|
| [`ailusion-journey-fe`](https://github.com/thuphamfintechtai/ailusion-journey-fe) | Next.js — giao diện người dùng |
| [`ailusion-journey-be`](https://github.com/thuphamfintechtai/ailusion-journey-be) | Node.js — API, dữ liệu, nghiệp vụ |
| [`ailusion-journey-llm`](https://github.com/thuphamfintechtai/ailusion-journey-llm) | Python — suy luận LLM |
| [`ailusion-journey-data-collector`](https://github.com/thuphamfintechtai/ailusion-journey-data-collector) | Airflow — DAG thu thập dữ liệu du lịch (điểm đến, giá, thời tiết) đổ về kho dữ liệu cho BE dùng |
| `ailusion-journey-mcp` | **repo này** — lớp MCP cho LLM gọi tool |

Luồng dữ liệu:

```
data-collector (Airflow)  ->  kho dữ liệu  ->  be (API)  ->  mcp (tool)  ->  llm / client
```
