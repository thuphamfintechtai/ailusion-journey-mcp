"""Tự động phát hiện và đăng ký MCP tools, resources, prompts.

Quy ước: mỗi file trong src/tools, src/resources, src/prompts export một
hoặc nhiều hàm tên bắt đầu bằng `register_`, nhận đúng một tham số `mcp`.
Thêm tool mới = thêm file, không phải sửa chỗ nào khác.
"""

import importlib
import os

import structlog
from fastmcp import FastMCP

logger = structlog.get_logger(__name__)

DISCOVERY_PATHS = [
    {"path": "tools", "pattern": "register_", "type": "Tools"},
    {"path": "resources", "pattern": "register_", "type": "Resources"},
    {"path": "prompts", "pattern": "register_", "type": "Prompts"},
]


def auto_register_all(mcp: FastMCP) -> None:
    """Quét cả ba thư mục và đăng ký mọi thứ tìm được."""
    total_registered = 0

    for discovery in DISCOVERY_PATHS:
        logger.info("Đang quét thư mục", loai=discovery["type"])
        try:
            total_registered += _scan_and_register(
                mcp,
                discovery["path"],
                discovery["pattern"],
                discovery["type"],
            )
        except Exception as e:
            logger.error("Quét thất bại", loai=discovery["type"], error=str(e))

    logger.info("Đăng ký tự động xong", tong_so_da_dang_ky=total_registered)


def _scan_and_register(
    mcp: FastMCP, package_path: str, pattern: str, component_type: str
) -> int:
    """Quét một package và đăng ký các hàm khớp prefix."""
    registered_count = 0
    current_dir = os.path.dirname(__file__)
    target_dir = os.path.join(current_dir, package_path)

    if not os.path.exists(target_dir):
        logger.warning("Không tìm thấy thư mục", duong_dan=target_dir)
        return 0

    for filename in sorted(os.listdir(target_dir)):
        # Bỏ qua __init__.py và file bắt đầu bằng "_" (dùng cho template/WIP).
        if not filename.endswith(".py") or filename.startswith("_"):
            continue

        module_name = filename[:-3]
        full_module_name = f"src.{package_path}.{module_name}"

        try:
            module = importlib.import_module(full_module_name)
        except Exception as e:
            logger.warning(
                "Không import được module",
                loai=component_type,
                module=module_name,
                error=str(e),
            )
            continue

        register_functions = [
            getattr(module, attr)
            for attr in dir(module)
            if attr.startswith(pattern) and callable(getattr(module, attr))
        ]

        for register_func in register_functions:
            try:
                register_func(mcp)
                logger.info(
                    "Đã đăng ký",
                    loai=component_type,
                    module=module_name,
                    ham=register_func.__name__,
                )
                registered_count += 1
            except Exception as e:
                logger.error(
                    "Đăng ký thất bại",
                    loai=component_type,
                    module=module_name,
                    ham=register_func.__name__,
                    error=str(e),
                )

    return registered_count


def get_available_components() -> dict:
    """Liệt kê component hiện có theo từng loại — dùng cho debug / test."""
    components: dict[str, list[str]] = {"tools": [], "resources": [], "prompts": []}
    base_dir = os.path.dirname(__file__)

    for component_type in components:
        component_dir = os.path.join(base_dir, component_type)
        if os.path.exists(component_dir):
            components[component_type] = sorted(
                f[:-3]
                for f in os.listdir(component_dir)
                if f.endswith(".py") and not f.startswith("_")
            )

    return components
