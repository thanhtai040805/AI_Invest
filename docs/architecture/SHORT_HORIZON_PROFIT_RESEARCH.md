# Nghiên cứu mô hình lợi nhuận ngắn hạn

Ngày lập: **02/10/2026**. Trạng thái: **challenger nghiên cứu offline**.

Cập nhật mục tiêu ngày **03/10/2026**: ML chỉ phục vụ lướt sóng, ưu tiên
lợi nhuận ròng sau mọi chi phí và thời gian đến lúc tổng lãi đã chốt dương.
H3 là kỳ hạn thử nghiệm chính; H5 là kỳ hạn so sánh được hỗ trợ. Tỷ lệ thắng
được báo cáo cùng payoff và rủi ro, không phải điều kiện thay thế lợi nhuận.

## 1. Mục tiêu và phạm vi

Challenger học khả năng một giao dịch theo chính sách đã định sẽ có lợi nhuận
sau chi phí, cùng lợi suất kỳ vọng và phân vị lợi suất thấp. Kết quả được đánh
giá bằng giao dịch đã đóng và NAV của danh mục có giới hạn tiền, vị thế và
thanh khoản. Không có bảo đảm lợi nhuận tương lai.

Mô hình này chưa thay Standalone ML đang vận hành. Các thành phần nghiên cứu
đọc bản xuất OHLCV và ghi artifact offline; không kết nối SAG, đặt lệnh broker,
thay cấu hình PROD hoặc ghi đè artifact mô hình hiện tại.

| Thành phần | Mã nguồn |
|---|---|
| Feature, universe và target | [short_horizon_profit_dataset.py](../../ai-engine/experiments/short_horizon_profit_dataset.py) |
| Hai family và calibration | [short_horizon_profit_model.py](../../ai-engine/app/domain/services/ml/short_horizon_profit_model.py) |
| Danh mục, khớp giá giả định và sổ tiền | [short_horizon_profit_portfolio.py](../../ai-engine/experiments/short_horizon_profit_portfolio.py) |
| Protocol, chọn candidate và báo cáo | [research_short_horizon_profit.py](../../ai-engine/scripts/research_short_horizon_profit.py) |

## 2. Định nghĩa giao dịch và target

Một mẫu là một mã tại thời điểm **sau close phiên t**:

1. Feature chỉ sử dụng quan sát đến close t.
2. Entry giả định tại **open t+1**.
3. Exit dự kiến tại **close t+H**, với H bằng 3 hoặc 5 phiên thị trường.
4. H=3 tương ứng giữ ba phiên tính cả phiên entry; exit cách entry hai phiên.
   Đây là quy ước nghiên cứu trên daily bar, không xác nhận khả năng khớp lệnh
   hoặc thời điểm chứng khoán thực tế trở nên khả dụng trong ngày.

Giá raw được quy đổi sang VND với hệ số 1.000 trong snapshot hiện tại. Giá
entry/exit dùng để tạo label đã cộng/trừ friction mỗi chiều. Target theo đơn vị
lợi suất thập phân là:

```text
net_return = exit_price_vnd × (1 − sell_fee_rate − sell_tax_rate)
             / [entry_price_vnd × (1 + buy_fee_rate)] − 1
profit_label = 1 nếu net_return > 0, ngược lại là 0
```

Label theo một đơn vị cổ phiếu chưa bao gồm phí tối thiểu mỗi lệnh, quy mô lệnh,
tiền chờ thanh toán hay khả năng chồng lấn vị thế. Simulator tính lại PnL từ
số cổ phiếu và phí của lệnh thực tế trong mô phỏng. Vì vậy `p_profit` dự báo
sự kiện có lãi của target đã định; không phải xác suất sống sót trong thời gian
khóa cổ phiếu hoặc xác suất đạt một take-profit tùy ý.

Bar tương lai thiếu, giá không hợp lệ hoặc không có continuous volume cần cho
entry/exit làm label bị censored/invalid. Nhãn này không được đổi thành lợi
suất 0. Training chỉ dùng label hợp lệ. Khi đánh giá, mọi row có universe hợp
lệ tại t vẫn được dự báo; kết quả tương lai không quyết định membership hoặc
điều kiện chọn lệnh tại t.

## 3. Feature và universe theo thời điểm quyết định

Feature sử dụng adjusted OHLC đã cung cấp: return 1/3/5/10/20/60 phiên,
volatility, ATR theo tỷ lệ giá, vị trí close trong biên ngày, gap, intraday
return, khoảng cách MA/breakout, reversal, volume/turnover ratio, beta và
relative strength với VNINDEX. Market context gồm return, volatility,
khoảng cách MA, breadth và xếp hạng thanh khoản cùng ngày. Chỉ danh sách
`metadata['feature_columns']` được đưa vào mô hình.

