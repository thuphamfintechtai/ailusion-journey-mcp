"""Đọc số tiền trong câu tiếng Việt: '500k', '2tr5', '3 củ', '1 tỏi', '$50', '2-3 triệu/người'.

Thuần luật, không gọi mạng. Quy ước:
- k / nghìn / ngàn = 1.000; tr / triệu / củ = 1.000.000; tỷ / tỏi = 1.000.000.000.
  Cố ý KHÔNG nhận 'm', 'b', 'lít', 'xị': dễ nhầm với mét ('cách 500m') hay lít xăng.
- '2tr5' = 2,5 triệu; '1tr25' = 1,25 triệu; '1tr250' = 1,25 triệu (số sau đơn vị là phần lẻ).
- '1.500.000' và '1,500,000' là dấu phân cách hàng nghìn; '2,5 triệu' / '2.5tr' là thập phân.
- Khoảng '2-3 triệu', 'từ 2 đến 3 tr': đơn vị ở số sau áp dụng cho cả số trước.
- Số trần không đơn vị chỉ nhận khi >= 1.000 (VD '5000000'), gắn `ambiguous=True`.
"""

import re
from dataclasses import dataclass, field

_HE_SO = {
    "k": 1e3, "nghìn": 1e3, "nghin": 1e3, "ngàn": 1e3, "ngan": 1e3,
    "tr": 1e6, "triệu": 1e6, "trieu": 1e6, "củ": 1e6, "cu": 1e6,
    "tỷ": 1e9, "ty": 1e9, "tỉ": 1e9, "tỏi": 1e9,
}
_TIEN_TE = {
    "đ": "VND", "d": "VND", "vnd": "VND", "vnđ": "VND", "đồng": "VND", "dong": "VND",
    "$": "USD", "usd": "USD", "đô": "USD", "do": "USD", "đô la": "USD", "dollar": "USD",
    "€": "EUR", "eur": "EUR", "euro": "EUR",
    "¥": "JPY", "jpy": "JPY", "yên": "JPY", "yen": "JPY",
    "₩": "KRW", "krw": "KRW", "won": "KRW",
    "thb": "THB", "baht": "THB", "bạt": "THB",
    "cny": "CNY", "tệ": "CNY", "nhân dân tệ": "CNY", "rmb": "CNY",
    "sgd": "SGD", "gbp": "GBP", "bảng": "GBP", "aud": "AUD", "twd": "TWD", "hkd": "HKD", "myr": "MYR",
}
_DON_VI_TINH = {
    "người": "person", "nguoi": "person", "ng": "person", "pax": "person", "khách": "person",
    "đêm": "night", "dem": "night", "ngày": "day", "ngay": "day", "vé": "ticket", "ve": "ticket",
    "phòng": "room", "phong": "room", "chuyến": "trip", "chuyen": "trip", "suất": "person",
}

_SO = r"\d+(?:[.,]\d+)*"
_HE_SO_RE = "|".join(sorted((re.escape(k) for k in _HE_SO), key=len, reverse=True))
_TT_RE = "|".join(sorted((re.escape(k) for k in _TIEN_TE), key=len, reverse=True))
_TINH_RE = "|".join(sorted((re.escape(k) for k in _DON_VI_TINH), key=len, reverse=True))

_MAU = re.compile(
    rf"""
    (?P<tt_truoc>\$|€|¥|₩)?\s*
    (?P<so1>{_SO})
    (?:\s*(?P<hs1>{_HE_SO_RE})(?P<le1>\d{{1,3}})?(?![a-zà-ỹ]))?
    (?:\s*(?:-|–|~|đến|den|tới|toi)\s*(?P<so2>{_SO}))?
    (?:\s*(?P<hs>{_HE_SO_RE})(?P<le>\d{{1,3}})?(?![a-zà-ỹ]))?
    (?:\s*(?P<tt>{_TT_RE})(?![a-zà-ỹ]))?
    (?:\s*(?:/|một|mot|mỗi|moi|cho\s+1|1)\s*(?P<tinh>{_TINH_RE})(?![a-zà-ỹ]))?
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass
class Amount:
    text: str
    value_min: float
    value_max: float
    currency: str
    per: str | None = None
    ambiguous: bool = False
    notes: list[str] = field(default_factory=list)


def _doc_so(s: str, co_he_so: bool) -> float:
    """'1.500.000' -> 1500000; '2,5' -> 2.5 (khi có đơn vị nhân); '1,500' -> 1500."""
    nhom = re.split(r"[.,]", s)
    if len(nhom) == 1:
        return float(s)
    # Mọi nhóm sau đều đủ 3 chữ số và không có đơn vị nhân -> dấu phân cách hàng nghìn.
    if all(len(g) == 3 for g in nhom[1:]) and (not co_he_so or len(nhom) > 2):
        return float("".join(nhom))
    if len(nhom) == 2:
        return float(f"{nhom[0]}.{nhom[1]}")
    return float("".join(nhom))


def parse_amounts(text: str, default_currency: str = "VND") -> list[Amount]:
    ket_qua: list[Amount] = []
    for m in _MAU.finditer(text):
        so1, so2 = m.group("so1"), m.group("so2")
        hs_rieng = m.group("hs1")
        hs = m.group("hs") or hs_rieng
        he_so = _HE_SO[hs.lower()] if hs else 1.0
        tt = m.group("tt_truoc") or m.group("tt")
        tien_te = _TIEN_TE[tt.lower()] if tt else default_currency

        a = _doc_so(so1, bool(hs)) * (_HE_SO[hs_rieng.lower()] if hs_rieng else he_so)
        b = _doc_so(so2, bool(hs)) * he_so if so2 else a
        if m.group("le1") and hs_rieng:
            a += float(m.group("le1")) / 10 ** len(m.group("le1")) * _HE_SO[hs_rieng.lower()]
            if not so2:
                b = a
        if m.group("le") and hs:
            le = m.group("le")
            b += float(le) / 10 ** len(le) * he_so
            if not so2:
                a = b

        mo_ho = not hs and not tt
        if mo_ho and max(a, b) < 1000:
            continue  # số nhỏ không đơn vị: thường là số người / số ngày, bỏ qua
        tinh = m.group("tinh")
        ket_qua.append(
            Amount(
                text=m.group(0).strip(),
                value_min=min(a, b),
                value_max=max(a, b),
                currency=tien_te,
                per=_DON_VI_TINH.get(tinh.lower()) if tinh else None,
                ambiguous=mo_ho,
                notes=["số không kèm đơn vị, hiểu là " + tien_te] if mo_ho else [],
            )
        )
    return ket_qua
