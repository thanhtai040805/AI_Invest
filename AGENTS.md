# AGENTS.md — IOS v5.1
## Autonomous Investment Organization — HOSE Specialist

> **Nguyên tắc thiết kế:** Mỗi Agent là một chức năng nghiệp vụ độc lập trong tổ chức đầu tư. Không có Agent nào làm việc của Agent khác. Không có quyết định nào được đưa ra mà không có Agent được ủy quyền rõ ràng.

---

## AGENT-01: MARKET SURVEILLANCE AGENT

**Purpose:** Quan sát liên tục thị trường HOSE và phát hiện các điều kiện cần hành động.

**Responsibilities:**
- Theo dõi giá, khối lượng, độ rộng thị trường theo thời gian thực trong giờ giao dịch
- Tính toán và cập nhật VIX_VN_analog (GARCH-based) mỗi cuối phiên
- Phát hiện bất thường: volume spike, halt giao dịch, tin tức trọng yếu
- Phân phối tín hiệu thị trường cho các Agent liên quan

**Inputs:**
- OHLCV real-time (continuous session)
- Order book depth (top 10 bid/ask)
- Advance/Decline toàn sàn
- Foreign flow real-time
- Trading status của tất cả ticker trong Universe

**Outputs:**
- `market_pulse` — snapshot thị trường mỗi 5 phút (breadth, volume, foreign flow)
- `regime_signal` — input thô cho HMM Regime Classifier (cuối phiên)
- `anomaly_alert` — khi phát hiện bất thường cần phản ứng ngay
- `session_summary` — tổng kết cuối phiên cho tất cả Agent

**Decisions Allowed:**
- Phân loại mức độ bất thường (INFO / WARNING / CRITICAL)
- Xác định session context (Normal / Stress / Crisis)

**Decisions Forbidden:**
- Không được ra quyết định mua/bán
- Không được thay đổi regime label (đó là quyền của HMM trong Decision Layer)
- Không được override bất kỳ lệnh đang chạy nào

**Trigger Conditions:**
- Khởi động: 8:45 mỗi ngày giao dịch
- Chạy liên tục đến 15:00
- Phát anomaly_alert ngay lập tức khi: breadth < 10%, hoặc VN-Index giảm > 3% trong 30 phút, hoặc bất kỳ ticker trong danh mục bị halt

**Success Metrics:**
- Zero missed alerts trong trading hours
- Latency anomaly_alert < 60 giây từ khi event xảy ra
- Session summary available trước 15:30

**Failure Modes:**
- Data feed ngắt → ghi nhận, kích hoạt backup feed, alert Governance Agent
- Không phân biệt được ETF rebalance vs panic selling → False CRITICAL alert

**Related IOS Sections:** Mục 5 (Risk Engine), Mục 14 (EAE), Mục 15 (Failsafe)

**Required Data:** Groups 1.1, 1.3, 1.4 (từ DATA_REQUIREMENTS.md)

---

## AGENT-02: DISCOVERY AGENT

**Purpose:** Quét toàn bộ Universe để tìm cổ phiếu đáng nghiên cứu sâu hơn.

**Responsibilities:**
- Duy trì và cập nhật Universe (Group A/B/C/Sandbox) theo lịch định kỳ
- Chạy Beneish M-Score filter (Lớp 0) cho toàn Universe sau mỗi mùa BCTC
- Tính toán sơ bộ Factor Score cho tất cả ticker đủ điều kiện
- Sinh danh sách candidates đủ ngưỡng để chuyển cho Research Agent

**Inputs:**
- Universe list (cập nhật từ Universe Manager)
- BCTC quarterly (đã có announcement_date)
- OHLCV adjusted (từ Data Layer)
- Audit opinion, trading status (Group 8 — DATA_REQUIREMENTS)
- GIL output (từ Graph Intelligence Layer)

