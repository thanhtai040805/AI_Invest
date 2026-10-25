# Truy vết khác biệt LIVE và Replay

Ngày kiểm tra: 25/10/2026, giờ Việt Nam. Phân tích ban đầu và bản sửa Agent-01
trên cùng branch PR #28; không chạy full Replay/LLM mới, không ghi DB hay triển khai.

Nguồn code: PR #28, branch `codex/factor-readonly-replay-review`, HEAD
`e61f3ddb93ed1aa004e186ef34380901f34e4aba`.
VPS thực chạy image AI Engine `da6b8fa364d2ae148de917e784b1dcd24eb3d0bf`.
Thư mục Git trên host ở SHA khác, có dirty files, nhưng container không mount
source host. Đã kiểm tra đoạn code liên quan bên trong container.

Trong báo cáo, LIVE là luồng tự chạy theo thị trường hằng ngày. VPS đang cấu hình
`MULTI_AGENT_MODE=SHADOW_RUNNER`, `STANDALONE_ML_MODE=SHADOW_RUNNER`; không phải
đang gửi lệnh thật qua broker. Enum `LIVE` hiện bị chuyển thành `DISABLED`
trong constructor pipeline vì chưa có broker gateway.

## Cập nhật: sửa bug thời gian và contract realtime Agent-01

Theo yêu cầu tiếp theo, sửa trực tiếp trên branch PR #28 hiện tại; không tạo
branch/PR mới. Chỉ một file runtime thay đổi: `market_surveillance.py`.

- LIVE lấy giờ hiện tại có timezone Việt Nam, độc lập timezone container.
- Timestamp có offset được chuyển về HCM; timestamp naive giữ quy ước giờ HCM.
- So sánh "hôm nay" cũng dùng ngày HCM. Giữ nguyên cách đọc date-only, công thức
  HMM/GARCH, gate BEAR, market_data_date, factor và runner Replay.
- Sửa nhánh realtime theo contract MarketDataService: nhận diện chỉ số bằng
  `name/indexName` bên cạnh alias cũ; `changePercent` luôn chia 100 để ra return;
  giữ EOD return khi biến động chỉ số thiếu; đọc `price/floor/ceiling/volume`.
- Không gắn nhãn snapshot có `stale=True` thành realtime và không suy ra chạm
  sàn từ giá bằng 0. Giữ nguyên threshold -6,9% / +6,9%, Crisis -3% và HMM.
- Thêm 23 trường hợp test offline vào CI, bao gồm các contract realtime trên.
  Trước sửa contract: 7 trường hợp fail, 11 pass; sau sửa và thêm case thiếu
  biến động chỉ số: 19 pass. Fixture mới dùng đúng trường normalized của service.

Đối chiếu Agent-01 bằng dữ liệu EOD trước phiên và model HMM hiện có trên VPS:
đọc hydration trong transaction read-only; chặn persistence bằng mock; DNSE
được mock không khả dụng để cố định cùng đầu vào. Chỉ gọi `process`, không
dispatch pipeline, không ghi state/log/receipt, không LLM hoặc train model.

| Ngày | Phiên LIVE cũ | Phiên LIVE sửa / Replay reference | Regime cả ba | P(BEAR) LIVE sửa = Replay reference |
|---|---|---|---|---:|
| 19/10 | CLOSED | CONTINUOUS_AM | BEAR_MARKET | 94,5447% |
| 20/10 | CLOSED | CONTINUOUS_AM | BEAR_MARKET | 86,7927% |
| 21/10 | CLOSED | CONTINUOUS_AM | BEAR_MARKET | 99,8848% |
| 22/10 | CLOSED | CONTINUOUS_AM | BEAR_MARKET | 98,1118% |
| 23/10 | CLOSED | CONTINUOUS_AM | BEAR_MARKET | 71,1715% |

LIVE sửa đi vào nhánh thử lấy realtime; Replay lịch sử không lấy realtime hôm
nay. Khi cùng fallback EOD, session/regime/HMM khớp 5/5. Kết quả này chưa tái
dựng được tác động của DNSE buổi sáng lịch sử vì chưa có snapshot tương ứng.
P(BEAR) tính lại 22/10 khác log cũ 90,9345%, nhưng cả code cũ và code sửa đều
cho 98,1118% với dữ liệu/model hiện có. Không quy chênh đó cho patch thời gian;
chưa cô lập được thay đổi dữ liệu hay model so với lúc ghi log.

