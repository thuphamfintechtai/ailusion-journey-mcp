"""Ước lượng 2 số tiên nghiệm cho rating Bayes từ chính dữ liệu gmaps_place.

Thay 2 số tự đặt trong _db_common.rating_hieu_chinh (RATING_TIEN_NGHIEM = 4.3,
SO_REVIEW_TIEN_NGHIEM = 30) bằng số ước lượng theo từng (điểm đến, loại hình).

Mô hình (Bayes thực nghiệm, chuẩn - chuẩn):
    sao trung bình của chỗ i:  r_i ~ N(theta_i, s2 / n_i)
    sao thật của các chỗ cùng nhóm:  theta_i ~ N(mu, tau2)
    => rating hiệu chỉnh = (C * mu + n_i * r_i) / (C + n_i),   C = s2 / tau2  (số review ảo)

    s2   : phương sai sao của 1 review, tính từ phân bố 1-5 sao (reviews_per_rating)
    tau2 : chênh lệch sao thật giữa các chỗ, ước lượng DerSimonian-Laird (1986)
    mu   : trung bình có trọng số 1 / (s2/n_i + tau2)

Nhóm không đủ chỗ (K_MIN) hoặc tau2 = 0 thì lùi lên nhóm cha:
    (điểm đến, loại hình) -> (loại hình, toàn bộ) -> toàn bộ.

Chạy (máy có kết nối Postgres, cùng .env với server):
    python scripts_test/uoc_luong_rating_tien_nghiem.py --out data/rating_prior.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

K_MIN = 20  # tự đặt: số chỗ tối thiểu để ước lượng riêng 1 nhóm; dưới mức này lùi lên nhóm cha


def phuong_sai_sao(phan_bo: dict[str, Any] | None) -> tuple[float, int] | None:
    """(phương sai sao của 1 review, tổng số review trong phân bố) từ {'1': a, ..., '5': e}."""
    if not phan_bo:
        return None
    if isinstance(phan_bo, str):
        phan_bo = json.loads(phan_bo)
    dem = {int(k): int(v or 0) for k, v in phan_bo.items() if str(k) in {"1", "2", "3", "4", "5"}}
    n = sum(dem.values())
    if n < 2:
        return None
    tb = sum(k * c for k, c in dem.items()) / n
    ps = sum(c * (k - tb) ** 2 for k, c in dem.items()) / (n - 1)
    return ps, n


def uoc_luong_nhom(cho: list[dict[str, float]]) -> dict[str, Any] | None:
    """cho: [{'r': sao tb, 'n': số review, 's2': phương sai 1 review hoặc None}]. Trả mu, tau2, s2, C."""
    co_s2 = [c["s2"] for c in cho if c.get("s2") is not None]
    if not co_s2:
        return None
    s2_chung = sorted(co_s2)[len(co_s2) // 2]  # trung vị, không bị vài chỗ phân cực kéo lệch
    if s2_chung <= 0:
        return None
    # Dùng chung 1 phương sai cho cả nhóm (trung vị): chỗ có toàn 5 sao không làm phương sai = 0,
    # và công thức rút gọn (C * mu + n * r) / (C + n) cần đúng 1 số C cho cả nhóm.
    ds = [(c["r"], c["n"], s2_chung) for c in cho if c["n"] > 0]
    k = len(ds)
    if k < 2:
        return None
    w = [n / s2 for _, n, s2 in ds]
    sw = sum(w)
    mu_fe = sum(wi * r for wi, (r, _, _) in zip(w, ds)) / sw
    q = sum(wi * (r - mu_fe) ** 2 for wi, (r, _, _) in zip(w, ds))
    mau = sw - sum(wi * wi for wi in w) / sw
    tau2 = max(0.0, (q - (k - 1)) / mau) if mau > 0 else 0.0
    if tau2 <= 0:
        return {"k": k, "mu": mu_fe, "tau2": 0.0, "s2": s2_chung, "C": None}
    w2 = [1 / (s2 / n + tau2) for _, n, s2 in ds]
    mu = sum(wi * r for wi, (r, _, _) in zip(w2, ds)) / sum(w2)
    return {"k": k, "mu": mu, "tau2": tau2, "s2": s2_chung, "C": s2_chung / tau2}


def tinh_tat_ca(dong: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nhom: dict[tuple[str, str], list] = defaultdict(list)
    for d in dong:
        r, n = d.get("rating"), d.get("review_count")
        if not r or not n:
            continue
        v = phuong_sai_sao(d.get("reviews_per_rating"))
        c = {"r": float(r), "n": int(n), "s2": v[0] if v else None}
        dest, cat = (d.get("destination") or "").strip(), (d.get("category") or "").strip()
        nhom[(dest, cat)].append(c)
        nhom[("*", cat)].append(c)
        nhom[("*", "*")].append(c)
    kq = []
    toan_bo = uoc_luong_nhom(nhom[("*", "*")])
    for (dest, cat), cho in sorted(nhom.items()):
        uoc = uoc_luong_nhom(cho) if len(cho) >= K_MIN else None
        muc = "rieng"
        if not uoc or uoc["C"] is None:
            uoc, muc = uoc_luong_nhom(nhom[("*", cat)]) if len(nhom[("*", cat)]) >= K_MIN else None, "theo_loai"
            if not uoc or uoc["C"] is None:
                uoc, muc = toan_bo, "toan_bo"
        if not uoc or uoc["C"] is None:
            continue
        kq.append({"destination": dest, "category": cat, "so_cho": len(cho), "muc": muc,
                   "rating_tien_nghiem": round(uoc["mu"], 3), "so_review_tien_nghiem": round(uoc["C"], 1),
                   "tau2": round(uoc["tau2"], 4), "s2": round(uoc["s2"], 3)})
    return kq


async def _lay_du_lieu() -> list[dict[str, Any]]:
    sys.path.insert(0, os.getcwd())
    from src.services.travel_db import khoa_diem_den, travel_db
    try:
        dong = await travel_db.fetch(
            "SELECT destination, category, rating, review_count, reviews_per_rating FROM gmaps_place")
    finally:
        await travel_db.close()
    # Gom theo khoá điểm đến như lúc tra (src/services/rating_prior.py): DB lưu lẫn 'danang' và
    # 'da-nang', 'hanoi' và 'ha-noi' — không gom thì một nơi bị tách làm 2 nhóm.
    return [{**dict(d), "destination": khoa_diem_den(d.get("destination"))} for d in dong]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/rating_prior.json")
    a = ap.parse_args()
    dong = asyncio.run(_lay_du_lieu())
    kq = tinh_tat_ca(dong)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"tinh_luc": datetime.now(timezone.utc).isoformat(), "k_min": K_MIN,
                   "so_dong_du_lieu": len(dong), "nhom": kq}, f, ensure_ascii=False, indent=1)
    co_pb = sum(1 for d in dong if d.get("reviews_per_rating"))
    dem = defaultdict(int)
    for x in kq:
        if x["destination"] != "*":
            dem[x["muc"]] += x["so_cho"]
    print(f"Dòng dữ liệu: {len(dong)} · có phân bố sao: {co_pb}")
    print(f"Số chỗ theo mức ước lượng: {dict(dem)}")
    tb = [x for x in kq if x["destination"] == "*" and x["category"] == "*"]
    if tb:
        print(f"Toàn bộ: rating tiên nghiệm {tb[0]['rating_tien_nghiem']}, số review ảo {tb[0]['so_review_tien_nghiem']}")
    print(f"Đã lưu {len(kq)} nhóm vào {a.out}")


if __name__ == "__main__":
    main()