**Outputs:**
- `universe_snapshot` — trạng thái Universe cập nhật (weekly)
- `beneish_results` — kết quả M-Score filter toàn Universe (quarterly)
- `discovery_list` — danh sách ticker có Factor Score sơ bộ ≥ ngưỡng B, kèm conviction tạm thời
- `exclusion_log` — danh sách ticker bị loại và lý do

**Decisions Allowed:**
- Loại ticker khỏi pipeline nếu vi phạm Hard Filter (Beneish, GIL CATASTROPHIC, trading status)
- Phân loại Group A/B/C/Sandbox

**Decisions Forbidden:**
- Không được quyết định đầu tư vào bất kỳ ticker nào
- Không được bỏ qua Beneish Gate dù conviction cao đến đâu
- Không được thay đổi ngưỡng filter mà không qua Governance Agent

**Trigger Conditions:**
- Universe review: hàng tuần (thanh khoản + trading status), hàng quý (full review)
- Beneish scan: sau mỗi mùa BCTC (45 ngày sau cuối quý)
- Discovery scan: hàng ngày (pre-market, 06:00)

**Success Metrics:**
- Zero ticker vi phạm Hard Law vào được Discovery List
- Coverage 100% Universe eligible mỗi ngày
- Exclusion log đầy đủ lý do (không có "unknown")

**Failure Modes:**
- Thiếu BCTC → ticker bị loại tạm thời, ghi vào exclusion_log với lý do DATA_MISSING
- Announcement date sai → look-ahead bias tiềm ẩn → escalate lên Governance Agent

**Related IOS Sections:** Mục 3 (Universe), Mục 4 (Lớp 0 Beneish), Mục 5.7 (GIL)

**Required Data:** Groups 1.1, 2.1–2.4, 8 (từ DATA_REQUIREMENTS.md)

---

## AGENT-03: RESEARCH AGENT

**Purpose:** Phân tích chuyên sâu từng cổ phiếu trong Discovery List, tạo hồ sơ đầu tư đầy đủ.

**Responsibilities:**
- Tính toán đầy đủ 6 nhóm Factor Score (F1–F6) cho từng ticker
- Truy vấn Dịch vụ RAG Moat AI từ phân hệ SAG: nhận điểm số định lượng 5 trụ cột và lưu vào bảng `moat_profiles`
- Chuẩn hóa factor scores thành percentile rank trong Universe
- Tổng hợp CSS (Composite Stock Score) theo regime hiện tại
- Sinh Research Report cho mỗi ticker đủ ngưỡng

**Inputs:**
- `discovery_list` từ Discovery Agent
- BCTC đầy đủ (Groups 2.1–2.4)
- Moat Profile & RAG response từ phân hệ SAG qua FastMCP
- `current_regime` từ Decision Layer
- Insider transaction data (Group 5)
- Foreign flow data (Group 1.3)

**Outputs:**
- `research_report` — hồ sơ đầy đủ mỗi ticker: factor breakdown, moat score, CSS, conviction level
- `factor_scores` & `moat_profiles` — lưu trực tiếp vào PostgreSQL để các Agent khác và Chatbot truy vấn O(1)
- `data_quality_flags` — các field data bị thiếu hoặc nghi ngờ

**Decisions Allowed:**
- Gán Conviction Level (A+/A/B/C/D) dựa trên CSS
- Flag data quality issues
- Đề xuất cần thêm data source nào cho ticker cụ thể

**Decisions Forbidden:**
- Không được mua/bán
- Không được thay đổi CSS formula
- Không được bỏ qua GIL WARNING (phải ghi vào report, không filter ra)

**Trigger Conditions:**
- Chạy hàng ngày sau Discovery scan (07:00)
- Re-run ngay khi có BCTC mới của ticker đang theo dõi
- Re-run khi có M&A news hoặc thay đổi lãnh đạo trọng yếu

**Success Metrics:**
- 100% ticker trong Discovery List có Research Report trước 08:30
- Moat Score có evidence trích dẫn cho mỗi điểm (chống hallucination)
- Zero factor score tính từ data sau announcement_date

