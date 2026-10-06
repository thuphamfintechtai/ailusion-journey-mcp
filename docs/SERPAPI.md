# SerpApi — dữ liệu cho Ailusion Journey

SerpApi trả kết quả Google (Hotels, Flights, Maps, Search...), TripAdvisor,
Yelp... dưới dạng JSON có cấu trúc. Tài liệu này ghi lại:

1. Cách cấu hình key trong repo
2. Dashboard SerpApi có những dữ liệu gì và lấy lại bằng API ra sao
3. Quota, chi phí, cái gì tính tiền và cái gì miễn phí
4. Các engine dùng được cho 7 ràng buộc của Ailusion Journey, kèm tham số
   và field trả về quan trọng

Thông tin lấy từ dashboard và tài liệu tại serpapi.com ngày 2026-10-01.
SerpApi thay đổi khá thường xuyên, kiểm tra lại link gốc trước khi dựa vào
một field cụ thể.

---

## 1. Cấu hình

```env
# .env
SERPAPI_API_KEYS=<key_1>,<key_2>
SERPAPI_BASE_URL=https://serpapi.com
SERPAPI_TIMEOUT=60
```

| Biến | Ý nghĩa |
|---|---|
| `SERPAPI_API_KEYS` | Một hoặc nhiều key, cách nhau bởi dấu phẩy. Lấy ở [Manage API Key](https://serpapi.com/manage-api-key) |
| `SERPAPI_BASE_URL` | Mặc định `https://serpapi.com` |
| `SERPAPI_TIMEOUT` | Giây. SerpApi cần vài giây cho mỗi search mới, nên để rộng hơn `HTTP_TIMEOUT` |

Trong code:

- `settings.serpapi_key_list` — list key đã tách và bỏ khoảng trắng
- `src/services/serpapi_client.py` — `serpapi_client.search(engine, **params)`
  và `serpapi_client.account()`, có cơ chế fallback giữa các key (bảng dưới).

Fallback giữa các key: mỗi lần gọi thử lần lượt các key, bắt đầu từ key thành
công gần nhất. Key lỗi bị cho nghỉ để lần sau không tốn request vào nó:

| Lỗi | Xử lý | Biến cấu hình (giây) |
|---|---|---|
| 401 / 403, hoặc 200 kèm "Invalid API key" | Nghỉ, thử key kế tiếp | `SERPAPI_INVALID_KEY_COOLDOWN` (86400) |
| 429 "run out of searches" (hết quota tháng) | Nghỉ, thử key kế tiếp | `SERPAPI_QUOTA_COOLDOWN` (21600) |
| 429 khác (chạm giới hạn theo giờ) | Nghỉ, thử key kế tiếp | `SERPAPI_RATE_LIMIT_COOLDOWN` (900) |
| 5xx, timeout, lỗi mạng | Không nghỉ, thử key kế tiếp | |
| 4xx khác (sai tham số) | Raise luôn | |

Khi mọi key đều đang nghỉ thì raise `SerpApiError(het_key=True)` ngay mà không
gọi SerpApi; tool trả `error_code=-4` để model biết là đừng gọi lại liền.
Trạng thái nghỉ nằm trong bộ nhớ process, restart server là reset.
`serpapi_client.key_status()` trả trạng thái từng key (đã che) để debug.
- Log của `httpx` bị hạ xuống mức WARNING vì URL SerpApi chứa `api_key`.

Kiểm tra quota từng key (Account API, không tốn search):

```bash
python -m scripts_test.check_serpapi_keys
```

> `.env` đã nằm trong `.gitignore`. Không commit key, không dán key vào issue
> hay log. Nếu lỡ lộ, vào Manage API Key bấm **Regenerate API Key**.

Ví dụ trong một tool (đi qua service, đúng quy ước ở DEVELOPMENT_GUIDE):

```python
from ..services.serpapi_client import SerpApiError, serpapi_client

data = await serpapi_client.search(
    "google_hotels",
    q="Hội An",
    check_in_date="2026-11-20",
    check_out_date="2026-11-22",
    adults=2,
    currency="VND",
    gl="vn",
    hl="vi",
)
for p in data.get("properties", []):
    print(p["name"], p.get("rate_per_night", {}).get("extracted_lowest"))
```

---

## 2. Dashboard có gì

Đăng nhập tại https://serpapi.com/dashboard. Mỗi tài khoản (và mỗi thành viên
team) có **một** private API key riêng — muốn có 2 key thì là 2 tài khoản hoặc
2 thành viên trong Team.

