# AIINVEST — TÀI LIỆU GIỚI THIỆU DỰ ÁN
## Góc nhìn kép: Broker (Nghiệp vụ môi giới & phân tích) + Chuyên gia Kinh tế (Mô hình, hiệu quả, kinh tế học)
> Mục đích: nguồn duy nhất để dựng slide thuyết trình giới thiệu dự án (pitching / gọi vốn / demo khách hàng).
> Phạm vi: `SAG\` (Financial Evidence Engine) + `ai-engine\` (Autonomous Quant Organization). Tổng < 1000 dòng.

---

## 1. TÓM TẮT ĐIỀU HÀNH (EXECUTIVE SUMMARY — 60 giây)

**AIInvest = Tổ chức đầu tư tự hành cho thị trường HOSE**, gồm 2 trụ cột bổ trợ:

| Trụ cột | Tên | Vai trò 1 câu |
|---|---|---|
| **Bộ não bằng chứng** | **SAG v2 — Financial Evidence Engine (MOAT/GIL)** | Đọc BCTC/Báo cáo quản trị, bóc tách Fact/Relation/MoatSignal có trích dẫn tới từng dòng, chấm lợi thế cạnh tranh (MOAT) và cảnh báo gian lận/dòng vốn (GIL). |
| **Cỗ máy hành động** | **ai-engine — Autonomous Investment Organization (IOS v5.1)** | 12 AI-Agents vận hành chu trình đầu tư khép kín: quan sát thị trường → lọc cổ phiếu → nghiên cứu → luận điểm → phản biện → phân bổ vốn → kiểm soát rủi ro → khớp lệnh → giám sát → tự học. |

**Công thức giá trị:** `Bằng chứng kiểm chứng được (SAG) + Kỷ luật định lượng có Hiến pháp (ai-engine) = Alpha bền vững trên HOSE, tuân thủ T+2.5.`

**Con số headline (walk-forward OOS 5–7 năm, 1652 phiên HOSE, có trong `ai-engine/docs/`):**
- Win-rate: **64–74%** (tùy tier/filter).
- Alpha 5 ngày: **+1.0–1.47%** (~55–73%/năm khi annual hoá).
- Sharpe: **2.0–2.45**.
- Bear-market: **100% cash** (dual-book production, EXP-017).

---

## 2. VẤN ĐỀ THỊ TRƯỜNG (TẠI SAO CẦN AIINVEST?)

### 2.1. Góc nhìn Broker (người làm nghề môi giới)
1. **Quá tải thông tin, thiếu bằng chứng:** Mỗi quý hàng trăm BCTC PDF hàng trăm trang. Broker/KH cá nhân không thể đọc hết, dễ dính tin đồn, "phím hàng" không kiểm chứng.
2. **Báo cáo phân tích thiếu truy vết:** Báo cáo CTCK thường đưa kết luận mà không chỉ rõ số liệu nằm ở trang/dòng nào → KH khó tin, khó đối chất.
3. **Tư vấn cảm tính, không kỷ luật cắt lỗ/chốt lời:** Không có quy tắc sizing, stop-loss, giới hạn ngành → cháy NAV khi thị trường đảo chiều.
4. **Rủi ro gian lận BCTC & giao dịch nội gián/sân sau:** Vụ việc pha loãng, công ty sân sau, bảo lãnh chéo — broker thiếu công cụ forensic để cảnh báo sớm.

### 2.2. Góc nhìn Kinh tế (người làm mô hình)
1. **HOSE có vi cấu trúc đặc thù:** Biên độ ±7%, lô 100, T+2.5, ATC, nghẽn thanh khoản, méo mó VN30 — mô hình ngoại nhập bê nguyên xi sẽ sai.
2. **Alpha suy giảm nếu chỉ dùng giá:** Cần kết hợp đa因子: kỹ thuật + cơ bản + dòng tiền ngoại/nội gián + quản trị + vĩ mô + hành vi bầy đàn.
3. **Overfitting là kẻ thù số 1:** Nhiều quant fund khoe backtest đẹp nhưng không walk-forward, không tính phí/thuế/trượt giá → live vỡ trận.
4. **Thiếu cơ chế quản trị vốn cấp hiến pháp:** Không giới hạn %/mã/ngành, không phân biệt tiền thật/shadow, không ledger kiểm toán → không thể scale thành quỹ.

---

## 3. GIẢI PHÁP TỔNG THỂ

```
BCTC PDF / CafeF / Vietstock / DNSE OHLCV / Insider / Foreign / Macro SBV
        │                    │
        ▼                    ▼
 ┌─────────────┐     ┌──────────────────┐
 │ SAG v2      │────▶│ ai-engine        │────▶ Lệnh / Danh mục / Cảnh báo
 │ Evidence    │ Moat│ 12 Agents + ML   │ Dual-book
 │ + GIL flag  │+GIL │ + Hiến pháp 4 Điều│
 └─────────────┘     └──────────────────┘
        │                    │
        └────▶ UI: Evidence cards, Graph 3D, Chat có trích dẫn