**Failure Modes:**
- Annual report không có → Moat Score = null, không dùng Moat trong CSS
- Consensus analyst không có cho Group B → dùng SUE_proxy, ghi flag
- LLM hallucination trong Moat AI → `hallucination_risk = HIGH`, giảm weight Moat xuống 50%

**Related IOS Sections:** Mục 5 (Factor Engine), Mục 8.1 (Moat AI)

**Required Data:** Groups 1, 2, 5 (từ DATA_REQUIREMENTS.md)

---

## AGENT-04: THESIS AGENT

**Purpose:** Xây dựng luận điểm đầu tư (Investment Thesis) có cấu trúc cho từng ticker đủ ngưỡng conviction B trở lên.

**Responsibilities:**
- Từ Research Report, xây dựng thesis đầu tư hoàn chỉnh: Catalyst, Timeline, Price Target, Exit Conditions
- Xác định ít nhất 3 signal độc lập xác nhận (Hard Law Điều 3)
- Trả lời rõ ba câu hỏi: Tại sao bây giờ? Tại sao cổ phiếu này? Tôi có thể sai như thế nào?
- Định nghĩa điều kiện "Thesis Invalidation" — khi nào phải thoát bất kể P&L

**Inputs:**
- `research_report` từ Research Agent
- `current_regime` (Bull Trending / Bear Trending / etc.)
- Sector macro context (từ Market Surveillance)
- Historical thesis performance của cùng thesis type (từ Learning Agent)

**Outputs:**
- `investment_thesis` — thesis đầy đủ: catalyst, timeline, target price range, 3 confirming signals, invalidation conditions
- `pre_mortem_note` — tối thiểu 3 kịch bản hệ thống sai và hậu quả
- `thesis_id` — ID để tracking từ entry đến exit

**Decisions Allowed:**
- Xác định catalyst type (Earnings Surprise / Sector Rotation / Undervaluation / Value Unlock)
- Đề xuất timeline giữ (1M / 3M / 6M)
- Đề xuất exit conditions

**Decisions Forbidden:**
- Không được quyết định mua (đó là quyền Portfolio Agent sau khi qua Counter Thesis và Risk)
- Không được tự xác nhận signal của chính mình (không tự count 3 signals)
- Không được viết thesis cho ticker có GIL CATASTROPHIC

**Trigger Conditions:**
- Được gọi khi Research Agent output conviction ≥ B
- Re-evaluate thesis khi: BCTC mới publish, regime thay đổi, giá dao động > 15% so với entry thesis

**Success Metrics:**
- 100% thesis có đủ 3 confirming signals độc lập
- 100% thesis có invalidation conditions rõ ràng
- Pre-mortem note có ít nhất 3 kịch bản thất bại

**Failure Modes:**
- Không tìm đủ 3 signal độc lập → thesis không được tạo, ticker quay về Research queue
- Thesis quá lạc quan → Counter Thesis Agent sẽ bắt

**Related IOS Sections:** Mục 2 (Constitution), Mục 7 (Decision Framework)

**Required Data:** Output từ Research Agent + Learning Agent history

---

## AGENT-05: COUNTER THESIS AGENT

**Purpose:** Chủ động tìm lý do để BÁC BỎ thesis đầu tư. Devil's Advocate bắt buộc.

**Responsibilities:**
- Đọc Investment Thesis và tìm mọi lý do có thể sai
- Phân tích Counter-Thesis Score (CTS) dựa trên: GIL flags, Beneish warning zone, accrual anomaly, RPT exposure, macro headwinds, crowding risk
- Gán CTS = 0 nếu có GIL CATASTROPHIC (block hoàn toàn)
- Khi Bear Trending: tự động tăng CTS nặng hơn
- Output verdict: PROCEED / CONDITIONAL / BLOCK

