# Agent-01: đối chứng full Replay sau sửa bug LIVE

## Kết luận

Hoàn tất LOCAL Replay `--reset`, 28 phiên 12/08–23/09/2026, vốn 1 tỷ,
tối đa 3 suất lệnh, full HOSE universe và xử lý tuần tự. Bản sửa Agent-01
không làm thay đổi NAV/cash ở bất kỳ phiên nào so với baseline LOCAL đã lưu.
Toàn bộ 116 receipt kinh tế cũng giống LOCAL, không chỉ NAV cuối kỳ.

**Xác nhận không ghi nhận ảnh hưởng tiêu cực của bản sửa Agent-01 tới NAV,
cash, receipt kinh tế và phí/thuế trong 28 phiên đối chứng này.** Đây không
phải cam kết về mọi quyết định LLM hay lợi nhuận LIVE tương lai.

| Chỉ tiêu | Run mới | Baseline LOCAL | PROD lịch sử cùng cửa sổ |
|---|---:|---:|---:|
| NAV cuối | 1.005.857.685đ | 1.005.857.685đ | 1.004.416.888đ |
| Cash cuối | 974.362.685đ | 974.362.685đ | 984.121.888đ |
| Receipt BUY / SELL | 56 / 60 | 56 / 60 | 52 / 62 |
| Phí / thuế | 1.418.815đ | 1.418.815đ | 1.378.112đ |
| Chênh NAV mới | — | **0đ** | **+1.440.797đ** |

Chênh với PROD đã có trong baseline LOCAL trước sửa Agent-01. Run này tái hiện
đúng chênh đó; không tạo thêm lợi nhuận hay tổn thất so với LOCAL.
Chữ PROD ở đây chỉ lịch sử đối chứng đã lưu, không phải một run LIVE độc lập mới.

## Điều kiện và an toàn run

- Branch `codex/factor-readonly-replay-review`, nền PR #28 `e61f3dd`, cộng patch
  bug Agent-01 đang review. Không đổi runtime/config/policy trong khi run.
- Dùng DeepInfra `deepseek-ai/DeepSeek-V4.1-Flash-1017`, gọi LLM thật, không phát
  lại response cũ để ép kết quả giống baseline.
- Dùng lại snapshot version 1 của baseline LOCAL, SHA256
  `730aa18a67bf4169ffcc1ff89cdfffe09a5be9680957f30758cc84d3ec6de581`.
- Model artifact sao chép từ workspace baseline; hash runtime/model được kiểm.
  Các thay đổi bootstrap/LIVE có sẵn khi port PR không đổi đường full-universe
  historical của run không truyền `--symbols` này.
- Kiểm DSN đích là localhost/aiinvest; backup tài khoản và các bảng audit/
  phụ thuộc bị cleanup trước reset, đọc kiểm từng backup gzip. PROD chỉ là
  dữ liệu đối chiếu; không reset/deploy hoặc ghi dữ liệu VPS trong lượt này.
- `MULTI_AGENT_MODE` và `STANDALONE_ML_MODE` của process Replay là DISABLED;
  fill được historical runner ghi vào paper account. Automation tắt và SAG hold bật.
- Attempt đầu dừng trước phiên chốt đầu tiên do launcher worktree thiếu `.env`
  riêng của LOCAL ai-engine, DNSE trả 401. Đã sửa loader cấu hình, kiểm cùng API
  PNJ trả 200, giữ nguyên attempt đầu rồi chạy attempt 2 với reset mới. Attempt
  đầu có 6 call LLM, tách riêng khỏi số liệu run hoàn tất.
- Run hợp lệ hoàn tất lúc 20:48:12 ngày 25/10/2026 HCM; thời lượng 56m52s.

## Những khác biệt với LOCAL đã báo trong khi chạy

### 1. LLM đổi nội dung và fatal gate nhưng chưa đổi receipt

