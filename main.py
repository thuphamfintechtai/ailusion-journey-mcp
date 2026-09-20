#!/usr/bin/env python3
"""Entry point cho Ailusion Journey MCP Server.

Transport chọn bằng biến môi trường `MCP_TRANSPORT`:
  - http  (mặc định) — Streamable HTTP, dùng khi deploy
  - stdio             — dùng khi chạy local trong Claude Code / Claude Desktop
  - sse               — legacy, chỉ giữ cho client cũ

Việc warmup service nằm trong `lifespan` của FastMCP (xem src/fastmcp_server.py),
không gọi lại ở đây để tránh khởi tạo hai lần.
"""

import os
import sys

if __name__ == "__main__":
    from src.fastmcp_server import mcp

    transport = os.getenv("MCP_TRANSPORT", "http").lower()
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))

    if transport == "stdio":
        # stdout là kênh giao thức JSON-RPC — mọi thứ in ra phải đi stderr.
        print("Khởi động Ailusion Journey MCP Server (stdio)", file=sys.stderr)
        mcp.run(transport="stdio", show_banner=False)
    else:
        print("Khởi động Ailusion Journey MCP Server")
        print(f"HTTP Server:  http://{host}:{port}")
        print(f"MCP Endpoint: http://{host}:{port}/mcp")
        print(f"Health Check: http://{host}:{port}/health")
        print("=" * 50)

        # show_banner=False: banner mặc định của FastMCP có emoji, trái quy ước
        # không dùng icon của project (xem DEVELOPMENT_GUIDE mục 3).
        mcp.run(transport=transport, host=host, port=port, show_banner=False)