**Adjusted OHLC tạo price feature; raw OHLC tạo label, giá fill và thanh khoản
VND.** `raw_factor` và thay đổi corporate action trong cửa sổ label là thông
tin chẩn đoán, không phải feature hoặc bộ lọc biết trước outcome. Các thay đổi
factor trong thời gian đã nắm giữ được phân biệt với sự kiện tại entry; ngưỡng
thay đổi đáng kể hiện tại là 1%.

Universe mỗi close t yêu cầu:

- Bar quyết định và volume hợp lệ; continuous volume hiện tại dương.
- Ít nhất 61 phiên lịch thị trường liên tiếp có lịch sử sạch.
- ADTV20 tính từ continuous volume quá khứ, theo raw close, tối thiểu 10 tỷ
  VND/ngày.
- Thuộc top 100 thanh khoản của các mã đủ điều kiện tại ngày đó.
- Có market return 60 phiên.

Không dùng tổng thanh khoản toàn kỳ để chọn lại universe lịch sử. Adjusted
overnight gap lớn hơn 12% được quan sát tại close quyết định sẽ cách ly bar
và ảnh hưởng điều kiện lịch sử sạch. Đây là giả định chất lượng dữ liệu cần
kiểm chứng, không phải bằng chứng mọi biến động lớn đều sai.

NaN còn lại trong feature được giữ đến model: median imputer học chỉ từ tập
fit; family linear tiếp tục dùng scaler học từ cùng tập. Thiếu feature bắt
buộc, feature không phải số, infinity, row hoàn toàn thiếu hoặc feature hoàn
toàn thiếu trong train làm fit/predict bị từ chối. Không tự thêm feature bằng 0.

Universe theo ngày có tính nhân quả trong **danh sách mã đã xuất**. Điều đó
không khắc phục việc danh sách mã đầu vào được lấy từ hiện tại, thiếu lịch sử
niêm yết và mã đã hủy niêm yết.

## 4. Hai family có số cấu hình hữu hạn

| Family | Probability head | Mean return head | Lower return head |
|---|---|---|---|
| `linear` | LogisticRegression, C=1, sau median imputer và StandardScaler | Ridge, alpha=10 | Mean đã hiệu chỉnh cộng phân vị 10% residual calibration chung |
| `boosted` | LightGBM binary classifier | LightGBM regression, squared loss | LightGBM quantile regression, alpha=0.10 |

