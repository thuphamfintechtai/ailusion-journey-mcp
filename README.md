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
| `get_place_details` | `google_maps` (`type=place`) | Giờ mở cửa, giờ đông khách, tiếp cận |
| `get_place_reviews` | `google_maps_reviews` | Review và chủ đề được nhắc nhiều |
| `search_tripadvisor` | `tripadvisor` | Rating đối chiếu, diễn đàn |

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
| `optimize_route` | `tsp/v4` | Sắp thứ tự đi các điểm cho ngắn nhất |

Tool dùng Tavily (tìm kiếm web, cấu hình `TAVILY_API_KEYS`, gói free 1.000
credit/tháng, code ở `src/services/tavily_client.py`). Mỗi kết quả có
`source_type`: `chinh_thong` (.gov.vn), `bao_chi` (báo có giấy phép) hoặc `khac`:

| Tool | Endpoint | Dùng cho |
|---|---|---|
| `web_search` | `search` | Kiểm chứng thông tin, tìm kinh nghiệm, phản biện |
| `local_news` | `search` (`topic=news`) | Tin cấm đường, sạt lở, bão, quy định mới theo địa phương |
| `fetch_page` | `extract` | Đọc một trang đã biết URL, lọc đoạn liên quan bằng `focus` |
| `search_events` | `search` | Concert, festival, pháo hoa có ngày cụ thể |

Tool không dùng SerpApi (không tốn quota):

| Tool | Dùng cho |
|---|---|
| `vn_calendar` | Ngày nghỉ lễ / nghỉ bù, âm lịch, lễ hội theo tỉnh, mức cao điểm. Cách mở rộng: [docs/VN_CALENDAR.md](docs/VN_CALENDAR.md) |
| `convert_lunar_date` | Đổi ngày âm - dương lịch |

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
