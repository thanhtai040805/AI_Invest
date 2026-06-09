# HOSE Factor Engine — Technical Specification (Việt Nam)

> **Phiên bản:** 1.0  
> **Phạm vi:** Tiền xử lý factor, xử lý dữ liệu khuyết, pipeline 3 bước, đồng bộ trục thời gian  
> **Áp dụng:** Sàn HOSE (HSX)

---

## 1. Factor Formulas — Điều Chỉnh Cho HOSE

### 1.1 VOL_20D_ORTHO (Trực giao hóa biến động ngắn hạn)

**Vấn đề:** `VOL_20D` và `VOL_60D` tương quan Spearman > 0.85 → đa cộng tuyến mạnh khi dùng đồng thời.

**Giải pháp:** Cross-sectional OLS Regression tại mỗi ngày $t$:

$$\text{VOL\_20D}_{i,t} = \alpha_t + \beta_t \cdot \text{VOL\_60D}_{i,t} + \epsilon_{i,t}$$

- $\text{VOL\_20D}_{i,t} = \sigma(R_{i, t-19:t}) \times \sqrt{252}$ — độ lệch chuẩn tỷ suất sinh lời 20 phiên
- $\text{VOL\_60D}_{i,t} = \sigma(R_{i, t-59:t}) \times \sqrt{252}$ — độ lệch chuẩn tỷ suất sinh lời 60 phiên
- $\epsilon_{i,t}$ = phần dư → **giá trị factor lưu trữ**: $\text{VOL\_20D\_ORTHO}_{i,t}$

**Implementation mapping (existing):** `factor_orthogonalization.py` dùng Gram-Schmidt/PCA giữa các factor, chưa có cross-sectional OLS riêng cho cặp VOL_20D / VOL_60D. Cần bổ sung.

**Implementation mapping (proposed):** File mới `factors/vol_ortho.py` hoặc mở rộng `factors/factor_orthogonalization.py`:

```python
# Pseudocode — cross-sectional orthogonalization
def orthogonalize_vol_20d(panel_at_t: pd.DataFrame) -> pd.Series:
    """
    panel_at_t: DataFrame with columns ['VOL_20D', 'VOL_60D'], index = symbols
    Returns: Series of residuals (VOL_20D_ORTHO)
    """
    from scipy import stats
    x = panel_at_t['VOL_60D'].values
    y = panel_at_t['VOL_20D'].values
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 10:
        return panel_at_t['VOL_20D']  # fallback
    slope, intercept, _, _, _ = stats.linregress(x[mask], y[mask])
    resid = y - (intercept + slope * x)
    return pd.Series(resid, index=panel_at_t.index)
```

### 1.2 CEILING_STREAK — Đếm chuỗi trần HOSE

**Quy tắc HOSE:** Biên độ ±7%, bước giá (price step) theo vùng giá:
- Giá ≤ 10,000 VND: bước giá 10 VND
- 10,000 < giá ≤ 50,000: bước giá 50 VND
- 50,000 < giá ≤ 100,000: bước giá 100 VND
- Giá > 100,000: bước giá 500 VND

**Công thức ceiling price động:**

$$P_{\text{ceil},t} = \text{floor}\left(\frac{P_{t-1} \times 1.07}{\text{step}}\right) \times \text{step}$$

**Logic streak:**

$$\text{Streak}_{i,t} = 
\begin{cases} 
\text{Streak}_{i,t-1} + 1 & \text{nếu } R_{i,t} \ge 6.5\% \text{ và } P_{i,t} = P_{\text{ceil},i,t} \\
0 & \text{nếu ngược lại}
\end{cases}$$

**Implementation mapping (current):** `vn_ic_tester.py:462-467` — dùng `stocks.ceiling` (tĩnh) và đếm số phiên đóng cửa ≥ ceiling trong 10 ngày / 10. Chưa đúng với spec vì:
1. Ceiling price là động theo giá hôm trước, không phải giá trị tĩnh từ DB
2. Threshold 6.5% (không phải 7% do làm tròn)
3. Cần logic streak đếm liên tiếp, reset về 0 khi không đủ điều kiện

**Implementation mapping (proposed):** Sửa trong `vn_ic_tester.py`:

```python
def _ceiling_price(prev_close: float) -> float:
    step = (10 if prev_close <= 10_000
            else 50 if prev_close <= 50_000
            else 100 if prev_close <= 100_000
            else 500)
    raw_ceil = prev_close * 1.07
    return math.floor(raw_ceil / step) * step

def compute_ceiling_streak(closes: np.ndarray) -> int:
    streak = 0
    for i in range(len(closes) - 1, 0, -1):
        prev = closes[i - 1]
        p_ceil = _ceiling_price(prev)
        ret = (closes[i] / prev) - 1
        if ret >= 0.065 and abs(closes[i] - p_ceil) / p_ceil < 0.001:
            streak += 1
        else:
            break
    return streak
```

### 1.3 ROE_NORM & NM — Chuẩn hóa TTM (Trailing Twelve Months)

**Vấn đề hiện tại:** Code hiện dùng `financial_ratios.roe` và `financial_ratios.net_margin` trực tiếp (latest value), không qua TTM accumulation → nhiễm mùa vụ BCTC.

**Công thức TTM:**

$$\text{LNST\_TTM}_{i,t} = \sum_{q=0}^{3} \text{LNST\_cMẹ}^{(t-q)}$$

$$\text{DT\_TTM}_{i,t} = \sum_{q=0}^{3} \text{DT\_thuần}^{(t-q)}$$

$$\text{ROE}_{i,t} = \frac{\text{LNST\_TTM}_{i,t}}{\text{VCSH}_{i,t}}$$

$$\text{NM}_{i,t} = \frac{\text{LNST\_TTM}_{i,t}}{\text{DT\_TTM}_{i,t}}$$

**Implementation mapping (current):** `vn_ic_tester.py:404-419` — đọc `roe`, `nm` từ `financial_ratios` (single-period). Cần chuyển sang đọc từ `financial_statements` (các báo cáo quý) và tự tổng hợp TTM.

**Implementation mapping (proposed):** Thêm trong `vn_ic_tester.py` hoặc service riêng:

```python
def compute_ttm_roe_nm(fs_quarters: list[dict], equity: float) -> tuple[float, float]:
    """
    fs_quarters: list of 4 quarterly financial statements (IS + BS)
    Returns (roe, net_margin)
    """
    if len(fs_quarters) < 4:
        return (None, None)
    recent = fs_quarters[:4]
    total_ni = sum(q.get('net_income', 0) or 0 for q in recent)
    total_rev = sum(q.get('revenue', 0) or 0 for q in recent)
    roe = total_ni / equity if equity and equity > 0 else None
    nm = total_ni / total_rev if total_rev and total_rev > 0 else None
    return (roe, nm)
```

---

## 2. Missing Data Matrix — Xử Lý Dữ Liệu Trống

| Tình huống | Nguyên nhân | Giải pháp | Trạng thái code |
|---|---|---|---|
| **GM, EVEBITDA_INV** khuyết cho Ngân hàng, CK, Bảo hiểm | Đặc thù ngành tài chính: không có GVHB/Doanh thu thuần truyền thống | Giữ `NaN`, không điền 0/median. Sector-neutralizer tự bỏ qua khi tính Z-score | ✅ `sector_neutralizer.py` — `skip_sectors` config field (hiện chưa dùng cho BANK) |
| **ROE_NORM, NM, ACCRUAL, CFO_TO_NI** khuyết do chậm BCTC | Trễ hạn nộp báo cáo (>60 ngày) | Forward fill (`ffill`) từ quý gần nhất. Nếu >2 quý (180 ngày) trống → `NaN` + tag thanh lọc | ⚠️ `pipeline.py:impute_panel()` dùng ffill toàn bảng, chưa có cơ chế timeout 180 ngày |
| Khuyết chỉ số giá (SIZE, VOL, MOM) do thiếu dữ liệu OHLCV | Mã mới niêm yếu chưa đủ 60 phiên | NaN tự nhiên → lọc khỏi universe ngày đó qua `compute_factors_at()` | ✅ `vn_ic_tester.py:298-301` — skip nếu <20 rows |
| **FOREIGN_NET_5D** khuyết | Phiên đó khối ngoại không giao dịch mã này | Zero-fill: không mua/bán → tác động ròng = 0 | ⚠️ `vn_ic_tester.py:478-483` — xử lý qua mcap, chưa zero-fill rõ ràng |
| **CEILING_STREAK** khuyết | Mã mới hoặc chưa có dữ liệu ceiling | NaN → skip factor | ✅ |
| **F_SCORE, ALTMAN_Z, ACCRUAL** khuyết cho ngành Tài chính | Cấu trúc BCTC khác biệt | NaN, sector-neutralizer dùng `skip_sectors` | ⚠️ Cần thêm `skip_sectors=["BANKS", "FINANCIAL_SERVICES"]` config |