Phân loại: xác định sai phiên/ngày do UTC là bug đã tái hiện và sửa. Gate BEAR
không có bằng chứng là bug: cùng đầu vào Replay reference cũng ra BEAR. Luồng
factor D so với D-1 có thật, nhưng thiếu cache đã có fallback chủ đích; chỉ
khác nhánh hoặc NAV tốt hơn chưa chứng minh bug. Đổi ngày đọc/cache là thay
đổi đầu vào cần quyết định riêng, nên không làm trong bản sửa Agent-01 này.

Validation sau sửa và kiểm lại luồng LIVE: 155 tests PASS, 2 seeded-DB Beneish cases deselected,
3 warning joblib/NumPy có sẵn; Ruff E9,F63,F7,F82 PASS. Gộp thay đổi vào PR #28;
chưa merge/deploy. Đối chứng full Replay tiếp theo xác nhận không ảnh hưởng
tiêu cực tới NAV/cash/receipt/fee trong 28 phiên; xem
`2026-10-25-agent01-reset-replay-28.md` để có số liệu và giới hạn đầy đủ.

Kiểm bổ sung qua đúng public call path
`AgentRegistry.dispatch → BaseAgent.run_event → MarketSurveillanceAgent.process`
với context LIVE như daily pipeline, không truyền `current_time`. Dùng
MarketDataService thật với hub/Redis/DB giả lập tại I/O để kiểm cả contract
service, envelope SUCCESS và câu lệnh audit. Cả bốn trường hợp pass:

| Feed | Kết quả |
|---|---|
| Hub có quote DNSE mới và VNINDEX `name`, -0,1% | CONTINUOUS_AM, DNSE_REALTIME_STREAM, Normal; không phát sinh Crisis giả |
| Snapshot chỉ có baseline stale | CONTINUOUS_AM, POSTGRESQL_EOD fallback |
| Snapshot rỗng | CONTINUOUS_AM, POSTGRESQL_EOD fallback |
| Feed phát sinh exception | CONTINUOUS_AM, POSTGRESQL_EOD fallback; dispatch vẫn SUCCESS với EOD hợp lệ |

Mỗi trường hợp xác nhận audit output bằng đúng data trả về và giữ nguyên
`market_data_date=D` của LIVE. Không thay đổi runtime thêm trong lượt kiểm này.
Đây là kiểm tích hợp cục bộ qua facade thật, không phải một phiên thị trường
đang mở với kết nối DNSE mới thực tế.

VPS kiểm lại vẫn healthy trên image `da6b8fa`, còn đoạn `datetime.now()` cũ.
Automation và DNSE đều enabled, Multi-Agent là SHADOW_RUNNER. Do bản sửa
chưa được deploy, không xác nhận production đã hết lỗi. Bằng chứng hiện tại
chứng minh hành vi bản sửa trong phạm vi kiểm và parity trên cùng EOD input;
chưa chứng minh hiệu quả đầu tư LIVE bằng Replay hoặc một phiên realtime
thực tế sau triển khai.

### Phân loại lại ba khoảng cách được hỏi

| Khoảng cách | Bằng chứng | Kết luận trong phạm vi này |
|---|---|---|
| Factor D / D-1 | Replay cutoff D-1 được ghi rõ trong Project Memory 24/09; exact-date miss đã có fallback và producer chính dùng cùng FactorService | Khác đường đọc là có thật; chưa chứng minh sai kết quả. Đổi as-of/cache là điều chỉnh đầu vào, giữ nguyên. |
| Partial fill / ATC | Project Memory 23/09 ghi rõ ATO/ATC unsupported, partial-fill lifecycle nằm ngoài mô hình Shadow; code/tests hiện thực đúng giới hạn đó | Bổ sung khả năng khớp mới, không phải bug contract đã chứng minh. Không thay đổi trong fix Agent-01. |
| Polling 300s/60s và thời gian LLM | Interval được cấu hình rõ; runner Replay dùng thời gian sự kiện lịch sử, LIVE phải chờ tính toán | Khác biệt cấu trúc cần thiết kế/đo riêng; không tự coi là bug và không đổi trong patch này. |

### Các bug realtime Agent-01 mới xác nhận

MarketDataService trả `_map_market_index` với tên ở `name` và biến động ở
`changePercent`. Agent trước đó không nhận `name`, nên có thể bỏ qua VNINDEX
thực và giữ return EOD. Với alias cũ `symbol`, mức -0,1% lại bị giữ nguyên
-0,1 thay vì -0,001, có thể kích hoạt Crisis giả theo ngưỡng -3%.
Hai lỗi này phải sửa cùng nhau: nhận tên đúng mà giữ phép đổi đơn vị cũ sẽ
làm nhánh realtime mới đọc được chỉ số nhưng có nguy cơ diễn giải sai biến động.

