"""Dọn field rỗng khỏi response của tool trước khi trả cho model.

Vì sao
------
Các tool build response theo một khuôn cố định rồi gán ``None`` cho những gì
không có. Với model thì mỗi key rỗng vẫn là một dòng phải đọc và phải suy luận
xem "null" nghĩa là *không có dữ liệu* hay *quên điền* — tốn token mà không
thêm thông tin. Bỏ hẳn key đi thì "vắng mặt" tự nó là câu trả lời.

Chỉ bỏ ``None`` và chuỗi rỗng. ``0``, ``False`` và list rỗng được giữ nguyên vì
chúng là giá trị thật: ``"so_diem_da_chon": 0`` khác hẳn với việc không có key đó.
"""

from typing import Any


def drop_empty(value: Any) -> Any:
    """Trả bản sao của ``value`` đã bỏ mọi key có giá trị ``None`` hoặc ``''``.

    Đệ quy vào dict và list lồng nhau.

    >>> drop_empty({"a": 1, "b": None, "c": "", "d": 0, "e": False})
    {'a': 1, 'd': 0, 'e': False}
    >>> drop_empty({"m": {"x": None, "y": "v"}, "l": [{"k": None, "j": 2}]})
    {'m': {'y': 'v'}, 'l': [{'j': 2}]}
    >>> drop_empty({"keep_empty_list": [], "keep_zero": 0})
    {'keep_empty_list': [], 'keep_zero': 0}
    """
    if isinstance(value, dict):
        return {k: drop_empty(v) for k, v in value.items() if v is not None and v != ""}
    if isinstance(value, list):
        return [drop_empty(v) for v in value]
    return value


def success_response(data: Any, **meta: Any) -> dict[str, Any]:
    """Khuôn response thành công, đối xứng với ``format_mcp_tool_error_response``."""
    response: dict[str, Any] = {"status": "success", "data": data}
    if meta:
        response.update(meta)
    return drop_empty(response)
