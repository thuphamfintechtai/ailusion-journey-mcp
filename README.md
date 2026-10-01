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

python main.py                # HTTP  -> http://localhost:8000/mcp
MCP_TRANSPORT=stdio python main.py   # stdio -> dùng trong Claude Code/Desktop
```

Kiểm tra:

```bash
curl localhost:8000/health
pytest
```

## Chạy server và MCP Inspector

MCP Inspector là công cụ chính thức để xem và gọi thử tool/resource mà không
cần client thật. Cần Node.js (dùng `npx`, không phải cài gì thêm).

### 1. Chạy server

```bash
source venv/bin/activate
python main.py
```

Server lên ở `http://localhost:8000/mcp`, health ở `http://localhost:8000/health`.

Nếu port đang bị chiếm:

```bash
lsof -ti:8000 | xargs -r kill -9
```

### 2. Inspector - chế độ CLI (nhanh, hợp để script và CI)

```bash
# Liệt kê tool
npx -y @modelcontextprotocol/inspector --cli http://localhost:8000/mcp \
  --transport http --method tools/list

# Liệt kê resource
npx -y @modelcontextprotocol/inspector --cli http://localhost:8000/mcp \
  --transport http --method resources/list

# Đọc một resource
npx -y @modelcontextprotocol/inspector --cli http://localhost:8000/mcp \
  --transport http --method resources/read --uri "system://health"

# Gọi một tool (đổi tool-name và tham số theo tool của bạn)
npx -y @modelcontextprotocol/inspector --cli http://localhost:8000/mcp \
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
| URL | `http://localhost:8000/mcp` |

Bấm **Connect**. Tab `Tools` / `Resources` / `Prompts` sẽ hiện những gì
`auto_register` đã nạp.

Inspector dùng 2 port: `6274` (web) và `6277` (proxy). Dọn khi cần:

```bash
lsof -ti:6274,6277 | xargs -r kill -9
```

### 4. Test bằng chính Claude Code

Cách sát thực tế nhất, vì đây đúng là cách tool sẽ được gọi:

```bash
# Server HTTP đang chạy sẵn
claude mcp add --transport http ailusion-journey http://localhost:8000/mcp

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
hiện tại hết quota hoặc bị từ chối. Kiểm tra quota từng key (miễn phí):

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
scripts_test/            Script thử tay (check_serpapi_keys.py)
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