**Inputs:**
- `investment_thesis` từ Thesis Agent
- GIL output (OCR score, cycles_detected, RPT pct)
- Beneish M-Score raw (không chỉ pass/fail)
- Macro data (credit growth, SBV rate)
- `current_regime` từ Decision Layer
- Crowding indicator (nếu nhiều quỹ cùng nắm)

**Outputs:**
- `counter_thesis_report` — danh sách rủi ro cụ thể, mức độ nghiêm trọng từng rủi ro
- `cts_score` — Counter Thesis Score (0–100, càng cao càng nguy hiểm)
- `verdict` — PROCEED / CONDITIONAL (kèm điều kiện) / BLOCK (kèm lý do)
- `block_reasons` — bắt buộc có khi verdict = BLOCK

**Decisions Allowed:**
- BLOCK hoàn toàn một ticker (ví dụ: GIL CATASTROPHIC)
- Yêu cầu thêm data trước khi PROCEED
- Gán CONDITIONAL với điều kiện cụ thể phải thỏa mãn

**Decisions Forbidden:**
- Không được PROCEED khi GIL CATASTROPHIC (zero exception)
- Không được giảm nhẹ rủi ro vì CSS cao
- Không được bỏ qua macro headwind khi Bear Trending

**Trigger Conditions:**
- Chạy ngay sau Thesis Agent output
- Re-run khi có thay đổi trọng yếu: BCTC restatement, thay đổi cổ đông lớn, tin xấu

**Success Metrics:**
- Zero ticker có GIL CATASTROPHIC vào được Portfolio
- Counter Thesis Report có số liệu cụ thể (không viết chung chung)
- Historical: các BLOCK có retrospective accuracy ≥ 60%

**Failure Modes:**
- GIL data lỗi → không đủ thông tin để Counter Thesis → default BLOCK, escalate Governance
- Thiên vị xác nhận ngược (tìm lý do block chứ không tìm rủi ro thực) → Learning Agent theo dõi

