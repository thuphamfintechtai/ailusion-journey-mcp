# Lịch Việt Nam — `vn_calendar`, `convert_lunar_date`

Tool trả cho từng ngày: nghỉ hay làm (có nghỉ bù / làm bù), ngày âm lịch, lễ
hội theo tỉnh, ngày kỷ niệm và mức cao điểm đi lại 0-3 kèm lý do.

Không có ngày lễ nào bị hard-code trong code Python. Dữ liệu ghép từ nhiều
nguồn, nguồn ưu tiên cao thắng khi mâu thuẫn:

| Nguồn | Ưu tiên | Lấy ở đâu | Cập nhật |
|---|---|---|---|
| `rules` | 10 | `src/data/calendar/rules.json` | Chỉ sửa khi luật đổi (thêm ngày lễ mới, lễ hội mới) |
| `google_ics` | 50 | Lịch ngày lễ VN công khai của Google Calendar, không cần key | Tự động, cache `CALENDAR_ICS_TTL` giây |
| `overrides` | 100 | `src/data/calendar/overrides/<năm>.json` | Khi Chính phủ ra quyết định mà Google chưa có |

Quy tắc gộp ngày nghỉ (code ở `src/services/vn_calendar/service.py`):

1. Ngày nằm trong khoảng của một file override -> override quyết định.
2. Nguồn ưu tiên cao có dữ liệu ngày nghỉ cho năm đó (`covers_day_offs`) -> bỏ
   ngày nghỉ "dự kiến" của nguồn thấp hơn. Năm Google chưa có (thường 2-3 năm
   tới) thì dùng ước tính từ `rules`, trường `source` trong kết quả là `rules`.
3. Không nguồn nào nói gì -> Thứ 7, Chủ nhật là nghỉ.

Lễ hội và ngày kỷ niệm gộp từ mọi nguồn, bỏ trùng tên.

## Mở rộng

**Thêm ngày lễ / lễ hội lặp lại hằng năm**: thêm một phần tử vào `events`
trong `rules.json`. Ngày âm ghi theo âm lịch, code tự quy ra dương lịch cho mọi
năm. Luật mới áp dụng từ năm nào thì đặt `from_year`.

```json
{"id": "hoi_lim", "name": "Hội Lim", "calendar": "lunar", "month": 1, "day": 12,
 "duration_days": 3, "kind": "festival", "provinces": ["Bắc Ninh"], "crowd": "cao"}
```

**Thêm mùa cao điểm**: thêm vào `seasons` (dương hoặc âm lịch, vắt qua năm được).

**Quyết định nghỉ lễ của một năm** (VD Tết được nghỉ dài hơn luật): tạo
`overrides/<năm>.json`. Repo `ailusion-journey-data-collector` có thể ghi file
này tự động; đổi thư mục bằng `CALENDAR_OVERRIDES_DIR`.

```json
{"year": 2027, "source": "...", "source_url": "...", "verified": true,
 "days_off": [{"from": "2027-02-04", "to": "2027-02-10", "name": "Nghỉ Tết Nguyên Đán"}],
 "working_days": [{"date": "2027-02-20", "name": "Làm bù"}]}
```

**Thêm nguồn mới** (VD API của đối tác, bảng trong Postgres): viết class có
`name`, `priority`, `async load(year) -> SourceResult` trong
`src/services/vn_calendar/sources.py`, đăng ký vào `SOURCE_FACTORIES`, rồi thêm
tên vào `CALENDAR_SOURCES`. Không phải sửa tool hay service.

## Cấu hình

| Biến | Mặc định |
|---|---|
| `CALENDAR_SOURCES` | `rules,google_ics,overrides` |
| `CALENDAR_ICS_URL` | Lịch `vi.vietnamese#holiday` của Google |
| `CALENDAR_ICS_TTL` | `86400` |
| `CALENDAR_RULES_FILE` | trống = `src/data/calendar/rules.json` |
| `CALENDAR_OVERRIDES_DIR` | trống = `src/data/calendar/overrides/` |

Google không tải được thì dùng bản cache cũ; chưa có cache thì bỏ qua nguồn đó
và trả `warnings` trong kết quả, tool vẫn chạy bằng `rules` + `overrides`.

## Lưu ý dữ liệu

- `overrides/2027.json` hiện có `verified: false`: chưa đối chiếu văn bản gốc.
- Ngày lễ hội trong `rules.json` là ngày truyền thống theo âm lịch; quy mô và
  lịch khai hội thực tế mỗi năm có thể khác.
- Âm lịch tính theo thuật toán Hồ Ngọc Đức, múi giờ UTC+7 (thư viện `lunar-vn`).
