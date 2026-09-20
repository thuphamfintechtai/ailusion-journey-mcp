"""Cấu hình structured logging.

Lưu ý transport stdio: stdout là kênh JSON-RPC của MCP. Nếu log lẫn vào stdout
thì client parse lỗi ngay. Nên khi `MCP_TRANSPORT=stdio` mọi log đi ra stderr.
"""

import logging
import os
import sys

import structlog

from ..config.settings import settings


def setup_logging() -> None:
    """Setup structlog. Gọi một lần, trước khi tạo FastMCP instance."""
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # stdio transport chiếm stdout cho giao thức, nên log phải sang stderr.
    stream = sys.stderr if os.getenv("MCP_TRANSPORT", "http").lower() == "stdio" else sys.stdout

    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        stream=stream,
        force=True,
    )

    renderer = (
        structlog.dev.ConsoleRenderer(colors=True)
        if settings.log_level.upper() == "DEBUG"
        else structlog.processors.JSONRenderer(ensure_ascii=False)
    )

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            renderer,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