Snapshot stock của hub dùng `price/floor/ceiling/volume`; Agent lại đọc
`matchPrice/floorPrice/ceilingPrice/totalVolume`. Các phiên giá chạm band nhưng
biến động làm tròn chưa chạm 6,9% có thể bị đếm thiếu. Test dùng hai mã ở band
với biến động +/-6,89% và một mã giá 0: sau sửa đếm đúng 1 sàn, 1 trần.

`get_snapshot` có thể trả đủ universe lấy từ PostgreSQL với `stale=True`.
Agent cũ chỉ cần stocks không rỗng là gắn DNSE_REALTIME_STREAM và ghi đè
thống kê EOD. Test 20 mã stale giảm sàn tái hiện Crisis giả; sau sửa giữ
fallback EOD. Không đặt thêm ngưỡng coverage hay thay đổi công thức thị trường.

Các bug contract này chưa chứng minh là nguyên nhân các receipt/log LIVE
19–23/10: cả năm lần sáng đều không chạy nhánh realtime do bug giờ UTC.
Không lấy mức tăng cuối ngày của VNINDEX để kết luận HMM buổi sáng sai.

Đã chạy lại phép đối chiếu EOD 5 ngày bằng patch đầy đủ, vẫn khớp Replay
reference 5/5. File bằng chứng cục bộ:
`ai-engine/scratch/output/agent01_live_20261019_23/eod_comparison.json`.
Core DB chỉ giữ 407 candle cổ phiếu lúc 09:45 cho mỗi ngày, không có nến chỉ
số trong bảng intraday được kiểm tra; phần nguồn gồm raw, LOCF và forward-fill,
được ghi/backfill sau đó. Chúng không đủ tái dựng đầy đủ snapshot realtime
VNINDEX/breadth/price bands đúng lúc chạy. Không gọi pipeline historical rồi
gắn nhãn kết quả đó là LIVE thực tế.

## Kết luận và mức bằng chứng

LIVE và Replay chưa dùng cùng ngữ cảnh thời gian, đường lấy factor, cách khớp
lệnh và cách giám sát vị thế. Vì vậy cùng chuỗi Agents không bảo đảm cùng
quyết định hoặc danh mục. Replay được giữ làm mốc hành vi mong muốn.

- Lỗi đã xảy ra trên VPS: Agent-01 nhận sai phiên giao dịch do UTC.
- Nguyên nhân trực tiếp khiến các buổi sáng gần đây không mua: gate BEAR trả
  về ngay trước Research/Thesis/Counter/CIO.
- Khoảng cách đã xác nhận trong code: ngày lookup factor, partial fill/ATC,
  đồng hồ tín hiệu và tần suất/đầu vào giám sát vị thế.
- Chưa có đối chứng cùng ngày, cùng state để quy từng khoảng cách thành số tiền
  lợi nhuận LIVE bị mất. Không coi lỗi giờ là bằng chứng HMM phân loại sai.

## 1. Agent-01 dùng UTC như giờ giao dịch Việt Nam

Call path:

`DailyPipelineDaemon.trigger_manual` → `DailyInvestmentPipeline.run` →
`AgentRegistry.dispatch("market_surveillance", replay_context)` →
`MarketSurveillanceAgent.process` → `SessionContextManager.get_session`.

Pipeline chỉ thêm `current_time=09:45+07:00` khi Replay
(`daily_pipeline_orchestrator.py:228–239`). LIVE không truyền thời gian;
Agent-01 fallback `datetime.now()` không có timezone
(`market_surveillance.py:297`). Session manager so sánh trực tiếp giờ đó với
lịch HOSE (`session_context_manager.py:29`).

Container thực dùng UTC. Cùng một thời điểm: 09:46 HCM = 02:46 UTC.
Hàm trả `CLOSED` với datetime UTC naive, `CONTINUOUS_AM` với datetime HCM.
Nhánh lấy DNSE realtime yêu cầu phiên đang khớp hoặc `is_realtime=True`
(`market_surveillance.py:321`), nên bị bỏ qua trong các lần chạy sáng này.

Bằng chứng đọc từ `log_market_surveillance`; toàn bộ thời gian dưới đây là HCM:

| Ngày chạy | Giờ ghi log | session_code | data_source | effective_date | regime |
|---|---|---|---|---|---|
| 19/10 | 09:46:17 | CLOSED | POSTGRESQL_EOD | 16/10 | BEAR_MARKET |
| 20/10 | 09:46:14 | CLOSED | POSTGRESQL_EOD | 19/10 | BEAR_MARKET |
| 21/10 | 09:46:10 | CLOSED | POSTGRESQL_EOD | 20/10 | BEAR_MARKET |
| 22/10 | 09:46:03 | CLOSED | POSTGRESQL_EOD | 21/10 | BEAR_MARKET |
| 23/10 | 09:46:19 | CLOSED | POSTGRESQL_EOD | 22/10 | BEAR_MARKET |

Universe Discovery cũng gọi `get_session(datetime.now())` ở dòng 87, nên có
cùng rủi ro bỏ qua bước bổ sung trạng thái giao dịch realtime khi chạy UTC.
Đây là nhánh lấy dữ liệu bị sai; không phải mọi thành phần DNSE đều ngừng chạy.

## 2. LIVE gần đây dừng ngay ở gate BEAR, trước khi chọn cổ phiếu

`daily_pipeline_orchestrator.py:331–368` trả
`BEAR_DEFENSE_100PCT_CASH` khi regime là BEAR hoặc context Crisis. Không đi
tới các pha Research, Thesis, Counter, CIO và phân bổ mua mới.

`job_states` xác nhận lần chạy 23/10 bắt đầu 09:46:16, kết thúc 09:46:19 HCM,
status này. Không có Research log mới sau ngày tạo thực tế 17/10 trong DB VPS
tại lúc kiểm tra. Năm log sáng ở bảng trên đều có regime BEAR.

Lúc 17:30 ngày 23/10, Agent-01 ghi RANGE_BOUND với effective_date 23/10.
Đó là đánh giá trên dữ liệu EOD mới, không chứng minh đánh giá buổi sáng sai.
Daily daemon đánh dấu ngày đã hoàn tất và không tự chạy lại chỉ vì regime
thay đổi (`daily_pipeline_daemon.py:96–105, 141–149`).

Điều này giải thích trực tiếp hiện tượng không có lệnh mua mới gần đây.
Chưa có bằng chứng để bỏ gate BEAR hoặc hạ ngưỡng rủi ro.

## 3. LIVE và Replay tra factor ở hai ngày khác nhau

Pipeline đặt `market_data_date=D` cho LIVE, `D-1 ngày lịch` cho Replay
(`daily_pipeline_orchestrator.py:231`). Research gọi
`get_factor_score(..., score_date=market_data_date)` ở dòng 118. Repository
đòi `score_date = %s`, không tìm bản gần nhất ở trước ngày đó
(`intelligence_repository.py:40`). Thiếu cache thì chuyển sang
`FactorService.compute_factors_for_ticker` ở dòng 143.

Producer daily chạy sau đóng cửa, ghi factor theo trade_date
(`daily_etl.py:338`, `factor_service.py:210`). Trên VPS, 407 dòng factor ngày
22/10 được cập nhật 16:22:41–16:22:49 HCM; lúc kiểm tra chưa có dòng ngày 23/10.
Do đó nếu Research được gọi sáng 23/10, lookup LIVE ngày 23/10 sẽ miss;
lookup Replay ngày 22/10 có thể hit, nếu qua kiểm tra tính hợp lệ của cache.

Đây là cơ chế tạo khác đầu vào F1–F6/CSS dù cùng ngày giao dịch. Không khẳng
định fallback luôn cho điểm thấp hơn. Các buổi sáng vừa kiểm tra đã dừng ở
BEAR nên chưa thực thi nhánh Research này.

## 4. LIVE thiếu hai khả năng khớp lệnh Replay đang có

LIVE: `trade_execution.py:395–404` gọi `shadow_fill` cho toàn bộ shares.
Hàm chỉ trả giá khi đủ toàn bộ lượng tại giá giới hạn trong một snapshot;
thiếu thì raise (`shadow_fill.py:42–52`). BUY có thể thành pending và được
thử lại, nhưng mỗi lần vẫn cần đủ lượng trong snapshot đó.

Replay: `fill_pending` lấy `min(left, executable_shares(...))`, làm tròn lô,
ghi receipt phần khớp và tiếp tục phần còn lại
(`replay_agent_pipeline.py:329`). Ví dụ kiểm offline: lệnh 300 cp được khớp
100 rồi 200 từ hai snapshot; LIVE không tích lũy như vậy.