Các prompt Thesis của bốn trường hợp dưới đây có cùng hash với LOCAL.
Response Thesis khác nội dung, dẫn tới prompt Counter khác; Counter thay đổi
fatal flag, rồi CIO đổi gate. Đây là chuỗi quan sát được, không phải phép A/B
cô lập riêng tác động ngôn từ ở Counter khỏi tính bất định của chính LLM.

| Ngày / mã | Counter và CIO so với LOCAL | Điểm dừng của domino trong run |
|---|---|---|
| 19/08 HVN | BLOCK → CONDITIONAL; fatal true → false; CIO cap 0 → 8% | Allocation tạo mục tiêu 1,13%, nhưng không khớp BUY ngày đó. Không đổi NAV. |
| 21/08 HVN | BLOCK → CONDITIONAL; cap 0 → 8% | Vị thế từ 20/08 đã có; không tạo receipt kinh tế mới khác baseline. |
| 21/08 NAB | CONDITIONAL → BLOCK; fatal false → true; cap 8% → 0 | LOCAL vốn phân bổ thêm 0 vì lệch 0,12% trong deadband 2%. Chặn sớm không mất lệnh mua thực thi. |
| 26/08 IJC | BLOCK → CONDITIONAL; fatal true → false; cap 0 → 8% | Allocation vẫn thêm 0 do lệch 0,08% trong deadband. Các SELL bảo vệ vị thế vẫn giống LOCAL. |

Ngày 19/08, HVN được duyệt đã sử dụng một suất trong ba suất lệnh, khiến run
dừng Research sớm hơn LOCAL. Có 5 dòng Research ít hơn: EVF, IJC, NKG, SBT,
VGC. Cả 5 dòng baseline đều CSS < 60 và conviction dưới B, không đủ vào Thesis.
Vì vậy có khác đường xử lý, nhưng không mất một BUY khả thi trong lần đối chứng.

Run mới có 1.984 dòng Research chung với LOCAL; F1–F6, CSS, conviction và toàn
bộ Research gate của các dòng chung đều giống nhau. LOCAL có 1.989 dòng.
Không có mã/ngày Research mới ngoài tập LOCAL.

Một ví dụ khác từ đầu run: so với PROD, HPG ngày 12/08 có CTS 24,3 → 33,3 và
CIO cap 15% → 8%, nhưng mục tiêu Kelly chỉ 1,31%, executable target 1,11%.
Hai cap đều không ràng buộc; risk giảm size rồi lượng khớp vẫn như nhau.

### 2. Query ATC không có tie-break làm log Agent-01 khác

`market_surveillance.py` có query `ORDER BY volume_atc DESC LIMIT 50`, không
có khóa phụ. Ngày EOD 17/08 có 213 mã đủ điều kiện cùng volume_atc bằng 0.
Tập 50 mã hợp lệ vì thế không được bảo đảm ổn định giữa các lượt query/run.

Số ATC warning LOCAL → mới:

| Ngày chạy | LOCAL | Mới | Tác động context |
|---|---:|---:|---|
| 12/08 | 6 | 7 | Không đổi |
| 13/08 | 4 | 7 | Không đổi |
| 14/08 | 5 | 6 | Không đổi |
| 17/08 | 8 | 5 | Không đổi |
| 18/08 | 2 | 3 | Normal → Stress; INFO → WARNING |
| 20/08 | 3 | 6 | Không đổi |
| 24/08 | 19 | 15 | Không đổi |
| 26/08 | 2 | 5 | Normal → Stress; INFO → WARNING |

HMM probabilities và regime vẫn giống LOCAL ở đủ 28 ngày. Riêng 18/08 là
BEAR nên mua mới đã bị khóa; 26/08 vẫn RANGE_BOUND và các thay đổi quan sát
không truyền thành receipt/NAV khác. Query này không được sửa giữa run.

Để kiểm nhân quả, gọi riêng code Agent-01 cũ và mới với cùng một hydration
EOD cố định cho từng ngày: **28/28 data + trace giống hoàn toàn**. Các phép
gọi kiểm chỉ đọc DB, mock persistence, không LLM và không thêm reset.
Điều này tách ảnh hưởng patch giờ/feed khỏi sự bất định lấy top 50 khi đồng hạng.