| Trang | Đường dẫn | Dữ liệu |
|---|---|---|
| Your Account | `/dashboard` | Tên, plan, vai trò, private key, số search tháng này, quota tháng, tổng search từ lúc tạo tài khoản, biểu đồ 7 ngày |
| API Key | `/manage-api-key` | Xem và **Regenerate** key (key cũ mất hiệu lực ngay) |
| Your Plan | `/plan` | Trạng thái, plan, giá, ngày gia hạn, quota, còn lại, extra credits, search giờ này / giờ trước, giới hạn theo giờ, Ludicrous Speed |
| Extra Credits | `/extra-credits` | Credit cộng thêm ngoài plan, dùng sau khi hết quota plan. Không mua được, chỉ cho Enterprise hoặc trường hợp đặc biệt |
| Team | `/team-members` | Thành viên, vai trò (Admin, Developer, Billing, Viewer), MFA, key của từng người |
| API Absolute Numbers | `/searches/reports` | Số search theo thời gian, lọc theo engine |
| API Engines | `/searches/engine-reports` | Phân bổ search theo engine |
| API Response Times | `/searches/time-taken` | Thời gian trung bình mỗi search thành công (giây), theo giờ / ngày / tháng |
| API Success Rates | `/searches/success-rate` | Tỉ lệ thành công theo engine |
| API Users | `/searches/user-reports` | Search theo từng thành viên team |
| Your Searches | `/searches` | Lịch sử từng search, mở lại JSON / HTML |
| Playground | `/playground` | Thử engine và tham số trên giao diện, xem JSON trả về |

Các báo cáo đều lọc được theo engine. Danh sách engine trong bộ lọc hiện có
hơn 120 mục, các nhóm liên quan du lịch: Google Hotels (+ Autocomplete,
Photos, Reviews), Google Flights (+ Autocomplete, Deals), Google Travel
Explore, Google Maps (+ Autocomplete, Directions, Photos, Photo Meta, Posts,
Reviews, Contributor Reviews), Google Local, Google Events, Tripadvisor
(+ Place, Reviews), Yelp (+ Place, Reviews), OpenTable Reviews, Apple Maps,
Bing Maps, DuckDuckGo Maps.

### Lấy dữ liệu dashboard bằng API

Trang **Your Plan** tương ứng 1-1 với **Account API** — dùng cái này để theo
dõi quota tự động thay vì mở dashboard:

```
GET https://serpapi.com/account.json?api_key=<key>
```

Miễn phí, không tính vào quota.

| Field | Ý nghĩa |
|---|---|
| `account_id`, `account_email` | Định danh tài khoản |
| `account_status` | `Active`... |
| `plan_id`, `plan_name`, `plan_monthly_price` | Plan hiện tại |
| `plan_renewal_date` | Ngày gia hạn, `null` nếu không có subscription |
| `searches_per_month` | Quota tháng của plan |
| `plan_searches_left` | Còn lại trong quota plan |
| `extra_credits` | Credit cộng thêm |
| `total_searches_left` | `plan_searches_left + extra_credits` |
| `this_month_usage` | Đã dùng tháng này |
| `this_hour_searches`, `last_hour_searches` | Số search giờ này / giờ trước |
| `account_rate_limit_per_hour` | Giới hạn theo giờ |
| `api_key` | Key (client trong repo tự bỏ field này) |

Lịch sử search lấy lại bằng **Searches Archive API**:

```
GET https://serpapi.com/searches/<search_id>.json?api_key=<key>
```

`search_id` là `search_metadata.id` của lần search trước. Lấy lại được JSON /
HTML trong **31 ngày**.

### Ảnh chụp tài khoản lúc khảo sát (2026-10-01)

Tài khoản đăng nhập dashboard lúc viết tài liệu:

| Mục | Giá trị |
|---|---|
| Plan | Free Plan, $0 |
| Trạng thái | Active, gia hạn 2026-11-01 |
| Quota / tháng | 250 |
| Đã dùng | 0 |
| Giới hạn / giờ | 250 (trang Pricing ghi Free = 50/giờ; tin con số `account_rate_limit_per_hour` của chính tài khoản) |
| Extra credits | 0 |

