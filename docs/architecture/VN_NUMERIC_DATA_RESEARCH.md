# Kiểm toán dữ liệu số cho ML lướt sóng Việt Nam

**Cập nhật:** 04/10/2026. Giai đoạn hiện tại là `NUMERIC_DATA_RESEARCH`:
kiểm nguồn và thời điểm có dữ liệu trước khi đăng ký thuật toán mới. Chỉ
dùng quan sát số có đơn vị và provenance; bỏ tin tức, sentiment, embedding,
điểm LLM, giá trị sinh từ mã/hash và dữ liệu SAG đang bị đóng.

## Model đang được gọi trong code

Luồng sản xuất chính là `train_pipeline.py` →
`train_hybrid_stacking.py` → `HybridStackingRanker`; kênh standalone gọi
chính artifact ranker này khi `predict_universe` [lấy đầu vào và dự báo](../../ai-engine/app/domain/services/ml/standalone_ml_channel.py).
File export mặc định là `ai-engine/data/models/hybrid_stacking_ranker.pkl`;
code tự nạp file lúc module được import. Hệ thống cũng có HMM/regime và nhiều
chế độ dự báo khác; tên file tồn tại không chứng minh model nào đang phục vụ
một prediction PROD cụ thể. Snapshot PROD/shadow trước đây cũng không phải
giao dịch broker thật.

### Dữ liệu được đọc khi train

`fetch_training_data(cutoff)` chọn 100 mã theo tổng
`close × volume_continuous` từ 01/01/2020 tới `cutoff`; sau đó đọc OHLCV daily
cho các mã này và VNINDEX từ 01/01/2014 tới cutoff. Mẫu train cuối cùng bắt
đầu 01/01/2018. Đây là khoảng lịch sử dài và có nhiều mã, nhưng code không
lọc dated VN30 membership hay chứng minh đủ mã niêm yết/hủy niêm yết. Danh sách
100 mã được tính bằng thanh khoản trong cả khoảng tới cutoff; khi chạy
walk-forward 2020–2025, chính cách chọn universe này có thể nhìn vào turnover
của những năm test. Vì vậy số năm dữ liệu tự nó không chứng minh walk-forward
không lookahead ở bước chọn universe.

Mỗi mã đưa qua `FeatureForge` từ OHLCV và VNINDEX. Trong số feature sinh ra có:

- Return/momentum và volatility 5/10/20/60/120 phiên, Sharpe rolling, MACD,
  RSI, extreme-reversal, fractional differentiation.
- Volume/turnover anomaly, VWAP proxy, Amihud/Kyle proxy, vị trí giá trong
  biên ngày, ceiling/floor streak.
- Relative strength và correlation so với VNINDEX; breadth, sector/ecosystem
  leader, hub-shock và divergence lấy từ bản đồ ngành/hệ sinh thái viết tĩnh.
- Foreign-flow ratio, insider net shares, PE/PB/ROE và Beneish từ các truy
  vấn dữ liệu riêng. Các trường này **không đồng nghĩa** model đã nhận được
  dữ liệu nguồn thật hoặc dữ liệu được công bố đúng trước từng quyết định.

Labels học rank tương đối theo return 5 phiên so với median cross-section;
regressor dự báo return 3 phiên; survival gate đánh dấu đáy giá ngày 1–2
không thấp hơn −3,5%. Chúng không trực tiếp tối ưu NAV sau phí/thuế, khớp
lệnh theo hàng chờ, vị thế tiền mặt, quyền cổ đông hoặc toàn bộ settlement.
Giá forward close/high/low từ cùng bảng daily bars không phải giá khớp broker.

HMM/regime là một nhánh riêng với ranker: code train HMM ghép VNINDEX daily
bars, `breadth_ma50` từ `market_regime`, `vninbr_interbank_rate` từ
`macro_indicators` và tổng `net_value` theo ngày từ `foreign_flow`. Nếu thiếu
breadth, SQL thay bằng 50; nếu thiếu foreign flow, thay bằng 0; lãi suất vẫn
có thể NULL. Đây là mô tả hợp đồng code, không phải bằng chứng các chuỗi đã
có đủ lịch sử hoặc HMM đó đã được dùng trong một quyết định PROD cụ thể.

### Lỗ hổng dữ liệu cần giải quyết

