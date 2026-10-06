"""Hàm xử lý chuỗi tiếng Việt dùng chung."""

import re
import unicodedata


def bo_dau(s: str) -> str:
    """'Đà Nẵng' -> 'da nang': bỏ dấu, chữ thường, gom khoảng trắng."""
    s = s.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return s.strip()