Replay còn có `fill_closing_auction` ở dòng 398, được gọi tại 14:45.
LIVE shadow daemon chỉ xử lý phiên continuous ở dòng 32–35;
`shadow_fill` từ chối `closing_auction` ở dòng 10; pending bị hết hạn lúc 14:45
(`portfolio_repository.py:977`). Lệnh chưa đủ điều kiện khớp trước 14:30 sẽ
không được LIVE xử lý ở ATC như Replay.

Đây là thiếu parity ở LIVE. Chưa quy được mức thiệt hại thực tế từ DB hiện có.

## 5. LIVE quan sát vị thế và đặt tín hiệu theo nhịp khác Replay

LIVE position daemon mặc định quét mỗi 300 giây, chuyển 60 giây lúc
14:00–14:45 (`position_monitoring_daemon.py:32–48`), lấy giá hiện tại.
Replay duyệt nến 1 phút, truyền low làm current_price và dùng high cập nhật
peak (`replay_agent_pipeline.py:655–676`). LIVE có thể bỏ qua một đỉnh/đáy
xuất hiện giữa hai lần quét, dẫn tới khác trailing stop và thời điểm thoát.

Replay cố định signal_time 09:45 và enqueue lệnh sau mốc lịch sử đó
(`replay_agent_pipeline.py:890, 947, 976`), dù cần thời gian thực để gọi LLM.
LIVE xử lý tuần tự và chỉ gửi sang execution sau khi phân tích xong
(`daily_pipeline_orchestrator.py:591–595, 768–785`); Research đọc giá tại lúc
xử lý từng mã. Vì vậy thời gian tính toán trở thành độ trễ thị trường ở LIVE.
Chưa đo được ảnh hưởng của độ trễ này trong các ngày gần đây do gate BEAR
đã chặn toàn bộ chuỗi phân tích phía sau.

## 6. Phiên bản và sổ kết quả hiện tại chưa là đối chứng LIVE độc lập

VPS chưa có PR #28. Các sửa mapping, ownership factor, financial handoff và
timestamp học trên PR chưa áp dụng cho container đang chạy. Giữ nguyên code
Replay nhưng chạy LIVE trên bản cũ vẫn có thể cho quyết định khác.

Đọc receipt của chính account Multi-Agent trên VPS cho thấy 164 receipt
`POSTGRES_REPLAY` và 1 receipt ngoài Replay (HAH SELL 100 ngày 19/10).
NAV 1.010.101.872đ ngày 23/10 của account này chứa lịch sử Replay; không thể
gọi toàn bộ đó là thành tích LIVE độc lập. Account Standalone ML là sổ riêng.

Kết quả 1.005.857.685đ so với 1.004.416.888đ trong PR là đối chiếu run LOCAL
với lịch sử PROD của cửa sổ 12/08–23/09. Nhãn PROD mô tả nơi lưu/chạy, không
tự chứng minh các receipt được sinh bởi daemon realtime. Chưa có căn cứ để
đổi phần chênh đó thành tổn thất LIVE do một lỗi duy nhất.

## Thứ tự xử lý đề xuất, chưa thực hiện

1. Sửa ngữ cảnh giờ Việt Nam cho LIVE ở boundary pipeline và các caller tự lấy
   giờ; kiểm tra phiên thực trả CONTINUOUS_AM lúc 09:45 HCM.
2. Chốt quy ước ngày dữ liệu EOD/cache để LIVE dùng cùng đầu vào như Replay.
   Giữ rõ D-1 ngày lịch hiện tại của Replay, bao gồm nhánh fallback cuối tuần.
3. Đưa khả năng khớp từng phần và ATC sang LIVE Shadow, giữ validation giá,
   độ mới order book, governance, giới hạn tiền và T+2.5.
4. Đồng bộ nguồn/nhịp quan sát vị thế và đo độ trễ từ tín hiệu đến lệnh.
5. Đối chiếu cùng ngày và cùng trạng thái danh mục/calibration, ghi điểm lệch
   đầu tiên. Không hạ gate hoặc sửa Replay để làm kết quả giống LIVE.

## Kiểm chứng đã chạy

- SSH và SQL read-only trên core PostgreSQL; không truy cập SAG.
- Assert offline gọi đúng SessionContextManager: cùng instant 23/10 02:46:19Z
  cho CLOSED với UTC naive, CONTINUOUS_AM khi chuyển HCM.
- 12 tests PASS: `test_shadow_fill.py`, `test_replay_historical_book.py`,
  `test_replay_domino_controls.py`; 3 warning joblib/NumPy có sẵn.
- Không chạy LLM trả phí, Replay mới, migration/reset/backfill, merge/deploy.
- Chưa chứng minh lợi nhuận phản thực tế của LIVE nếu sửa các khoảng cách trên.