---

## 3. Pre-processing Pipeline — 3 Bước Chuẩn Hóa

Pipeline này áp dụng cho **mỗi factor riêng lẻ** tại **mỗi ngày giao dịch** $t$, đã được triển khai trong `pipeline.py` và `sector_neutralizer.py`. Dưới đây là đối chiếu giữa spec và hiện trạng:

### Bước 1: Filter Universe (Lọc vũ trụ đầu tư)

| Spec | Trạng thái code |
|---|---|
| Chỉ chọn mã HOSE | ✅ `get_symbols_at()` lọc từ OHLCV |
| Lọc 20% mã thanh khoản thấp nhất (giá trị GD trung bình 20 phiên) | ✅ `_liquidity_filter()` — giữ mã ≥ 5 tỷ VND/ngày |
| Loại mã có nguy cơ thanh lọc (>2 quý không có BCTC) | ❌ **Chưa implement** — cần thêm bước kiểm tra `CHAM_BAO_TC` flag trước khi vào pipeline |

**Cần bổ sung:** Thêm step check `CANH_BAO_TC` / `CHAM_BAO_TC` từ `risk_flags_v2.py` vào pipeline filter.

### Bước 2: Cross-sectional Winsorization

| Spec | Trạng thái code |
|---|---|
| Tính phân vị 1% và 99% trên toàn sàn (từng ngày) | ✅ `winsorize_panel()` trong `pipeline.py` |
| Per-sector winsorize override (ví dụ: BANKS + ROE_NORM dùng 5%-95%) | ✅ `sector_neutralizer.py` — `KNOWN_FACTOR_CONFIGS` sector_overrides |
| Binary factors (TET_WINDOW, FORCED_SELLING): pass-through | ✅ `sector_neutralizer.py` — distribution detection |
| Discrete ordinal (CEILING_STREAK, PIOTROSKI_F): rank-normalize | ✅ `sector_neutralizer.py` |

### Bước 3: Sector Neutralization + Z-Score

**Spec:** Hồi quy OLS với sector dummy, lấy residual, chuẩn hóa Z-score.

**State diagram:**
```
Raw_Factor
    │
    ▼
[Winsorize per-sector]
    │
    ▼
[Group by sector]
    │
    ├─ n < 4 valid obs → OTHER_INDUSTRIALS (auto-reroute)
    │
    ▼
[Z-score within sector]
    ├─ std = 0 → Z = 0
    ├─ n < min_valid → Z = 0
    └─ normal → Z = (x - μ) / σ
    │
    ▼
[Percentile rank within sector → [0, 100]]
    │
    ▼
Final_Factor_Rank
```

| Spec | Trạng thái code |
|---|---|
| Sector Z-score (defensive loop) | ✅ `_compute_sector_zscore()` trong `sector_neutralizer.py` |
| Sector auto-reroute (n<4 → OTHER_INDUSTRIALS) | ✅ `sector_neutralizer.py` |
| Rank to [0, 100] | ✅ `rank_within_sector()` trong `sector_neutralizer.py` |
| **Look-ahead bias protection** | ❌ **Chưa implement** — cần release_date alignment |

---

## 4. Look-Ahead Bias Prevention

**Vấn đề hiện tại:** Code dùng `ratio_date <= dt` và `DISTINCT ON ... ORDER BY ratio_date DESC` để lấy BCTC mới nhất. Điều này vi phạm look-ahead bias nếu `ratio_date` là ngày kết thúc quý chứ không phải ngày công bố.

**Ví dụ:**
- Q4/2025 kết thúc 31/12/2025
- BCTC công bố 25/01/2026
- Với code hiện tại, nếu tính factor tại ngày 05/01/2026, đã lấy được số Q4/2025 — sai!

### Giải pháp: Release Date Alignment

**Cấu trúc dữ liệu cần thêm:**

```sql
ALTER TABLE financial_statements ADD COLUMN release_date date;
```

Hoặc nếu không có `release_date`, dùng rule-based heuristic:
- BCTC Quý: release = period_end + 20 ngày làm việc (tối đa)
- BCTN (báo cáo năm): release = period_end + 30 ngày làm việc
- Nếu chưa đủ thời gian → giữ nguyên dữ liệu quý trước

**Logic pipeline khi tính factor:**

