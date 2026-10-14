"""Ailusion Journey MCP Server — khởi tạo FastMCP instance."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import structlog
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auto_register import auto_register_all
from .config.settings import settings
from .services.http_client import close_http_clients
from .services.travel_db import travel_db
from .utils.logging import setup_logging

# Setup logging trước mọi thứ khác
setup_logging()
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app):
    """Vòng đời server: warmup khi start, dọn dẹp khi shutdown."""
    logger.info("Đang khởi động Ailusion Journey MCP Server...")
    await initialize_services()
    logger.info("Khởi động hoàn tất")

    yield

    logger.info("Đang tắt server...")
    await cleanup_services()
    logger.info("Tắt server hoàn tất")


logger.info("Đang tạo FastMCP server instance...")
VERSION = "1.1.0"

mcp = FastMCP(
    name="Ailusion Journey",
    version=VERSION,
    instructions="""
    MCP server của Ailusion Journey — lập kế hoạch du lịch cá nhân hoá.

    Các tool ở đây sinh và điều chỉnh lịch trình theo 7 ràng buộc:
    ngân sách, thời gian, sở thích, thời tiết, phương tiện, mật độ di chuyển,
    và khả năng tiếp cận (accessibility).

    Dữ liệu lấy từ ailusion-journey-be; phần suy luận ngôn ngữ tự nhiên
    gọi sang ailusion-journey-llm.
    """,
    lifespan=lifespan,
)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """Health check endpoint cho k8s probe / monitoring."""
    return JSONResponse(
        {
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": "Ailusion Journey MCP Server",
            "version": VERSION,
            "environment": settings.environment,
        }
    )


# Tự động quét và đăng ký mọi tool / resource / prompt.
# Thêm file mới vào src/tools/ là xong — không phải sửa file này.
logger.info("Đang đăng ký tools, resources và prompts...")
auto_register_all(mcp)
logger.info("Đăng ký tự động hoàn tất")


async def initialize_services():
    """Warmup các service trước khi nhận request đầu tiên.

    Không raise — server vẫn phải start được kể cả khi upstream chưa sẵn sàng,
    để health check báo trạng thái thay vì crash loop.
    """
    logger.info(
        "Đã nạp cấu hình services",
        backend=settings.backend_base_url,
        llm=settings.llm_base_url,
    )
    # TODO: thêm warmup thật khi có service (ping BE, mở connection pool DB...)


async def cleanup_services():
    """Đóng connection khi shutdown."""
    try:
        await close_http_clients()
        logger.info("Đã đóng các HTTP client")
        await travel_db.close()
        logger.info("Đã đóng pool Postgres")
    except Exception as e:
        logger.error("Lỗi khi dọn dẹp services", error=str(e))