1. **Universe:** 100 mã dựa trên tổng thanh khoản tới cutoff là lựa chọn có
   nhìn về sau trong các fold lịch sử; cần danh sách eligibility/liquidity
   tại từng ngày, membership VN30 có `published_at/effective_from/to`, cùng mã
   đã hủy niêm yết. Không dùng snapshot hiện nay thay cho lịch sử.
2. **Quyền và vintage:** adjusted OHLC hiện tại không lưu phiên bản từng được
   thấy ở từng ngày; entitlement tiền/cổ phiếu/rights không được mô phỏng đủ
   trong label. `date`, `ratio_date` hay `trade_date` không thay cho timestamp
   nguồn biết được quan sát. Thiếu availability/revision history thì chưa
   chứng minh feature hoặc label PIT.
3. **Dòng tiền ngoại:** `foreign_flow` là bảng riêng, không nằm trong bảng
   bars. Trainer gọi `FeatureForge`, và `FeatureForge` truy vấn bảng riêng
   trực tiếp theo mã/ngày. Frozen bars export có 1.011.757 bar/407 mã nhưng
   chỉ chứa OHLC, volume và adjusted OHLC; nó không chứa raw foreign buy/sell
   hay proprietary flow. Riêng broker-swing derived dataset, các cột
   `foreign_flow_ratio` và `foreign_flow_ratio_5d` **thiếu ở toàn bộ
   1.008.861 dòng**; tên cột này khác với feature production
   `foreign_flow_ratio_20d`, nên không thể dùng kết quả đó để suy ra coverage
   bảng PROD. Production `FeatureForge` tự truy vấn bảng riêng và điền 0 khi
   không có dòng. Một pipeline khác,
   `DataEnricher.fetch_foreign_flow`, có fallback tính giá trị từ MD5 ticker;
   code cho thấy đường này tồn tại, **chưa chứng minh** nó được gọi trong một
   prediction PROD cụ thể.
4. **Thanh khoản/vi cấu trúc:** volume ngày không phải volume liên tục nếu
   phần ATO/ATC/thỏa thuận chưa tách đúng; mã hóa nhánh fallback chưa tách
   thành volume liên tục=volume tổng. `order_flow_imbalance_proxy` được tính
   từ vị trí close trong range, không phải imbalance bid/ask, PIN hay flow từ
   lệnh. Daily data không cho biết spread, queue, partial fill hoặc lượng bán
   khả dụng sau settlement.
5. **Macro/insider/fundamental:** OHLCV snapshot không có các series này.
   Hệ thống có bảng macro và truy vấn lãi suất liên ngân hàng, nhưng cần xác
   minh coverage, units, source, lịch publication/vintage và null so với zero.
   `trade_date` insider có thể khác ngày thông tin tới thị trường. Quarterly
   ratios phải as-of ngày công bố; không áp lịch sự kiện bằng ngày quý kết thúc.
6. **Imputation/feature contract:** comment của standalone nói 51 feature;
   code thực tế lặp trên `model.feature_cols` của artifact đang nạp và thêm
   cột còn thiếu bằng 0. Nhiều nhánh lỗi/mất nguồn cũng đổi sang 0 hoặc
   forward-fill. Không đọc pickle để kiểm đếm schema artifact PROD, nên số
   cột chính xác trong bản PROD chưa xác minh. Một vector đủ số chiều chưa
   chứng minh đủ nguồn dữ liệu thật. Cần ghi cờ
   `observed/missing/stale/synthetic`, provenance và hash bên cạnh mỗi
   feature, không biến missing thành tín hiệu trung tính.

Tài liệu nghiên cứu cũ nói về 12 năm lịch sử/10 năm foreign flow đầy đủ.
Đây là tuyên bố trong research note, chưa được tái xác nhận bằng file source,
vintage, tỷ lệ phủ theo mã/ngày và hash trong pipeline đang được audit. Không
xóa kết quả cũ; hạ mức tin cậy cho feature đó tới khi provenance được tái lập.

## Dữ liệu số ưu tiên