### 3. Chất lượng response LLM khác nhau

Run hợp lệ có 130 call thành công, đúng model; 0 retry/HTTP/transport/JSON error,
0 traceback, 0 Agent error. Có 5 response finish_reason=length, so với 11 của
LOCAL. HPG 13/08 dùng baseline template do thiếu invalidation hợp lệ; LOCAL
cũ ghi fallback VND 24/08. Không gọi là mọi response đều đầy đủ.

Các khác biệt response/gate được giữ trong trace; không kết luận chúng vô hại
cho mọi tương lai chỉ vì receipt của lần này trùng nhau.

## Domino giải thích chênh +1.440.797đ với PROD

### CTG → mẫu học → Kelly PVT

24/08 là lookup as-of Chủ nhật 23/08, thiếu exact-date cache nên dùng generic
fallback. Mapping tài chính đã có trong baseline LOCAL làm D/E CTG
0,9355 → 14,4971; F2 85,41 → 15 và CSS 72,52/B → 58,44/C.
Run mới/LOCAL không mua CTG 300@32.100 như PROD, không có hai mẫu lỗ tương ứng.

07/09: PVT vẫn cùng factor/CSS nhưng calibration mới/LOCAL p/R 0,5556/1,63,
PROD 0,5357/1,53. Incremental allocation 3,22% thay vì 2,62%, BUY 1.600 thay
vì 1.300 cổ phiếu @20.150. Ngay cuối ngày, phần PVT lớn hơn đóng góp -70.000đ,
CTG +339.340đ, tổng NAV chênh +269.340đ. Đây không phải lúc nào cũng có lợi ngay.

11/09 tiếp tục có feedback từ lịch sử lệnh đã đóng: BUY PVT 1.400 thay vì
900 cổ phiếu @22.400. Các partial exit/fee/mark sau đó khác. Đóng góp cuối kỳ:
CTG +339.340đ; PVT +790.090đ. Hai mẫu CTG → calibration đã có kiểm chứng offline
trong fixture causal controls của PR; đoạn feedback sau đó có thêm nhiều phụ thuộc.

### HAG → gate ba tín hiệu → suất lệnh → PVP

18/09: cache snapshot LOCAL/new có HAG F3=62,12 thay vì PROD 41,96. Đây là
đầu vào baseline được giữ nguyên, không phải rebuild factor hoặc sửa CSS trong run.
Tín hiệu độc lập mở từ 2/3 lên 3/3. New/LOCAL mua HAG 800@13.950; PROD không mua.
HAG dùng một suất lệnh; lịch mua PVP vì thế khác.

PROD mua PVP 600@18.250 ngày 18/09. New/LOCAL mở PVP ngày 21/09 với 1.400@18.100;
PROD chỉ mua thêm 700 cùng ngày, sau lô 600 trước đó. Không diễn giải 1.400 vs
700 như tỷ lệ của tổng vị thế: tổng cổ phiếu lúc đó còn phụ thuộc holdings/exits.

| Mã | Chênh đóng góp cuối 23/09 sau phí và mark |
|---|---:|
| CTG | +339.340đ |
| PVT | +790.090đ |
| HAG | +28.840đ |
| PVP | +282.527đ |
| Tổng | **+1.440.797đ** |

Đây là phân rã kế toán, không phải bốn tác động nhân quả độc lập. HAG còn
giữ 800 cổ phiếu cuối kỳ nên phần đóng góp bao gồm mark-to-market chưa thực hiện.
Tất cả chênh trên đã có ở LOCAL trước sửa Agent-01 và được tái hiện đúng.

## Validation và bằng chứng

- Cash từ receipt và NAV từ cash + số cổ phiếu × mark: đủ 28 phiên, sai số 0đ.
- 116 economic receipts giống LOCAL: ticker/side/quantity/price/time/fee/tax/cash_delta.
  UUID và metadata diễn giải không được gọi là receipt byte-identical.
