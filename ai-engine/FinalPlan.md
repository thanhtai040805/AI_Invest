# AIInvest AI Engine v3.0 — Tài Liệu Cho AI Coding Agent
> **Phạm vi:** Chỉ cổ phiếu sàn HOSE (HSX). HNX/UPCOM không áp dụng.  
> **Nguyên tắc tối thượng:** Alpha đến từ quant core. LLM chỉ làm định tính. LLM tuyệt đối KHÔNG sinh số giao dịch.  
> **Thứ tự thực thi bắt buộc:** P0 hoàn thành → P1 → P2. Không được bỏ qua.

---

## MỤC LỤC

1. [Đánh Giá Tài Liệu Hiện Tại — Khắt Khe](#1-đánh-giá-tài-liệu-hiện-tại)
2. [P0 — Lỗi Chí Mạng Phải Sửa Ngay](#2-p0--lỗi-chí-mạng)
3. [Backtest Engine Chuẩn HOSE](#3-backtest-engine-chuẩn-hose)
4. [Factor Research Framework](#4-factor-research-framework)
5. [Portfolio Construction & Risk Model](#5-portfolio-construction--risk-model)
6. [Risk System 7 Lớp — Hiệu Chuẩn Lại](#6-risk-system-7-lớp--hiệu-chuẩn-lại)
7. [An Toàn Thực Thi & Live Trading](#7-an-toàn-thực-thi--live-trading)
8. [LLM Layer — Tái Thiết Kế](#8-llm-layer--tái-thiết-kế)
9. [MLOps & Hạ Tầng Dữ Liệu](#9-mlops--hạ-tầng-dữ-liệu)
10. [Evaluation & Monitoring](#10-evaluation--monitoring)
11. [Đặc Thù HOSE Bắt Buộc Biết](#11-đặc-thù-hose-bắt-buộc-biết)
12. [Go-Live Checklist](#12-go-live-checklist)
13. [Cấu Trúc Thư Mục Đề Xuất](#13-cấu-trúc-thư-mục-đề-xuất)

---

## 1. Đánh Giá Tài Liệu Hiện Tại

### Điểm Mạnh Giữ Lại
- Tách lớp kiến trúc rõ ràng (App / State / Agents / Quant / Risk / Dataflows) — cấu trúc tốt.
- Nhận thức về T+2, ±7% biên độ HOSE, cầm cố/giải chấp, room ngoại, Tết — **đây là điểm hiếm thấy, giữ lại toàn bộ**.
- Walk-forward + Benjamini-Hochberg correction — tư duy đúng, cần fix implementation.
- EventBus/SSE/replay/heartbeat/stale-run recovery — kỹ thuật tốt, không cần đổi.
- 7-layer risk với sector override — khung tư duy đúng.

### Vấn Đề Cốt Lõi — Phán Xét Thẳng

| # | Vấn Đề | Mức Độ | Hệ Quả Nếu Không Sửa |
|---|---------|--------|----------------------|
| 1 | ML train/test split ngẫu nhiên trên time series | **Nghiêm trọng** | Backtest lạc quan giả tạo 100% |
| 2 | Fundamental không có published_date (PIT) | **Nghiêm trọng** | IC factor value/quality bị thổi phồng |
| 3 | Không có adj_close / corporate action | **Nghiêm trọng** | Return momentum/volatility sai hoàn toàn |
| 4 | Không có chi phí giao dịch (phí, thuế, slippage) | **Nghiêm trọng** | Lý do #1 lãi giấy lỗ thật |
| 5 | T+2 implementation sai (không chặn bán trước T+2) | **Nghiêm trọng** | Fill không khả thi trong thực tế |
| 6 | `confidence = 0.75 + 0.1*(có dấu {}) + 0.05*(len>100)` | **Vô nghĩa hoàn toàn** | Sizing dựa trên con số bịa |
| 7 | LLM sinh `target_price`, `stop_loss`, `position_size` | **Nguy hiểm** | LLM hallucinate số giao dịch |
| 8 | IC weights tĩnh (lấy từ full-sample) | **Nghiêm trọng** | Look-ahead ở cấp tổ hợp factor |
| 9 | DB không có mã hủy niêm yết | **Nghiêm trọng** | Survivorship bias thổi phồng hiệu suất |
| 10 | `minimaxai/minimax-m2.7` không phải model ID thật | **Runtime error** | Fallback âm thầm hoặc crash |
| 11 | 29 swarm preset + 100+ agent role | **Over-engineering** | Token/latency khổng lồ, zero proven alpha |
| 12 | IntentRouter dùng regex thuần | **Giòn** | Sai với tiếng Việt không dấu/lẫn lộn |
| 13 | Lock trần/sàn không được xử lý trong backtest | **Nghiêm trọng** | Fill 100% khi thực tế không khớp được |
| 14 | Altman Z / Beneish M dùng ngưỡng US GAAP | **Sai ngữ cảnh** | False positive/negative cao trên VAS |
| 15 | Model pickle vào tempdir | **Không tái lập** | Mất model sau restart |

**Kết luận:** Hệ thống hiện tại có kiến trúc tốt nhưng phần sinh lời thật (quant factor) đang dính hàng loạt lỗi leakage. Nếu go-live như hiện tại → thua lỗ gần như chắc chắn.

---

## 2. P0 — Lỗi Chí Mạng

> **Quy tắc:** Không được động vào P1/P2 trước khi tất cả P0 có Definition of Done (DoD) xanh.

### P0.1 — Loại Bỏ Look-Ahead / Data Leakage Trong ML

**File cần tạo:** `quant/validation/purged_cv.py`

```python
class PurgedWalkForwardCV:
    """
    Temporal cross-validation theo López de Prado (AFML Chapter 7).
    
    Args:
        n_splits: số fold (khuyến nghị 5-10 cho dataset VN)
        embargo_days: số ngày embargo sau train_end (>= horizon, mặc định 5)
        horizon: forward return horizon (ngày)
    
    HARD RULES:
    - Không shuffle bất kỳ dữ liệu nào
    - test index không được nằm trong [train_end - embargo, train_end]
    - Feature normalization (z-score, winsorize, impute) phải fit ONLY trên train set
    - Không dùng statistics tính trên toàn bộ dataset
    """
    def __init__(self, n_splits: int, embargo_days: int, horizon: int):
        assert embargo_days >= horizon, "Embargo phải >= horizon để loại label overlap"
        self.n_splits = n_splits
        self.embargo_days = embargo_days
        self.horizon = horizon
    
    def split(self, X, y=None, groups=None):
        """Yield (train_idx, test_idx) tuples theo expanding window."""
        ...
    
    def validate_no_leakage(self, train_idx, test_idx, dates) -> bool:
        """Unit test: không có test index nào trong embargo window."""
        train_end = dates[train_idx[-1]]
        embargo_start = train_end - pd.Timedelta(days=self.embargo_days)
        test_dates = dates[test_idx]
        return not any((test_dates >= embargo_start) & (test_dates <= train_end))
```

**DoD:**
- `python -m pytest quant/validation/test_purged_cv.py -v` pass 100%
- Test bắt buộc: không có index test nào trong `[train_end - embargo_days, train_end]`
- Mọi `train_model()` và `compute_ic_series()` đi qua `PurgedWalkForwardCV`
- Walk-forward expanding/rolling window bắt buộc cho mọi báo cáo hiệu suất

---

### P0.2 — Point-in-Time (PIT) Fundamentals

**Schema bắt buộc:**

```sql
-- Bảng tài chính phải có published_date
ALTER TABLE financial_ratios ADD COLUMN published_date DATE;
ALTER TABLE financial_statements ADD COLUMN published_date DATE;

-- Quy tắc lag bảo thủ nếu không có published_date thật:
-- published_date = period_end_date + 45 ngày
-- (VN doanh nghiệp công bố quý chậm 20-45 ngày sau kỳ kết thúc)

-- View PIT-safe:
CREATE VIEW financial_ratios_pit AS
SELECT * FROM financial_ratios
WHERE published_date IS NOT NULL;
```

**Rule trong code:**
```python
def get_fundamentals_at_date(symbol: str, as_of_date: date) -> dict:
    """
    BẮTBUỘC: Chỉ trả về dữ liệu có published_date <= as_of_date.
    CẤMTUYỆT: Dùng period_end_date làm proxy cho published_date.
    """
    query = """
        SELECT * FROM financial_ratios
        WHERE symbol = %s 
          AND published_date <= %s
        ORDER BY published_date DESC
        LIMIT 1
    """
```

**DoD:**
- Mọi factor value/quality (ROE, NM, GM, YOY_REV, Piotroski) đọc qua PIT query
- Có unit test: factor tại ngày `2023-01-15` không được chứa dữ liệu published sau `2023-01-15`
- Nếu không có `published_date` thật → lag cứng 45 ngày, log warning

---

### P0.3 — Corporate Actions & Adjusted Price

**Schema:**
```sql
CREATE TABLE corporate_actions (
    id          SERIAL PRIMARY KEY,
    symbol      VARCHAR(10) NOT NULL,
    ex_date     DATE NOT NULL,        -- Ngày GDKHQ
    record_date DATE,
    type        VARCHAR(20) NOT NULL, -- SPLIT, DIVIDEND_CASH, DIVIDEND_STOCK, RIGHTS
    ratio       NUMERIC(10,6),        -- hệ số tách/thưởng cổ phiếu (vd 1.2 = +20%)
    cash_amount NUMERIC(15,2),        -- tiền cổ tức (VND/cp)
    note        TEXT,
    UNIQUE(symbol, ex_date, type)
);

CREATE TABLE price_adjusted (
    symbol      VARCHAR(10) NOT NULL,
    date        DATE NOT NULL,
    open_raw    NUMERIC(12,0),
    high_raw    NUMERIC(12,0),
    low_raw     NUMERIC(12,0),
    close_raw   NUMERIC(12,0),       -- Giá thô (hiển thị cho user)
    close_adj   NUMERIC(15,4),       -- Giá đã điều chỉnh đầy đủ (tính toán)
    volume      BIGINT,
    adj_factor  NUMERIC(10,6) DEFAULT 1.0,
    PRIMARY KEY (symbol, date)
);
```

**Rules:**
- `close_adj` = dùng cho MỌI tính toán: return, momentum, volatility, RSI, MACD, Bollinger
- `close_raw` = chỉ dùng để hiển thị giá cho người dùng
- Pipeline tái tính `adj_factor` phải chạy lại sau mỗi sự kiện corporate action

**DoD:**
- Backtest dùng 100% `close_adj`
- Có `corporate_actions` table với ít nhất 2018→nay cho universe HOSE
- Có script `scripts/rebuild_adj_prices.py` chạy lại adj khi thêm CA mới
- Unit test: giá ngày GDKHQ không có jump đột ngột trong adj series

---

### P0.4 — Chi Phí Giao Dịch Thực Tế HOSE

**File:** `backtest/cost_model.py`

```python
# Thông số HOSE thực tế (cập nhật theo broker)
HOSE_COST_PARAMS = {
    "brokerage_rate_buy": 0.0010,    # 0.10% phí mua (DNSE)
    "brokerage_rate_sell": 0.0010,   # 0.10% phí bán
    "tax_rate_sell": 0.0010,         # 0.10% thuế bán (chỉ chiều bán, kể cả lỗ)
    "min_brokerage_fee": 10_000,     # Phí tối thiểu 10,000 VND
    "lot_size": 100,                 # 100 cổ phiếu/lô
}

def estimate_cost(
    side: str,           # "BUY" | "SELL"
    price: float,        # giá khớp (VND)
    quantity: int,       # số lượng cổ phiếu (đã làm tròn lô)
    adv_20d: float,      # average daily value 20 ngày (VND)
    spread_pct: float = 0.001,  # estimated bid-ask spread
) -> dict:
    """
    Trả về breakdown chi phí đầy đủ.
    
    Market impact model: Almgren-Chriss simplified
        impact = spread/2 + sigma * sqrt(qty_value / adv_20d) * impact_coeff
    
    KHÔNG được dùng giá close lý tưởng — phải có slippage tối thiểu 1 bước giá.
    """
    notional = price * quantity
    
    # Bước 1: Brokerage
    brokerage = max(notional * HOSE_COST_PARAMS[f"brokerage_rate_{side.lower()}"],
                    HOSE_COST_PARAMS["min_brokerage_fee"])
    
    # Bước 2: Tax (chỉ bán)
    tax = notional * HOSE_COST_PARAMS["tax_rate_sell"] if side == "SELL" else 0
    
    # Bước 3: Slippage (≥ 1 bước giá)
    price_step = get_hose_price_step(price)
    min_slippage = price_step / price  # tối thiểu 1 bước giá
    
    # Bước 4: Market impact
    participation = (notional / adv_20d) if adv_20d > 0 else 1.0
    impact_pct = spread_pct / 2 + 0.005 * (participation ** 0.5)
    
    total_slippage = max(min_slippage, impact_pct) * notional
    
    return {
        "brokerage": brokerage,
        "tax": tax,
        "slippage": total_slippage,
        "total_cost": brokerage + tax + total_slippage,
        "total_cost_pct": (brokerage + tax + total_slippage) / notional,
    }

def round_to_lot(quantity: float, lot_size: int = 100) -> int:
    """Làm tròn xuống về bội số của lot_size."""
    return int(quantity // lot_size) * lot_size
```

**DoD:**
- Backtest report BẮT BUỘC in 2 dòng: `Gross Return` và `Net Return (after all costs)`
- Quyết định go/no-go CHỈ dựa trên Net Return
- `python -m pytest backtest/test_cost_model.py` — test round-trip cost ~0.25-0.30% cho lệnh điển hình

---

### P0.5 — T+2 Execution Model Đúng (HOSE)

**Sự thật cơ chế HOSE (phải hard-code vào backtest engine):**

```python
class HOSEExecutionModel:
    """
    Cash account HOSE:
    - Mua khớp ngày T → cổ phiếu VỀ tài khoản T+2 (chiều)
    - Không bán được phần vừa mua trong T, T+1
    - Bán ngày T → tiền VỀ T+2
    - Holding period tối thiểu thực tế: 2 phiên giao dịch
    
    KHÔNG phải T+2 calendar days — phải tính T+2 TRADING days
    (bỏ qua weekend, nghỉ lễ VN)
    """
    
    SETTLEMENT_LAG = 2  # trading days
    
    def can_sell(self, symbol: str, buy_date: date, sell_date: date) -> bool:
        trading_days_held = count_trading_days(buy_date, sell_date, market="HOSE")
        return trading_days_held >= self.SETTLEMENT_LAG
    
    def get_fill_price(self, symbol: str, date: date, 
                       session: str = "ATC") -> float:
        """
        session: "ATO" | "ATC" | "VWAP" | "CONTINUOUS"
        KHÔNG dùng close price thuần túy — phải có slippage.
        Mặc định ATC cho end-of-day signal.
        """
        ...
    
    def handle_lock_limit(self, symbol: str, date: date, 
                           side: str) -> tuple[bool, float]:
        """
        Trả về (can_fill, actual_qty_ratio).
        Nếu dư mua trần (BUY) hoặc dư bán sàn (SELL) → không khớp hoặc khớp một phần.
        KHÔNG giả định fill 100% khi lock.
        """
        ...
```

**Lịch giao dịch HOSE (phải có):**
```python
VN_HOLIDAYS = [
    # Tết Nguyên Đán (approx, cập nhật hàng năm)
    # 2024: 8-16/2, 2025: 27/1-2/2, 2026: 16-23/2
    # Lễ 30/4, 1/5, 2/9, 1/1
    # ...phải dùng file lịch chính thức HOSE
]

def count_trading_days(start: date, end: date, market: str = "HOSE") -> int:
    """Đếm ngày giao dịch thực tế, bỏ qua VN_HOLIDAYS."""
```

**DoD:**
- Backtest chặn bán bất kỳ vị thế mua chưa đủ T+2 trading days
- Đặc biệt: ngày mua = ngày T, bán sớm nhất = ngày T+2 (trading day thứ 2 sau T)
- Test: thử bán ngay ngày mua → backtest từ chối fill

---

### P0.6 — Xóa Bỏ "Confidence" Giả

**Xóa ngay đoạn này trong `orchestrator.py`:**
```python
# XÓA NGAY — đây là con số vô nghĩa
confidence = 0.75
if contains JSON chars: confidence += 0.10
if content length > 100: confidence += 0.05
```

**Thay bằng calibrated probability:**
```python
class CalibratedConfidence:
    """
    Confidence phải map ra xác suất thắng thực nghiệm.
    Vd: confidence=0.60 phải có hit rate lịch sử 60% ở bin [0.55, 0.65].
    
    Phương pháp:
    1. Platt Scaling (sigmoid calibration) — cho output ML
    2. Isotonic Regression — flexible, cho ensemble output
    3. Reliability diagram để visualize calibration
    4. Brier Score để đo calibration quality (thấp hơn = tốt hơn)
    """
    
    def calibrate(self, raw_scores: np.ndarray, 
                  outcomes: np.ndarray) -> None:
        """Fit calibration model trên out-of-sample data."""
        from sklearn.calibration import CalibratedClassifierCV, calibration_curve
        ...
    
    def predict_proba(self, raw_score: float) -> float:
        """Trả về calibrated probability [0, 1]."""
        ...
    
    def reliability_diagram(self) -> plt.Figure:
        """Hiển thị calibration quality."""
        ...
```

**Đối với ensemble (factor + ML):**
```python
def compute_ensemble_confidence(
    factor_score: float,    # IC-weighted factor composite
    ml_proba: float,        # Calibrated ML probability
    signal_strength: float, # Normalized magnitude
    agreement: float,       # Degree of consensus across models
) -> float:
    """
    Confidence = weighted average của calibrated probabilities + signal strength.
    KHÔNG có magic number hardcode.
    """
```

**DoD:**
- Reliability diagram cho thấy calibration đường gần đường 45°
- Brier Score < 0.25 (tốt hơn naive forecast)
- Confidence 0.6 → hit rate 55-65% trong out-of-sample test

---

### P0.7 — Tách LLM Ra Khỏi Quyết Định Số

**Phân loại cứng output LLM:**

```python
# ALLOWED — LLM được phép sinh
LLM_ALLOWED_OUTPUTS = {
    "narrative_text",       # Diễn giải, tổng hợp bằng ngôn ngữ tự nhiên
    "qualitative_flag",     # "tin tiêu cực", "rủi ro cao", "cần xem xét"
    "hypothesis_text",      # Gợi ý giả thuyết để quant test
    "news_classification",  # Phân loại tin: positive/negative/neutral + confidence
    "report_section",       # Văn bản báo cáo có trích dẫn nguồn
}

# BANNED — LLM TUYỆT ĐỐI KHÔNG được sinh
LLM_BANNED_OUTPUTS = {
    "target_price",         # Phải từ quant model
    "stop_loss",            # Phải từ risk model (ATR, drawdown)
    "position_size",        # Phải từ vol-scaled sizing
    "buy_sell_hold",        # Phải từ factor composite + threshold
    "price_prediction",     # Phải từ ML model
    "confidence_score",     # Phải từ calibrated model
}

class LLMOutputGuardrail:
    """
    Hard validation: parse output LLM, reject nếu chứa số không truy vết.
    """
    
    NUMBER_PATTERN = re.compile(r'\b\d+(?:\.\d+)?(?:%|VND|đồng|%)?\b')
    
    def validate(self, llm_output: str, tool_results: dict) -> ValidationResult:
        """
        Tìm mọi số trong llm_output.
        Với mỗi số: kiểm tra có tồn tại trong tool_results không.
        Nếu có số không truy vết được → reject với explanation.
        """
        numbers_in_output = self.NUMBER_PATTERN.findall(llm_output)
        untraced = [n for n in numbers_in_output 
                    if not self._is_in_tool_results(n, tool_results)]
        
        if untraced:
            return ValidationResult(
                valid=False,
                reason=f"LLM sinh số không truy vết: {untraced}. "
                       f"Yêu cầu mọi số phải đến từ tool result.",
            )
        return ValidationResult(valid=True)
```

**DoD:**
- `portfolio_manager.py` KHÔNG sinh `target_price`, `stop_loss`, `position_size`
- Tất cả các số trong output LLM phải pass `LLMOutputGuardrail.validate()`
- Integration test: feed LLM output có số bịa → guardrail reject

---

### P0.8 — Survivorship-Free Database

```sql
-- Bảng universe lịch sử
CREATE TABLE hose_universe_history (
    symbol          VARCHAR(10) NOT NULL,
    listed_date     DATE NOT NULL,
    delisted_date   DATE,            -- NULL nếu vẫn niêm yết
    delist_reason   VARCHAR(100),    -- 'VOLUNTARY', 'FORCED', 'MERGER', 'BANKRUPTCY'
    exchange        VARCHAR(5) DEFAULT 'HOSE',
    PRIMARY KEY (symbol)
);

-- Query universe tại ngày t (đúng cách):
CREATE OR REPLACE FUNCTION get_universe_at_date(as_of DATE)
RETURNS TABLE(symbol VARCHAR) AS $$
    SELECT symbol FROM hose_universe_history
    WHERE listed_date <= as_of
      AND (delisted_date IS NULL OR delisted_date > as_of)
      AND exchange = 'HOSE';
$$ LANGUAGE SQL;
```

**DoD:**
- DB chứa ít nhất các mã đã hủy niêm yết từ 2018→nay có liên quan (FTM, ITA, HVG, ROS, TTF...)
- Backtest engine dùng `get_universe_at_date(t)` để build universe tại mỗi ngày t
- Test: universe năm 2020 khác universe năm 2024 (phản ánh delist/list)

---

### P0.9 — Xác Minh Model ID LLM

**Kiểm tra ngay, trước khi deploy:**
```python
VERIFIED_MODEL_IDS = {
    "groq_0": "llama-3.3-70b-versatile",     # ✅ Đã xác minh trên Groq
    "groq_1": "qwen/qwen3-32b",              # ⚠️ Cần verify availability trên Groq endpoint
    "nvidia": "UNKNOWN — cần xác minh",      # ❌ minimaxai/minimax-m2.7 chưa xác nhận
}

# Script kiểm tra:
# python scripts/verify_llm_models.py
# → ping từng model với prompt ngắn
# → log model id thật, version, context window
# → fail nếu model không response
```

**DoD:**
- `scripts/verify_llm_models.py` pass với HTTP 200 cho tất cả model đang dùng
- Không có model ID placeholder trong config

---

## 3. Backtest Engine Chuẩn HOSE

**File:** `backtest/engine.py`

### Kiến Trúc Event-Driven, Point-in-Time

```python
class HOSEBacktestEngine:
    """
    Event-driven backtester chuẩn cho HOSE.
    
    Nguyên tắc:
    - PIT: tại ngày t, chỉ dùng dữ liệu available trước t
    - T+2: chặn bán trước khi settle
    - Lock trần/sàn: không fill hoặc fill một phần
    - Chi phí đầy đủ: phí + thuế + slippage + impact
    - Lot size: 100 cổ phiếu (HOSE)
    """
    
    def run(self, 
            strategy: QuantStrategy,
            start_date: date,
            end_date: date,
            initial_capital: float = 1_000_000_000,  # 1 tỷ VND
            ) -> BacktestResult:
        
        portfolio = Portfolio(initial_capital)
        
        for t in self.trading_calendar.range(start_date, end_date):
            # 1. Universe tại ngày t (survivorship-free)
            universe = get_universe_at_date(t)
            universe = self._apply_liquidity_filter(universe, t, min_adv_bn=5.0)
            
            # 2. Features tại ngày t (PIT — chỉ dữ liệu published <= t)
            features = self._get_pit_features(universe, t)
            
            # 3. Signals từ quant pipeline (deterministic, no LLM)
            signals = strategy.generate_signals(features, t)
            
            # 4. Risk gate (CRS scoring, PIT news/flags)
            signals = self.risk_gate.filter(signals, t)
            
            # 5. Portfolio optimization
            target_weights = self.optimizer.optimize(signals, portfolio, t)
            
            # 6. Orders (với turnover control)
            orders = portfolio.compute_orders(target_weights, 
                                              turnover_limit=0.20)  # max 20%/tháng
            
            # 7. Simulate fills tại T+execution_lag
            fills = self._simulate_fills(orders, t)
            
            # 8. Apply costs
            for fill in fills:
                cost = estimate_cost(fill.side, fill.price, fill.quantity,
                                     fill.adv_20d)
                portfolio.apply_fill(fill, cost)
            
            # 9. Mark-to-market
            portfolio.mark_to_market(t, self.price_feed)
    
    def _simulate_fills(self, orders, t):
        fills = []
        for order in orders:
            # Chặn bán trước T+2
            if order.side == "SELL":
                if not self.t2_model.can_sell(order.symbol, 
                                               order.original_buy_date, t):
                    continue  # skip, không fill
            
            # Xử lý lock trần/sàn
            can_fill, fill_ratio = self.t2_model.handle_lock_limit(
                order.symbol, t, order.side)
            if not can_fill:
                continue
            
            # Làm tròn lô
            quantity = round_to_lot(order.quantity * fill_ratio)
            if quantity == 0:
                continue
            
            # Giá khớp (ATC + slippage, không phải close lý tưởng)
            fill_price = self._get_fill_price(order.symbol, t, 
                                               session="ATC") 
            fills.append(Fill(order.symbol, fill_price, quantity, order.side, t))
        
        return fills
```

### Metrics Báo Cáo Bắt Buộc

```python
@dataclass
class BacktestReport:
    # Returns (GROSS và NET — bắt buộc cả hai)
    gross_cagr: float
    net_cagr: float             # Chỉ xét cái này để go/no-go
    gross_sharpe: float
    net_sharpe: float
    sortino_ratio: float
    calmar_ratio: float
    
    # Drawdown
    max_drawdown: float
    max_drawdown_duration_days: int
    recovery_time_days: int
    
    # Trading statistics
    hit_rate: float             # % lệnh thắng
    profit_factor: float        # Gross profit / Gross loss
    avg_win_pct: float
    avg_loss_pct: float
    annual_turnover: float      # Số lần quay vòng vốn/năm
    
    # Cost analysis
    total_brokerage: float
    total_tax: float
    total_slippage: float
    total_costs: float
    
    # Robustness (chống overfitting)
    deflated_sharpe_ratio: float   # Theo Bailey et al.
    pbo: float                     # Probability of Backtest Overfitting (Combinatorially Symmetric CV)
    
    # Attribution
    alpha_vs_vnindex: float        # Jensen's alpha
    beta_vs_vnindex: float
    information_ratio: float
    
    # Capacity
    estimated_capacity_bn: float   # Vốn tối đa trước khi impact ăn alpha
    
    # Baseline comparison (bắt buộc)
    baseline_name: str             # "Buy & Hold VN30 ETF (E1VFVN30)"
    baseline_cagr: float
    baseline_sharpe: float
    outperformance: float          # net_cagr - baseline_cagr
```

**DoD:**
```bash
# Phải chạy được lệnh này và xuất report đầy đủ
python -m backtest.run \
    --strategy composite \
    --start 2018-01-01 \
    --end 2024-12-31 \
    --output reports/backtest_composite_2018_2024.json

# Pass điều kiện:
# - net_sharpe > 1.0
# - net_cagr > baseline_cagr (E1VFVN30)
# - max_drawdown < 30%
# - deflated_sharpe > 0
# - pbo < 50%
```

---

## 4. Factor Research Framework

### Walk-Forward IC Weighting (Không Dùng Full-Sample IC)

```python
class WalkForwardFactorCombiner:
    """
    IC weights tại ngày t CHỈ được tính từ dữ liệu TRƯỚC t.
    
    Method: Rolling IC weighting với Bayesian shrinkage
    - Window: 252 ngày giao dịch (1 năm)
    - Shrinkage: Bayesian prior = equal weight (1/N)
    - Regularization: Ledoit-Wolf hoặc Ridge regression
    """
    
    def fit_weights_at_date(self, 
                             factor_panel: pd.DataFrame,
                             forward_returns: pd.Series,
                             as_of_date: date,
                             lookback_days: int = 252) -> dict[str, float]:
        """
        Tính IC weights tại as_of_date dùng dữ liệu [as_of_date - lookback, as_of_date).
        
        Returns: {factor_name: weight} — đã normalize sum = 1
        """
        # Lấy dữ liệu trong cửa sổ rolling
        mask = (factor_panel.index >= as_of_date - timedelta(days=lookback_days*1.5)
               ) & (factor_panel.index < as_of_date)
        
        # Compute IC per period per factor
        ic_series = {}
        for factor in factor_panel.columns:
            ic_series[factor] = compute_ic_series(
                factor_panel.loc[mask, factor], 
                forward_returns.loc[mask]
            )
        
        # Mean IC với shrinkage
        mean_ic = {f: np.mean(ic) for f, ic in ic_series.items()}
        shrunk_weights = self._ledoit_wolf_shrinkage(mean_ic)
        
        return shrunk_weights
```

### Alpha Decay Test

```python
def test_alpha_decay(factor: pd.Series, returns_by_horizon: dict) -> pd.DataFrame:
    """
    Đo IC theo nhiều horizon: 1, 3, 5, 10, 20 ngày.
    Output: bảng IC/ICIR theo horizon → quyết định rebalance frequency.
    
    Kết luận từ decay test:
    - IC cao ở horizon ngắn → rebalance thường (weekly)
    - IC cao ở horizon dài → rebalance ít (monthly)
    - IC giảm nhanh → factor momentum
    - IC giảm chậm → factor quality/value
    """
    results = {}
    for h, ret in returns_by_horizon.items():
        ic_series = compute_ic_series(factor, ret)
        results[h] = {
            "mean_ic": ic_series.mean(),
            "icir": ic_series.mean() / ic_series.std(),
            "pct_positive": (ic_series > 0).mean(),
        }
    return pd.DataFrame(results).T
```

### Regime-Aware Factor Testing

```python
VNINDEX_REGIME = {
    "UPTREND":   lambda ret_252d: ret_252d > 0.15,    # >15% YoY
    "DOWNTREND": lambda ret_252d: ret_252d < -0.10,   # <-10% YoY
    "SIDEWAYS":  lambda ret_252d: True,                # else
}

def ic_by_regime(factor: pd.Series, 
                 returns: pd.Series, 
                 vnindex_returns: pd.Series) -> pd.DataFrame:
    """
    Đo IC trong từng regime.
    Nhiều factor đảo dấu IC theo regime — phải biết trước khi dùng.
    """
```

### Universe HOSE Thực Tế

```python
HOSE_UNIVERSE_CONFIG = {
    "min_adv_20d_bn": 5.0,     # Tối thiểu 5 tỷ VND ADV 20 ngày
    "min_market_cap_bn": 100,  # Tối thiểu 100 tỷ VND market cap
    "min_stocks_for_ranking": 80,  # Nâng từ 30 lên 80 (universe HOSE ~200-250 mã thanh khoản)
    "max_price_limit_pct": 0.95,   # Loại mã đang ở trần (dư mua 95%+ của ATO)
    "exclude_suspended": True,
    "exclude_delisted": True,
}
```

---

## 5. Portfolio Construction & Risk Model

### Covariance Estimation

```python
from sklearn.covariance import LedoitWolf

class HOSERiskModel:
    """
    Factor risk model cho HOSE.
    Dùng Ledoit-Wolf shrinkage để ổn định covariance matrix.
    """
    
    def estimate_covariance(self, 
                             returns: pd.DataFrame,
                             lookback_days: int = 252) -> np.ndarray:
        """
        Returns: Shrunk covariance matrix (annualized).
        """
        lw = LedoitWolf()
        lw.fit(returns.tail(lookback_days).dropna())
        cov_matrix = lw.covariance_ * 252  # Annualize
        return cov_matrix
```

### Mean-Variance Optimizer với Ràng Buộc HOSE

```python
class HOSEPortfolioOptimizer:
    
    CONSTRAINTS = {
        "max_weight_per_stock": 0.05,      # Tối đa 5%/mã
        "max_weight_per_sector": 0.25,     # Tối đa 25%/ngành (ICB Level 1)
        "max_portfolio_beta": 1.2,         # Beta danh mục không quá 1.2
        "min_stocks": 15,                  # Tối thiểu 15 mã (diversification)
        "max_stocks": 30,                  # Tối đa 30 mã (concentration limit)
        "turnover_limit_monthly": 0.30,    # Max 30% turnover/tháng (vì phí cao)
        "min_adv_coverage_days": 10,       # Thanh khoản: có thể xả trong 10 ngày
    }
    
    def optimize(self, 
                 signals: pd.Series,    # Factor composite scores
                 cov_matrix: np.ndarray,
                 current_weights: pd.Series,
                 constraints: dict = None) -> pd.Series:
        """
        Tối ưu mean-variance / max-Sharpe với ràng buộc.
        Dùng cvxpy để solve QP.
        """
        import cvxpy as cp
        ...
```

### Volatility Targeting

```python
def scale_to_vol_target(
    weights: pd.Series,
    cov_matrix: np.ndarray,
    target_vol_annual: float = 0.15,    # 15% annualized target vol
    max_leverage: float = 1.0,          # Long-only: max leverage = 1.0
) -> pd.Series:
    """
    Scale exposure để nhắm vol danh mục mục tiêu.
    Long-only: giảm weights hoặc tăng cash khi vol cao.
    """
    portfolio_vol = np.sqrt(weights @ cov_matrix @ weights)
    scale = min(target_vol_annual / portfolio_vol, max_leverage)
    return weights * scale
```

### Beta Hedge với VN30F1M

```python
class RegimeBasedHedge:
    """
    Khi regime xấu (DOWNTREND): hedge beta bằng VN30F1M hoặc giảm exposure.
    HOSE không có short selling → chỉ giảm tỷ trọng cổ phiếu / tăng cash.
    
    VN30F1M futures: có thể short để hedge (nếu broker hỗ trợ).
    """
    
    def get_hedge_ratio(self, 
                         portfolio_beta: float,
                         regime: str,
                         target_beta: float = 0.5) -> float:
        """
        Trong DOWNTREND: target beta 0.3-0.5 (giảm exposure)
        Trong UPTREND: target beta 0.8-1.0
        Trong SIDEWAYS: target beta 0.6-0.8
        """
```

### Position Sizing — Vol-Scaled Kelly

```python
def compute_position_size(
    signal_strength: float,         # Normalized factor composite [-3, 3]
    calibrated_confidence: float,   # Calibrated win probability [0, 1]
    volatility: float,              # Realized vol của mã (annualized)
    portfolio_vol_target: float = 0.15,
    kelly_fraction: float = 0.25,   # Fractional Kelly (conservative)
    max_position_pct: float = 0.05, # Hard cap 5%
) -> float:
    """
    KHÔNG dùng confidence giả để sizing.
    Full Kelly quá aggressive → dùng fractional Kelly (25% Kelly).
    """
    # Kelly criterion: f = (p*b - q) / b = edge / odds
    p = calibrated_confidence  # win prob
    q = 1 - p
    b = abs(signal_strength)   # approx odds ratio
    kelly_f = (p * b - q) / b if b > 0 else 0
    
    # Fractional Kelly + vol scaling
    raw_size = kelly_fraction * kelly_f * (portfolio_vol_target / volatility)
    return min(max(raw_size, 0), max_position_pct)
```

---

## 6. Risk System 7 Lớp — Hiệu Chuẩn Lại

### Nguyên Tắc Hiệu Chuẩn

**Mọi ngưỡng phải đến từ phân phối thực tế HOSE, không phải copy từ thị trường Mỹ.**

```python
class HOSERiskThresholdCalibrator:
    """
    Calibrate ngưỡng risk từ historical data HOSE.
    
    Quy trình:
    1. Lấy full historical data HOSE (2010-2024)
    2. Tính phân phối từng metric (vol, CVaR, volume_ratio, ...)
    3. Đặt ngưỡng theo percentile (vd p85 = "high", p95 = "very high")
    4. Cross-validate: ngưỡng đó có predict được bad outcome không?
    """
    
    # Ngưỡng SƠ BỘ — phải calibrate lại bằng dữ liệu HOSE thực tế
    # Các con số dưới đây là placeholder, KHÔNG phải final
    PLACEHOLDER_THRESHOLDS = {
        "vol_20d_high": 0.035,      # Cần calibrate
        "vol_20d_very_high": 0.05,  # Cần calibrate
        "cvar_95_high": 0.04,       # Cần calibrate
        "amihud_high": 0.01,        # Cần calibrate
        "volume_ratio_extreme": 5.0, # Cần calibrate
    }
```

### Altman Z / Beneish M — Chỉ Dùng Định Tính

```python
# Altman Z calibrated trên US GAAP — KHÔNG áp ngưỡng cứng cho VAS
# Beneish M calibrated trên US data — tương tự

class VNAccountingFlags:
    """
    Thay vì dùng ngưỡng Mỹ, dùng các tín hiệu định tính VN-specific:
    
    - Ý kiến kiểm toán ngoại trừ / từ chối (từ thuyết minh BCTC)
    - Thay đổi đơn vị kiểm toán liên tiếp
    - Chênh lệch lớn giữa lợi nhuận kế toán và lợi nhuận thuế
    - Accrual ratio cao (định tính: "earnings quality thấp")
    - Working capital âm kéo dài
    
    Output: FLAG_ACCOUNTING_CONCERN (không phải score cứng)
    """
```

### PIT cho Risk News/Flags

```python
# Quy tắc bắt buộc cho CafeF scraper:
class CafeFScraper:
    def scrape(self, keyword: str, date_range: tuple) -> list[dict]:
        """
        BẮTBUỘC: Mỗi bản tin phải có:
        - publish_date: ngày đăng thật (không phải ngày scrape)
        - effective_date: ngày risk flag có hiệu lực
        
        CẤMTUYỆT: Dùng tin publish_date > backtest_date trong backtest.
        """
        return [{
            "title": ...,
            "url": ...,
            "publish_date": ...,   # BẮT BUỘC
            "effective_date": ..., # = publish_date (hoặc ngày sự kiện)
            "symbols": [...],
            "flags": [...],
        }]
```

### Kill-Switch (P0 cho Live Trading)

```python
class KillSwitch:
    """
    Auto dừng giao dịch khi vi phạm các ngưỡng an toàn.
    Đây là P0 cho live — không có cái này thì không được live.
    """
    
    LIMITS = {
        "max_daily_loss_pct": 0.03,         # Dừng nếu lỗ >3% trong ngày
        "max_drawdown_pct": 0.15,           # Dừng nếu drawdown >15%
        "max_orders_per_minute": 10,        # Rate limit lệnh
        "max_notional_per_order": 500_000_000,  # Max 500tr VND/lệnh
        "stale_data_threshold_minutes": 5,  # Dừng nếu data feed >5 phút cũ
    }
    
    def check_and_trigger(self, portfolio: Portfolio, 
                           market_state: MarketState) -> KillSwitchResult:
        """
        Gọi ở đầu mỗi order cycle.
        Nếu trigger → log, alert, dừng toàn bộ order submission.
        """
```

---

## 7. An Toàn Thực Thi & Live Trading

### Pipeline 3 Giai Đoạn Bắt Buộc

```
Paper Trading  →  Shadow Trading  →  Live (vốn nhỏ)  →  Scale
   ≥ 1-2 tháng      ≥ 1-2 tháng       ≥ 3 tháng
   (backtest        (chạy song song,   (tracking error
   on historical)    fill giả lập,      vs shadow < 2%)
                     đối chiếu fill)
```

**KHÔNG nhảy thẳng từ Paper sang Live.**

### Order Guards

```python
class OrderGuard:
    """
    Mọi lệnh DNSE phải pass qua OrderGuard trước khi submit.
    """
    
    WHITELIST_SYMBOLS = set()  # Danh sách mã được phép giao dịch (cấu hình)
    
    def validate(self, order: Order) -> tuple[bool, str]:
        checks = [
            self._check_whitelist(order),
            self._check_max_notional(order),
            self._check_max_position(order),
            self._check_trading_hours(order),   # Chỉ trong giờ HOSE
            self._check_idempotency(order),      # Tránh double-submit
            self._check_kill_switch(),
        ]
        
        for passed, reason in checks:
            if not passed:
                self._log_blocked_order(order, reason)
                return False, reason
        
        return True, "OK"
    
    def _check_idempotency(self, order: Order) -> tuple[bool, str]:
        """
        Mỗi lệnh có idempotency_key = hash(symbol + side + quantity + date).
        Reject nếu key đã được submit trong session hiện tại.
        """
```

### Reconciliation

```python
class DailyReconciliation:
    """
    Chạy mỗi sáng trước khi thị trường mở.
    Đối soát vị thế/tiền giữa hệ thống và DNSE.
    """
    
    def reconcile(self) -> ReconciliationResult:
        system_positions = self._get_system_positions()
        dnse_positions = self._get_dnse_positions_via_api()
        
        mismatches = self._compare(system_positions, dnse_positions)
        
        if mismatches:
            self._alert(mismatches)          # Telegram/email alert
            self._freeze_new_orders()        # Dừng order mới
            # Human phải xác nhận trước khi resume
        
        return ReconciliationResult(mismatches=mismatches)
```

### Human-in-the-Loop

```python
# Giai đoạn đầu live: MỌI lệnh cần human confirm
class HumanConfirmationGate:
    
    def request_confirmation(self, orders: list[Order], 
                              timeout_seconds: int = 300) -> bool:
        """
        Gửi summary lệnh qua Telegram/email.
        Chờ human approve (reply "OK") trong timeout.
        Nếu timeout → cancel toàn bộ lệnh ngày hôm đó (safe default).
        """
```

---

## 8. LLM Layer — Tái Thiết Kế

### Cắt Xuống 3-5 Workflow Hữu Ích

**Giữ lại:**
1. **News/Qualitative RAG** — đọc tin, phân loại, trích dẫn nguồn → feed vào risk layer
2. **Report Generation** — giải thích quyết định quant cho người đọc (văn bản, có nguồn)
3. **Hypothesis Ideation** — gợi ý factor mới để quant test (human approve trước)

**Xóa bỏ:**
- 29 swarm preset (giữ tối đa 3-5 preset cần thiết)
- Bull/Bear debate flow (không tạo alpha đo được)
- Portfolio Manager LLM (thay bằng deterministic optimizer)
- Aggressive/Conservative/Neutral debaters
- Research Manager LLM

### IntentRouter — Thay Regex Bằng Classifier

```python
class IntentClassifier:
    """
    Thay thế regex thuần bằng embedding + logistic regression.
    Regex làm lớp nhanh (first pass), ML làm lớp verify.
    """
    
    INTENTS = ["CHAT", "RESEARCH", "SIGNAL", "REPORT", "ALERT"]
    CONFIDENCE_THRESHOLD = 0.70  # Dưới ngưỡng → hỏi lại user
    
    def classify(self, text: str) -> IntentResult:
        # Layer 1: Regex (fast path — <1ms)
        regex_intent = self._regex_classify(text)
        if regex_intent and self._regex_confidence(text) > 0.90:
            return IntentResult(intent=regex_intent, confidence=0.90, method="regex")
        
        # Layer 2: Embedding + Logistic (slower — ~50ms)
        embedding = self.embed(text)
        proba = self.classifier.predict_proba([embedding])[0]
        max_proba = proba.max()
        
        if max_proba < self.CONFIDENCE_THRESHOLD:
            return IntentResult(intent="CLARIFY", confidence=max_proba, 
                               clarification_needed=True)
        
        return IntentResult(
            intent=self.INTENTS[proba.argmax()],
            confidence=max_proba,
            method="ml"
        )
```

### Real-Time Signal — Không Dùng LLM

```python
# QUYẾT ĐỊNH: Real-time signal = pure quant, không có LLM trong hot path
# ReAct loop 50 iteration + context compaction ≠ real-time

LATENCY_BUDGET = {
    "hot_path_signal_ms": 100,    # Pure quant: < 100ms
    "cold_path_research_ms": None, # LLM async/EOD: không giới hạn
}

# Hot path (< 100ms):
# factor_composite → risk_gate → signal → order_guard → submit
# Không có LLM call nào trong chuỗi này

# Cold path (async, sau giờ):
# news_rag → report_generation → hypothesis_ideation
```

### Structured Output Validation

```python
# Mọi output ra quyết định phải qua Pydantic schema
class QuantSignal(BaseModel):
    symbol: str
    direction: Literal["BUY", "SELL", "HOLD"]
    factor_composite_score: float      # Đến từ deterministic quant
    calibrated_confidence: float       # Đến từ calibrated model
    entry_price: float                 # Đến từ market data (KHÔNG từ LLM)
    stop_loss: float                   # Đến từ ATR-based risk model
    target_price: Optional[float]      # Đến từ mean-reversion target (KHÔNG từ LLM)
    position_size_pct: float           # Đến từ vol-scaled Kelly
    risk_flags: list[str]              # Đến từ 7-layer risk system
    llm_narrative: Optional[str]       # Diễn giải văn bản (không ảnh hưởng sizing)
    
    @validator("calibrated_confidence")
    def confidence_must_be_calibrated(cls, v):
        assert 0 <= v <= 1, "Confidence phải trong [0, 1]"
        # TODO: thêm check calibration source
        return v
```

---

## 9. MLOps & Hạ Tầng Dữ Liệu

### Feature Store PIT

```python
class PITFeatureStore:
    """
    Một nguồn sự thật cho feature, có versioning và as-of query.
    
    Interface:
    - get_features(symbols, as_of_date) → pd.DataFrame
    - BẢO ĐẢM: không feature nào chứa thông tin sau as_of_date
    """
    
    def get_features(self, 
                      symbols: list[str],
                      as_of_date: date,
                      feature_names: list[str] = None) -> pd.DataFrame:
        """
        Query feature tại as_of_date theo PIT semantics.
        Mọi join với fundamental phải dùng published_date <= as_of_date.
        """
```

### Model Registry

```python
# Dùng MLflow (hoặc lightweight alternative)
# KHÔNG pickle model vào tempdir

class ModelRegistry:
    """
    Lưu: model artifact, params, metrics, dataset hash, commit hash.
    Tái lập: cho cùng commit + dataset → reproduce kết quả.
    """
    
    def save_model(self, 
                    model: Any,
                    params: dict,
                    metrics: dict,
                    dataset_hash: str,
                    commit_hash: str,
                    ) -> str:
        """Returns: model_id để load sau."""
    
    def load_model(self, model_id: str) -> Any:
        """Load model từ registry."""
    
    def compare_runs(self, model_ids: list[str]) -> pd.DataFrame:
        """So sánh metrics giữa các run."""
```

### Data Quality Checks

```python
class DataQualityChecker:
    
    CHECKS = [
        "no_negative_price",           # Giá không âm
        "no_zero_price",               # Giá không = 0
        "no_missing_trading_days",     # Không mất ngày giao dịch
        "volume_anomaly_detection",    # Volume > 10x ADV → suspect
        "price_continuity",            # Không jump >30% không có CA
        "corporate_action_completeness", # CA coverage đủ không
    ]
    
    def run_checks(self, data: pd.DataFrame) -> DataQualityReport:
        """Fail-fast nếu có data quality issue nghiêm trọng."""
```

### Database Migration — SQLite → PostgreSQL

```python
# PaperTrade và SessionLog phải chuyển từ SQLite sang PostgreSQL
# SQLite WAL mode không đủ cho multi-thread heavy workload

# Nếu chưa migrate ngay: bật WAL mode
import sqlite3
conn = sqlite3.connect("app.db")
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")

# Migration script: scripts/migrate_sqlite_to_pg.py
```

### Reproducibility

```python
import random, numpy as np, torch

def set_global_seed(seed: int = 42):
    """Cố định seed cho mọi thư viện."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    # XGBoost: seed trong params
    # sklearn: random_state=seed

class BacktestRun:
    def __init__(self, ...):
        self.seed = 42
        self.commit_hash = subprocess.getoutput("git rev-parse HEAD")
        self.config_snapshot = copy.deepcopy(config)
        # Lưu cùng với results để reproduce
```

---

## 10. Evaluation & Monitoring

### Bỏ LLM-Judge Cho Đánh Giá Tín Hiệu

```python
# LLM chấm 1-10 không đo được tiền
# Giữ LLM-judge CHỈ cho chất lượng văn bản báo cáo

class SignalEvaluator:
    """
    Eval thật = realized PnL.
    """
    
    def evaluate_signal(self, 
                          signal_id: str,
                          realized_return: float,
                          days_held: int) -> SignalEvalResult:
        return SignalEvalResult(
            signal_id=signal_id,
            realized_return=realized_return,
            hit=realized_return > 0,
            # MFE: Maximum Favorable Excursion
            # MAE: Maximum Adverse Excursion
            # Brier: calibration score
        )
    
    def get_performance_by_source(self) -> pd.DataFrame:
        """
        Phân tách performance theo nguồn: factor / ML / DL / composite
        Metrics: hit rate, avg return, Sharpe, Brier, turnover
        """
```

### Alpha Decay & Drift Alerts

```python
class AlphaMonitor:
    
    ALERT_THRESHOLDS = {
        "rolling_ic_drop": 0.02,        # IC rolling 60d giảm dưới 0.02 → alert
        "psi_threshold": 0.25,           # Population Stability Index > 0.25 → drift
        "tracking_error_vs_backtest": 0.05,  # >5% TE → investigate
    }
    
    def check_factor_health(self, factor_name: str) -> HealthStatus:
        """
        Cảnh báo khi:
        - Rolling IC sụt dưới ngưỡng
        - Feature distribution drift (PSI)
        - Live vs backtest tracking error vượt ngưỡng
        """
    
    def check_live_vs_backtest(self) -> dict:
        """
        So sánh live performance vs backtest expectation.
        Nếu live underperform backtest >2 std → investigate leakage.
        """
```

---

## 11. Đặc Thù HOSE Bắt Buộc Biết

> AI coding agent PHẢI hard-code các đặc thù này, không được dùng default từ thị trường khác.

### Biên Độ Dao Động

```python
HOSE_PRICE_LIMITS = {
    "normal": 0.07,              # ±7% giá tham chiếu
    "ipo_first_day": 0.20,       # ±20% ngày chào sàn
    "after_long_suspension": 0.20, # ±20% sau đình chỉ dài
    "gdkhq_adjustment": None,    # Giá tham chiếu điều chỉnh theo CA
}

# Ngưỡng "cận trần" và "cận sàn" để detect risk:
NEAR_CEILING = 0.069   # ≥ +6.9% = cận trần (risk signal)
NEAR_FLOOR = -0.069    # ≤ -6.9% = cận sàn (risk signal)
FLOOR_HIT = -0.070     # = -7% = chạm sàn
```

### Bước Giá HOSE

```python
HOSE_PRICE_STEPS = [
    (10_000,   10),    # Giá ≤ 10,000 VND → bước 10
    (50_000,   50),    # Giá ≤ 50,000 VND → bước 50
    (100_000, 100),    # Giá ≤ 100,000 VND → bước 100
    (200_000, 500),    # Giá ≤ 200,000 VND → bước 500
    (float("inf"), 1_000),  # Giá > 200,000 VND → bước 1,000
]

def snap_to_price_step(price: float) -> float:
    """Làm tròn giá về bước giá hợp lệ (round down cho lệnh mua, round up cho bán)."""
    for limit, step in HOSE_PRICE_STEPS:
        if price <= limit:
            return math.floor(price / step) * step
    return math.floor(price / 1000) * 1000
```

### Phiên Giao Dịch HOSE

```python
HOSE_SESSIONS = {
    "PRE_OPEN":           ("08:30", "09:00"),
    "ATO":                ("09:00", "09:15"),  # Khớp lệnh định kỳ mở cửa
    "CONTINUOUS_MORNING": ("09:15", "11:30"),
    "LUNCH":              ("11:30", "13:00"),
    "CONTINUOUS_PM":      ("13:00", "14:30"),
    "ATC":                ("14:30", "14:45"),  # Khớp lệnh định kỳ đóng cửa
    "CLOSED":             ("14:45", "08:30"),
}

# Điểm khớp cho backtest:
# EOD signal → ATC ngày hôm sau (14:30-14:45)
# Intraday signal → VWAP hoặc CONTINUOUS price
```

### Thanh Toán T+2

```python
# HOSE Cash Account (tài khoản thường):
# Mua T → cổ phiếu về T+2 → bán sớm nhất T+2 (chiều, sau khi settle)
# Thực tế giao dịch: bán sớm nhất là NGÀY T+2 (trading days)

# Margin Account (nếu có):
# Có thể bán T+1 nhưng phải có margin facility — đừng assume

SETTLEMENT_DAYS = 2  # Trading days (không phải calendar days)
```

### Room Ngoại

```python
def check_foreign_room(symbol: str, as_of_date: date) -> dict:
    """
    Một số mã có room ngoại hạn chế (vd max 49% hoặc 30%).
    Khi room ngoại = 0% → khối ngoại không mua được → có thể ảnh hưởng thanh khoản.
    Risk flag: FOREIGN_ROOM_EXHAUSTED
    """
```

### Lịch Nghỉ VN (Bắt Buộc Chính Xác)

```python
# Phải dùng lịch nghỉ chính thức HOSE/SSC hàng năm
# Không thể hardcode — phải có mechanism cập nhật hàng năm
# Tối thiểu: năm 2018-2026 phải đầy đủ

class VNTradingCalendar:
    def is_trading_day(self, date: date) -> bool:
        """Trả về False nếu ngày nghỉ VN hoặc cuối tuần."""
    
    def next_trading_day(self, date: date) -> date:
        """Ngày giao dịch tiếp theo."""
    
    def count_trading_days(self, start: date, end: date) -> int:
        """Đếm ngày giao dịch trong khoảng [start, end]."""
```

---

## 12. Go-Live Checklist

Phải pass TẤT CẢ điều kiện dưới đây trước khi live với tiền thật:

### P0 Checklist
- [ ] Backtest net-of-cost 2018→2024 đánh bại Buy & Hold VN30 ETF (E1VFVN30)
- [ ] Net Sharpe > 1.0 (sau phí, sau thuế, sau slippage)
- [ ] Max Drawdown ≤ 30%
- [ ] Deflated Sharpe Ratio > 0
- [ ] Probability of Backtest Overfitting (PBO) < 50%
- [ ] Pass qua các downturn: 2018 (VN-Index -10%), 2020 (COVID), 2022 (-35%)
- [ ] Purged Walk-Forward CV test: không có leakage (unit test green)
- [ ] PIT fundamentals: không có look-ahead trong factor value/quality
- [ ] Adjusted prices: dùng adj_close cho mọi tính toán
- [ ] Corporate actions đầy đủ 2018-nay
- [ ] Survivorship-free database: có mã hủy niêm yết
- [ ] Confidence là calibrated probability (Brier score < 0.25)
- [ ] Reliability diagram: calibration curve gần đường 45°
- [ ] LLM không sinh target_price / stop_loss / position_size / BUY/SELL signal
- [ ] LLMOutputGuardrail: mọi số trong LLM output có trích dẫn tool data
- [ ] Kill-switch hoạt động (test: trigger và dừng order thành công)
- [ ] Order guards: idempotency, whitelist, max notional (test: double-submit bị chặn)
- [ ] Reconciliation DNSE hoạt động (test: phát hiện mismatch)
- [ ] All LLM model IDs verified (HTTP 200 từ provider)

### P1 Checklist
- [ ] Walk-forward IC weights (không dùng full-sample IC)
- [ ] Portfolio optimizer có covariance (Ledoit-Wolf)
- [ ] Sector concentration limit ≤ 25%
- [ ] Volatility targeting: scale exposure về target vol
- [ ] Beta hedge mechanism (VN30F1M hoặc cash)
- [ ] Risk thresholds calibrated từ dữ liệu HOSE (không copy từ Mỹ)
- [ ] Alpha decay monitoring: rolling IC alert
- [ ] Feature drift monitoring: PSI alert
- [ ] Model registry: reproducible runs
- [ ] SQLite → PostgreSQL migration (hoặc WAL mode)
- [ ] IntentRouter: embedding classifier thay regex
- [ ] Hot path (signal) < 100ms (không có LLM)

### Paper → Shadow → Live
- [ ] Paper trading ≥ 1-2 tháng (logic đúng, không bug)
- [ ] Shadow trading ≥ 1-2 tháng (tracking error vs backtest < 5%)
- [ ] Live (vốn nhỏ, ~50-100 triệu) ≥ 3 tháng
- [ ] Human confirm mọi lệnh trong giai đoạn đầu live
- [ ] Daily reconciliation DNSE: không có mismatch ≥ 2 ngày liên tiếp

---

## 13. Cấu Trúc Thư Mục Đề Xuất

```
app/
├── brain/
│   ├── agents/          # Giảm xuống 3-5 agent workflow
│   ├── providers/       # LLM clients (giữ nguyên, fix model ID)
│   ├── state/           # EventBus, SessionService (giữ nguyên)
│   ├── memory/          # Cross-session memory (giữ nguyên)
│   └── risk/            # 7-layer risk (calibrate thresholds)
│
├── quant/
│   ├── factors/
│   │   ├── vn_ic_tester.py          # Fix PIT, adj_close
│   │   ├── factor_analysis_core.py  # Giữ nguyên
│   │   ├── sector_neutralizer.py    # Giữ nguyên
│   │   └── factor_orthogonalization.py
│   ├── validation/
│   │   ├── purged_cv.py             # MỚI — P0.1
│   │   └── test_purged_cv.py        # Unit tests
│   ├── pipeline.py                  # Fix: fit normalization trên train only
│   └── composite_pipeline.py        # Fix: walk-forward IC weights
│
├── backtest/
│   ├── engine.py                    # MỚI — event-driven PIT engine
│   ├── cost_model.py                # MỚI — P0.4
│   ├── execution.py                 # MỚI — T+2 model, lock limit
│   ├── metrics.py                   # MỚI — full metric suite
│   └── test_*.py
│
├── portfolio/
│   ├── optimizer.py                 # MỚI — mean-variance + constraints
│   ├── risk_model.py                # MỚI — Ledoit-Wolf covariance
│   ├── sizing.py                    # MỚI — vol-scaled Kelly
│   └── hedge.py                     # MỚI — beta hedge logic
│
├── ml/
│   ├── alpha_predictor.py           # Fix: temporal split, no shuffle
│   ├── calibration.py               # MỚI — Platt/Isotonic calibration
│   └── model_registry.py            # MỚI — MLflow/lightweight registry
│
├── live/
│   ├── order_guard.py               # MỚI — P0 live safety
│   ├── kill_switch.py               # MỚI — circuit breaker
│   ├── reconciliation.py            # MỚI — daily DNSE reconcile
│   └── paper_shadow_gate.py         # MỚI — 3-stage pipeline
│
├── data/
│   ├── feature_store.py             # MỚI — PIT feature store
│   ├── corporate_actions.py         # MỚI — CA pipeline
│   ├── trading_calendar.py          # MỚI — VN holiday calendar
│   └── quality_checker.py           # MỚI — data quality checks
│
├── llm/
│   ├── guardrail.py                 # MỚI — output validation
│   ├── intent_classifier.py         # Update — embedding + logistic
│   ├── news_rag.py                  # Giữ nguyên (lightweight TF-IDF)
│   └── report_generator.py          # Giữ, đây là 1 trong 3 workflow hữu ích
│
└── monitoring/
    ├── alpha_monitor.py             # MỚI — decay & drift alerts
    ├── signal_evaluator.py          # Update — realized PnL eval
    └── dashboard.py                 # Attribution dashboard
```

---

## Triết Lý Cuối

> Trên HOSE, lợi nhuận bền đến từ:
> 1. **Edge định lượng nhỏ nhưng thật** — đã trừ phí, đã test leakage
> 2. **Quản trị rủi ro** — sống sót qua downturn 2018, 2020, 2022
> 3. **Kỷ luật thực thi** — T+2, lot size, lock limit, không bán khi sàn
> 
> **Không đến từ** 29 con agent LLM tranh luận bull/bear.
> 
> **Đo lường tàn nhẫn.** Chỉ scale cái đã chứng minh bằng tiền thật.

---

*Tài liệu này tổng hợp từ AI_BRAIN_DOCUMENTATION.md (v2.0) và AIInvest AI Engine v3.0 Spec.  
Phiên bản: 3.0 | Ngày: 2026-06-13 | Chỉ áp dụng cho HOSE/HSX.*