```python
def get_latest_financials(symbol: str, eval_date: date) -> dict:
    """
    Chỉ lấy báo cáo tài chính có release_date <= eval_date.
    Nếu không có release_date, dùng heuristic:
      - Nếu eval_date - period_end < 20 ngày (quý) hoặc < 30 ngày (năm)
        → skip báo cáo đó, dùng báo cáo cũ hơn
    """
    # Forward-fill logic
    available = [
        fs for fs in all_fs[symbol]
        if _effective_date(fs) <= eval_date
    ]
    return available[0] if available else None
```

### Impact Analysis

| Factor | Mức độ ảnh hưởng | Mô tả |
|---|---|---|
| ROE_NORM | **CRITICAL** | Dùng LNST và VCSH từ BCTC |
| NM | **CRITICAL** | Dùng LNST và Doanh thu từ BCTC |
| PE_INV, PB_INV | **HIGH** | Dùng từ financial_ratios (nguồn: BCTC) |
| EARN_YLD, FCF_YLD | **HIGH** | Dùng từ financial_ratios |
| ACCRUAL | **CRITICAL** | Dùng BS items từ BCTC |
| CFO_TO_NI | **CRITICAL** | Dùng CFO từ CF + NI từ IS |
| ALTMAN_Z | **HIGH** | Dùng 5 biến từ BS + IS |
| PIOTROSKI_F | **MEDIUM** | Dùng ROE + mcap |
| MOM, VOL, AMIHUD | **NONE** | Chỉ dùng OHLCV (không BCTC) |

---

## 5. Gap Analysis — Tổng hợp việc cần làm

### Priority Map

| # | Hạng mục | File ảnh hưởng | Mức ưu tiên |
|---|---|---|---|
| 1 | Thêm `VOL_20D_ORTHO` — cross-sectional OLS cho cặp VOL_20D / VOL_60D | `factors/factor_orthogonalization.py` hoặc file mới | **P1** |
| 2 | Sửa `CEILING_STREAK` — ceiling động theo bước giá HOSE | `vn_ic_tester.py:462-467` | **P1** |
| 3 | Thêm skip_sectors config cho BANKS, FINANCIAL_SERVICES | `sector_neutralizer.py:KNOWN_FACTOR_CONFIGS` | **P1** |
| 4 | Look-ahead bias: release_date alignment cho BCTC | `vn_ic_tester.py`, `financial_ratios` queries | **P0** |
| 5 | TTM accumulation cho ROE_NORM, NM | `vn_ic_tester.py:404-419` | **P1** |
| 6 | Filter universe: thêm check CHAM_BAO_TC | `pipeline.py` + `risk_flags_v2.py` | **P2** |
| 7 | Forward fill timeout (180 ngày) | `pipeline.py:impute_panel()` | **P2** |
| 8 | Zero-fill cho FOREIGN_NET_5D | `vn_ic_tester.py:478-483` | **P2** |

### Sơ đồ luồng dữ liệu tổng thể (proposed)

```
OHLCV (DB)
   │
   ▼
compute_factors_at()        ◄── financial_statements (release_date aligned)
   │                              │
   ▼                              ▼
Raw Factor Panel            TTM Accumulator (ROE_NORM, NM)
   │                              │
   ▼                              ▼
Bước 1: Filter Universe     Sector Classification
   │    ├─ HOSE only               │
   │    ├─ Liquidity 20% trim      ▼
   │    └─ Risk flag check    Sector Map
   ▼         │
Bước 2: Winsorize
   │    ├─ Per-sector thresholds
   │    ├─ Binary → pass-through
   │    └─ Discrete → rank
   ▼
Bước 3: Sector Z-score → Rank [0, 100]
   │
   ▼
IC Tester (Spearman rank IC)
```

---

## 6. Tham chiếu module

| Module | Vai trò trong pipeline |
|---|---|
| `quant/pipeline.py` | Impute → Winsorize → Normalize (generic panel) |
| `quant/factors/sector_neutralizer.py` | Phân phối factor, winsorize per-sector, sector Z-score, rank transform |
| `quant/factors/factor_orthogonalization.py` | Orthogonalization giữa các factor (Gram-Schmidt, PCA, cluster avg) |
| `quant/factors/vn_ic_tester.py` | IC benchmark chính: data loading, factor compute, sector map, IC scoring |
| `quant/factors/__init__.py` | Namespace |
| `services/risk_flags_v2.py` | Computed risk flags (CHAM_BAO_TC, CANH_BAO_TC) |
| `dataflows/vendors/vn/sector_groups.py` | 16 ICB sector groups cho VN |