| Ưu tiên | Nhóm trường | Dùng trả lời câu hỏi nào | Điều kiện mở khóa |
|---|---|---|---|
| 0 | OHLC raw/adjusted; volume khớp liên tục/ATO/ATC/thỏa thuận; VWAP giao dịch; venue, lịch phiên, giá trần/sàn/tick | Giá tham chiếu còn hợp lệ không, thanh khoản và fill/cost thật đến đâu | Mapping đơn vị, source, split volume, raw factor, phiên nguồn và ngày thiếu |
| 0 | Listing/delisting/suspension; VN30 constituents; shares/free float; corporate cash/stock/rights entitlements và ngày công bố/hiệu lực | Rổ cổ phiếu có thể biết tại ngày đó là gì, total-return đúng chưa | Dated publication/effective record; giữ lại mã đã rời sàn; không backfill |
| 1 | Foreign/proprietary buy/sell khớp lệnh và thỏa thuận; room riêng | Áp lực dòng tiền hay room constraint có thêm thông tin sau chi phí? | Payload mẫu đã đối soát với sàn/broker, đơn vị/cumulative vs interval, historical coverage/licence. Room không phải sở hữu; không mặc định API hiện tại có lịch sử đủ dùng |
| 1 | Trades + bid/ask price/quantity theo thời gian; spread, depth, dấu lệnh/auction | Dư địa vào/ra có thực thi được sau T+2 không? | Snapshot/trade sequence đồng bộ, timestamp, retention/queue/partial fills, nguồn và quyền dùng |
| 1 | Broker order/fill/reject/cash/receivable/sellable quantity/fees/tax | Lợi nhuận ròng ở NAV 1 tỷ có thật, tiền có dùng lại được chưa? | Paper/live tách riêng, timestamp giao dịch và đối soát sổ tiền/settlement |
| 2 | VN30F cash basis, hợp đồng đáo hạn, OI, turnover, foreign/proprietary futures | Phái sinh có cảnh báo hedge/áp lực thị trường hữu ích cho cổ phiếu không? | Lịch hết hạn, đồng bộ giờ; OI không cho biết chiều mở vị thế |
| 2 | ETF NAV/unit, units outstanding, creation/redemption, basket/weights, premium/discount | Có dòng tạo/lập quỹ cơ học ảnh hưởng cổ phiếu thành phần không? | Snapshot đúng ngày công bố; NAV đổi không tự chứng minh có dòng vốn |
| 2 | Tỷ giá SBV và liên ngân hàng, turnover/kỳ hạn, OMO/tín phiếu/yields | Chế độ tiền tệ/thanh khoản giải thích nhóm ngành nào? | Đúng đơn vị và publication lag; tỷ giá tham chiếu không phải giá khớp FX |
| 3 | Chỉ số breadth/sector concentration và số mã tăng/giảm/trần/sàn | Edge của mã còn sau khi trừ tác động thị trường/ngành? | Dated universe và cả mã đình chỉ/hủy niêm yết để mẫu số không sống sót |
| 3 | Số liệu tài chính quý/insider/share-count; CPI/IIP/thương mại | Cải thiện bộ lọc rủi ro hoặc phân nhóm thanh khoản/ngành? | Numeric disclosure và thời điểm public có vintage. Chỉ giữ nếu chứng minh giá trị tăng thêm trên kỳ 3–7 phiên |

## Nguồn kiểm chứng và giới hạn hiện tại

