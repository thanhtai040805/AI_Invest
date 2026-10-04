# LAB ML lướt sóng cổ phiếu Việt Nam

Ngày lập: **04/10/2026**. Mục tiêu đang mở: tìm và kiểm chứng một chính sách
lướt sóng tạo lợi nhuận ròng quan sát được, đến bằng chứng giao dịch broker thật.
**LAB được dựng xong hoặc paper có lãi chưa hoàn thành mục tiêu này.**

Người dùng đã xác nhận **NAV nghiên cứu/shadow 1 tỷ VND**. Vốn nạp broker,
ngân sách lỗ, giới hạn vị thế/ngành và quyền chạy lệnh thật chưa được chốt.
Các giới hạn simulator là giả định nghiên cứu, không tự trở thành quyền cấp
vốn thật. Không có cam kết một mã cụ thể hoặc mọi giai đoạn đều sinh lời.

## 1. Cách vận hành có điểm dừng

| Thành phần | Vai trò |
|---|---|
| [run_swing_lab.py](../../ai-engine/scripts/run_swing_lab.py) | Điều phối một vòng có giới hạn, lưu trạng thái và kết quả; tránh chạy trùng. |
| [protocol.json](../../ai-engine/lab/protocol.json) | Protocol bền vững: mục tiêu, family, giới hạn thử, lịch sử thất bại và điều kiện promotion. |
| [experiments/](../../ai-engine/lab/experiments/) | Manifest đăng ký **trước** từng lần chạy: giả thuyết, đối chứng, dữ liệu, kỳ đánh giá, tiêu chí loại và deadline. |
| [vn_swing_lab/](../../ai-engine/scratch/vn_swing_lab/) | Ledger và artifact từng run; giữ nguồn/hash, output và quyết định, không ghi đè lịch sử. |
| [LAB001_feasibility.json](../../ai-engine/lab/experiments/LAB001_feasibility.json) | Vòng đầu: chẩn đoán kinh tế trên DEV 2023–2025, chưa train mô hình mới. |

Active goal theo toàn bộ mục tiêu lợi nhuận thực. Heartbeat của chat hiện tại
đã được đặt **mỗi 6 giờ**, ID `lab-ml-l-t-s-ng-vi-t-nam`. Đây là lịch đánh
thức để xem xét bước tiếp theo, không phải tiến trình train chạy liên tục.
Mỗi lần chỉ khởi động tối đa một vòng LAB đã đăng ký: deadline mặc định
900 giây, tối đa 3.600 giây theo protocol. Có run đang chạy thì tiếp tục
theo dõi run đó; không tạo run thứ hai.
Giữ yên khi dữ liệu, giả thuyết và trạng thái không đổi; báo khi có kết quả,
pivot, lỗi ảnh hưởng công việc hoặc quyết định cần người dùng.

Vòng lặp:

1. Đọc ledger: dữ liệu nào mới, kỳ nào đã xem, family nào đã bị loại, blockers
   nào còn mở. Xác minh freshness, provenance và input/code hash.
2. Chọn **một** câu hỏi kinh tế có thể bác bỏ. Khóa manifest, baseline, kỳ đánh
   giá, ngân sách vốn/chi phí/rủi ro và tiêu chí quyết định trước khi xem outcome.
3. Thực hiện đúng manifest trong deadline. Không mở sweep mới vì kết quả xấu.
4. Ghi forecast, quyết định, lệnh bị từ chối, khớp/không khớp, quyền và sổ vốn.
   Báo cohort không có đủ outcome, không thay outcome thiếu bằng 0.
5. So với đối chứng cùng điều kiện; ghi kết quả/bằng chứng và **một bước kế
   tiếp cụ thể**. CLI hiện chỉ chạy chẩn đoán có giới hạn và loại kết quả
   thua; bước kinh tế/forward còn thiếu contract phải giữ `NEEDS_DATA`.
   Tách lỗi chạy khỏi kết quả kinh tế.
6. Nếu không có dữ liệu mới hoặc giả thuyết độc lập, chờ mốc tiếp theo đã ghi;
   không train lại cùng input chỉ vì heartbeat đến hạn.