Key của tài khoản này **không trùng** với 2 key trong `.env`, nên 2 key kia
thuộc tài khoản khác. Chạy `scripts_test/check_serpapi_keys.py` để xem plan
thật của từng key.

---

## 3. Quota và chi phí

| Plan | Giá / tháng | Search / tháng | Throughput / giờ |
|---|---|---|---|
| Free | $0 | 250 | 50 |
| Starter | $25 | 1.000 | 200 |
| Developer | $75 | 5.000 | 1.000 |
| Production | $150 | 15.000 | 3.000 |
| Big Data | $275 | 30.000 | 6.000 |
| Searcher | $725 | 100.000 | 20.000 |
| Volume | $1.475 | 250.000 | 50.000 |
| Infrastructure | $2.750 | 500.000 | 100.000 |
| Cloud 1M trở lên | từ $3.750 | từ 1.000.000 | từ 110.000 |

Cái gì tính vào quota:

| Loại | Tính quota? |
|---|---|
| Search thành công | Có |
| Search lỗi / thất bại | Không |
| Search lấy từ cache (cùng mọi tham số, trong vòng **1 giờ**) | Không |
| Account API (`/account.json`) | Không |
| Locations API (`/locations.json`) | Không |

Hệ quả cho repo:

- Nếu cả 2 key đều là Free thì tổng chỉ **500 search / tháng**. Ước tính mỗi
  lịch trình tốn 5-15 search (khách sạn + vé máy bay + vài nhóm địa điểm + chỉ
  đường), nên phải cache phía mình, không gọi SerpApi theo từng câu chat.
- Chuẩn hoá tham số trước khi gọi (cùng định dạng ngày, cùng `hl`/`gl`/`currency`)
  để trúng cache 1 giờ của SerpApi.
- Không đặt `no_cache=true` trừ khi thật sự cần giá mới nhất.
- Phần thu thập định kỳ nên nằm ở `ailusion-journey-data-collector`
  (Airflow) rồi lưu kho; MCP chỉ gọi SerpApi trực tiếp khi kho không có.

---

## 4. Search API chung

Mọi engine dùng chung một endpoint:

```
GET https://serpapi.com/search.json?engine=<engine>&api_key=<key>&...
```

Tham số chung:

| Tham số | Ý nghĩa |
|---|---|
| `engine` | `google` (mặc định), `google_hotels`, `google_maps`... |
| `api_key` | Bắt buộc |
| `output` | `json` (mặc định), `html` (HTML gốc để debug), `md` (Markdown gọn cho LLM, cũng gọi được qua `/search.md`) |
| `no_cache` | `true` để bỏ qua cache. Không dùng chung với `async` |
| `async` | `true` để gửi rồi lấy sau qua Searches Archive API |
| `device` | `desktop` (mặc định), `tablet`, `mobile` |
| `zero_trace` | Chỉ Enterprise — không lưu tham số và kết quả trên server SerpApi |

Mọi response có:

| Khối | Nội dung |
|---|---|
| `search_metadata` | `id`, `status` (`Processing` -> `Success` hoặc `Error`), `json_endpoint`, `raw_html_file`, `created_at`, `processed_at`, `total_time_taken` |
| `search_parameters` | Tham số SerpApi đã thực sự dùng (vd `location_requested` và `location_used`) |
| `error` | Có khi lỗi hoặc không có kết quả. SerpApi có thể trả HTTP 200 kèm `error` khi truy vấn không ra gì |
| `serpapi_pagination` | Token / link trang sau (tuỳ engine) |

### Google Search (`engine=google`)

Docs: https://serpapi.com/search-api

Tham số hay dùng:

| Nhóm | Tham số |
|---|---|
| Truy vấn | `q` (bắt buộc, hỗ trợ `site:`, `inurl:`...) |
| Vị trí | `location` (cấp thành phố, tra bằng Locations API) **hoặc** `uule` **hoặc** `lat` + `lon`; `radius` (mét) |
| Ngôn ngữ / quốc gia | `hl` (vd `vi`), `gl` (vd `vn`), `google_domain` (vd `google.com.vn`), `cr`, `lr` |
| Lọc | `tbs`, `safe`, `nfpr`, `filter` |
| Loại search | `tbm`: `isch` ảnh, `lcl` local, `vid` video, `nws` tin tức, `shop` mua sắm |
| Phân trang | `start` (0, 10, 20...) |
| Địa điểm cụ thể | `ludocid` (CID của place), `kgmid` (Knowledge Graph ID) |