- DNSE liệt kê API thị trường cho OHLC, trades, price-level, historical
  bid/ask, foreign trading và ngày giao dịch. Đây là bằng chứng loại payload
  có endpoint; chưa chứng minh chiều dài lịch sử, sample thật, điều khoản dùng
  hoặc dữ liệu archived trong hệ thống. Xem
  [API thị trường](https://developers.dnse.com.vn/docs/dnse/market-data/),
  [dòng tiền ngoại](https://developers.dnse.com.vn/docs/dnse/get-foreign-trading/),
  [lịch sử bid/ask](https://developers.dnse.com.vn/docs/dnse/get-quotes/) và
  [lịch sử khớp lệnh](https://developers.dnse.com.vn/docs/dnse/get-history-trades/).
- HNX có bảng kết quả/phái sinh với OI, volume, foreign/proprietary statistics
  và các gói dữ liệu chính thức. Cần kiểm tra lịch sử máy đọc được, timestamp
  và điều khoản trước khi dùng. Xem
  [kết quả phái sinh HNX](https://web02.hnx.vn/vi-vn/phai-sinh/ket-qua-giao-dich.html)
  và [gói dữ liệu HNX](https://www.hnx.vn/vi-vn/dich-vu-cctt/du-lieu-cung-cap-list.html).
- SBV công bố lịch phát hành số liệu: tỷ giá tham chiếu hàng ngày, interbank
  rate hàng tuần, interbank results hàng ngày nhưng có độ trễ. Feature phải
  phản ánh đúng ngày dữ liệu có thể được biết. Xem
  [SBV statistical release schedule](https://www.sbv.gov.vn/documents/d/sbv_portal/527697).
- SSIAM công bố NAV/đơn vị quỹ, số lượng chứng chỉ quỹ và dữ liệu tạo/mua lại
  của ETF VN30; đây là chuỗi của một quỹ cụ thể, không đại diện dòng tiền toàn
  chỉ số. Xem [công bố SSIAM VN30](https://ssiam.com.vn/quy-etf-ssiam-vn30?nav_page=19).
- VN30 của HOSE chọn thành phần đủ điều kiện từ VNAllshare theo quy mô và
  thanh khoản. Quy tắc chọn chỉ số không đảm bảo lợi nhuận lướt sóng; không
  được gán thành phần hiện tại cho các năm trước. Xem
  [Quy tắc HOSE Ground Rules 4.0](https://staticfile.hsx.vn/Uploads/LocalFiles/ef15ff11e799483abd11677ad0443887/20250114_20241230_QD%20747%20HOSE%20Index%20Ground%20Rules.pdf).

## Kết quả kiểm toán dữ liệu

### Đối chiếu PROD chỉ đọc (04/10/2026)

Container `ai-engine` đang chạy image gắn revision `1d5170065507`; SHA-256 của
ba file train/inference trong container khớp với source revision đó. File
ranker mặc định có trong container là
`hybrid_stacking_ranker.pkl` (SHA-256
`d50837bebd3c2d29e66aa3ccb3836e9d2af80e33f727b1fd7e4eecf25c888046`); HMM
artifact là `hmm_regime_v2.pkl`. Không deserialize pickle, nên chưa xác minh
feature schema hoặc ngày train nằm bên trong artifact. Trạng thái image/code
đang chạy không chứng minh từng trường đã được đọc thành công trong lần dự báo.

PostgreSQL PROD có 1.186.633 dòng
`market_data_daily_calculation`/408 mã từ 28/07/2000 đến 02/10/2026;
523.204 dòng `foreign_flow`/409 mã từ 27/06/2016 đến 01/10/2026 (517.897
CafeF và 5.307 Vietstock; khóa mã/ngày không trùng); 29.913 dòng
`insider_trades`/402 mã; 26.634 dòng `financial_ratios`/407 mã; và 17.864
dòng `macro_indicators`/32 chỉ báo. Hai insider rows có ngày 1900-01-01,
dấu hiệu cần xử lý như dữ liệu ngày bất thường.

Theo đúng truy vấn universe trong trainer tới cutoff 02/10/2026, top 100 mã
được chọn có 280.474 bar từ 2014 tới 02/10/2026. Chỉ 138.510 bar (49,4%) khớp một dòng
`foreign_flow` cùng mã/ngày; 18.403 dòng khớp có net flow bằng 0 và 120.107
dòng khác 0. Vì `FeatureForge` left-join rồi fill missing bằng 0, mã/ngày
không có dòng có thể bị nhập nhằng với dòng flow bằng 0 ở feature. Trong cùng
280.474 bar, `volume_continuous` luôn bằng `volume_total`, nhưng 1.712 bar vẫn
có `volume_ato` hoặc `volume_atc` dương (tổng ATO 128.588.800, ATC
884.223.200 đơn vị). Vì vậy tên cột continuous chưa được chứng minh là volume
liên tục thuần.

Tách theo năm cho thấy top100 foreign-flow coverage là 0% trong 2014–15,
34,3% trong 2016, 56–60% trong 2017–19 và ổn định khoảng 56–57% mỗi năm từ
2020 đến 2026. Bảng có các trường buy/sell/net volume và value, gần như đầy
đủ; phép kiểm tra số học tìm thấy 80 dòng CafeF và 42 dòng Vietstock mà net
không bằng buy trừ sell. Cần xác minh rounding/định nghĩa trước khi loại bỏ
hay dùng các dòng này.

`foreign_net_vol` trong daily view không thể dùng thay cho bảng riêng:
trong 498.231 ngày-mã CafeF giao nhau giữa hai bảng, có 302.922 trường hợp
bảng riêng có flow khác 0 nhưng daily view bằng 0; chỉ 206 trường hợp cả hai
khác 0 và cùng giá trị. Daily view ghi `dnse_history` cho phần lớn lịch sử,
nhưng foreign net khác 0 chỉ xuất hiện ở 3/768.746 dòng nguồn đó. Ranker gọi
bảng `foreign_flow` riêng, nên cần giữ đúng đường dữ liệu này và kiểm tra
missing-vs-zero ngay trong feature output.

`macro_indicators` có 3.025 dòng `vninbr_interbank_rate` từ 02/12/2014 tới
26/09/2026. Bảng `market_regime` hiện chỉ có 57 ngày từ 10/07/2026 tới
01/10/2026; HMM train ghép các ngày cũ còn lại bằng breadth mặc định 50.
Nhánh HMM thay flow ngoại thiếu bằng 0. Đây là lỗ hổng coverage đáng kể nếu
các chuỗi trong bảng được dùng cho train HMM như code mô tả.

Đây là kiểm tra schema, count, range và join coverage trực tiếp trên PROD chỉ
đọc; không phải audit mọi giá trị hoặc xác minh provenance/available-at.
`foreign_flow` được kiểm tra độc lập với bars: LAB003 không có raw flow vì
frozen export không chứa bảng này và một số cột feature dẫn xuất đang missing;
điều đó không có nghĩa bảng PROD `foreign_flow` vắng mặt. PROD có bảng riêng,
và phép join cho thấy bảng này khớp khoảng 49,4% bar trong rổ train. Tên table,
row count và code join vẫn không chứng minh nguồn đúng, số liệu hợp lệ hay
đúng thời điểm được biết.

### Thử endpoint Vietstock trong `foreign_flow.py`

Đã gọi đường fetch của script ở chế độ chỉ đọc cho FPT, truyền khoảng
04/10/2016–04/10/2026 và giữ kết quả trong bộ nhớ. Vietstock trả 247 ngày,
từ 06/10/2025 đến 02/10/2026. Gọi riêng các khoảng 2016–17, năm 2020 và
năm 2024 đều trả 0 dòng. Với phép thử này, chia request theo năm cũng không
khôi phục được lịch sử cũ; chưa thể khẳng định các mã/API khác có giới hạn
giống hệt, nhưng script hiện không thể backfill 10 năm bằng Vietstock.

Đã xác nhận client dùng sai tên tham số: bundle JavaScript của trang gửi
`PageIndex`, còn phiên bản cũ gửi `Page`. Thay sang `PageIndex` và dùng `Rows`
để nhận tổng số bản ghi. Request thử với `PageSize=100` trả ba trang 100/100/47
không trùng ngày; `Rows=247`. Request 10 năm với PageSize 250 vẫn chỉ có 247
dòng, trang kế tiếp rỗng; các khoảng cũ 2016–17, 2020 và 2024 vẫn rỗng.
Như vậy phân trang client đã được sửa và xác minh; giới hạn lịch sử của nguồn
Vietstock vẫn chưa được khắc phục.

DB PROD đã có FPT foreign-flow cho 2.464/2.495 phiên bar trong cùng khoảng
10 năm: 2.454 matched rows từ CafeF và 10 từ Vietstock; 31 phiên không có
row khớp và 3 dòng flow nằm ngoài lịch bar. Đây là coverage của một mã, không
đại diện toàn universe; ngày không có row chưa phân biệt được không phát sinh
giao dịch ngoại với thiếu dữ liệu nguồn.

### Bản LOCAL sau reconciliation foreign flow và cách ly ngày ngoài phiên (04/10/2026)

Đã đối chiếu lại bảng `foreign_flow` trong DB LOCAL được khôi phục từ snapshot
PROD; không ghi ngược lên PROD. Refresh Vietstock trả 99.558 dòng cho 407 mã
HOSE trong cửa sổ API còn sẵn 06/10/2025–02/10/2026. Ghép theo các cặp mã–phiên
có nến trong 20 phiên index gần nhất đạt 7.406/7.406 (405 mã có nến trong cửa
sổ). Đây là coverage hiện tại sau reconciliation, không sửa được lịch sử cũ:
trên toàn khoảng bảng có quan sát, còn 4.099 phiên nến thiếu dòng flow trong
547.637 cặp (99,252% khớp). Không có row vẫn là `unknown`, không phải zero-flow.

Trong 467.607 dòng CafeF trước cleanup có 225 dòng ngày 06/06/2026, là thứ Bảy
và không có nến HOSE. Đã lưu đủ dòng gốc cùng hash trong artifact quarantine
LOCAL, sau đó loại đúng 225 dòng khỏi bảng phân tích; không tự dời chúng sang
thứ Sáu hay thứ Hai. Trong sáu trường mua/bán/net kiểm tra, 224 dòng không
trùng phiên kề nào, một dòng trùng phiên trước. Giá trị đó không đủ xác định
ngày sự kiện thật. Sau cleanup: bảng có 566.966 dòng (CafeF 467.382,
Vietstock 99.584), không còn ngày cuối tuần ở hai nguồn, và coverage 20 phiên
gần nhất vẫn 100%. Có 23.428 foreign-flow rows không có OHLCV cùng mã/ngày;
không thể coi toàn bộ là lỗi ngày vì mã có thể không phát sinh nến trong phiên.

Schema bảng chỉ có `source` và `created_at` để nhận diện nguồn/thời điểm lưu;
không có `source_record_id`, `published_at`, `available_at` hay `revision_id`.
Sau refresh còn 7 dòng CafeF và 1.005 dòng Vietstock mà `net_volume` hoặc
`net_value` không bằng gross buy trừ sell. Chưa có hợp đồng trường nào chứng
minh đây là sai số thay vì khác phạm vi khớp lệnh/thỏa thuận; giữ nguyên raw
fields và không dùng phép suy diễn để sửa chúng. Vì thiếu provenance và định
nghĩa trường, flow vẫn chưa qua gate dùng làm tín hiệu lịch sử PIT.

Rà soát LOCAL tìm thấy 3.249 bar vi phạm quan hệ OHLC; trong đó 445 close cao
hơn high và 925 close thấp hơn low. Cả 3.249 khóa ticker/ngày cũng vi phạm ở
các trường adjusted của `market_data_daily`; view `market_data_daily_calculation`
nhân/chia các trường ấy bằng close ratio và không phải một nguồn sửa độc lập.
Phân bố `data_source` trong các dòng lỗi: 2.211 `cafef_unadj`, 795 `dnse_history`,
211 `dnse_event_ratio_fill`, 26 `dnse_index_ratio_1_fill`, 3
`dnse_neighbor_ratio_fill` và 3 `dnse_index_fill`. Phân bố này chỉ cho biết
nguồn/giai đoạn ghi trong DB, chưa xác định được cơ chế gốc gây sai giá.

Kiểm tra sau cleanup bằng so sánh float tuyệt đối từng báo cáo thêm 35.404
dòng close lệch biên high/low trong `market_data_daily_calculation`. Đối chiếu
preimage cho thấy cả 35.404 sai khác chỉ do làm tròn phép nhân double: gap lớn
nhất `1e-13` đơn vị giá, không dòng nào vượt `1e-9`; high/low và open không vi
phạm. Đây không phải 35.404 nến lỗi mới và không bị sửa. Khi kiểm tra view
tính toán phải dùng tolerance; candle đầu vào vẫn được kiểm range nghiêm.

Đã cách ly trên LOCAL ngày 04/10/2026: giữ nguyên preimage của 3.249 dòng trong
`scratch/vn_swing_lab/repairs/OHLC_INVALID_LOCAL_20261004/`, xóa chúng khỏi bảng
nghiên cứu `ohlcv` (cột giá có ràng buộc NOT NULL), đồng thời đặt
`open_adj/high_adj/low_adj/close_adj/close_unadj` thành NULL cho đúng 3.249 khóa
ở `market_data_daily`. Volume, provenance và các trường khác không đổi; PROD
không bị sửa. Sau transaction còn 0 vi phạm OHLC trong bảng `ohlcv`. Thay đổi
được chặn tái diễn ở các ba đường nạp đã tìm thấy: historical ingestion,
daily backfill và CLI backfill đều bỏ qua candle có giá không hữu hạn, không
dương hoặc nằm ngoài high/low. Đây chưa phải bằng chứng phục hồi được giá gốc.

Rà soát volume LOCAL cho thấy cả 1.186.633/1.186.633 dòng gán
`volume_continuous = volume_total`; trong 5.938 dòng còn có ATO/ATC dương nên
đẳng thức mâu thuẫn trực tiếp với split. Code nạp cũng điền total vào continuous
khi thiếu intraday và điền zero vào auction fields. Vì DB không có cờ vintage
cho split quan sát và split mặc định, đã snapshot đầy đủ rồi đặt
`volume_continuous` cùng `adtv20_continuous` thành NULL cho toàn bộ bản LOCAL.
Các trường `volume_atc` và `volume_ato` không có per-row provenance nên cũng
được đặt NULL; toàn bộ giá trị trước đó vẫn nằm trong snapshot nén có hash.
`volume_total` và mọi trường không liên quan được giữ. Writer hiện ghi NULL
khi split intraday không có; ADTV chỉ tính khi đủ 20 phiên có split đã quan sát.
Không suy continuous bằng phép trừ auction khỏi total. `volume_total` vẫn chưa
xác minh có bao gồm negotiated/auction hay không.

Volume snapshot vẫn chưa tách được giao dịch liên tục khỏi ATO/ATC/thỏa thuận;
quyền, vintage và universe PIT còn thiếu. Vì thế dữ liệu **chưa đủ sạch/đầy đủ
để mở nghiên cứu edge** dù lỗi OHLC đã được cách ly.

Không chạy `refresh_all`, vì hàm này ghi trực tiếp vào `foreign_flow`. Trước
khi cân nhắc chạy, cần sửa/đánh giá ba bẫy trong code: `skip_existing` coi
1.000 dòng là đã xong chứ không kiểm đủ 10 năm; INSERT mới bỏ qua cột `source`
nên nhận default `cafef` dù tải Vietstock (chỉ nhánh conflict đặt
`vietstock`); `_insert_rows` nuốt lỗi DB nên caller vẫn có thể đếm dòng là
đã nạp. Không có backfill PROD nào được thực hiện trong lần thử này.

LAB003 kiểm kê bars đóng băng và một số feature số dẫn xuất theo nguồn/năm;
ghi nhận missing/zero/nonfinite, kiểm tra OHLC/volume, liệt kê trường nguồn
và hash code/input. Export có 1.011.757 dòng, 407 mã từ 05/01/2015 đến
01/10/2026, không có khóa `ticker/date` trùng. Có 2.884 dòng OHLC không hợp
lệ, 35 phiên cổ phiếu thiếu phiên VNINDEX, 37.141 dòng volume bằng 0 và không
có trường `published_at`, `available_at` hay revision. `volume_continuous`
bằng `volume_total` ở mọi dòng; `continuous_volume_share` chỉ có một giá trị:
1. Export này không chứng minh volume khớp liên tục đã được tách khỏi đấu giá.
Các dòng volume bằng 0 không có mã lý do trong file.

The two columns in this frozen broker-swing derived dataset are missing in
**all 1.008.861 rows**; they are not measured neutral flow. Their names differ
from production FeatureForge's `foreign_flow_ratio_20d`, and this local export
does not measure coverage in the separate PROD `foreign_flow` table. A distinct
static enrichment path has a ticker-hash fallback, but this audit does not
prove it ran for any particular PROD prediction.

Audit LAB003 trên file local không gọi provider, nạp model pickle,
truy cập SAG hay train model. Kết quả vẫn là `DIAGNOSTIC_ONLY`; một đường code
hoặc trang API không tự xác minh được dữ liệu, tín hiệu dòng tiền hay khả năng
sinh lời. Lần chạy đầu dừng vì lỗi định dạng báo cáo; lần chạy đã sửa và đăng
ký trước là LAB003. Bản ghi cũ vẫn được giữ trong ledger bất biến. Xem
[danh mục số liệu](../../ai-engine/lab/numeric_data_catalog.json) và
[bộ điều khiển](../../ai-engine/scripts/run_swing_lab.py).

LAB001 was a one-off diagnostic on already viewed DEV 2023–2025, no fitting:
existing adaptive policy NAV was −5,61%/−4,59%/−4,68%; frozen model forecasts
under zero transaction costs still gave −2,14%/−1,75%/+0,05%. This did not
establish that fees alone caused the losses. Among 1.050 observed
positive-EV forecasts, average predicted +0,866%, gross price return −0,342%
and net return −0,840%. This is in-sample diagnostic only, not unseen
confirmation. The learned exit earned 77,97m VND less than fixed H7 across
the same observed entries/sizes, before accounting for their changed slots
and future cash actions.

**No new algorithm family is enabled until the data-stage exit evidence is
met.** Audit all data classes, then collect the smallest auditable numerical
source needed for one economic mechanism. Compare incremental NAV/PnL against
OHLCV, cash, settlement and universe controls before adding data or model
complexity.
