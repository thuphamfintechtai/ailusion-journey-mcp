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


_TIEN_TO_HANH_CHINH = re.compile(
    r"^(xa|phuong|thi tran|thi xa|quan|huyen|thanh pho|tp|tinh|dac khu)\s+"
)


def ten_goc_hanh_chinh(s: str) -> str:
    """'Phường Bến Nghé' / 'P. Bến Nghé' -> 'ben nghe'; 'Quận 3' / 'Q.3' -> '3'."""
    s = bo_dau(re.sub(r"^\s*(TP|P|Q|X|TT)\.\s*", " ", s, flags=re.IGNORECASE))
    return _TIEN_TO_HANH_CHINH.sub("", s).strip()


_VIET_TAT = (
    (re.compile(r"^\s*TP\.?\s*", re.IGNORECASE), "Thành phố "),
    (re.compile(r"^\s*TT\.\s*", re.IGNORECASE), "Thị trấn "),
    (re.compile(r"^\s*P\.\s*", re.IGNORECASE), "Phường "),
    (re.compile(r"^\s*Q\.\s*", re.IGNORECASE), "Quận "),
    (re.compile(r"^\s*X\.\s*", re.IGNORECASE), "Xã "),
)


def mo_rong_viet_tat(s: str) -> str:
    """'P. Bến Nghé' -> 'Phường Bến Nghé', 'Bà Rịa - Vũng Tàu' -> 'Bà Rịa Vũng Tàu' (hàm search
    của thư viện vietnam-provinces không hiểu viết tắt và gạch nối)."""
    for mau, day_du in _VIET_TAT:
        if mau.match(s):
            s = mau.sub(day_du, s, count=1)
            break
    return re.sub(r"\s*-\s*", " ", s).strip()


_TIEN_TO_CO_DAU = re.compile(
    r"^(xã|phường|thị trấn|thị xã|quận|huyện|thành phố|tp\.?|tỉnh|đặc khu)\s+", re.IGNORECASE
)


def ten_goc_co_dau(s: str) -> str:
    """Như ten_goc_hanh_chinh nhưng giữ dấu: 'Phường Hạ Long' -> 'hạ long' (khác 'hà long')."""
    s = unicodedata.normalize("NFC", mo_rong_viet_tat(s)).lower()
    return _TIEN_TO_CO_DAU.sub("", re.sub(r"\s+", " ", s)).strip()


def tach_tu(s: str | None) -> list[str]:
    """'Bún CHẢ, Hà Nội!' -> ['bún', 'chả', 'hà', 'nội']: NFC, chữ thường, giữ dấu, bỏ dấu câu."""
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFC", s or "").lower())


def co_dau(tu: str) -> bool:
    return bo_dau(tu) != tu


def go_co_dau(toks: list[str]) -> bool:
    """Người dùng gõ tiếng Việt có dấu (ít nhất một từ có dấu) hay gõ không dấu cả câu."""
    return any(co_dau(t) for t in toks)


def khop_tu(tu: str, van_co_dau: str, van_khong_dau: str, giu_dau: bool = False) -> bool:
    """Từ có dấu, hoặc câu gõ có dấu (`giu_dau`), thì so khớp giữ dấu: 'bò' không khớp 'bộ',
    'lễ hội hoa' không khớp 'văn hóa'. Câu gõ không dấu thì khớp mọi kiểu dấu ('bo' khớp 'bò',
    'bộ'). Hai văn bản là các từ cách nhau một dấu cách, có đệm dấu cách hai đầu."""
    if giu_dau or co_dau(tu):
        return f" {tu} " in van_co_dau
    return f" {tu} " in van_khong_dau


def van_ban_de_khop(s: str | None) -> tuple[str, str]:
    """Văn bản -> (bản có dấu, bản không dấu), mỗi bản đã tách từ và đệm dấu cách hai đầu."""
    tu = tach_tu(s)
    return f" {' '.join(tu)} ", f" {' '.join(bo_dau(t) for t in tu)} "
