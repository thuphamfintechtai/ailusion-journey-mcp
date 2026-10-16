"""Sao tin được: Bayes theo tiên nghiệm ước lượng từ dữ liệu, thiếu tiên nghiệm thì Hoeffding."""

import pytest

from src.services import rating_prior as rp
from src.tools._db_common import diem_pho_bien, rating_hieu_chinh


def _dong(dest, cat, mu, c, muc):
    return {"destination": dest, "category": cat, "so_cho": 30, "muc": muc,
            "rating_tien_nghiem": mu, "so_review_tien_nghiem": c, "tau2": 0.1, "s2": 1.0}


@pytest.fixture
def bang(monkeypatch):
    nhom = [_dong("*", "*", 4.44, 9.4, "toan_bo"), _dong("*", "Spa", 4.8, 8.7, "rieng"),
            _dong("dalat", "Spa", 4.6, 20.0, "rieng")]
    monkeypatch.setattr(rp, "bang_tien_nghiem", lambda: {(x["destination"], x["category"]): x for x in nhom})


def test_tra_tien_nghiem_lui_theo_muc(bang):
    assert rp.tien_nghiem("Đà Lạt", "Spa")["rating_tien_nghiem"] == 4.6  # riêng điểm đến
    assert rp.tien_nghiem("hue", "Spa")["rating_tien_nghiem"] == 4.8  # lùi lên loại toàn quốc
    assert rp.tien_nghiem("hue", "Bảo tàng")["muc"] == "toan_bo"  # lùi lên toàn bộ
    d = rp.sao_tin_duoc(5.0, 3, "dalat", "Spa")
    assert d["method"] == "bayes" and d["value"] == round((20 * 4.6 + 3 * 5.0) / 23, 3)


def test_thieu_tien_nghiem_thi_lui_ve_hoeffding(monkeypatch):
    monkeypatch.setattr(rp, "bang_tien_nghiem", lambda: {})
    d = rp.sao_tin_duoc(4.8, 40, "dalat", "Spa")
    assert d == {"value": 4.026, "method": "hoeffding"}  # cùng số với bước chấm cũ của LLM
    assert rp.sao_tin_duoc(None, 10) is None and rp.sao_tin_duoc(4.5, 0) is None


def test_sap_theo_sao_tin_duoc_nhung_popular_giu_cach_cu(bang):
    it_review = {"rating": 5.0, "review_count": 3, "category": "Spa", "_dest": "dalat"}
    dong_review = {"rating": 4.7, "review_count": 3000, "category": "Spa", "_dest": "dalat"}
    assert rating_hieu_chinh(dong_review) > rating_hieu_chinh(it_review)
    # "popular" vẫn là sao × log10(review + 10) (tự đặt) để chỗ nổi tiếng còn lọt vào.
    noi_tieng = {"rating": 4.4, "review_count": 99_000, "category": "Điểm thu hút khách du lịch"}
    spa = {"rating": 5.0, "review_count": 3_500, "category": "Spa"}
    assert diem_pho_bien(noi_tieng) > diem_pho_bien(spa)


def test_file_tien_nghiem_that_doc_duoc():
    """data/rating_prior.json đi kèm repo: có dòng toàn bộ dữ liệu để mọi nhóm đều lùi được."""
    rp.bang_tien_nghiem.cache_clear()
    bang = rp.bang_tien_nghiem()
    assert ("*", "*") in bang
    assert 1 <= bang[("*", "*")]["rating_tien_nghiem"] <= 5