```

- **SAG trả lời câu hỏi "SỰ THẬT là gì, ở đâu?"** — mọi con số đều có `line_span + quote_hash + document_role`.
- **ai-engine trả lời "HÀNH ĐỘNG gì, bao nhiêu, khi nào?"** — mọi lệnh đều qua Thesis → Counter-Thesis → CIO → Kelly → Risk → Execution.
- Hai hệ nối qua `sag_connector.py` (typed client SAG v2: moat/gil/ingest/upload/tree) và pipeline `bctc_to_sag_pipeline.py` (OCR MinerU → SAG → GIL flag vào DB).

---

## 4. TRỤ CỘT 1: SAG — FINANCIAL EVIDENCE ENGINE

### 4.1. Định vị (Broker nói với KH)
> "Thay vì đọc 300 trang BCTC HPG, bạn hỏi SAG và nhận câu trả lời kèm đúng trang/dòng trích dẫn. Mọi nhận định MOAT/GIL đều truy vết được."

- Tên sản phẩm: `SAG`, trợ lý mặc định `Zleap` (`apps/api/sag_api/branding.py`).
- Slogan kỹ thuật: `SAG v2 financial evidence engine for MOAT/GIL` (`pyproject.toml`).
- Ngôn ngữ chính: **Tiếng Việt** (mặc định `vi-VN`, timezone `Asia/Ho_Chi_Minh`), hỗ trợ en/zh.

### 4.2. Mô hình "Bộ 3 tài liệu active" — điểm khác biệt cốt lõi
Mỗi mã (VD: HPG) chỉ duy trì **đúng 3 document active** (unique partial constraint trong DB):
1. `ANNUAL_BACKBONE` — xương sống năm (toàn cảnh tài chính).
2. `LATEST_QUARTER` — quý mới nhất (xung lực).
3. `GOVERNANCE_REPORT` — quản trị (rủi ro con người/sân sau).

Lợi ích kinh tế: giảm nhiễu, giảm chi phí embedding/LLM, đảm bảo so sánh cùng kỳ, tránh "rác tri thức" như RAG thông thường.

### 4.3. Ontology tài chính Việt Nam (~860 dòng, `sag/financial_ontology.py`)
- **~45 loại thực thể:** TICKER, FVTPL, LOAN_PORTFOLIO, phân loại nợ nhóm 1–5, SUBSIDIARY_AFFILIATE, RELATED_PARTY, CAPITAL_EVENT…
- **~35 loại sự kiện:** REVENUE_EBITDA_SHOCK, RELATED_PARTY_TRANSACTION, DIVESTITURE, MARGIN_LENDING_CHANGE…
- **Chuẩn hoá tên:** `Hòa Phát→HPG, Vietcombank→VCB…` (`CANONICAL_TICKER_ALIASES`).
- Tự suy luận `doc_type` từ tên file → giảm thao tác tay cho broker.

Ý nghĩa cho slide: **đây không phải chatbot generic — là "ngôn ngữ mẹ đẻ" của BCTC Việt Nam.**

### 4.4. Pipeline ingest → evidence (chuẩn kiểm toán)
`Upload PDF/MD → canonicalize_markdown → dedup sha256 → Document QUEUED → Job process_document → parse_markdown_tree (DocumentTreeNode) → extraction LLM JSON manifest (prompt `prompts/extract.yaml v3.1` + ontology) → validate_persist (Entity/Fact/Relation/Moat/EvidenceSpan) → embedding chunks → READY`

- Mỗi Fact có `semantic_key + value_numeric + unit + currency + period + validation`.
- Mỗi Relation có `subject/object/amount_vnd/ownership_pct`.
- Mỗi MoatSignal có `pillar/direction/strength/durability`.
- Mỗi trích dẫn có `quote_hash + char span + line_span` → chống bịa số (anti-hallucination).
- `ReviewQueue`: Relation phải duyệt `OPEN → VALIDATED` mới dùng cho báo cáo chính thức → broker kiểm soát chất lượng.
- `ProcessingRun` có lease/heartbeat/idempotency, retry 3 lần → vận hành ổn định.

### 4.5. MOAT & GIL — hai "điểm chấm" mà broker cần nhất
- **MOAT (5 pillars):** đánh giá lợi thế cạnh tranh, score + multiplier + coverage, cache trong `AssessmentRun`.
- **GIL (Gian lận / dòng vốn / liên quan):** soi equity, giao dịch bên liên quan (RPT), chu kỳ tăng vốn, bảo lãnh → kết quả `PASS / WARNING / CATASTROPHIC`.
- UI v2: upload theo ticker, cây document, `search evidence top_k=8`, `evidence-graph (facts+relations)`, nút `assess MOAT/GIL`.

### 4.6. V1 Legacy đi kèm (tài sản sẵn có)
- Knowledge Base `Source → Document → Chunk`, chat Agent multi-turn có citation, search `vector (nhanh) / multi (entity-expand + LLM rerank)` stream SSE.
- Universe 3D (`3d-force-graph/three`, LOD desktop/mobile), MCP cho Claude/Cursor, Dify retrieval, OpenAI-compatible `/chat/completions`.
- Auth JWT 7 ngày, Jobs, Activity log, i18n 3 locale, diagnostics `X-Request-Id`.

### 4.7. Công nghệ SAG (để slide "Tech Stack" chính xác)
- Backend: Python ≥3.11, FastAPI, SQLAlchemy asyncio, Alembic, Pydantic v2, Litellm (multi-provider: OpenAI/Anthropic/Gemini/302ai/SiliconFlow).
- DB dev: SQLite WAL; prod: PostgreSQL + pgvector + `unaccent/pg_trgm`, storage S3/R2.
- LLM dev: DeepSeek-V3; prod: DeepSeek-V4-Flash; Extraction: Qwen2.5-7B; Embedding: bge-large-en-v1.5 (dev) / Qwen3-Embedding-8B 4096d (prod).
- Parse PDF: MinerU 2.5/4.0 (VLM/OCR) ưu tiên, fallback MarkItDown.
- Frontend: Next.js 15 standalone + React 19 + Tailwind + Radix + react-markdown, graph 3D.

---

## 5. TRỤ CỘT 2: AI-ENGINE — TỔ CHỨC ĐẦU TƯ TỰ HÀNH

### 5.1. Định vị (Kinh tế học)
> "Một quỹ quant thu nhỏ, tự vận hành, có hiến pháp, có nhật ký kiểm toán SHA-256, tuân thủ T+2.5 và vi cấu trúc HOSE."

- Kiến trúc: **Hexagonal** (domain/application/infrastructure/adapters/presentation) + **Event-driven** (RabbitMQ topic `aiinvest.events` + DLX/DLQ + in-memory fallback).
- Chế độ: `MULTI_AGENT_MODE=SHADOW_RUNNER`, `STANDALONE_ML_MODE=LIVE`, NAV khởi tạo 1 tỷ + 500 triệu (cấu hình `.env.example`).
- Hiến pháp 4 Điều (luật cứng trong `rules/hard_laws.py` + `failsafe.py` + governance ledger).

### 5.2. Hiến pháp 4 Điều (slide "Risk Constitution" — broker rất thích)
1. **Điều 1 — Sinh tồn:** rủi ro ≤2% NAV/lệnh, tính floor-gap T+2.5 (13.51%), tail-risk, CDC.
2. **Điều 2 — (quản trị/universe):** chỉ giao dịch universe đạt chuẩn thanh khoản + Beneish.
3. **Điều 3 — Bằng chứng:** luận điểm cần 3 tín hiệu độc lập + pre-mortem.
4. **Điều 4 — Tập trung:** ≤15%/mã, ≤35%/ngành, `weight_cap` của CIO.

### 5.3. 12 Agents (tổ chức đầu tư)
| # | Agent | Việc | File gợi ý |
|---|---|---|---|
| 01 | market_surveillance | HMM regime, VIX_VN, halted, distribution days, breadth | market/hmm_regime_engine |
| 02 | universe_discovery | Layer0 Beneish, ADTV20, GIL | rules/beneish |
| 03 | equity_research | F1–F6 + Moat AI → CSS/Conviction A+/A/B | services/factor_service |
| 04 | investment_thesis | 3 tín hiệu độc lập + pre-mortem | rules/thesis_engine |
| 05 | counter_thesis | Devil Advocate, CTS, liquidity trap | rules/counter_thesis |
| 06 | portfolio_risk | Điều 1, tail-risk, CDC, T+2.5 | rules/risk/* |
| 07 | portfolio_allocation | Bayesian Kelly sizing + Điều 4 | portfolio/kelly_engine |
| 08 | trade_execution | EAE VWAP slicing LIVE/SHADOW | rules/execution/eae |
| 09 | position_monitoring | 4 tầng: HardStop -3.5%/-7%, Breakeven +2.5→+0.2%, ATR trailing, Climax +18–20% | rules/stop_loss |
| 10 | reinforcement_learning | IC rolling, causal learning, Kelly matrix | learning/causal_learning |
| 11 | system_governance | Hiến pháp, SHA-256 ledger | governance/* |
| 12 | strategy_cio | Trọng tài cuối, weight_cap | — |

Nền tảng chung: `BaseAgent` (process/run_event/audit-log/publish/subscribe/as_tool) + `AgentRegistry` plug-and-play.

### 5.4. Ba pipeline vận hành (slide "Daily Operating Rhythm")
- **Daily ETL (18:00–19:00, 9 steps):** OHLCV → indicators 40+ → GARCH → insider/foreign → financial AlphaStock → corporate docs (auto trigger BCTC→SAG) → F1–F6 → macro. News mặc định off để tiết kiệm cost.
- **Daily Pipeline (09:15):** 12 pha dual-book (Multi-Agent 12–15% NAV + Standalone ML 20% NAV), `BEAR_DEFENSE_100%_CASH`, NAV động từ PG.
- **EOD (sau ATC 15:00, 5 pha):** check nến → settlement paper_trades → regime → causal + Kelly → SHA-256 ledger.
- Daemons: daily 09:15, position 09:00–14:45, EOD 15:15, ETL 18:00.

### 5.5. Stack ML/Quant (slide "Alpha Engine")
- **FeatureForge 80+:** momentum, liquidity, microstructure, frac_diff, chuỗi trần/sàn HOSE ±7%, foreign/insider, PE/PB/ROE, RS vs VNINDEX.
- **Hybrid Stacking Ranker:** Beneish + LambdaMART + Ridge 3D + Survival Gate.
- **Graph Contagion:** 8 feats lan truyền liên cổ phiếu.
- **Dual-Tier Sniper:** A+ full 12–15% + A half 4–6% + Regime Switch.
- **HMM 6-state + GARCH + CSAD + ATC anomaly + VN30 distortion + SBV tracker.**
- **RL:** IC rolling, causal learning, policy weights F1–F6, Kelly Bayes matrix.
- **LLM Moat/GIL** từ SAG đổ về như một kênh alpha cơ bản.

### 5.6. Backtest chuẩn HOSE (niềm tin của kinh tế gia)
- Engine event-driven, point-in-time (PIT), khoá T+2, trần/sàn, phí/thuế/slippage/impact, lô 100 (`app/backtest/`).
- Eval walk-forward 2020–2026, triple-barrier, HMM adaptive (`experiments/eval_engine.py` + 12 `eval_*.py`).

### 5.7. Kết quả nghiên cứu OOS (`docs/MASTER_RESEARCH_JOURNAL.md`, EXP-001→017)
| EXP | Ý tưởng | Kết quả OOS headline |
|---|---|---|
| 008 | Cross-Sectional LambdaMART NDCG@5 | WR 62.83%, Alpha5d +1.067%, Sharpe 2.04 |
| 009 | Conformal Z≥2.9σ + Devil Advocate | WR 63.86%, Alpha +1.426%, Sharpe 2.45 |
| 010 | Asymmetric ATR Trailing, Sniper Z≥2.65σ | WR 64–70%, Expectancy +1.395%, Payoff 1.77x |
| 011 | Graph Contagion 8 feats | Alpha +1.10% (+55%/năm), Sharpe 2.14 |
| 012 | Universe N=150 ADTV≥10B + Z≥3.8σ | WR 65.37%, Alpha +1.466% (+73.3%/năm) |
| 013 | Dual-Tier A+ full + A half + Regime | WR 64.23–71.30% |
| 014 | Fixed vs ATR exit, Breakeven +2.5% shield | ATR-TP +9.81%, Climax +20% |
| 015 | T+2.5 Hybrid 3 nhánh | WR 70.94%, Expectancy +0.302% (A+ +0.607%), Cum +44.7% |
| 016 | Beneish M≤-1.78 Layer0 | Tier A+ WR 74%, Exp +1.017%, AvgLoss -4.85% |
| 017 | Dual-book production | 100% cash Bear, 0 crash Live DB |

> Lưu ý trung thực cho slide: đây là kết quả nghiên cứu nội bộ walk-forward, chưa phải audited fund return. Dùng chữ "OOS backtest", không ghi "cam kết lợi nhuận".

### 5.8. Dữ liệu & hạ tầng
- OHLCV DNSE (REST + WS msgpack + Redis relay), BCTC AlphaStock/CafeF PDF, insider/foreign, macro SBV/GSO/yfinance, factor precompute, audit SHA-256.
- Python 3.11, FastAPI, PostgreSQL/Prisma, RabbitMQ, Redis, DNSE OpenAPI, LightGBM/sklearn/HMM, LLM multi-provider (Nvidia minimax-m2.7 doc dài, OpenRouter qwen3-coder, Groq qwen3-32b realtime), Docker, pytest (32 tests + backtest).
- API: `/api/stream|market|stock|screener|backtest|factors|config|core|security|admin/*` + health chi tiết + admin trigger pipeline thủ công.

---

## 6. LUỒNG END-TO-END (1 SLIDE DEMO)

1. **18:00 ETL:** gom OHLCV + BCTC mới → OCR MinerU → SAG ingest → GIL flag.
2. **09:15 Daily:** Regime → RL weights → Beneish Universe → Research F1–F6 → Thesis (3 tín hiệu) → Counter → CIO → Kelly sizing → Risk check → Execution (SHADOW/LIVE).
3. **Trong phiên:** position_monitoring 4 tầng stop/breakeven/ATR/climax + tape/breadth anomaly.
4. **15:15 EOD:** settlement → học nhân quả → cập nhật Kelly matrix → ledger SHA-256.
5. **KH/Broker hỏi:** chat SAG trả lời kèm evidence cards + graph → broker dùng để tư vấn, KH tự kiểm chứng.

---

## 7. ĐIỂM MẠNH VƯỢT TRỘI VS DỰ ÁN KHÁC (SLIDE "WHY WE WIN")

### 7.1. Vs chatbot chứng khoán generic / ChatGPT / Dify thuần
- **Evidence có hash + line_span**, không nói suông; review-queue VALIDATED.
- **Ontology BCTC Việt Nam** (nợ nhóm 1–5, FVTPL, RPT, sân sau) — generic LLM không có.
- **Bộ 3 active docs/mã** chống rác tri thức; dedup sha256.
- MinerU VLM/OCR + MarkItDown fallback cho PDF scan tiếng Việt.

### 7.2. Vs app broker truyền thống (TCBS, SSI, VNDirect, Vietcap) & fintech (Finhay, TOPI, Infina)
- Họ mạnh khớp lệnh + biểu đồ; **AIInvest mạnh forensic + quant kỷ luật** (MOAT/GIL, Beneish, HMM regime, Kelly, Hiến pháp).
- Họ tư vấn theo chuyên viên; **AIInvest tư vấn theo bằng chứng truy vết + backtest OOS công khai nội bộ.**
- Dual-book SHADOW/LIVE + 100% cash Bear → **sống sót thị trường gấu**, điều app retail hiếm làm.

### 7.3. Vs quỹ quant / room tín hiệu
- **Hiến pháp 4 Điều + ledger SHA-256 + pre-mortem + Devil Advocate** → giảm bias, kiểm toán được.
- **HOSE-native:** T+2.5 floor-gap, trần/sàn ±7%, lô 100, ATC, VN30 distortion, ADTV filter — không bê mô hình Mỹ.
- **Walk-forward + cost đầy đủ** (phí/thuế/slippage/impact), không khoe in-sample.
- **Tự học EOD** (causal + Kelly matrix), không phải bộ tham số chết.

### 7.4. Vs RAG open-source (LangChain demo, LlamaIndex sample)
- Hexagonal + Event Bus + 4 daemons + RBAC/JWT + jobs/activity/monitoring → **production-grade**, không phải notebook.
- 12 agents có registry plug-and-play, tool approval/cancel, memory store.
- Universe 3D LOD + MCP + OpenAI-compat → dễ tích hợp Claude/Cursor/Dify.

### 7.5. Bảng so sánh 1-slide
| Tiêu chí | AIInvest | Chatbot generic | App broker | Quant ngoại nhập |
|---|---|---|---|---|
| Trích dẫn tới dòng BCTC | ✅ hash+line | ❌ | ❌/mờ | ❌ |
| Forensic GIL/Beneish/RPT | ✅ | ❌ | ❌ | hiếm |
| Hiến pháp vốn + Kelly + T+2.5 | ✅ | ❌ | cơ bản | một phần |
| Walk-forward OOS + cost HOSE | ✅ | ❌ | ❌ | thường thiếu |
| Bear 100% cash + dual-book | ✅ | ❌ | ❌ | hiếm |
| Tiếng Việt + HOSE microstructure | ✅ sâu | nông | ✅ nhưng thiếu AI | ❌ |

---

## 8. MÔ HÌNH KINH DOANH & KINH TẾ HỌC (CHO SLIDE GO-TO-MARKET)

### 8.1. Khách hàng & giá trị (Broker định vị)
1. **Broker/Cộng tác viên:** công cụ ra báo cáo có trích dẫn + cảnh báo GIL → tăng tỷ lệ chốt KH, giảm rủi ro tư vấn sai.
2. **NĐT cá nhân active:** screener + evidence + kỷ luật cắt lỗ → tiết kiệm thời gian đọc BCTC.
3. **CTCK/Quỹ/ICP:** API/SDK + MCP + OpenAI-compat → nhúng vào app hiện hữu; white-label Universe 3D.
4. **Doanh nghiệp niêm yết/IR:** theo dõi MOAT/GIL của chính mình và peer.

### 8.2. Luồng doanh thu gợi ý
- SaaS theo seat (broker/l disputed) + gói Pro NĐT.
- API call (search/assess/ingest) + phí ingest PDF MinerU.
- Phí success/overlay cho danh mục dual-book (cần tư vấn pháp lý giấy phép).
- White-label + triển khai on-prem cho CTCK (Postgres/R2 riêng).

### 8.3. Kinh tế học (Economist lập luận)
- **Chi phí biên thấp:** ingest một lần/dedup sha256, assessment cache, ETL ban đêm, news-off mặc định, LLM routing auto + fallback → scale thêm mã/KH không tăng tuyến tính.
- **Hiệu ứng mạng tri thức:** càng nhiều BCTC được validate → ontology + review-queue càng chuẩn → moat dữ liệu.
- **Moat kỹ thuật:** ontology VN + pipeline BCTC→SAG→GIL + Hiến pháp + ledger là tài sản khó sao chép trong 3–6 tháng.
- **Quản trị rủi ro = bảo toàn vốn:** 100% cash Bear + Kelly + giới hạn ngành/mã → drawdown kiểm soát, expectancy dương nhờ payoff 1.77x chứ không nhờ "đánh trúng 100%".

### 8.4. Unit economics cần đo khi pilot (ghi vào slide Appendix)
- Cost/1 BCTC ingest (MinerU + LLM extract + embedding), cost/1 search, cost/1 assessment.
- Time-to-evidence (phút/BCTC), % Relation auto-validated, % GIL WARNING→đúng.
- Pilot KPI: WR live, slippage thực, uptime daemons, NPS broker.

---

## 9. RỦI RO & KIỂM SOÁT (TRUNG THỰC — TĂNG NIỀM TIN)

| Rủi ro | Kiểm soát hiện có | Việc tiếp theo |
|---|---|---|
| Hallucination số liệu | quote_hash + line_span + VALIDATED queue | Thêm cross-check BCTC gốc |
| Overfit | Walk-forward + PIT + cost + triple-barrier | Audit độc lập, OOS live |
| Data delay/sai DNSE/CafeF | Data-quality engine, halted check, fallback | SLA + cảnh báo |
| Live thua Bear | BEAR_DEFENSE cash + dual-book SHADOW | Giới hạn NAV live giai đoạn đầu |
| Pháp lý tư vấn/ủy thác | Tách research vs execution, ledger | Xin ý kiến luật sư chứng khoán |
| Secrets lộ | `.env` gitignore, JWT, lock LLM config | Vault/KMS prod |

---

## 10. ROADMAP GỢI Ý (3 SLIDE HOẶC 1 TIMELINE)

- **Q1 (Hiện tại):** SAG v2 MOAT/GIL cho top VN30, ai-engine SHADOW_RUNNER, backtest portal.
- **Q2:** Mở rộng HOSE 150 mã ADTV≥10B, Standalone ML LIVE giới hạn, app broker pilot, audit OOS độc lập.
- **Q3:** White-label CTCK, MCP marketplace, Universe mobile, compliance pack (MiFID-like log).
- **Q4:** Multi-market (HNX/UPCoM), options/hedge overlay, giấy phép quản lý danh mục (nếu theo hướng quỹ).

---

## 11. KỊCH BẢN SLIDE ĐỀ XUẤT (12–15 SLIDES, 15 PHÚT)

1. Cover: AIInvest — Quỹ tự hành HOSE có bằng chứng.
2. Vấn đề: broker ngập BCTC + NĐT dính bẫy (2.1).
3. Vấn đề: HOSE đặc thù, alpha giá đơn lẻ chết (2.2).
4. Giải pháp: SAG + ai-engine (mục 3, diagram).
5. Demo SAG: hỏi HPG → evidence cards + line_span (4.4–4.5).
6. MOAT/GIL: ví dụ PASS/WARNING/CATASTROPHIC (4.5).
7. Tổ chức 12 agents + Hiến pháp 4 Điều (5.2–5.3).
8. Alpha Engine + backtest chuẩn HOSE (5.5–5.6).
9. Con số OOS: bảng EXP + disclaimer (5.7).
10. Why we win: bảng so sánh (7.5).
11. Business model + unit economics (8).
12. Risk & controls (9) — slide trung thực.
13. Roadmap + ask (pilot/vốn/hợp tác CTCK) (10).
14. Appendix: kiến trúc, tech stack, API.
15. Appendix: thuật ngữ (MOAT/GIL/Beneish/Kelly/HMM/CSAD).

**Lời thoại Broker (30s):** "KH hỏi sao mua HPG — tôi không nói miệng, tôi mở SAG: đây là Fact doanh thu trang X dòng Y, đây là RPT sân sau đã flag WARNING, đây là backtest dual-book WR 65% Sharpe 2.1. Anh kiểm chứng được từng dòng."
**Lời thoại Economist (30s):** "Chúng tôi không dự đoán giá — chúng tôi xây phân phối expectancy dương: win-rate 64–74%, payoff 1.77x, sizing Kelly có mũ, Bear thì cash. Alpha đến từ cross-section + forensic + kỷ luật, không từ tiên tri."

---

## 12. PHỤ LỤC THUẬT NGỮ (CHO NGƯỜI NGOÀI NGÀNH)

- **MOAT:** lợi thế cạnh tranh bền vững (thương hiệu, chi phí, mạng lưới, switching cost).
- **GIL:** cờ cảnh báo gian lận/dòng vốn/bên liên quan (related-party, tăng vốn ảo, bảo lãnh chéo).
- **Beneish M-score:** mô hình phát hiện làm đẹp BCTC (ngưỡng Layer0 ≤-1.78 trong dự án).
- **Kelly:** công thức tối ưu % vốn/lệnh theo edge và odds, có mũ trần để sống sót.
- **HMM regime:** phân trạng thái thị trường (bull/bear/sideway/biến động) để bật/tắt chiến lược.
- **T+2.5:** chu kỳ thanh toán HOSE — mua hôm nay ~2.5 phiên mới bán được, phải tính floor-gap 13.51%.
- **Walk-forward/OOS:** kiểm tra ngoài mẫu theo thời gian, chống overfit.
- **SHADOW vs LIVE:** chạy mô phỏng song song vs tiền thật, dual-book để so sánh.

---

## 13. NGUỒN & TÍNH XÁC THỰC

- Mọi mô tả SAG đối chiếu `SAG/apps/api/sag_api/*` (ontology, financial.py, services, prompts/extract.yaml), `SAG/apps/web/*` (knowledge page, api.ts, universe), `.env.example`, Dockerfile, tests ~40 file.
- Mọi mô tả ai-engine đối chiếu `ai-engine/app/*` (agents, pipeline, rules, services ML/quant, infra DNSE/RabbitMQ, backtest), `docs/MASTER_RESEARCH_JOURNAL.md + QUANT_RESEARCH_NOTE_*`, `experiments/eval_*.py`, `tests/*`, `.env.example`, Dockerfile.
- Số liệu hiệu quả lấy nguyên văn OOS trong docs, không annual hoá bừa bãi, không cam kết lợi nhuận.
- Tài liệu này ~450 dòng, đạt yêu cầu <1000 dòng, sẵn sàng tách thành slide.

*Hết — dùng trực tiếp để dựng deck. Ưu tiên hình: diagram mục 3, bảng 7.5, bảng EXP 5.7, timeline mục 10.*
