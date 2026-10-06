"""Che dữ liệu cá nhân trong văn bản tiếng Việt bằng regex (không gọi mạng, không gửi dữ liệu đi đâu).

Nhận diện: email, số thẻ (kiểm Luhn), CCCD 12 số, CMND 9 số / số tài khoản / hộ chiếu (chỉ khi
có từ khoá đi kèm, tránh che nhầm số tiền), số điện thoại di động và bàn Việt Nam, biển số xe.
CHƯA nhận diện tên người và địa chỉ nhà — cần model NER tiếng Việt (VD Presidio + model PII).
"""

import re
from dataclasses import dataclass

_TU_KHOA_CMND = r"(?:cmnd|chứng minh(?: nhân dân)?|cmt)"
_TU_KHOA_TK = r"(?:stk|số tài khoản|so tai khoan|số tk|tk ngân hàng|tài khoản|account(?: number)?|acc)"
_TU_KHOA_HO_CHIEU = r"(?:hộ chiếu|ho chieu|passport|số hc)"

# (loại, regex, nhóm chứa giá trị cần che). Thứ tự = ưu tiên khi các mẫu chồng lên nhau.
_MAU: list[tuple[str, re.Pattern[str], int]] = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE), 0),
    # Có từ khoá "STK / tài khoản" thì là số tài khoản, kể cả khi tình cờ hợp lệ Luhn.
    ("bank_account", re.compile(rf"{_TU_KHOA_TK}\D{{0,25}}?(?<!\d)(\d[\d ]{{6,18}}\d)(?!\d)", re.IGNORECASE), 1),
    ("card", re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"), 0),
    ("cccd", re.compile(r"(?<!\d)0\d{11}(?!\d)"), 0),
    ("id_card", re.compile(rf"{_TU_KHOA_CMND}\D{{0,15}}?(?<!\d)(\d{{9}})(?!\d)", re.IGNORECASE), 1),
    ("passport", re.compile(rf"{_TU_KHOA_HO_CHIEU}\W{{0,15}}?\b([A-Z]\d{{7,8}})\b", re.IGNORECASE), 1),
    # Di động 0[35789]x và máy bàn 02x(x), có thể viết +84 / 84, mã vùng trong ngoặc "(028)".
    ("phone", re.compile(r"(?<![\d+])\(?(?:\+84|84|0)[\s.-]?(?:[35789]\d|2\d{1,2})\)?(?:[\s.-]?\d){7,8}(?!\d)"), 0),
    ("license_plate", re.compile(r"\b\d{2}[- ]?[A-Z]{1,2}\d?[- ]?\d{3}\.?\d{2}\b"), 0),
]

NHAN_PII = {
    "email": "[EMAIL]",
    "card": "[SO_THE]",
    "cccd": "[CCCD]",
    "id_card": "[CMND]",
    "bank_account": "[SO_TAI_KHOAN]",
    "passport": "[HO_CHIEU]",
    "phone": "[SO_DIEN_THOAI]",
    "license_plate": "[BIEN_SO]",
}


def _luhn(so: str) -> bool:
    chu_so = [int(c) for c in so if c.isdigit()]
    if not 13 <= len(chu_so) <= 19:
        return False
    tong = 0
    for i, d in enumerate(reversed(chu_so)):
        if i % 2:
            d *= 2
            d -= 9 if d > 9 else 0
        tong += d
    return tong % 10 == 0


@dataclass
class PiiMatch:
    kind: str
    start: int
    end: int


def tim_pii(text: str, kinds: set[str] | None = None) -> list[PiiMatch]:
    da_lay: list[PiiMatch] = []

    def chong(a: int, b: int) -> bool:
        return any(a < m.end and m.start < b for m in da_lay)

    for loai, mau, nhom in _MAU:
        if kinds and loai not in kinds:
            continue
        for m in mau.finditer(text):
            a, b = m.span(nhom)
            if loai == "card" and not _luhn(m.group(0)):
                continue
            if not chong(a, b):
                da_lay.append(PiiMatch(loai, a, b))
    return sorted(da_lay, key=lambda m: m.start)


def che_pii(text: str, style: str = "label", kinds: set[str] | None = None) -> tuple[str, list[PiiMatch]]:
    """style='label' -> '[SO_DIEN_THOAI]'; style='partial' -> giữ 3 ký tự cuối: '*******567'."""
    ds = tim_pii(text, kinds)
    phan: list[str] = []
    vi_tri = 0
    for m in ds:
        phan.append(text[vi_tri : m.start])
        goc = text[m.start : m.end]
        if style == "partial" and m.kind != "email":
            phan.append("*" * max(len(goc) - 3, 3) + goc[-3:])
        elif style == "partial":
            ten, _, mien = goc.partition("@")
            phan.append(f"{ten[:1]}***@{mien}")
        else:
            phan.append(NHAN_PII[m.kind])
        vi_tri = m.end
    phan.append(text[vi_tri:])
    return "".join(phan), ds
