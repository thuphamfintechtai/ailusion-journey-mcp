# Development Guide — ailusion-journey-mcp

## 1. Nguyên tắc phân tầng

```
tool  ->  service  ->  upstream (BE / LLM / DB)
```

- **tool**: chỉ validate input, orchestrate, format output. Không gọi `httpx`
  trực tiếp, không viết SQL.
- **service**: mọi I/O nằm ở đây. Test được độc lập, mock được bằng `respx`.
- **utils**: thuần hàm, không state.

Lý do: khi đổi endpoint BE hoặc thêm cache, chỉ sửa service — không đụng tool nào.

## 2. Viết một tool

```python
def register_<ten>_tool(mcp: FastMCP):
    @mcp.tool(name="<ten>", description="...", tags={...}, meta={...})
    async def <ten>_tool(param: Annotated[str, Field(description="...")]) -> dict:
        try:
            ...
        except Exception as e:
            logger.error("<ten> thất bại", error=str(e), exc_info=True)
            return format_mcp_tool_error_response(...)
    return <ten>_tool
```

Bắt buộc:

| Quy ước | Vì sao |
|---|---|
| Tên file = tên tool | Dễ tìm, và `auto_register` log theo tên module |
| Hàm bắt đầu bằng `register_` | `auto_register` quét theo prefix này |
| File bắt đầu bằng `_` thì bị bỏ qua | Dùng cho template / WIP chưa muốn lộ |
| Tên tool tối đa 60 ký tự | Một số client cắt tên tool dài |
| `Annotated[..., Field(description=...)]` cho mọi tham số | Model đọc description để biết truyền gì |
| Không để exception lọt ra ngoài | Exception thô làm hỏng phiên MCP của client |
| Trả `dict`, qua `drop_empty()` | Bỏ field rỗng, tiết kiệm token của model |
| Log và ghi chú bằng tiếng Việt, không icon | Xem mục 3 |

### Viết `description` cho ai?

Cho **model**, không phải cho người. Model quyết định gọi tool nào chỉ dựa vào
`name` + `description` + mô tả tham số. Nên viết rõ:

- Tool làm gì, trả về gì, **đơn vị** gì (VND? phút? km?)
- Khi nào nên dùng, khi nào **không** nên dùng (phân biệt với tool gần giống)
- Định dạng tham số (`'YYYY-MM-DD'`, `['SSI','VCB']`, ...)

## 3. Quy ước log và ghi chú

Toàn bộ codebase thống nhất:

- **Không dùng icon / emoji** ở bất cứ đâu — log, `print()`, docstring, tên
  server, README. Log đi qua JSON renderer và vào hệ thống thu thập log; emoji
  chỉ làm nhiễu khi grep và vỡ khi encoding khác nhau.
- **Log message viết bằng tiếng Việt**, dạng câu ngắn mô tả việc đang xảy ra:
  `"Đang gọi upstream"`, `"Đăng ký thất bại"`, `"Health check upstream thất bại"`.
- **Ghi chú (comment, docstring) viết bằng tiếng Việt.** Giải thích *vì sao*,
  không lặp lại *cái gì* mà code đã nói.
- Tên biến, tên hàm, key của structlog: giữ không dấu để grep được
  (`tong_so_da_dang_ky=3`, không phải `tổng_số=3`).

```python
# Đúng
logger.info("Đã đăng ký tool", module=module_name, ham=func.__name__)
logger.error("Gọi backend thất bại", error=str(e), exc_info=True)

# Sai: dùng icon, và viết bằng tiếng Anh
logger.info("[icon] Registered tool")
logger.error("[icon] Backend call failed")
```

## 4. Error response

Dùng chung khuôn ở `src/utils/error_handler.py`:

```python
format_mcp_tool_error_response(error_message, exception=..., input_context={...})
format_validation_error(field_name, field_value, "phải lớn hơn 0")
format_upstream_error("ailusion-journey-be", exception=e)
```

`error_detail` chỉ xuất hiện khi `ENVIRONMENT=development` — production không
lộ stack trace ra cho model.

## 5. Transport

| Giá trị `MCP_TRANSPORT` | Dùng khi |
|---|---|
| `http` (mặc định) | Deploy container, nhiều client kết nối |
| `stdio` | Chạy local trong Claude Code / Claude Desktop |
| `sse` | Client cũ chưa hỗ trợ streamable HTTP |

**Lưu ý:** với `stdio`, **stdout là kênh giao thức JSON-RPC**. Mọi `print()` phải có
`file=sys.stderr`. `setup_logging()` đã tự chuyển log sang stderr khi ở chế độ
stdio — đừng `print()` bừa trong tool.

## 6. Cấu hình Claude Code (stdio)

`.mcp.json`:

```json
{
  "mcpServers": {
    "ailusion-journey": {
      "command": "python",
      "args": ["main.py"],
      "cwd": "/đường/dẫn/ailusion-journey-mcp",
      "env": { "MCP_TRANSPORT": "stdio" }
    }
  }
}
```

Hoặc bản HTTP đang chạy:

```bash
claude mcp add --transport http ailusion-journey http://localhost:8000/mcp
```

## 7. Test

```bash
pytest                      # smoke test
pytest tests/test_server.py -v
```

Tool gọi upstream thì mock bằng `respx` thay vì gọi BE thật:

```python
import respx, httpx

@respx.mock
async def test_my_tool():
    respx.get("http://localhost:3000/api/v1/destinations").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    ...
```

`scripts_test/` để script thử tay (gọi BE thật, in kết quả) — không chạy trong CI.

## 8. Checklist trước khi commit

- [ ] Tool có `name`, `description`, `tags`, `meta`
- [ ] Mọi tham số có `Field(description=...)`
- [ ] try/except bao toàn bộ, trả error response chuẩn
- [ ] Không `print()` (dùng `logger`)
- [ ] Log và ghi chú bằng tiếng Việt, không có icon/emoji
- [ ] `pytest` xanh
- [ ] Server start được: `python main.py`