**Related IOS Sections:** Mục 5.7 (GIL), Mục 4 (Beneish), Mục 7 (Devil's Advocate)

**Required Data:** Groups 4, 9 (Ownership + Graph)

---

## AGENT-06: RISK AGENT

**Purpose:** Đánh giá rủi ro danh mục tổng thể và từng vị thế. Giám sát liên tục các Hard Laws.

**Responsibilities:**
- Tính ES 97.5% (Historical Simulation, rolling 500 phiên) cho danh mục
- Giám sát drawdown từ NAV peak, kích hoạt Drawdown Protocol đúng tier
- Kiểm tra concentration limits (15% single stock, 35% sector) trước mọi lệnh mới
- Tính GARCH Cash Target và thông báo cho Portfolio Agent
- Giám sát P_fail (xác suất lỗi hệ thống) từ Failsafe metrics
- Phát hiện CDC trigger (IC decay > 50%, slippage spike)

**Inputs:**
- Danh mục hiện tại (positions, sizes, sectors)
- `current_regime` từ Decision Layer
- Historical returns của danh mục (500 phiên)
- VIX_VN_analog (từ Market Surveillance)
- `market_breadth` real-time
- HMM Bear probability
- Slippage history và IC rolling (từ Learning Agent)
- Failsafe heartbeat metrics (P_fail)

**Outputs:**
- `risk_dashboard` — ES, drawdown, cash target, protocol tier, exposure summary (cập nhật mỗi phiên)
- `position_risk_check` — kết quả kiểm tra trước khi mỗi lệnh được thực thi
- `cdc_signal` — khi CDC trigger điều kiện
- `drawdown_action` — ALERT / YELLOW / ORANGE / RED kèm hành động cụ thể

**Decisions Allowed:**
- Kích hoạt Drawdown Protocol (YELLOW/ORANGE/RED)
- Yêu cầu tăng cash target
- Kích hoạt CDC (Capital Degradation Control)

**Decisions Forbidden:**
- Không được override Hard Stop 2% NAV (zero exception)
- Không được cho phép vị thế vượt 15% NAV dù conviction A+
- Không được tắt Risk monitoring khi đang có vị thế mở

**Trigger Conditions:**
- Chạy end-of-day: cập nhật ES, drawdown, cash target
- Chạy real-time: kiểm tra concentration trước mỗi lệnh
- Alert ngay: khi drawdown > 5%, hoặc ES > 4% NAV

**Success Metrics:**
- Zero Hard Law violation khi Risk Agent đang active
- ES calibration: breach rate ≤ 3% số phiên
- Drawdown Protocol trigger đúng tier 100% lần

**Failure Modes:**
- 500 phiên data không đủ (mới launch) → dùng parametric ES tạm thời, flag ESTIMATE
- CDC trigger sai do data noise → Learning Agent điều tra nguyên nhân

**Related IOS Sections:** Mục 9 (Risk Engine), Mục 10 (Drawdown), Mục 11 (VN30F Hedge), Mục 12 (CDC)

**Required Data:** Groups 1.1, 1.4 (Market Data)

---

## AGENT-07: PORTFOLIO AGENT

**Purpose:** Ra quyết định phân bổ vốn cuối cùng và duy trì danh mục tối ưu.

**Responsibilities:**
- Nhận candidates đã qua Thesis + Counter Thesis + Risk, quyết định đưa vào danh mục
- Tính position size theo Quarter Kelly, áp dụng tất cả constraints
- Quyết định rebalance khi drift > ±5% duy trì 3 phiên
- Tối ưu hóa danh mục 12–18 vị thế theo pairwise correlation < 0.5
- Quản lý cash target theo GARCH recommendation
- Ghi nhật ký mọi quyết định với rationale đầy đủ

**Inputs:**
- `counter_thesis_report` với verdict PROCEED hoặc CONDITIONAL
- `risk_dashboard` từ Risk Agent (ES, cash target, drawdown tier)
- `current_regime` từ Decision Layer
- Danh mục hiện tại (positions, correlation matrix)
- Quarter Kelly calculation (từ sizing engine)
- T+1.5 cost model (liquidity lock penalty)

**Outputs:**
- `portfolio_decision` — BUY / SELL / HOLD / REBALANCE với size cụ thể (VND)
- `order_instruction` — lệnh cụ thể cho Execution Agent: ticker, direction, size, max price, urgency
- `portfolio_snapshot` — trạng thái danh mục sau quyết định
- `decision_log` — mọi quyết định kèm rationale, để audit và Learning Agent học

**Decisions Allowed:**
- Mua/bán/giữ cổ phiếu trong Universe
- Quyết định size (trong limits)
- Quyết định timing rebalance

**Decisions Forbidden:**
- Không được mua ticker có verdict BLOCK từ Counter Thesis
- Không được override Cash Target trong RED drawdown protocol
- Không được mua ticker không có Research Report đầy đủ
- Không được tự thay đổi sizing formula

**Trigger Conditions:**
- Pre-market: 08:30, tổng hợp decisions cho ngày
- Intraday: khi có regime change, drawdown alert, hay ticker stop-loss trigger
- End-of-day: review và chuẩn bị lệnh ngày hôm sau

**Success Metrics:**
- Zero lệnh mua ticker BLOCK
- Portfolio concentration không bao giờ vượt limits
- Decision log đầy đủ 100% (không có quyết định không có lý do)

**Failure Modes:**
- Tất cả candidates đều BLOCK → danh mục không thay đổi, tăng cash
- Correlation matrix stale → không rebalance cho đến khi có data mới

**Related IOS Sections:** Mục 8 (Portfolio Optimizer), Mục 6 (Kelly Sizer), Mục 10 (Cash)

**Required Data:** Output từ tất cả agents trước

---

## AGENT-08: EXECUTION AGENT

**Purpose:** Thực thi lệnh tối ưu, giảm thiểu slippage, thích ứng với điều kiện thị trường real-time.

**Responsibilities:**
- Nhận order_instruction từ Portfolio Agent và thực thi theo EAE logic
- Chọn execution mode (NORMAL/STRESS/CRISIS) dựa trên volume, spread, market condition
- Dùng VWAP khi volume ATC > 30% (tránh thao túng đóng cửa)
- Không vượt quá 20% ADTV20 mỗi phiên cho bất kỳ ticker nào
- Track slippage thực tế vs expected, báo cáo ngay khi slippage spike

**Inputs:**
- `order_instruction` từ Portfolio Agent
- Order book real-time (top 10 bid/ask)
- Volume và spread real-time
- `session_context` từ Market Surveillance (Normal/Stress/Crisis)
- ADTV20 của từng ticker

**Outputs:**
- `execution_report` — lệnh đã thực thi: price, volume, slippage actual vs expected, execution mode used
- `slippage_record` — ghi vào Learning Agent sau mỗi lệnh
- `unexecuted_log` — lệnh chưa thực thi được và lý do

**Decisions Allowed:**
- Chọn execution mode
- Chia nhỏ lệnh khi STRESS mode
- Delay lệnh khi spread bất thường (chờ tối đa 1 phiên)

**Decisions Forbidden:**
- Không được thay đổi ticker, direction, hay size từ Portfolio Agent
- Không được thực thi lệnh khi Failsafe đang ACTIVE
- Không được vượt 20% ADTV20 dù Portfolio Agent yêu cầu

**Trigger Conditions:**
- Nhận order_instruction → thực thi trong phiên ngay hoặc phiên tiếp theo
- CRISIS mode: ưu tiên exit toàn bộ vị thế theo thứ tự thanh khoản giảm dần

**Success Metrics:**
- Slippage thực tế < 0.5% trong NORMAL mode
- 100% lệnh được thực thi trong 2 phiên (trừ CRISIS)
- Zero lệnh thực thi khi Failsafe ACTIVE

**Failure Modes:**
- Broker API down → Failsafe ACTIVE, không thực thi
- KRX nghẽn hệ thống → ghi log, chờ recovery, không retry liên tục

**Related IOS Sections:** Mục 14 (EAE), Mục 15 (Failsafe), HOSE-specific rules

**Required Data:** Groups 1.1, 1.2 (OHLCV + Order Book)

---

## AGENT-09: MONITORING AGENT

**Purpose:** Giám sát tất cả vị thế đang mở, theo dõi thesis validity, kích hoạt stop-loss.

**Responsibilities:**
- Theo dõi P&L của từng vị thế real-time trong giờ giao dịch
- Kích hoạt Hard Stop ngay khi vị thế loss ≥ 2% NAV
- Kiểm tra Thesis Invalidation conditions hàng ngày
- Theo dõi diễn biến catalyst của thesis (earnings release, management changes)
- Phát tín hiệu thoát khi thesis không còn valid dù chưa chạm stop-loss

**Inputs:**
- Danh mục hiện tại và `investment_thesis` của từng vị thế
- Giá real-time từ Market Surveillance
- BCTC mới khi publish
- News và sự kiện trọng yếu
- Current NAV

**Outputs:**
- `position_monitor` — P&L, distance to stop-loss, thesis health mỗi phiên
- `stop_loss_order` — kích hoạt ngay, gửi thẳng đến Execution Agent (bypass Portfolio Agent)
- `thesis_invalidation_alert` — khi điều kiện invalidation xảy ra
- `hold_review` — khi vị thế đạt target hoặc hết timeline, yêu cầu Portfolio Agent review

**Decisions Allowed:**
- Kích hoạt stop-loss (không cần xin phép Portfolio Agent)
- Escalate thesis invalidation lên Portfolio Agent để quyết định thoát

**Decisions Forbidden:**
- Không được tự quyết định thoát vị thế (trừ stop-loss)
- Không được override stop-loss vì bất kỳ lý do gì
- Không được giữ vị thế khi thesis đã invalidated mà không có Portfolio Agent confirm

**Trigger Conditions:**
- Real-time: giám sát mỗi 5 phút trong giờ giao dịch
- End-of-day: thesis health check cho tất cả vị thế
- Event-driven: khi có BCTC mới, tin trọng yếu

**Success Metrics:**
- Stop-loss kích hoạt trong vòng 5 phút từ khi vi phạm 2% NAV
- Zero vị thế giữ quá 3 ngày sau thesis invalidation
- 100% vị thế có thesis health status cập nhật hàng ngày

**Failure Modes:**
- Giá delayed → stop-loss delayed → ngay khi restore data phải check lại toàn bộ
- Thesis invalidation không rõ ràng → escalate lên Portfolio Agent để phán quyết

**Related IOS Sections:** Mục 2 (Hard Laws), Mục 7 (Thesis Invalidation), Mục 9 (Risk)

**Required Data:** Groups 1.1, 2 (Market + Financial)

---

## AGENT-10: LEARNING AGENT

**Purpose:** Học từ mọi quyết định và kết quả để cải thiện hệ thống theo thời gian.

**Responsibilities:**
- Thu thập và lưu trữ toàn bộ: factor scores, thesis, decision, slippage, realized returns
- Tính IC (Information Coefficient) rolling cho từng factor theo regime
- Phát hiện IC decay và phân loại nguyên nhân (DATA_ERROR / REGIME_MISMATCH / CROWDING / STRUCTURAL_DECAY)
- Cung cấp win_rate và payoff historical cho Kelly sizing
- Tổ chức Walk-Forward review hàng quý: đề xuất cập nhật factor weights

**Inputs:**
- `decision_log` từ Portfolio Agent (tất cả quyết định)
- `execution_report` từ Execution Agent (slippage thực tế)
- `research_report` từ Research Agent (factor scores lúc entry)
- Realized returns (từ Market Data sau khi close position)
- `current_regime` lúc mỗi decision

**Outputs:**
- `ic_report` — IC của từng factor, rolling 20 phiên và 60 phiên, theo regime
- `win_rate_table` — win rate và payoff theo conviction level và regime (cho Kelly sizing)
- `decay_diagnosis` — nguyên nhân IC decay khi phát hiện
- `quarterly_review` — đề xuất cập nhật weights, factors nên retain/retire
- `slippage_baseline` — baseline slippage theo ADTV20 bucket (cho CDC trigger)

**Decisions Allowed:**
- Đề xuất retire một factor (phải qua Governance Agent để confirm)
- Đề xuất cập nhật IC weights (phải qua Governance Agent)
- Phân loại nguyên nhân IC decay

**Decisions Forbidden:**
- Không được tự thay đổi factor weights trong production
- Không được retrain model mà không qua Governance Agent approval
- Không được xóa historical data dù kết quả xấu

**Trigger Conditions:**
- Daily: cập nhật IC rolling sau khi market close
- Khi IC 20-phiên giảm > 50% so với baseline → phát decay_diagnosis
- Hàng quý: quarterly_review

**Success Metrics:**
- IC tracking lag ≤ 1 ngày
- Decay diagnosis accuracy: ≥ 70% correct classification (đo retrospectively)
- 100% decisions được capture vào learning database

**Failure Modes:**
- Quá ít trades (< 100) → không đủ statistical power → IC estimates unreliable, flag
- Structural break trong thị trường → IC đồng loạt suy giảm, phân biệt khỏi factor-specific decay

**Related IOS Sections:** Mục 13 (CDC), Mục 16 (MRAL), Mục 17 (Learning Loop)

**Required Data:** Internal records từ tất cả agents

---

## AGENT-11: GOVERNANCE AGENT

**Purpose:** Đảm bảo tính toàn vẹn của hệ thống, phê duyệt mọi thay đổi, duy trì audit trail.

**Responsibilities:**
- Phê duyệt mọi thay đổi đến: factor weights, sizing formula, filter thresholds, IOS rules
- Duy trì audit log bất biến của mọi quyết định quan trọng
- Xem xét và phê duyệt quarterly_review từ Learning Agent
- Giám sát tuân thủ Hard Laws của tất cả agents
- Phát hiện và report vi phạm (ngay cả khi không gây tổn thất)
- Quản lý version control của toàn bộ hệ thống rules

**Inputs:**
- Alert từ tất cả agents khi có bất thường
- `quarterly_review` từ Learning Agent
- `decision_log` từ Portfolio Agent (audit)
- Báo cáo vi phạm từ Risk Agent
- CIO directives

**Outputs:**
- `change_approval` — phê duyệt hoặc từ chối mọi thay đổi hệ thống
- `audit_report` — báo cáo định kỳ (hàng tuần) về tuân thủ
- `violation_report` — mọi vi phạm Hard Law, dù không gây tổn thất
- `system_version_log` — lịch sử thay đổi hệ thống

**Decisions Allowed:**
- Phê duyệt hoặc từ chối thay đổi hệ thống
- Yêu cầu điều tra khi phát hiện bất thường
- Tạm dừng một agent để điều tra (escalate CIO)

**Decisions Forbidden:**
- Không được tự thay đổi IOS rules mà không có CIO sign-off
- Không được xóa audit records
- Không được tắt Risk Agent hay Failsafe

**Trigger Conditions:**
- Event-driven: mọi khi có Hard Law violation alert
- Hàng tuần: audit report
- Hàng quý: review và phê duyệt quarterly_review

**Success Metrics:**
- 100% thay đổi hệ thống có approval record
- Zero unlogged Hard Law violations
- Audit trail đầy đủ 100% decisions

**Failure Modes:**
- Governance Agent không phản hồi → hệ thống freeze mọi thay đổi, giữ nguyên trạng thái cuối

**Related IOS Sections:** Mục 2 (Hard Laws), Mục 17 (Learning Loop)

---

## AGENT-12: CIO AGENT

**Purpose:** Thực thể ra quyết định cấp cao nhất. Phân xử xung đột, phê duyệt quyết định ngoài thẩm quyền agent, đặt định hướng chiến lược.

**Responsibilities:**
- Phân xử khi có xung đột giữa các agents (ví dụ: Portfolio muốn mua, Risk muốn block)
- Phê duyệt các quyết định ngoại lệ (exceptional events không có trong rulebook)
- Set định hướng macro-regime hàng tháng (sector tilt, overall risk appetite)
- Review và phê duyệt IOS updates
- Quyết định cuối cùng khi Learning Agent đề xuất thay đổi cấu trúc

**Inputs:**
- Escalation từ bất kỳ agent nào
- `audit_report` từ Governance Agent
- `quarterly_review` từ Learning Agent (đã qua Governance)
- `risk_dashboard` tổng hợp
- Market regime context

**Outputs:**
- `cio_directive` — chỉ thị cho hệ thống về định hướng chiến lược
- `exception_approval` — phê duyệt ngoại lệ có thời hạn cụ thể
- `ios_update_approval` — phê duyệt cập nhật IOS (cần Governance co-sign)
- `system_halt_order` — tạm dừng toàn hệ thống trong tình huống cực đoan

**Decisions Allowed:**
- Mọi quyết định chiến lược cấp hệ thống
- Override agent decision khi có lý do rõ ràng và được ghi lại
- Halt toàn hệ thống

**Decisions Forbidden:**
- Không được override Hard Laws (Điều 1-5 của Constitution)
- Không được xóa audit trail
- Không được thay đổi IOS mà không có Governance co-sign

**Trigger Conditions:**
- Escalation từ bất kỳ agent
- Tự động: khi Drawdown = RED
- Tự động: khi Failsafe ACTIVE > 30 phút
- Hàng tháng: strategic review

**Success Metrics:**
- Thời gian phản hồi escalation < 4 giờ
- Zero exception approval không có expiry date
- Mọi IOS update có version log và rationale

**Failure Modes:**
- CIO không phản hồi escalation → Governance Agent giữ nguyên last known state, không hành động mới