- 4.008 dòng factor nguồn giữ hash
  `162754b25172d4b7a0f3aa09fb9c08b5abdf283ad909aa8b5cef8a5b171dd7df`.
- 237 file runtime/launcher/profile giữ hash; RL weights không đổi. 90 dòng
  paper learning: unmatched entry 0, unmatched exit 0 theo timestamp HCM.
- Trước run: 155 tests PASS, 2 seeded-DB cases deselected, Ruff và diff check PASS.
- Không thay code để ép NAV; các lỗi/difference đã báo trong commentary khi quan sát.

Bằng chứng máy đọc nằm dưới thư mục ignored
`ai-engine/scratch/output/agent01_replay_28_20261025_attempt2/`:
`run_manifest.json`, `final_validation.json`, `comparison_analysis_latest.json`,
`snapshot_latest.json`, `alerts.jsonl`, `agent_decisions.jsonl`,
`Agent01_all28_old_new_same_input_control.json`,
`ATC_tie_control_20260818.json`, `first_gate_prompt_controls.json`,
`PVT_20260907_calibration_domino.json`, `HAG_PVP_20260918_order_slot_domino.json`
và trace HTTP/LLM. Backup/raw prompts không đưa vào Git.

Code và báo cáo được gộp chung trong PR #28 để review; chưa merge/deploy.
Kết quả xác nhận hồi quy NAV cho cửa sổ/đầu vào này;
không chứng minh toàn hệ thống deterministic hay lợi nhuận LIVE tương lai.

## Đối chiếu NAV từng phiên

Cash từng phiên cũng có delta LOCAL bằng 0; bảng dưới giữ phần NAV để dễ đọc.

| Ngày | NAV mới (đ) | Δ LOCAL (đ) | Δ PROD (đ) |
|---|---:|---:|---:|
| 2026-08-12 | 999.950.000 | 0 | 0 |
| 2026-08-13 | 999.805.000 | 0 | 0 |
| 2026-08-14 | 999.865.680 | 0 | 0 |
| 2026-08-17 | 999.800.680 | 0 | 0 |
| 2026-08-18 | 999.630.680 | 0 | 0 |
| 2026-08-19 | 999.624.130 | 0 | 0 |
| 2026-08-20 | 999.624.130 | 0 | 0 |
| 2026-08-21 | 1.000.345.318 | 0 | 0 |
| 2026-08-24 | 1.000.671.900,5 | 0 | 100.000 |
| 2026-08-25 | 1.000.052.821,5 | 0 | 190.000 |
| 2026-08-26 | 1.000.405.396,5 | 0 | 236.245 |
| 2026-08-27 | 1.000.311.396,5 | 0 | 231.245 |
| 2026-08-28 | 1.000.318.763,5 | 0 | 226.245 |
| 2026-09-03 | 1.000.129.036,5 | 0 | 271.245 |
| 2026-09-04 | 1.000.056.896,5 | 0 | 281.245 |
| 2026-09-07 | 999.519.768,5 | 0 | 269.340 |
| 2026-09-08 | 1.001.844.768,5 | 0 | 674.340 |
| 2026-09-09 | 1.001.439.668,5 | 0 | 617.980 |
| 2026-09-10 | 1.001.565.898,5 | 0 | 710.775 |
| 2026-09-11 | 1.000.903.838,5 | 0 | 510.775 |
| 2026-09-14 | 1.001.731.748,5 | 0 | 810.775 |
| 2026-09-15 | 1.002.151.748,5 | 0 | 860.775 |
| 2026-09-16 | 1.002.809.388,5 | 0 | 1.129.430 |
| 2026-09-17 | 1.003.197.068,5 | 0 | 1.129.430 |
| 2026-09-18 | 1.002.884.298,5 | 0 | 1.318.270 |
| 2026-09-21 | 1.003.001.068 | 0 | 1.203.270 |
| 2026-09-22 | 1.005.490.413 | 0 | 1.408.270 |
| 2026-09-23 | 1.005.857.685 | 0 | 1.440.797 |