**Tối đa hai lần thất bại cho cùng một economic family**, tính cả những lần
đã làm trước LAB. Family được nhận diện bằng cơ chế kỳ vọng lợi nhuận, tập
thông tin và hành động được định giá. Đổi tên LightGBM/Ridge, thêm head/agent,
đổi seed, threshold hay lịch refit trên cùng cơ chế không tạo family mới.
Sau lần thứ hai phải chuyển cơ chế kinh tế, dữ liệu, hành động hoặc universe
có luận cứ mới. Không phải lúc nào cũng cần dùng hết hai lần.

Một lần chạy hợp lệ bác bỏ giả thuyết hoặc không đạt gate đã khóa tính là
thất bại. Sửa lỗi hạ tầng/tái lập không tạo bằng chứng độc lập và không xóa
thất bại cũ. Mỗi lỗi chỉ có một kế hoạch sửa với deadline; lỗi lặp lại được
đưa thành blocker thay vì vòng retry vô hạn. Thiếu dữ liệu thiết yếu không
cho phép tiếp tục tăng độ phức tạp của model để thay thế thông tin đó.

## 2. Bắt đầu từ bằng chứng đã có

Đăng ký các hướng cũ là **rejected**, giữ artifact và kỳ đã xem. Không reset
failure budget bằng việc tạo LAB mới. Những số dưới đây mô tả các run trước,
không phải kết quả của LAB001.

| Hướng đã chạy | Bằng chứng nhập ledger | Quyết định |
|---|---|---|
| Profit heads OHLCV H3/H5 cố định | Annual và monthly đều không qua DEV gate. Annual 01–07/2026 NAV −0,1702%; monthly 2026 đã xem lại. Full refit H3 24 tháng: DEV mean trade âm cả ba gate; 01–07/2026 −2,8232%, 10 trade, PF 0,0934. | Cùng cơ chế đã thất bại nhiều lần; không tiếp tục thay lịch refit/threshold. |
| Breakout/pullback/rebound + arbiter + learned exit | DEV 406 trade, PF 0,6876; NAV 2023/2024/2025 lần lượt −5,61%/−4,59%/−4,68%. 2026 đã xem: −13,59%, 102 trade, PF 0,2221. | Rejected. Thêm nhiều luồng chưa tạo edge; phải chẩn đoán selection, calibration và giá trị hành động trước pivot. |
| Định giá EV trong cohort được chọn | DEV 1.050 forecast EV dương: dự báo +0,866%, quan sát −0,840%; cả bốn quartile EV dương đều có mean outcome âm. | Tăng ngưỡng trên cùng thang EV chưa có căn cứ. |
| Paper/replay PROD | Book có lệnh `REPLAY` và `SHADOW_PAPER`; không phải fills broker thật. Có trạng thái phòng thủ cash nhưng sổ vẫn còn holdings. | Chỉ là bằng chứng mô phỏng/vận hành; không nhập thành thành tích tiền thật hoặc hệ tự học sinh lời. |