Boosted dùng cấu hình cố định: 120 estimators, learning rate 0.05, depth 3,
7 leaves, tối thiểu 40 mẫu/leaf, feature fraction 0.8, L1=0.1 và L2=5.
Random state là 42; CPU dùng `deterministic=True` và `force_col_wise=True`.
Không có hyperparameter sweep ngoài hai family này. Các objective binary,
regression L2 và quantile cùng tham số regularization được mô tả trong
[tài liệu LightGBM](https://lightgbm.readthedocs.io/en/stable/Parameters.html).

Mean head học trực tiếp `net_return` với squared loss và regularization để
ước lượng mean cần cho expected value. Target không bị winsorize và không
được thay bằng `p_profit × take_profit`. Dữ liệu outlier hoặc corporate action
sai vẫn có thể gây ảnh hưởng lớn; lựa chọn loss không sửa được dữ liệu nguồn.

`predict(X)` trả cùng index với X:

| Cột | Ý nghĩa |
|---|---|
| `p_profit` | Ước lượng xác suất net target > 0 sau calibration |
| `expected_net_return` | Mean net target dự báo sau hiệu chỉnh bias |
| `downside_q10` | Ước lượng phân vị 10% của return |
| `score` | Bằng `expected_net_return`, giữ nguyên đơn vị lợi suất |

Score không được chuẩn hóa Z theo nhóm candidate hiện tại. Không dùng raw Z
để diễn giải conviction hoặc chuyển trực tiếp thành tỷ trọng vốn.

### Calibration và artifact

Classifier được fit trước; một LogisticRegression C=10 học phép sigmoid trên
logit của probability tại block calibration **muộn hơn train**. Mean return
được cộng mean residual của block này. Với boosted, q10 được cộng phân vị
10% residual so với quantile head; với linear, dùng residual so với mean đã
hiệu chỉnh.

Việc tách dữ liệu classifier và calibrator phù hợp nguyên tắc calibration
trong [tài liệu scikit-learn](https://scikit-learn.org/stable/modules/calibration.html).
Đây là một phép hiệu chỉnh thống kê; khả năng hiệu chỉnh tốt khi đổi regime
cần được đo lại ngoài mẫu.

`downside_q10` không phải khoảng tin cậy của expected return, mức lỗ tối đa
hoặc bảo đảm coverage ngoài mẫu. Linear giả định residual shape chung;
boosted cũng chịu rủi ro drift và thiếu mẫu ở vùng tail.

Artifact joblib lưu family, feature names/order, imputer/scaler, estimator,
calibrator, bias/quantile offset, `trained_through`, policy/dataset metadata
và phiên bản contract. SHA256 artifact là `model_version`. Artifact chỉ được
load từ nguồn tin cậy; phiên bản contract không hợp lệ bị từ chối.

`recalibrate(...)` giữ nguyên estimator, imputer và scaler, cập nhật ba phép
hiệu chỉnh trên label đã hoàn tất. Nó kiểm tra calibration sau block train
gốc và cutoff không lùi. `predict(...)` từ chối ngày bằng hoặc trước
`trained_through`; caller phải purge ngày label end trước block forecast.

## 5. Protocol train, development, holdout và diagnostic

Mặc định có **12 candidate chính sách**; `--families` và `--horizons` cho phép
khai báo trước một tập con để tập trung training:

```text
2 family × 2 horizon × 3 gate = 12 candidate
```

| Gate | p_profit tối thiểu | expected_net_return phải lớn hơn |
|---|---:|---:|
| `open` | 0.50 | 0 |
| `selective` | 0.55 | 0.001 = 0.10% |
| `strict` | 0.60 | 0.002 = 0.20% |

Các ngưỡng trên là giả định nghiên cứu được khai báo trước holdout; chưa phải
ngưỡng vận hành đã được xác nhận.

Cửa sổ dữ liệu bắt đầu từ `prediction_start − training_window_months`;
`--training-window-months` mặc định là 60. Ba tháng cuối trước prediction
start được dành riêng cho calibration. Với cấu hình mặc định và prediction
start vào 01/01, phần fit còn khoảng bốn năm chín tháng trước khi purge label:

| Evaluation | Bắt đầu fit | Bắt đầu calibration | Evaluation period |
|---|---|---|---|
| DEV 2023 | 01/01/2018 | 01/10/2022 | Năm 2023 |
| DEV 2024 | 01/01/2019 | 01/10/2023 | Năm 2024 |
| DEV 2025 | 01/01/2020 | 01/10/2024 | Năm 2025 |
| Holdout, candidate đã khóa | 01/01/2021 | 01/10/2025 | 01/01–31/07/2026 |
| Diagnostic | Dùng lại model đã khóa | Không fit lại bằng diagnostic | 01/08–01/10/2026 |

Lần chạy thứ hai dùng `--calibration-mode monthly`: trước mỗi tháng, refresh
calibration bằng ba tháng gần nhất có `label_end_date < month_start` rồi
forecast tháng mới. Base estimators vẫn chỉ fit trước năm evaluation. Cửa sổ
giao dịch không bị cắt ở biên từng tháng. Trong diagnostic, label của tháng
trước có thể được dùng từ tháng tiếp theo khi đã hoàn tất.

`--calibration-mode monthly-refit` train lại estimator, imputer/scaler và
calibration trước mỗi tháng có forecast. Cửa sổ lùi từ đầu tháng mới; block
calibration ba tháng và purge theo label end vẫn được giữ. Mỗi lần fit có
artifact riêng; artifact cuối kỳ chứa model mới nhất đã thực sự dùng để
forecast. Không cắt giao dịch tại biên tháng hoặc đổi gate theo kết quả tháng.

Hai lần chạy đầu ngày 02/10 mỗi lần khai báo 12 candidate, gồm **24 candidate-policy-mode**,
không phải 24 kiến trúc khác nhau. Quyết định nghiên cứu monthly được đưa ra
sau khi đã xem kết quả lần đầu; năm 2026 của lần thứ hai được đánh dấu
`holdout_previously_inspected=true`. Không xem đó là xác nhận độc lập mới.

Purge theo **ngày outcome thực sự kết thúc**:

- Train yêu cầu `date < calibration_start` và
  `label_end_date < calibration_start`.
- Calibration yêu cầu `calibration_start <= date < prediction_start` và
  `label_end_date < prediction_start`.
- `trained_through` là ngày label end muộn nhất đã dùng, không chỉ là ngày
  feature cuối cùng.
- Runner yêu cầu ít nhất 1.000 train rows và 500 calibration rows; model yêu
  cầu cả nhãn có lãi và không có lãi trong mỗi block.

Mỗi family/horizon có forecast riêng; ba gate dùng chung forecast. Cấu hình
mặc định annual có 12 lần fit DEV và 36 lượt mô phỏng candidate-năm; monthly
refit tạo thêm model trước từng tháng. Runner ưu tiên candidate đạt tất cả
DEV limits, rồi chọn mean annual NAV return cao nhất. Nếu return bằng nhau,
ưu tiên thời gian đến tổng lãi đã chốt dương ngắn hơn. Nếu không candidate
nào đạt, candidate được chọn chỉ có vai trò diagnostic.

`selection_lock.json` được ghi **trước khi đánh giá holdout**. Sau đó chỉ
family/horizon đã chọn được fit với dữ liệu trước 2026 và gate được giữ cố
định. Không điều chỉnh cấu hình theo kết quả holdout. Dataset lịch sử có thể
được dựng trước; feature/universe phải vẫn causal và candidate selection chỉ
dùng DEV outcomes. Với monthly-refit, lịch train đã khai báo tiếp tục dùng
những outcome hoàn tất trước tháng forecast, trong khi family, horizon và
gate đã chọn vẫn giữ nguyên.

Holdout là chronological out-of-sample đối với fit và selection, không phải
cam kết con người chưa từng xem thị trường giai đoạn đó. Tháng 8–9/2026 đã
được phân tích trước, nên khoảng diagnostic không được gọi là holdout chưa
quan sát. Entry mới dừng H phiên trước ngày kết thúc period đã khai báo để
giảm vị thế chưa đến ngày exit; không dùng outcome từng mã để cắt period.

## 6. Chính sách danh mục và sổ tiền

| Giả định mặc định | Giá trị |
|---|---:|
| NAV ban đầu mỗi period | 1 tỷ VND |
| Số vị thế đồng thời tối đa | 5 |
| Tỷ trọng một mã tối đa | 10% NAV |
| Risk budget mỗi entry | 0.5% NAV |
| Cash buffer | 10% NAV |
| Liquidity participation | 1% ADTV20 shares biết tại decision |
| Downside floor dùng sizing | 1% |
| Notional entry tối thiểu | 10 triệu VND |
| Lot size giả định | 100 cổ phiếu |
| Brokerage mỗi chiều | 0.10%; tối thiểu 10.000 VND/lệnh |
| Sell tax | 0.10% sell notional |
| Roundtrip friction cơ sở | 20 bps; 10 bps mỗi chiều |
| Friction stress | 100 bps roundtrip |
| Sale cash settlement | 2 phiên sau phiên bán |

Candidate đạt gate được sắp theo expected net return, rồi p_profit. Một mã đang
nắm giữ không được mua thêm. Notional trước giới hạn cash, liquidity và lot là:

```text
downside_risk = max(1%, −downside_q10)
max_notional = min(10% × sizing_NAV, 0.5% × sizing_NAV / downside_risk)
```

Sizing NAV dùng settled cash, sale receivable và mark từ **close trước** cho
vị thế cũ. Cash chưa thanh toán không tài trợ entry. Sizing cũng giữ cash
buffer, dự trữ phí exit và làm tròn lot. Không vay margin hoặc ứng trước tiền
bán trong mô phỏng.

Simulator kiểm tra giá và execution volume tại phiên fill: entry không có
open/volume hợp lệ bị hủy; exit không có close/volume hợp lệ được hoãn. Không
fill bằng giá forward-filled. Vị thế chưa thoát được giữ trong sổ cùng stale
mark và cảnh báo, không bị xóa khỏi NAV.

Policy và horizon override của forecast chỉ chấp nhận H3/H5. Nếu thiếu quote
khớp hợp lệ tại ngày exit dự kiến, exit tiếp tục bị hoãn; giới hạn kỳ hạn
không tạo fill giả. CSV trade ghi thêm `actual_holding_sessions`; báo cáo có
mean/max thời gian giữ thực tế và số trade vượt kỳ hạn đã định.

Sale proceeds sau fee/tax được ghi receivable trong NAV và chuyển thành cash
tại close T+2, khả dụng cho open phiên tiếp theo. Đây là giả định cash-only
bảo thủ của simulator. DNSE mô tả kỳ thanh toán tiền bán T+2 và dịch vụ ứng
trước có thể tạo sức mua sớm hơn; simulator không mô phỏng dịch vụ đó.
[Hướng dẫn DNSE về ứng trước tiền bán](https://hdsd.dnse.com.vn/huong-dan-giao-dich-tien/ung-truoc-tien-ban).

Tỷ lệ fee, tax, lot, participation và timing trong bảng là policy của
experiment, không phải xác nhận biểu phí hay khả năng khớp tại mọi broker,
sàn và mã thuộc snapshot.

## 7. Đánh giá và giới hạn promotion

Chỉ tiêu forecast gồm Brier score, AUC, calibration bins, return RMSE,
q10 breach fraction và coverage/censored/adjustment diagnostics. Chỉ tiêu
portfolio gồm NAV return/drawdown/exposure, net closed win rate, average
win/loss, expectancy, profit factor, PnL từng cổ phiếu, rejections, open
positions và receivables. Win rate một mình không đủ xác nhận profitability.

DEV limits hiện tại: ít nhất 150 closed trades cộng ba năm, PF ít nhất 1.20,
mean net trade return ít nhất 0.20%, drawdown từng năm
không quá 10%, và NAV return dương ở mỗi năm. Candidate hoàn toàn không có
trade lỗ được xử lý bằng trạng thái PF riêng với positive gross profit.

Holdout gate yêu cầu DEV đã đạt, ít nhất 50 closed trades,
PF ít nhất 1.20 hoặc trạng thái không có trade lỗ, expectancy ít nhất
0.20%, NAV return dương, drawdown không quá 10%, và NAV return vẫn dương ở
friction stress. Các ngưỡng là tiêu chí nghiên cứu cố định, không bảo đảm mẫu
đủ lớn hoặc độc lập. DEV từng năm và holdout cuối kỳ còn vị thế không thoát
được sẽ không qua gate. 10% là tiêu chí drawdown đánh giá; simulator chưa có
circuit breaker để bảo đảm một giới hạn lỗ cứng trong giao dịch thực tế.

Runner bổ sung bootstrap PnL realized theo tuần, gồm cả tuần không giao dịch.
Từ 03/10, win rate là metric mô tả; bỏ yêu cầu 55% ở gate đánh giá không làm
thay đổi cổng p_profit của lệnh mua. Lợi nhuận phụ thuộc cả xác suất, lãi khi
thắng, lỗ khi thua và chi phí. Kết quả các lần chạy 02/10 vẫn được giữ nguyên
với protocol và gate đã khóa khi đó.

Các metric tốc độ dùng tổng PnL ròng đã đóng tại cuối từng phiên, sau khi cộng
tất cả lệnh cùng ngày: `first_positive_realized_pnl_date`,
`sessions_to_first_positive_realized_pnl` (0 tại decision close đầu tiên),
`realized_pnl_vnd_at_20_sessions` (null nếu chưa đủ 20 phiên) và
`profitable_realized_session_fraction`. Một lệnh thắng riêng lẻ không được
xem là cả tài khoản đã có lãi. NAV vẫn tính receivable chưa thanh toán;
realized PnL tại exit không đồng nghĩa tiền bán đã dùng được ngay.

Khoảng báo cáo chỉ mang tính mô tả: chưa hiệu chỉnh việc chọn candidate và
tuần có thể còn phụ thuộc nối tiếp. Không diễn giải thành khoảng bảo đảm lợi
nhuận tương lai.

Benchmark gồm cash 0%, VNINDEX price return, và quy tắc momentum/reversal cố
định qua simulator. Score của hai rule là ordering proxy, không phải expected
return được calibration. VNINDEX là benchmark giá với exposure khác danh mục;
Replay cũ cũng khác period, entry/exit policy và mô hình. Không gọi các so sánh
này là thí nghiệm đối chứng chỉ thay duy nhất model.

Ngay cả khi empirical gate đạt, runner trả
`NOT_ELIGIBLE_DATA_EXECUTION_VALIDATION_REQUIRED`. Các blockers hiện tại:

| Blocker | Quan sát/giới hạn và việc cần hoàn tất |
|---|---|
| Historical universe | Snapshot 407 mã theo danh sách hiện tại; chưa có bằng chứng đủ mã đã hủy niêm yết, lịch sử listing/exchange và membership theo ngày. Cần hoàn thiện trước khi loại trừ survivorship bias. |
| Market calendar | Có 35 phiên stock bar nhưng thiếu VNINDEX. Runner chỉ khi được cho phép mới thêm calendar placeholder với price NaN; không tạo hoặc forward-fill index level. Cần đối chiếu lịch và dữ liệu chỉ số. |
| Giá sai đáng kể | Protocol v2 ghi nhận 2.884 dòng raw OHLC vi phạm thứ tự ngoài tolerance. Raw OHLC được cách ly thành NaN cho label/fill và row vẫn được giữ. Cần xác minh, sửa nguồn có provenance rồi đánh giá lại theo protocol mới. |
| Data vintage | Source gồm nhiều nguồn và ratio/event fills; không có publication-time ledger của toàn bộ adjusted/raw history. Cần kiểm tra điều chỉnh hồi tố, unit và provenance của lịch sử dựng lại. |
| Dòng tiền ngoại | `foreign_net_vol` hiện diện nhưng toàn số 0 ở 2018–2025; chỉ 1.103/68.531 dòng stock 2026 khác 0. Chưa xác minh lịch sử flow thực; lần train này loại foreign-flow feature thay vì coi thiếu quan sát là tín hiệu trung tính. |
| Corporate entitlements | Raw-price PnL chưa hạch toán cash dividend, stock dividend, split/rights entitlement hoặc thay đổi số cổ phiếu. Factor flag chỉ cảnh báo, không hoàn thiện total-return accounting. |
| Execution | Không có order-book/depth, opening auction allocation, queue ở trần/sàn, partial fill, latency hay xác nhận broker. Daily open/close và positive volume không chứng minh toàn bộ lệnh khớp tại mức giá đó. |
| Confidence và drift | Cần thêm evaluation độc lập và calibration trên cohort được chọn/khớp; kiểm tra tail, ticker/sector/regime concentration, re-entry và sự phụ thuộc theo thời gian. |
| Vận hành | Cần contract về data, broker, settlement, policy/risk và monitoring cùng quy trình release/review trước khi đề nghị thay model PROD. |

## 8. Kết quả lần chạy 2/10/2026

**Cả hai lần chạy bị loại; chưa chứng minh được khả năng sinh lời cao.**
Nguồn: 1.011.757 daily bars, 407 mã stock, từ 05/01/2015 đến 01/10/2026,
được xuất PROD trong transaction chỉ đọc. SHA256 bản gzip:
`dd89de0d85fe6b454584a62ef66fe14bdb045b68144d41d58fd18d7dbb8973d1`.

Lựa chọn diagnostic của cả hai lần là `boosted_h3_open`: p ít nhất 0.50,
expected net return lớn hơn 0. Các cổng còn lại được giữ nguyên. Không có
candidate đạt toàn bộ DEV gate, nên không được chấp thuận thay model đang chạy.

### Development 2023–2025

NAV mỗi năm bắt đầu lại từ 1 tỷ VND; các annual return bên dưới không phải một
sổ danh mục chạy liên tục ba năm.

| Calibration | NAV 2023 | NAV 2024 | NAV 2025 | Tổng closed trades | Win rate net | PF net |
|---|---:|---:|---:|---:|---:|---:|
| Giữ cả năm | 0.00% | +5.98% | +4.64% | 193 | 55.44% | 1.471 |
| Refresh mỗi tháng | +0.79% | −1.93% | +8.42% | 441 | 48.75% | 1.112 |

Bản annual đứng ngoài toàn năm 2023 nên không đạt yêu cầu return dương ở mỗi
năm. Bản monthly không qua win rate/PF/expectancy và có năm thua. Monthly có
một năm đẹp không chứng minh rằng cơ chế này tốt hơn một cách ổn định.

### Tháng 1–7/2026

| Calibration | NAV ròng | Lãi/lỗ trên 1 tỷ | Closed trades | Win rate net | PF net | Max drawdown | Exposure bình quân |
|---|---:|---:|---:|---:|---:|---:|---:|
| Giữ cả năm | −0.1702% | −1.702.151 VND | 45 | 37.78% | 0.980 | 3.85% | 4.64% |
| Refresh mỗi tháng, reused diagnostic | −0.1698% | −1.698.137 VND | 4 | 25.00% | 0.866 | 1.03% | 0.42% |

Friction 100 bps roundtrip làm NAV annual còn −2.81%, monthly −0.41%.
Drawdown thấp ở monthly chủ yếu do gần như giữ toàn tiền mặt; không được diễn
giải thành chọn cổ phiếu tốt. Mẫu bốn trade không đủ kết luận tỷ lệ thắng dài hạn.

Annual mean net trade return −0.4879%; AUC của profit classifier trên 13.900
row labeled là 0.5340. Bin p=0.50–0.55 có profit thực 42.95% (468 rows);
bin p=0.55–0.60 chỉ 26.92% (26 rows). Ngay cả head có target đúng vẫn có thể
overestimate vùng chọn mua. q10 breach khoảng 9.52% đo tail risk, không tạo ra
alpha. Khoảng bootstrap weekly PnL annual là khoảng −2.84 triệu đến +3.82
triệu VND/tuần, gồm 0 và chưa hiệu chỉnh selection.

Trong cùng simulator H3, momentum cố định mất 26.48%, reversal mất 20.36%;
cash không lãi là 0%, VNINDEX price return −2.94%. Benchmark rule không được
tối ưu thêm sau khi xem các kết quả này.

### Lãi/lỗ từng mã, annual tháng 1–7/2026

| Mã | Closed trades | Thắng | PnL net VND |
|---|---:|---:|---:|
| PET | 3 | 0 | −16.397.364 |
| PVT | 1 | 0 | −7.807.975 |
| CTD | 1 | 0 | −6.517.121 |
| BVH | 2 | 0 | −6.439.432 |
| BSR | 2 | 1 | +13.861.331 |
| PVD | 3 | 2 | +13.634.018 |
| GVR | 2 | 2 | +11.000.261 |

Đây là phần trích; CSV giữ toàn bộ 24 mã. Không dùng bảng này để blacklist
mã thua hoặc whitelist mã thắng cho lần evaluation cùng kỳ.

### Tháng 8–1/10/2026, đã xem trước

Annual: 15 closed trades, thắng 26.67%, NAV −0.94%, PF 0.672. Monthly: không
có giao dịch, NAV 0%. Không có giao dịch không phải bằng chứng tỷ lệ lời tốt.

### Artifact và tái chạy

Các thư mục local được giữ trong vùng scratch đã ignore:

- `D:/AIInvest/ai-engine/scratch/profit_research_2026-10-02_v2/`: annual.
- `D:/AIInvest/ai-engine/scratch/profit_research_2026-10-02_v3_monthly/`: monthly.
- `D:/AIInvest/ai-engine/scratch/profit_research_2026-10-02_v4_monthly_fresh/`:
  dựng lại dataset và train toàn bộ từ đầu với cùng cấu hình monthly.

Lần v3 dùng lại base estimators và dataset của v2. Sau review provenance, khả
năng reuse cache đã được bỏ khỏi runner bàn giao. V4 train mới xác nhận cả
292.800 forecast ở 12 file DEV và mọi metric evaluation so sánh đều khớp v3
với sai lệch tối đa **0**; không thêm cấu hình hoặc tối ưu lại ngưỡng.
`fresh_reproduction.json` ghi đối chiếu, `protocol.json` v4 ghi hash bốn file
Python thực thi. Vì vậy kết luận monthly có thể tái lập từ source đã xuất.

- `protocol.json`: input hash, nguồn, quality, candidate và policy đã khai báo.
- `dataset_h3_metadata.json`, `dataset_h5_metadata.json`: feature/target contract,
  universe và coverage.
- `selection_lock.json`: quyết định chọn candidate từ DEV trước holdout.
- `selected_model.joblib`: artifact đã khóa, kèm cutoff và metadata.
- `report.json`: tổng hợp DEV selection, holdout, diagnostic, stress và trạng thái
  empirical/production sau khi runner kết thúc.
- `broker_audit_summary.json`, `devYYYY_*`: tái dựng PnL từng mã ở năm DEV
  được chọn, fees, monthly PnL và đối chiếu với số đã khóa.
- `holdout_trades.csv`, `holdout_nav.csv`, `holdout_by_stock.csv` và các bản
  tương ứng diagnostic/stress: bằng chứng PnL từng trade/mã và sổ NAV.
- `holdout_nav.png`: đường NAV cùng các benchmark và stress.
- `selected_model_last_calibration.joblib`: trạng thái hiệu chỉnh mới nhất;
  artifact giữ `REJECTED_PROFITABILITY_GATE`, chưa được tích hợp vào Standalone.

Ví dụ tạo lần chạy mới với output trống:

```powershell
Set-Location 'D:\AIInvest\ai-engine'
python scripts/research_short_horizon_profit.py --bars scratch/short_horizon_daily_bars_2026-10-02.csv.gz --output scratch/profit_research_new --allow-missing-index-sessions
python scripts/research_short_horizon_profit.py --bars scratch/short_horizon_daily_bars_2026-10-02.csv.gz --output scratch/profit_research_monthly_new --calibration-mode monthly --allow-missing-index-sessions --holdout-previously-inspected
```

Các lần chạy 02/10 đã thực thi train, forecast, chronological portfolio
evaluation và tái dựng PnL trên dữ liệu thật, chưa chạy hoặc thêm unit test.
Không deploy, đổi cấu
hình PROD hoặc đặt lệnh. Quy trình này áp dụng policy hiện tại đồng nhất cho
lịch sử nghiên cứu; không tái tạo mọi biểu phí/lô/settlement từng năm cũ.

## 9. Thứ tự sửa theo góc nhìn broker

1. **Sổ giá và quyền:** bổ sung nguồn raw execution được xác minh, điều chỉnh
   doanh nghiệp với ngày công bố/ex/record/payment, shares và receivable. Label
   phải khớp tiền và quyền thực nhận, có provenance và trạng thái chưa xác minh.
2. **Universe theo thời điểm:** lịch sàn, board/price band, listing/delisting,
   suspension và thanh khoản lịch sử; giữ cả mã biến mất khỏi danh sách hiện tại.
3. **Thông tin chọn mã:** xây dữ liệu khả dụng tại thời điểm quyết định về
   catalyst, dòng tiền và cấu trúc thanh khoản. Kiểm tra quan sát thiếu trước
   khi thêm feature; không học từ một cột toàn 0 như dữ liệu flow thực.
4. **Chiến lược có thể khớp:** replay quote/intraday theo thứ tự thời gian,
   opening auction/limit queue/partial fill; mô hình hóa entry cancellation và
   stop sau sellability. Giá fill và label phải dùng cùng một policy.
5. **Profit head và cấp vốn:** so sánh EV/p_profit/q10 trên cohort được chọn
   rồi thực sự khớp; hiệu chỉnh theo thời gian, kiểm tra payoff/loss tail và
   rủi ro danh mục/sector. Chỉ cấp vốn khi có lợi thế sau costs và kiểm định
   độc lập, kèm điều kiện đứng ngoài và circuit breaker vận hành.

Các lỗi chất lượng đầu vào đã được chặn trong challenger local. Lịch sử nguồn,
entitlement và khả năng execution chưa được chứng nhận. Thay thuật toán trên
cùng dữ liệu hiện tại chưa giải quyết được yêu cầu sinh lời cao.

## 10. Training tập trung H3 ngày 03/10/2026

Giả thuyết được khai báo trước lần chạy: estimator giữ nguyên cả năm có thể
không thích ứng với thị trường lướt sóng. Thử một cửa sổ 24 tháng, gồm 21 tháng
fit và ba tháng calibration, train lại toàn bộ trước mỗi tháng. Chỉ dùng
`boosted`, H3 và ba gate cũ; không thêm feature, sweep tham số, đổi sizing
hoặc sửa chi phí. Dataset được dựng lại từ cùng bản xuất có SHA256 đã ghi.

Trước đó, thử bỏ cổng xác suất trên forecast DEV đã lưu, giữ EV > 0 và mọi
giả định danh mục. Mean annual NAV của bản annual giảm từ +3,5379% xuống
−0,1707%; bản monthly giảm từ +2,4258% xuống −0,6478%. Một số thời điểm có lãi
sớm hơn nhưng năm 2025 thua nhiều hơn. Vì vậy lần full refit vẫn giữ các cổng
p_profit cũ. Không chọn ngưỡng bằng kết quả 2026.

Command của lần chạy đã hoàn tất:

```powershell
Set-Location 'D:\AIInvest\ai-engine'
python scripts/research_short_horizon_profit.py --bars scratch/short_horizon_daily_bars_2026-10-02.csv.gz --output scratch/profit_research_2026-10-03_v5_refit_h3 --calibration-mode monthly-refit --training-window-months 24 --families boosted --horizons 3 --allow-missing-index-sessions --holdout-previously-inspected
```

### Development 2023–2025

| Gate H3 | NAV 2023 | NAV 2024 | NAV 2025 | Closed trades | PF net | Mean net trade return |
|---|---:|---:|---:|---:|---:|---:|
| open | −2,8905% | −2,7686% | −11,3293% | 129 | 0,4538 | −1,7191% |
| selective | 0,0000% | +0,6594% | −3,7352% | 30 | 0,6569 | −1,1668% |
| strict | 0,0000% | 0,0000% | −1,4715% | 15 | 0,7523 | −1,1949% |

Không candidate đạt DEV gate. `boosted_h3_strict` là lựa chọn diagnostic vì
mean annual NAV ít âm nhất; hai năm không giao dịch không phải bằng chứng
sinh lời. Với gate open, 2023 chưa có tổng lãi đã chốt dương; 2024 lần đầu
dương ngày 25/04 nhưng cuối năm vẫn lỗ, 2025 lần đầu dương ngày 10/03 rồi
cuối năm lỗ 11,3293%. Có lãi sớm từng lúc chưa đáp ứng mục tiêu cuối kỳ.

### Evaluation 2026 đã từng xem

Candidate đã khóa `boosted_h3_strict`, NAV khởi đầu 1 tỷ VND:

| Period | NAV net | PnL net VND | Closed trades | Win rate | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| 01–07/2026, reused diagnostic | −2,8232% | −28.231.776 | 10 | 10,00% | 0,0934 | 2,8232% |
| 08–01/10/2026, previously inspected | 0,0000% | 0 | 0 | — | — | 0,0000% |

Friction 100 bps làm period đầu còn −3,5623%. Tổng lãi đã chốt chưa từng
dương trong period đầu; period sau không có giao dịch. Mọi trade đã đóng
đều giữ đúng ba phiên; không có exit bị hoãn vượt horizon trong lần chạy.
Trạng thái: **`REJECTED_PROFITABILITY_GATE`**. Full refit 24 tháng không cải
thiện hướng này; artifact mới không đủ điều kiện thay Standalone hiện tại.

Artifacts nằm tại `D:/AIInvest/ai-engine/scratch/profit_research_2026-10-03_v5_refit_h3/`:
protocol với code/data hash, dataset/metadata H3, model từng tháng, DEV
forecast, selection lock, report và CSV trade/NAV/stock/stress. Forecast
được đối chiếu với model qua tháng và metadata cutoff; chưa có model hash
trên từng row forecast. Sổ giá, entitlement, cohort thực khớp và một kỳ
forward mới vẫn cần được xác minh trước khi xét cấp vốn thực.

Kiểm tra tập trung: **22 tests passed**, bao gồm purge theo outcome, thay
estimator trước mỗi tháng, payoff bất đối xứng, timing PnL theo cuối phiên,
giới hạn H3/H5 và trì hoãn exit không thể khớp. Hai file test được đưa vào
CI AI Engine. Thay đổi chỉ nằm ở nghiên cứu, test, CI và tài liệu; không có
ghi DB/SAG, deploy hay đặt lệnh broker trong lần này.