Khối dữ liệu đáng chú ý cho du lịch (mỗi khối có trang docs riêng, xem menu
trái của trang Search API):

| Khối JSON | Dùng cho |
|---|---|
| `knowledge_graph` | Mô tả điểm đến, ảnh, nguồn Wikipedia |
| `local_results` / `local_map` | Quán, điểm tham quan gần: `title`, `rating`, `reviews`, `price`, `type`, `address`, `gps_coordinates`, `place_id` |
| `top_sights` | Điểm tham quan nổi bật của một thành phố |
| `popular_destinations` | Điểm đến phổ biến |
| `discover_more_places` | Gợi ý nơi tương tự |
| `events_results` | Sự kiện sắp diễn ra (thay cho Google Events API đã ngừng) |
| `answer_box` | Câu trả lời trực tiếp |
| `related_questions` | Câu hỏi liên quan (People also ask) |
| `organic_results` | Kết quả web thường: `title`, `link`, `snippet`, `source` |

---

## 5. Engine theo 7 ràng buộc của Ailusion Journey

| Ràng buộc | Engine chính | Dữ liệu lấy được |
|---|---|---|
| Ngân sách | `google_hotels`, `google_flights`, `google_travel_explore` | Giá phòng / đêm, tổng giá, giá vé, lịch sử giá vé |
| Thời gian | `google_maps` (giờ mở cửa), `google_maps_directions` (thời lượng di chuyển), `google_flights` (giờ bay) | `operating_hours`, `duration`, `departure_airport.time` |
| Sở thích | `google_maps`, `tripadvisor`, `google` (`top_sights`) | Loại địa điểm, rating, review nổi bật |
| Thời tiết | Không có engine thời tiết riêng — lấy từ nguồn khác của BE | |
| Phương tiện | `google_maps_directions`, `google_flights` | Thời lượng và chi phí theo từng phương tiện |
| Mật độ di chuyển | `google_maps_directions` | `typical_duration_range`, `durations` theo mode |
| Khả năng tiếp cận | `google_hotels` (`amenities`), `google_maps` (`type=place`), `google_maps_reviews` (`topics`) | Tiện nghi của khách sạn; với địa điểm thì tuỳ Google có dữ liệu hay không — thử trên Playground trước khi dựa vào |

### 5.1 Google Hotels — `google_hotels`

Docs: https://serpapi.com/google-hotels-api

| Tham số | Ghi chú |
|---|---|
| `q` | Bắt buộc. Tên thành phố / khu / khách sạn |
| `check_in_date`, `check_out_date` | Bắt buộc, `YYYY-MM-DD` |
| `adults` (mặc định 2), `children`, `children_ages` | Số khách |
| `currency` | Mặc định USD — đặt `VND` |
| `gl`, `hl` | `vn`, `vi` |
| `sort_by` | `3` giá thấp nhất, `8` rating cao nhất, `13` nhiều review nhất |
| `min_price`, `max_price` | Theo `currency` |
| `hotel_class` | Hạng sao 2-5 |
| `rating`, `amenities`, `property_types` | Lọc |
| `vacation_rentals` | `true` để tìm căn hộ / homestay |
| `property_token` | Lấy chi tiết một khách sạn |
| `next_page_token` | Trang sau |

Field trả về trong `properties[]`:

`name`, `type` (hotel / vacation rental), `gps_coordinates`,
`rate_per_night` và `total_rate` (có `extracted_lowest` dạng số),
`prices[]` (giá theo từng nguồn đặt phòng), `hotel_class`,
`overall_rating`, `reviews`, `location_rating`, `amenities[]`,
`excluded_amenities[]`, `images[]`, `check_in_time`, `check_out_time`,
`nearby_places[]` (kèm phương tiện và thời gian tới), `property_token`.
Ngoài ra có `brands[]`, `ads[]`, `serpapi_pagination`.

Engine phụ: Property Details (qua `property_token`), `google_hotels_reviews`,
`google_hotels_photos`, `google_hotels_autocomplete`.

### 5.2 Google Flights — `google_flights`

Docs: https://serpapi.com/google-flights-api

