"""MCP resource: trạng thái sức khoẻ của server và các upstream."""

import json
from datetime import datetime, timezone

import structlog
from fastmcp import FastMCP

from ..config.settings import settings
from ..services.http_client import backend_client, llm_client

logger = structlog.get_logger(__name__)


def register_health_check_resource(mcp: FastMCP):
    """Đăng ký health check resource."""

    @mcp.resource(
        uri="system://health",
        name="System Health Status",
        description="Trạng thái realtime của MCP server và các service upstream (BE, LLM)",
    )
    async def health_check_resource() -> str:
        try:
            backend_healthy = await backend_client.health_check()
            llm_healthy = await llm_client.health_check()

            status = {
                "status": "healthy" if (backend_healthy and llm_healthy) else "degraded",
                "environment": settings.environment,
                "services": {
                    "backend": "healthy" if backend_healthy else "unhealthy",
                    "llm": "healthy" if llm_healthy else "unhealthy",
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            return json.dumps(status, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error("Health check resource thất bại", error=str(e))
            return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)

    return health_check_resource
