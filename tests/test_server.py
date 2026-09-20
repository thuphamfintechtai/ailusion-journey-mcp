"""Smoke test: server import được, auto-register chạy, resource có mặt.

Chạy: pytest -q
"""

import pytest

from src.auto_register import get_available_components
from src.utils.response_utils import drop_empty


def test_server_imports():
    """Import fastmcp_server không nổ — nghĩa là auto_register đã chạy xong."""
    from src.fastmcp_server import mcp

    assert mcp is not None
    assert mcp.name == "Ailusion Journey"


def test_template_tool_is_not_registered():
    """File `_template.py` phải bị bỏ qua, không lộ lên server."""
    components = get_available_components()
    assert "_template" not in components["tools"]


@pytest.mark.asyncio
async def test_health_resource_registered():
    from src.fastmcp_server import mcp

    uris = {str(r.uri) for r in await mcp.list_resources()}
    assert "system://health" in uris


@pytest.mark.asyncio
async def test_tools_listing_works():
    """tools/list gọi được kể cả khi chưa có tool nào."""
    from src.fastmcp_server import mcp

    tools = await mcp.list_tools()
    assert isinstance(tools, list | tuple)
    # Tool mẫu `_template.py` không được lọt vào danh sách.
    assert "template_tool" not in {t.name for t in tools}


def test_drop_empty():
    assert drop_empty({"a": 1, "b": None, "c": "", "d": 0, "e": False}) == {
        "a": 1,
        "d": 0,
        "e": False,
    }
    assert drop_empty({"m": {"x": None, "y": "v"}}) == {"m": {"y": "v"}}
    assert drop_empty({"keep": [], "zero": 0}) == {"keep": [], "zero": 0}