Nguồn local được giữ trong các artifact bất biến:
[profit_v5/report.json](../../ai-engine/scratch/profit_research_2026-10-03_v5_refit_h3/report.json),
[broker_swing_v2/results.json](../../ai-engine/scratch/broker_swing_research_2026-10-03_v2/results.json)
và protocol, forecast, trades, NAV cùng thư mục. Nguồn code/review lịch sử
ở [PR #15 đã đóng, chưa merge](https://github.com/thanhtai040805/AI_Invest/pull/15);
LAB không phụ thuộc các tài liệu/model source cũ phải có trong main.
Snapshot local gồm 1.011.757 bar của 407 mã từ 2015 tới 01/10/2026, SHA256
`dd89de0d85fe6b454584a62ef66fe14bdb045b68144d41d58fd18d7dbb8973d1`.
Đã cách ly 2.884 row raw OHLC không hợp lệ; 35 phiên stock thiếu VNINDEX
không được tạo giá index. Lịch sử listing/delisting, publication/revision,
entitlements và executable quotes chưa được xác minh đủ. Đây là blockers
của bằng chứng lợi nhuận, không phải lý do bỏ các khoản lỗ khỏi sổ.
Các kết quả đẹp EXP cũ chỉ được nâng cấp thành evidence khi tái dựng được
giao dịch, chi phí và sổ NAV cùng provenance. Win rate, survival rate hoặc
annualize arithmetic không thay thế sổ vốn. Protocol đánh dấu dữ liệu đã
xem tới **02/10/2026**; không đổi phần này thành untouched holdout trong thử
nghiệm kế tiếp. Artifact scratch cần được bảo quản cùng hash: nó là đầu vào
local, không được giả định luôn tồn tại trên máy hoặc checkout khác.

Rà soát bản LOCAL database snapshot riêng ngày 04/10/2026 phát hiện thêm
3.249 bar OHLC-invalid ở 305 mã. Đã quarantine giá LOCAL, giữ preimage có
hash và thêm xác thực ở ba đường nạp; số 2.884 phía trên thuộc frozen CSV
được xuất trước đó, không phải cùng một tập đo.
Các trường volume continuous/ATO/ATC không có split provenance trong snapshot
này nên đã đặt NULL cùng ADTV dẫn xuất trên 1.186.633 dòng LOCAL;
volume_total được giữ.
Audit giá basis trên DB LOCAL cho thấy calculation view dựng open/high/low
“raw” từ giá điều chỉnh cùng dòng và không có publication/availability vintage;
giá trong view chưa phải lịch sử raw có thể khớp lệnh độc lập. Chi tiết và
hashes nằm trong [VN_NUMERIC_DATA_RESEARCH.md](VN_NUMERIC_DATA_RESEARCH.md)
và artifact `scratch/vn_swing_lab/repairs/PRICE_BASIS_AUDIT_LOCAL_20261004/`.

**LAB001** do [research_swing_lab_feasibility.py](../../ai-engine/scripts/research_swing_lab_feasibility.py)
thực hiện, chỉ dùng DEV 2023–2025, deadline **900 giây**. Tách gross movement,
cost drag, calibration và exit; so ledger cùng policy với chi phí bằng 0
để đo phần chi phí, không lấy nó làm chiến lược có thể giao dịch. Phân rã
regime, thanh khoản, volatility và độ lớn tick giả định; matched exits giữ
cùng mã/ngày/size. Nếu thiếu venue thì tick chỉ là HOSE proxy. Censored
outcomes, rights và execution gaps phải hiện trong báo cáo. LAB001 không
được chứng nhận VN30/non-VN30 hoặc một model có lợi nhuận.

## 3. Universe là biến thí nghiệm bắt buộc

VN30 chọn các cổ phiếu lớn và thanh khoản trong VNAllshare; tiêu chí chỉ số
không phải dự báo lợi nhuận ròng cho 3–5 phiên tới. Xem
[HOSE factsheet VN30 08/2026](https://staticfile.hsx.vn/Uploads/UploadDocuments/2487402/Form_Factsheet_MCIndices_VN_T08.2026.pdf)
và [HOSE Index Ground Rules 4.0](https://staticfile.hsx.vn/Uploads/LocalFiles/ef15ff11e799483abd11677ad0443887/20250114_20241230_QD%20747%20HOSE%20Index%20Ground%20Rules.pdf).
Không suy từ ba mã FPT/SHB/TCB rằng VN30 gây thua hoặc mã nhỏ tốt hơn.

Đăng ký ba universe trên **cùng ngày quyết định**:

| Universe | Membership hợp lệ |
|---|---|
| `VN30_PIT` | Thành viên VN30 thực có hiệu lực tại t, thông tin đã công bố trước quyết định; sau đó áp dụng điều kiện giao dịch/chất lượng/thanh khoản chung. |
| `LIQUID_NON_VN30_PIT` | Cổ phiếu thuộc sàn/phạm vi đã khóa, đủ điều kiện tại t nhưng không thuộc VN30 tại t; giữ cả lịch sử mã đã hủy niêm yết. |
| `LIQUID_UNION_PIT` | Hợp hai nhóm; model được chọn cơ hội theo cùng objective sau chi phí và được phép không mua. |

Membership cần `ticker`, venue, listing/delisting, suspension, ngày công bố,
`effective_from/effective_to` và nguồn. Thiếu lịch sử membership thì ghi
`VN30_ATTRIBUTION_UNAVAILABLE`; **không gán danh sách VN30 hiện tại ngược
về lịch sử**, không gom các row membership chưa biết vào non-VN30. Mỗi run
báo coverage và phần dữ liệu không đủ điều kiện, tránh survivorship bias.

Để đo tác động universe, khóa cùng signal/model policy, kỳ đánh giá, vốn 1 tỷ,
ngân sách vị thế/ngành, luật cash, phí/thuế, slippage, settlement và capacity.
Khai báo trước model được fit chung hay fit riêng theo nhánh; ba nhánh là
ba phép thử phải được tính trong ledger, không chọn winner bằng forward.
So sánh cả lãi ròng/NAV, tail, thời gian vốn bị khóa, turnover, fill rejection,
concentration và cơ hội đủ điều kiện. Ghép liquidity/volatility/sector/regime
để phân biệt lợi thế universe với việc chấp nhận rủi ro hoặc chi phí khác.

Funnel hiện có không thuần VN30: discovery có thể lấy HOSE, nhưng VN30 được
ưu tiên Group A và có ngoại lệ một số bộ lọc; orchestrator có thể cắt candidate
trước ML. Vì vậy cần đối chứng **toàn bộ tập đủ điều kiện → tập sau funnel →
lệnh được chọn → lệnh thực khớp**, ghi số và lý do loại ở mỗi bước. Không
đánh giá model trên một tập đã bị chọn sẵn rồi quy mọi khác biệt cho model.

## 4. Contract năm câu hỏi trước BUY

Một candidate phải có câu trả lời kiểm tra được cho cả năm câu. Nội dung
thuyết minh hoặc model confidence không đủ thay các trường dữ liệu sau.

| Câu hỏi | Contract và dữ liệu tối thiểu |
|---|---|
| **Sóng kỳ vọng từ đâu?** | Economic mechanism, setup/catalyst, nguồn và `available_at`; bằng chứng incremental sau market/sector context và cohort ngoài mẫu. Không suy nguyên nhân từ tên tin tức hay giá đã tăng. |
| **Giá vào còn hợp lệ?** | Giá/range entry, expiry, thời điểm forecast/quote, venue/tick/lot, gap/auction/limit conditions; revalidate net advantage tại giá có thể khớp, hủy nếu vượt điều kiện. |
| **Dư địa sau chi phí bao nhiêu?** | Mean net payoff, loss tail/uncertainty, horizon/decay, buy/sell/minimum fee, sell tax, spread/slippage/impact và size; calibration trên cohort được chọn và khớp. |
| **Khi nào luận cứ hết hiệu lực?** | Điều kiện invalidation quan sát được, thời gian hết hiệu lực, hành động HOLD/EXIT/CANCEL và baseline; lợi ích exit bao gồm vốn/slot tái sử dụng, không chỉ return riêng lệnh. |
| **Sau settlement thoát được khối lượng nào?** | `sellable_quantity`, cổ phiếu chờ về, lịch nghỉ, quyền, quote/depth/queue/partial fills và order status từ broker; kịch bản không bán được, receivable và tiền khả dụng. |

Portfolio overlay so BUY với **giữ cash và cơ hội khác**; cấp size từ net edge
đã calibration, uncertainty, concentration ngành và thanh khoản. Thiếu câu
trả lời bắt buộc thì không có lệnh BUY. Không dùng thứ hạng tương đối để bắt
buộc mua một mã khi mọi candidate có incremental value âm/chưa xác minh.

VSDC áp dụng thanh toán cổ phiếu T+2; SSI mô tả chứng khoán mua về sau 13h
T+2. Khả năng bán thực dựa trên trạng thái broker, không chỉ phép cộng ngày.
Hard stop không bảo đảm thoát khi cổ phiếu chưa khả dụng, mất thanh khoản
hoặc nằm sàn. Luật HOSE/lot/tick phải theo venue và ngày áp dụng.
[VSDC](https://vsdc.vn/vi/sd/XAz40d2Q-9j569TvBgLQaQ),
[SSI settlement FAQ](https://www.ssi.com.vn/khach-hang-ca-nhan/co-phieu-faq?tab=customer),
[SSI trading rules](https://www.ssi.com.vn/khach-hang-ca-nhan/quy-dinh-giao-dich/quy-dinh-giao-dich-co-phieu).

**SAG_CLOSED** vẫn là ranh giới bắt buộc: không kết nối SAG, đọc outputs,
assessment, OCR/GIL hoặc dùng kết quả SAG cũ để lấp contract. Workflow phụ
thuộc SAG phải ghi `SAG_CLOSED`; không đổi thành `DATA_INSUFFICIENT`, tín hiệu
âm hay xác nhận không có dòng tiền bất thường. Hướng flow độc lập chỉ dùng
nguồn được phép và provenance riêng.

## 5. Pivot phải đổi nguồn kỳ vọng lợi nhuận

Các hướng dưới là giả thuyết ưu tiên, **chưa phải edge đã được chứng minh tại
Việt Nam**. Daily OHLCV hiện chỉ đủ proxy price/volume: chưa có dữ liệu depth,
imbalance, forced sale hoặc foreign flow đủ lịch sử để nhận diện chính các
cơ chế đó. Muốn thử phiên bản dùng thông tin ấy phải bổ sung nguồn hợp lệ;
không coi cột flow toàn 0 là quan sát dòng tiền thực. Chỉ mở hướng khi có dữ
liệu cho câu hỏi cần đo. Không đồng thời
dựng mọi hướng hoặc một hệ nhiều agent để thay thế bằng chứng kinh tế.

| Hướng | Cơ chế và phép thử phân biệt |
|---|---|
| **Flow continuation sau settlement** | Kiểm dòng tiền ngoại/thanh khoản/FX cùng catalyst công bố có tạo persistence đủ dài qua T+2 và costs không. Đối chứng price-volume-only, flow bị lag đúng công bố và cohort event/non-event. FX là context cho cổ phiếu Việt Nam và exposure doanh nghiệp; không tự chuyển sang giao dịch FX. |
| **Liquidity-shock reversal** | Phân biệt bán ép/liquidity shock tạm thời với thông tin xấu tiếp diễn bằng spread/depth/imbalance và recovery. Đo khả năng entry/exit qua quote, partial fill và thời gian khóa; không gọi daily volume là order-book imbalance. |
| **Residual relative strength theo cross-section** | Loại phần market/sector exposure bằng fit quá khứ rồi kiểm phần mạnh riêng của mã giữa ba universe. So với raw momentum, cash và baseline cùng risk; paper residual momentum không xác nhận kỳ hạn 3–5 phiên HOSE. |
| **Meta-label và entry revalidation** | Giữ primary setup đã khóa; học BUY/ABSTAIN/size hoặc hủy khi giá vào làm mất net edge. Labels phải là outcome của policy có thể thực thi và forecast primary ngoài mẫu; đo incremental value so primary-only, không học lại trên in-sample signals. |
| **Incremental portfolio action value** | Định giá BUY so cash, EXIT + vốn tái dùng so HOLD; kiểm slot/settlement/sector/risk. Đối chứng static exit và risk+cash-only để biết cải thiện đến từ thông tin chọn mã hay đơn thuần giảm exposure/turnover. |

Nguồn nghiên cứu primary cho các cơ chế: [BIS WP1154](https://www.bis.org/publ/work1154.htm)
nghiên cứu foreign flow/FX/equity tại Thái Lan;
[Cont–Kukanov–Stoikov](https://arxiv.org/abs/1011.6402) đo order-flow imbalance
và market depth trên cổ phiếu Mỹ;
[Blitz–Huij–Martens](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2319861)
nghiên cứu residual momentum;
[Hudson & Thames meta-labeling study](https://hudsonthames.org/wp-content/uploads/2022/04/Does-Meta-Labeling-Add-to-Signal-Efficacy.pdf)
thử lớp chọn hành động trên primary signal;
[Gârleanu–Pedersen](https://www.nber.org/papers/w15205) xét trading với costs
và tín hiệu decay. Áp dụng vào LAB là suy luận cần bác bỏ, không sao chép
thành tích hoặc khẳng định causality từ thị trường khác.

## 6. Gate lợi nhuận và chuyển sang broker

**Các gate dưới đây là mục tiêu screening, chưa phải khả năng promotion
được CLI hiện tại chứng nhận.** CLI có thể chạy bounded diagnostics và
reject các khoản lỗ đã quan sát; economic advancement đang khóa ở
`NEEDS_DATA`. PnL dương hoặc đủ số mẫu trong CSV không tự mở khóa bước này.

Trước `forward_candidate`, bắt buộc có **validated evidence adapter** kiểm
chứng contract từ nguồn tới quyết định/sổ vốn:

- Session calendar/venue và thời điểm settlement, gồm ngày nghỉ và trạng
  thái giao dịch; không coi index placeholder là phiên có giá hợp lệ.
- Biểu phí/cost/fill policy theo account/venue/date/size, cùng stress và
  reconciliation; daily open/close chưa chứng minh giá có thể khớp.
- Decision/model/data timestamps, publication/revision và cutoffs; chứng
  minh forecast được ghi trước outcome, không chỉ thêm nhãn `forward`.
- PIT universe/listing/delisting/membership với publication/effective dates
  và coverage; không dùng cờ `pit_verified=true` tự khai báo làm bằng chứng.
- Corporate entitlements và raw/adjusted price/shares/cash ledger, gồm các
  sự kiện và thời điểm quyền thực nhận.

Adapter phải xác minh provenance, schema, hash và tính nhất quán của các
contract trên, xuất evidence có thể kiểm tra lại và chặn khi thiếu/sai.
Manifest booleans, lời cam kết hoặc CSV do run tự tạo không thay adapter
đó. Chưa có adapter hợp lệ thì giữ `NEEDS_DATA`, kể cả metric screening
đẹp; chưa có bằng chứng broker thật thì không thể `PROFIT_CONFIRMED`.

Protocol hiện ghi mục tiêu screening nghiên cứu: ít nhất 150 closed trades DEV và
50 evaluation, PF ít nhất 1,20, expectancy net ít nhất 0,20%, drawdown tối
đa 10%; PnL đã đóng, NAV và NAV stress dương mỗi kỳ yêu cầu. Cuối kỳ phải
không còn open position, unsettled receivable hoặc stale mark. Win rate chỉ
là metric mô tả. Các con số này là gate sàng lọc, không tự chứng minh đủ
mẫu độc lập và không phải ngân sách lỗ được duyệt cho broker thật.

Mỗi lần đăng ký phải khóa điều kiện `advance`/`reject`: số outcome đã đóng,
độ dài forward, regime coverage, uncertainty, drawdown/loss tail, concentration,
cost stress, calibration và capacity. Ghi số giao dịch hữu hiệu và dependence
theo ngày/tuần; nhiều lệnh cùng một cú sốc không phải nhiều mẫu độc lập. Dùng
time blocks/purge theo label end; giữ phần forward **chưa xem** và bắt đầu
đánh giá từ sau lúc khóa policy/data contract. Quá ít outcome là inconclusive,
không hạ gate vì muốn thấy pass. Calibration phải đo tại vùng hành động được
chọn/khớp và size dự kiến, không chỉ mean probability trên toàn universe.
Nếu muốn thay screening, phải đăng ký phiên bản protocol mới trước kỳ
outcome chưa xem, giữ kết quả theo gate cũ; không sửa gate sau khi bị loại.

Ba mức bằng chứng phải giữ riêng:

1. **Research:** net realized PnL và marked NAV trên ledger có cash/receivables,
   phí/thuế/quyền, capital constraints và fill policy. Lãi ròng tổng thể,
   performance theo regime, interval theo block, stress và cash/risk baselines
   phải đủ cho gate đã khóa. Lãi không chỉ đến từ một mã/ngành/năm hoặc lấy
   thêm tail risk. Risk-off được phép giữ cash; không ép trade để đủ sample.
2. **Shadow forward:** policy frozen, quyết định ghi trước outcome, dữ liệu
   fresh, entry revalidation, quote/fill rejection, settlement và broker-state
   reconciliation được theo dõi. Shadow cần kiểm cả những lệnh không khớp.
   Paper positive vẫn chưa xác nhận khả năng thực thi tiền thật.
3. **Broker thật:** chỉ xét sau hồ sơ hai mức trước và contract rủi ro/cấp vốn
   cụ thể được người dùng chốt. Bắt đầu với size có calibration và capacity
   phù hợp ngân sách thật; theo dõi lệnh/fills/fees/cash/rights từ broker,
   circuit breakers và reconciliation. Không suy NAV thật từ ledger paper.

Chi phí thực phải gồm phí từng chiều/phí tối thiểu, thuế bán, spread,
slippage/impact, phí ứng tiền/lãi margin nếu có. Thuế chuyển nhượng chứng
khoán cá nhân hiện là 0,1% giá chuyển nhượng mỗi lần theo
[Chính phủ, Nghị định 253/2026/NĐ-CP](https://xaydungchinhsach.chinhphu.vn/thue-thu-nhap-ca-nhan-doi-voi-thu-nhap-tu-chuyen-nhuong-chung-khoan-119260703164200344.htm).
Phí broker phải lấy đúng biểu phí tài khoản, không xem 0,1% trong simulator
là biểu phí phổ quát. Ước lượng cost từ fills thật theo size/liquidity/regime,
không mang hệ số thị trường khác sang; cơ sở phương pháp:
[Frazzini–Israel–Moskowitz, Trading Costs](https://www.aqr.com/Insights/Research/Working-Paper/Trading-Costs).

Bảng cuối mỗi kỳ phải có:

- **Net realized PnL** toàn bộ giao dịch đã đóng và phần tiền bán đã thanh
  toán/đang chờ; realized tại exit và cash đã về là hai mốc khác nhau.
- **Total marked NAV** gồm cash, receivables, holdings và entitlements; điều
  chỉnh nạp/rút vốn; báo tuổi mark và khoản chưa định giá được. Không chỉ
  chốt lệnh thắng trong khi để lỗ lớn chưa đóng ngoài báo cáo.
- Lỗ/rủi ro, market/sector exposure, size/capacity, cost forecast so fills,
  thời gian vốn bị khóa và độ mới dữ liệu; đối chiếu balances với broker.
- Thời gian tới tổng lãi đã chốt dương, kết quả sau mốc đó và cuối kỳ. Một
  trade thắng hoặc NAV dương thoáng qua không hoàn thành mục tiêu.

Mục tiêu chỉ được xem là đạt khi bằng chứng broker thật cho thấy lợi nhuận
ròng đã chốt và NAV tổng phù hợp, có đủ outcome/forward/regime coverage theo
contract đã khóa, trong ngân sách rủi ro đã duyệt và không còn lỗi đối soát
hay dữ liệu trọng yếu. Đó là kết luận về kỳ quan sát và size đã kiểm chứng,
không phải bảo đảm lợi nhuận tương lai. Nếu chưa đủ dữ liệu/thời gian/cấp vốn,
goal vẫn mở với bước tiếp theo cụ thể; không đóng goal để báo LAB đã xong.

Quy tắc hai thất bại, trial ledger và forward untouched là lựa chọn vận hành
của dự án nhằm hạn chế search vô tận. Nghiên cứu
[Bailey và cộng sự về backtest overfitting](https://sdm.lbl.gov/oapapers/ssrn-id2507040-bailey.pdf)
giải thích rủi ro tối ưu trên lịch sử; không chứng minh rằng giới hạn hai
lần thử tự tạo ra lợi nhuận.