| Tham số | Ghi chú |
|---|---|
| `departure_id`, `arrival_id` | Mã sân bay IATA (`SGN`, `HAN`, `DAD`, `CXR`, `PQC`...) hoặc kgmid địa điểm |
| `outbound_date`, `return_date` | `YYYY-MM-DD` |
| `type` | `1` khứ hồi, `2` một chiều, `3` nhiều chặng |
| `travel_class` | `1` phổ thông, `2` phổ thông đặc biệt, `3` thương gia, `4` hạng nhất |
| `adults`, `children`, `infants_in_seat`, `infants_on_lap` | Hành khách |
| `stops`, `max_price`, `max_duration`, `include_airlines`, `exclude_airlines` | Lọc |
| `currency`, `gl`, `hl` | `VND`, `vn`, `vi` |
| `departure_token` | Chọn chuyến đi để lấy chuyến về |
| `booking_token` | Lấy lựa chọn đặt vé |

Trả về: `best_flights[]` và `other_flights[]`, mỗi phần tử có `flights[]`
(từng chặng: sân bay đi / đến, giờ, hãng, máy bay), `layovers[]`,
`total_duration` (phút), `carbon_emissions`, `price`, `type`.
`price_insights` có giá thấp nhất, khoảng giá thường gặp, lịch sử giá.
`airports` có thông tin sân bay.

Engine phụ: `google_flights_autocomplete` (tìm mã sân bay),
Booking Options, Price Insights, Flights Deals.

### 5.3 Google Travel Explore — `google_travel_explore`

Docs: https://serpapi.com/google-travel-explore-api

Dùng khi người dùng chưa chọn điểm đến: "đi đâu cuối tháng 11 với 5 triệu".

| Tham số | Ghi chú |
|---|---|
| `departure_id` | Bắt buộc |
| `arrival_id` | Tuỳ chọn |
| `outbound_date`, `return_date` hoặc `month`, `travel_duration` | Ngày cố định hoặc linh hoạt |
| `type` | `1` khứ hồi, `2` một chiều |
| `currency`, `gl`, `hl` | |

`destinations[]`: `name`, `country`, `flight_price`, `hotel_price`,
`start_date`, `end_date`, `gps_coordinates`, `destination_airport`,
`flight_duration`, `number_of_stops`, `airline`, `airline_code`, `thumbnail`.

### 5.4 Google Maps — `google_maps`

Docs: https://serpapi.com/google-maps-api

| Tham số | Ghi chú |
|---|---|
| `q` | Truy vấn, vd `quán chay Hội An` |
| `ll` | `@lat,lng,zoom` (vd `@15.8801,108.3380,15z`) |
| `type` | `search` (danh sách) hoặc `place` (một địa điểm) |
| `place_id` hoặc `data_cid` | Địa điểm cụ thể (`data_id` đã cũ) |
| `hl`, `gl` | |
| `start` | Phân trang, bước 20 |
| Lọc | `min_price`, `max_price`, `min_rating`, `open_state`, `open_on_day`, `open_at_hour` |

`local_results[]`: `title`, `place_id`, `data_id`, `data_cid`,
`gps_coordinates`, `rating`, `reviews`, `price`, `type`, `types[]`,
`address`, `open_state`, `hours`, `operating_hours` (theo từng ngày),
`phone`, `website`, `thumbnail`, `service_options`, `unclaimed_listing`.
Khi `type=place` thì dữ liệu nằm ở `place_results`.

Engine phụ: `google_maps_reviews`, `google_maps_photos`,
`google_maps_directions`, `google_maps_contributor_reviews`,
`google_maps_autocomplete`, `google_maps_posts`.

### 5.5 Google Maps Directions — `google_maps_directions`

Docs: https://serpapi.com/google-maps-directions-api

| Tham số | Ghi chú |
|---|---|
| `start_addr` / `start_coords` / `start_data_id` | Điểm đi |
| `end_addr` / `end_coords` / `end_data_id` | Điểm đến |
| `travel_mode` | Lái xe, đi bộ, xe đạp, công cộng, xe hai bánh, máy bay, `best` (mặc định). Mã số xem docs |
| `prefer` | `bus`, `subway`, `train`, `tram_light_rail` (công cộng) |
| `avoid` | `highways`, `tolls`, `ferries` |
| `time` | `depart_at:<timestamp>` hoặc `arrive_by:<timestamp>` |
| `distance_unit` | km hoặc dặm |
| `hl`, `gl` | |

