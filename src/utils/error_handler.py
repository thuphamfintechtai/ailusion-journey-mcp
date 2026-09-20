"""Chuẩn hoá error response cho MCP tools.

Mọi tool khi gặp lỗi đều trả về cùng một khuôn dict, để phía LLM đọc được
nhất quán thay vì mỗi tool một kiểu.
"""

import os
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def format_mcp_tool_error_response(
    error_message: str,
    exception: Exception | None = None,
    error_code: int = -1,
    input_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Format error response chuẩn cho MCP tool.

    Args:
        error_message: Thông báo lỗi cho người dùng đọc
        exception: Exception gốc (chỉ lộ chi tiết ở môi trường development)
        error_code: Mã lỗi, mặc định -1
        input_context: Tham số đầu vào gây lỗi

    Returns:
        {
            "error_code": -1,
            "error": "...",
            "error_detail": "...",   # chỉ ở development
            "input": {...},
            "status": "error"
        }

    Note:
        Hàm này CHỈ format, không log. Log ở khối except của chính tool
        để giữ được stack trace đúng chỗ.

    Example:
        >>> try:
        ...     result = risky_operation()
        ... except ValueError as e:
        ...     logger.error("Operation failed", error=str(e), exc_info=True)
        ...     return format_mcp_tool_error_response(
        ...         error_message="Tham số không hợp lệ",
        ...         exception=e,
        ...         input_context={"destination": destination},
        ...     )
    """
    response: dict[str, Any] = {
        "error_code": error_code,
        "error": error_message,
        "status": "error",
    }

    if input_context:
        response["input"] = input_context

    environment = os.getenv("ENVIRONMENT", "production").lower()
    if environment == "development" and exception:
        response["error_detail"] = str(exception)
        response["error_type"] = type(exception).__name__

    logger.debug("Formatted error response", error_code=error_code, environment=environment)
    return response


def format_validation_error(
    field_name: str,
    field_value: Any,
    validation_message: str,
    error_code: int = -2,
) -> dict[str, Any]:
    """Format lỗi validate tham số đầu vào."""
    return format_mcp_tool_error_response(
        error_message=f"Tham số '{field_name}' không hợp lệ: {validation_message}",
        error_code=error_code,
        input_context={field_name: field_value},
    )


def format_upstream_error(
    service_name: str,
    exception: Exception | None = None,
    error_code: int = -3,
) -> dict[str, Any]:
    """Format lỗi khi gọi service upstream (BE, LLM) thất bại."""
    return format_mcp_tool_error_response(
        error_message=f"Không gọi được {service_name}. Vui lòng thử lại sau.",
        exception=exception,
        error_code=error_code,
    )