`directions[]`: `travel_mode`, `via`, `distance` (mét), `duration` (giây),
`formatted_distance`, `formatted_duration`, `typical_duration_range`,
`cost`, `currency`, `start_time` / `end_time` (công cộng), `trips[]`
(từng bước). Thêm `durations[]` (thời gian theo từng phương tiện) và
`places_info[]`.

Lưu ý: chế độ xe hai bánh rất hợp với Việt Nam. `typical_duration_range`
là chỉ số tốt nhất cho "mật độ di chuyển".

### 5.6 Google Maps Reviews — `google_maps_reviews`

Docs: https://serpapi.com/google-maps-reviews-api

Tham số: `place_id` hoặc `data_id` (bắt buộc), `hl`, `sort_by`
(`qualityScore`, `newestFirst`, `ratingHigh`, `ratingLow`), `topic_id`
hoặc `query`, `num` (1-20, chỉ dùng từ trang 2), `next_page_token`.

Trả về: `place_info` và `topics` (chỉ trang đầu), `reviews[]` có `user`,
`rating`, `date`, `iso_date`, `snippet`, `details`, `likes`, `response`
(chủ quán trả lời). Trang đầu mặc định 8 review.

`topics` (cụm từ khoá kèm số lượt nhắc) dùng được để suy ra sở thích và
khả năng tiếp cận mà không phải đọc từng review.

### 5.7 Tripadvisor — `tripadvisor`

Docs: https://serpapi.com/tripadvisor-search-api

| Tham số | Ghi chú |
|---|---|
| `q` | Bắt buộc |
| `ssrc` | `a` tất cả, `r` nhà hàng, `A` hoạt động, `h` khách sạn, `g` điểm đến, `v` nhà cho thuê, `f` diễn đàn |
| `lat`, `lon` | Tìm theo toạ độ |
| `tripadvisor_domain` | Mặc định `tripadvisor.com` |
| `offset`, `limit` | Phân trang |

`places[]`: `title`, `place_id`, `place_type` (`GEO`, `ACCOMMODATION`,
`EATERY`, `ATTRACTION_PRODUCT`, `VACATION_RENTAL`), `link`, `rating`,
`reviews`, `location`, `thumbnail`, `highlighted_review`.
Khi `ssrc=f` thì có `forums[]`.

Engine phụ: `tripadvisor_place`, `tripadvisor_reviews`.

### 5.8 Google Events — `google_events` (đã ngừng)

Trang Google Events không còn trả kết quả, SerpApi đánh dấu deprecated.
Lấy sự kiện qua `engine=google` với `q="sự kiện Đà Nẵng tháng 11"` và đọc
khối `events_results`.

### 5.9 Locations API

```
GET https://serpapi.com/locations.json?q=Da%20Nang&limit=5
```

Miễn phí. Trả `id`, `google_id`, `name`, `canonical_name`,
`country_code`, `target_type`, `reach`, `gps` ([lng, lat]).
Dùng `canonical_name` làm giá trị `location` cho Google Search.

---

## 6. Tham số mặc định cho người dùng Việt Nam

| Tham số | Giá trị |
|---|---|
| `hl` | `vi` |
| `gl` | `vn` |
| `currency` | `VND` (Hotels, Flights, Travel Explore) |
| `google_domain` | `google.com.vn` (Google Search) |
| `location` | `canonical_name` từ Locations API, vd thành phố đích |

Đã có sẵn `DEFAULT_LANGUAGE=vi` và `DEFAULT_CURRENCY=VND` trong settings,
tool nên truyền 2 giá trị này xuống SerpApi thay vì hard-code.

---

## 7. Link

| Nội dung | Link |
|---|---|
| Dashboard | https://serpapi.com/dashboard |
| Plan và quota | https://serpapi.com/plan |
| Manage API Key | https://serpapi.com/manage-api-key |
| Playground | https://serpapi.com/playground |
| Search API | https://serpapi.com/search-api |
| Account API | https://serpapi.com/account-api |
| Searches Archive API | https://serpapi.com/searches-archive-api |
| Locations API | https://serpapi.com/locations-api |
| Pricing | https://serpapi.com/pricing |
