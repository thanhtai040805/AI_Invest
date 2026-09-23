# AIInvest Project Memory & Architecture Harness
> **Version**: 1.0.0 (Living Architecture Memory)
> **Last Updated**: 2026-09-22
> **Purpose**: Source of truth for all AI agents and developers working on AIInvest. Eliminates hallucinations, establishes system boundaries, and documents core data pipelines.

---

## 1. System Ecosystem Overview

AIInvest is an autonomous investment and financial forensics organization engineered specifically for the HOSE (Ho Chi Minh Stock Exchange) market:

```
[BCTC PDF / Financial Data / OHLCV / News]
                   │
                   ▼
       ┌──────────────────────┐
       │   MinerU / OCR Engine│
       └──────────┬───────────┘
                  │ Markdown & Structured Tables
                  ▼
       ┌──────────────────────┐
       │       SAG v2         │ ──▶ Line-by-line evidence, quote_hash,
       │   Evidence Engine    │     MOAT (competitive advantage) &
       └──────────┬───────────┘     GIL (fraud/governance risk flags)
                  │
                  │ sag_connector.py (Typed Client)
                  ▼
       ┌──────────────────────┐
       │      ai-engine       │ ──▶ 12 Autonomous Quant Agents
       │   Quant Organization │     Clean Architecture: Domain, App, Infra
       └──────────┬───────────┘     Dual-book risk execution (T+2.5 HOSE rules)
                  │
                  ▼
       ┌──────────────────────┐
       │      back-end        │ ──▶ Express + Prisma ORM + PostgreSQL
       │   Core API Gateway   │     User auth, portfolios, alerts, order state
       └──────────┬───────────┘
                  │
                  ▼
       ┌──────────────────────┐
       │      front-end       │ ──▶ Next.js 15+ App Router, Tailwind CSS,
       │   Interactive UI     │     3D Evidence Graph, Trading view, Chat
       └──────────────────────┘
```

---

## 2. Monorepo Directory & Architectural Responsibilities

### `ai-engine/` (Python 3.11+, Clean Architecture)
- **`app/domain/`**:
  - **Core responsibility**: Business entities, value objects, domain logic, and abstract repository protocols (e.g. `bctc_pipeline_repository.py`).
  - **Rule**: Absolutely ZERO imports of database ORMs (SQLAlchemy), HTTP frameworks (FastAPI), or external SDKs.
- **`app/application/`**:
  - **Core responsibility**: Use-case orchestration, pipelines (e.g. `bctc_to_sag_pipeline.py`), multi-agent coordination.
  - **Rule**: Coordinates domain interfaces with infrastructure services without exposing HTTP transport concerns.
- **`app/infrastructure/`**:
  - **Core responsibility**: Concrete repository implementations (SQLAlchemy repositories), database engine sessions, Redis cache, SAG connector (`sag_connector.py`), external APIs.
  - **Rule**: Implements domain interfaces; contains all I/O details.
- **`app/presentation/`**:
  - **Core responsibility**: FastAPI routers, request/response DTOs, endpoint controllers.
  - **Rule**: Thin layer. Parses incoming requests, delegates to application services, serializes responses.
- **`app/core/`**:
  - **Core responsibility**: Application settings (`config.py`), structured logging, security tokens, DB connection pools.
- **`app/backtest/` & `app/eval/`**:
  - **Core responsibility**: Walk-forward backtesting, Sharpe/Sortino/Drawdown evaluations, statistical validation.

### `SAG/` (Financial Evidence Engine)
- **Core responsibility**: Ingests company reports, extracts facts with exact line numbers, hashes quotes (`quote_hash`), and flags forensic accounting anomalies (`GIL`). MOAT is retired; embeddings are optional and currently disabled.

### `back-end/` (Node.js / TypeScript)
- **Core responsibility**: Express backend service, Prisma ORM, user accounts, authorization, portfolio tracking, alert dispatch.

### `front-end/` (React / TypeScript)
- **Core responsibility**: Next.js 15+ App Router, UI components, chart visualizations, streaming agent reasoning, evidence inspector.

---

## 3. Key Relationships & Call Paths

### The BCTC-to-SAG Ingestion Pipeline:
1. **Trigger**: New BCTC PDF detected or uploaded via endpoint in `app/presentation/routers/` or scheduled batch job.
2. **OCR Parsing**: PDF passed to OCR processor (MinerU), converting document into Markdown with preserved line anchors. The clean phase removes OCR boilerplate, bilingual heading suffixes and long English mirror paragraphs interleaved with Vietnamese while strictly preserving statement tables (CDKT, KQKD, LCTT, BCVDVCSH) and short proper names. `OCR_ONLY` is a local/manual boundary; production promotes the result to `FULL` automatically.
3. **Evidence Extraction**: SAG builds the document tree, normalizes deterministic statement-table figures to VND when the table declares million/thousand units, then persists those facts plus grounded semantic entities, relations, observations and GIL disclosures.
4. **GIL Analysis**: Current GIL decisions use the active extracted document set; all completed Historical documents feed temporal deterioration analysis without being summed into the current exposure. Denominator ratios are period-aligned and use only classified relation flow kinds: service revenue/revenue, capital allocation/assets, related-party receivables/total receivables, payables/payables, loan balances/(cash + equity), and guarantees/equity. A balance numerator is restricted to issuer-outgoing relations, explicit balance flow kinds, the same accounting scope, and the same accounting universe as its denominator. Subset-to-total ratios above 1.0 emit `RATIO_INVARIANT_VIOLATION` and are excluded from interpretation. Sparse disclosures use explicit `DISCLOSED`, `CONFIRMED_NONE`, `NOT_DISCLOSED`, `UNRESOLVED`, or `NOT_APPLICABLE` states; missing evidence is never coerced to zero. Flow risk reports unknown sparse categories as null with status and computes `OBSERVED_EVIDENCE_ONLY` scores plus evidence coverage. Receivable concentration is reported separately from receivable-to-equity materiality. Aggregate evidence fields are separate from current balance fields; observed evidence amounts are provenance metrics and never denominator inputs. Temporal comparison is semantic: same issuer, accounting scope, metric meaning, measurement type, and compatible unit; balance snapshots can compare Annual and Latest Quarter, while flows require period normalization. Two comparable points are reported as observed change with low trend confidence. Relationship resolution counts all classified categories, with external affiliate kept separate from unresolved. External capital outflow is distinct from potential leakage and tunneling evidence. Insider observations expose explicit transaction amounts separately from ownership percentage value proxies; ownership is never treated as a transaction amount. Materiality is reported as economic and risk-adjusted materiality, with holding-company dividends contextualized. Risk severity is separate from review status and data confidence; missing required latest-quarter denominators mark data quality as `PARTIAL`.
5. **Persistence**: SAG stores canonical Markdown in R2, metadata/evidence in PostgreSQL, and uses local Markdown only as an evictable cache. Embeddings are optional and do not gate READY while disabled.
6. **Downstream Quant Signals**: `ai-engine` risk agent checks GIL flags before allocating capital in the dual-book portfolio.

---

## 4. Anti-Hallucination Verified Rules & Conventions

- **Database Access**: Always use repository pattern in `app/infrastructure/repositories/`. Never write ad-hoc SQL queries in application services.
- **Async Execution**: I/O operations in `ai-engine` are asynchronous (`async/await`). Ensure database sessions and HTTP client calls (`httpx` or `aiohttp`) are awaited properly.
- **Environment Variables**: Defined in `ai-engine/app/core/config.py` using Pydantic Settings. Do not access `os.environ` directly in domain or application code.
- **Zero Garbage Files**: No `.bak`, `.tmp`, or scratch scripts committed to production folders.

---

## 5. Living Scientific Architecture Diagrams Hub (`docs/diagrams/`)

- [full-12-agents-sovereign-architecture.html](file:///d:/AIInvest/docs/diagrams/full-12-agents-sovereign-architecture.html): Full 12-Agent Sovereign Quantitative Organization (IOS v5.1) featuring Separation of Powers (Tam Giác Quyền Lực: Compliance, Audit, Change), Sticky HMM, CSAD Herding, Layer-0 Beneish Gate, F1-F6 CSS, 3-Tier Counter-Thesis CTS, Game-Theoretic CIO Arbitration, Quarter Kelly Sizing, 5-Layer Pre-Trade Risk Gate, EAE VWAP Execution, 6-Tier Stop Loss T0-T5, and SHA-256 Hash Chained Ledger.
- [unified-quant-algorithms-blueprint.html](file:///d:/AIInvest/docs/diagrams/unified-quant-algorithms-blueprint.html): Master Unified Mathematical & Quantitative Architecture connecting all 6 algorithmic pillars (FFD $w_k$, Lead Shock Shift(1,2), Gram-Schmidt $u_k \perp u_j$, Adaptive Beneish $M_8/M_5$, Hard Laws Polytope $\mathcal{P}_{\text{HOSE}}$, Quarter Kelly Sizing).
- [paper-grade-algorithmic-data-flow.html](file:///d:/AIInvest/docs/diagrams/paper-grade-algorithmic-data-flow.html): End-to-end data pipeline from raw BCTC ingestion, SHA-256 quote hash, Milvus 1024-d embedding to Queue Accumulator $32 \to 24 \to 8$.
- [gil-decision-tree-gating.html](file:///d:/AIInvest/docs/diagrams/gil-decision-tree-gating.html): Temporal Corporate Graph (TCG) multi-layer graph ($G_{\text{own}}, G_{\text{gov}}, G_{\text{flow}}$) and 4-gate GIL decision tree.
- [SCIENTIFIC_ALGORITHMS_PROMPT_CATALOG.md](file:///d:/AIInvest/docs/diagrams/SCIENTIFIC_ALGORITHMS_PROMPT_CATALOG.md): Complete prompt catalog for reproducing publication-grade diagrams using `/scientific-diagram-prompt-crafter`.

## 2026-09-19 — System integrity hardening

- Internal admin, ingestion and bot routes now require service tokens; SAG production rejects debug bypasses, weak service tokens and default signing secrets.
- Portfolio cash, positions, orders, executions and account-scoped paper trades commit atomically. Missing users, insufficient cash/shares, T+2.5 locks and unavailable governance fail closed.
- Prisma migration history now reconstructs the full checked-in schema; DB constraints enforce unique positions and exactly one reaction target.
- Daily pipeline runs use PostgreSQL advisory locks and persisted job state. Market workers use Asia/Ho_Chi_Minh and the VN trading calendar.
- Backtests use canonical market data, next-session fills, fees, cash limits and real equity/trade metrics. Unsupported strategies and unsafe run identifiers are rejected.
- Runtime topology includes separate Core PostgreSQL and SAG pgvector PostgreSQL services. AI Engine runtime connects only to Core; SAG is isolated for research and is not an AI Engine dependency.

## 2026-09-20 — Deterministic GIL Extraction Architecture & Generalization Audit

- Added deterministic extraction components (`StatementTableParser`, `RptTableParser`, `FootnoteSemanticParser`, `GovernanceTableParser`, and `GilGraphCompiler`) as the authoritative `extract_and_persist_manifest` path. Production no longer calls or merges LLM extraction output; persisted rows are tagged `DETERMINISTIC_COMPILER`.
- **Universal Accounting Taxonomy (Circular 200/2014/TT-BTC & Circular 99/2025/TT-BTC)**:
  - Unit scale auto-detection and 500T sanity ceiling safeguards.
  - `classify_temporal_column(header)`: Deterministically classifies column temporal scope into `CURRENT_PERIOD` vs `PREVIOUS_PERIOD` across annual, quarterly, interim, and explicit date headers (`30/06/2024`, `31/12/2023`).
  - `parse_vn_number(raw)`: Equipped with recursive OCR glued dot-number decomposition (`(\.\d{3})(\d{1,3}\.)`) to unpack multi-line sub-transactions merged by MinerU OCR (e.g., `2.863.1252.863.1251.204.866`) into distinct numbers and avoid numeric overflows (`1e20`).
- **Universal VAS 26 Related Party Disclosure Parsing (`RptTableParser`)**:
  - Implemented ordered specificity keyword matching: Interest flows (`lai vay`, `thu lai`) are classified strictly before borrowing (`vay`), separating balances (`loan_balance`, `borrowing_balance`, `receivable_balance`, `payable_balance`) from operational flows (`interest_income`, `interest_expense`, `purchase`, `service_revenue`).
  - Strict alignment with `gil_service.py:_denominator_exposure_buckets`, eliminating unmapped flows (`non_ratio_flow_vnd = 0.0`).
  - Preserves issuer ticker as canonical relation subject (`subject_entity_id = issuer_ticker`) regardless of transaction flow direction.
- **Universal Issuer Alias Set Resolution (`gil_service.py`)**:
  - Outgoing relation filtering now operates over an alias set containing both the canonical ticker and full legal corporate name, preventing dropped exposure relations when entities alternate representations.
- **Statement of Changes in Equity Parser (`parse_equity_changes_table`)**:
  - Extracts canonical ending equity (`tong_cong_cuoiky`) and components from Notes when balance sheets are absent in interim quarterly reports.
- **Expanded Limits & Entity Validation**:
  - Length-guarded name sanitization preventing Pydantic validation failures on empty prefixes.
  - `ExtractionManifestIn` limits expanded to support conglomerate multi-entity disclosures without truncation.
- **Cross-Industry Benchmark Verification (local corpus, not market-wide certification)**:
  - Audited 5 issuers across conglomerate (`VIC`), heavy manufacturing (`HPG`), technology (`FPT`), retail (`MWG`), and commercial banking (`VCB`), with annual, latest/interim, and governance Markdown where available.
  - Evidence line grounding passed for the deterministic compiler corpus. This is not a claim that production extraction is 100% complete or that the full market has been covered.
  - Root-cause fixes now enforce table-role gating, current-period column selection, bank taxonomy specificity, governance person-column gating, ownership-percent column gating, and generic accounting-label rejection in RPT parsing.
- The five-issuer PostgreSQL reprocess completed 15/15 documents with `COMPLETE`; old Fact/Entity rows for those documents were purged before rebuild. `assess_gil()` now runs against the deterministic graph for VCB, VIC, MWG, FPT, and HPG. This is a cross-industry smoke audit, not a market-wide certification.

## 2026-09-21 — Deterministic Extraction Integration & Legacy Debris Elimination

- **Authoritative Pipeline Integration**:
  - `GilGraphCompiler` is integrated as the primary, authoritative extraction engine in `extraction_v2_service.py` and `financial_v2_service.py`.
  - Replaced ad-hoc `try ... except: pass` fallback with structured compiler execution and clear error logging.
  - Package structure formalized with modular `__init__.py` across `sag_api.extraction` subpackages (`compiler`, `parsers`, `anchors`, `frames`, `slot_filler`), resolving circular dependency between schema models and compiler.
- **Legacy Debris & Duplicate Fact Elimination**:
  - Completely purged obsolete `_persist_statement_table_facts` routine and its ad-hoc side-channel table crawler. Statement table facts are now extracted solely by `StatementTableParser` inside `GilGraphCompiler`, eliminating duplicate database records and ungrounded empty-quote rows in PostgreSQL.
  - Refactored `_classify_table_fact` and `_table_value_scale` into lightweight adapters compliant with circular-200 `accounting_taxonomy.py`.
- **Zero-Heuristic Enforcement**:
  - Removed all hardcoded ticker brand lists (`vinhomes`, `vinfast`, `fpt`, `hoa phat`, `bach hoa xanh`, `vietcombank`, etc.) from `subsidiary_table_parser.py`.
  - Replaced hardcoded bond deadlines (`2026-2028`) with dynamic regex extraction in `frame_slot_filler.py`.
  - Replaced verbatim phrase matching (`co phieu cua mot so cong ty con`) with structured financial term logic (`dam bao`/`the chap` + `co phieu`/`phan von gop` + `cong ty con`).
- **Regression & Benchmark Verification**:
  - All 59 core unit tests pass in under 35s.
  - Live PostgreSQL benchmark re-extraction verified across all 5 benchmark issuers (`VIC`, `VCB`, `HPG`, `FPT`, `MWG`): fact counts streamlined to exact, clean quotes with identical risk flags and headline metrics.

## 2026-09-21 — GIL Mathematical, Graph Theory & Decision Matrix Hardening

- **Flag Decision Hierarchy Repair (Demotion Bug Fix)**:
  - Fixed a critical logical flow bug where an independent `if ratio_invariant_violations:` block followed by `elif economic_risk_trigger:` silently overwrote `flag = "CATASTROPHIC"` with `flag = "WARNING"`, demoting catastrophic capital-flow circular tunneling to a medium warning.
  - Removed duplicate logic block containing corrupted mojibake character encoding (`PhÄ‚Â¡t hiĂ¡Â»â€¡n...`).
  - Restructured flag decision hierarchy to strict priority order: `CATASTROPHIC` (verified cycle) -> `WARNING/HIGH` (invariant violations) -> `WARNING/MEDIUM` (economic risk trigger) -> `WATCH/LOW_MEDIUM` (review required) -> `PASS/LOW`.
- **Accounting Invariant Violation Elevation**:
  - Unquantified subset-to-total ratio violations (`related_party_receivables / total_receivables > 1.0`, etc.) no longer pass silently as `PASS`; now strictly elevated to `flag = "WARNING"`, `risk = "HIGH"`.
- **Cycle Circulation Capacity (Network Bottleneck Theory)**:
  - Replaced naive edge summation `total = sum(...)` in `_cycle_materiality_ratio` with network bottleneck capacity $\min_{e \in C, \text{flow}(e) > 0} \text{amount}(e)$.
  - Eliminates $k\times$ artificial inflation of circular tunneling capital volume in length-$k$ cycles.
- **Cycle Automorphism Canonicalization**:
  - Implemented `_canonicalize_cycle(cycle)` returning the unique rotation beginning with the lexicographically smallest vertex.
  - Eliminates duplicate cycle counts in `_detect_cycles` and `_detect_ownership_loops` (e.g. $[A, B, C, A]$, $[B, C, A, B]$, $[C, A, B, C]$ deduplicate to 1 canonical cycle).
- **Negative Equity Safeguard**:
  - Enforced `equity > 0` and `doc_equity > 0` checks across denominator coverage, ratio candidates, guarantee ratio, and cycle materiality.
  - Prevents companies with negative equity (accumulated deficit) from generating negative ratios that erroneously score `0.0` (false `PASS`).
- **Multi-Hop Path Forward Verification**:
  - Added `endpoint != source` check in `_multi_hop_flow_count` and `_suspicious_multi_hop_flow_count` to prevent bilateral reciprocal transactions ($A \to B \to A$) from being miscounted as multi-hop paths to third parties.
- **Guarantee Snapshot Synchronization**:
  - Synchronized `amounts["guarantee"]` in `_flow_risk_breakdown` with reconciled `guarantee_state["value_vnd"]`, preventing raw stale relation sums from distorting the flow mix.
- **Canonical Four-Flag Domain Alignment & Temporal De-sensitization**:
  - Removed ad-hoc `WATCH` flag to restore strict adherence to `GILFlag` enum (`PASS`, `WARNING`, `CATASTROPHIC`, `DATA_INSUFFICIENT`).
  - Decoupled `review_required` from `gil_flag`: `review_required` populates `review_status = "REVIEW_REQUIRED"` for downstream Analyst/Agent-05 inspection while allowing healthy companies to receive `flag = "PASS"`.
  - De-sensitized temporal history requirements during early bootstrap stages: `_temporal_risk` requires $\ge 3$ comparable points before activating a trend score, eliminating false alarms when issuers only have 1–2 documents.


## 2026-09-21 — Corpus-Wide Unit Context & GIL Quality Gate

- Monetary scale is now scoped to an actual statement table. Narrative `billion VND` text cannot set the scale for a later VND/thousand-VND balance sheet; malformed multi-comma OCR numbers are rejected.
- Accounting code `270` on a non-total row is no longer classified as `total_assets`, and multi-row statement headers are supported.
- Read-only audit of 766 annual/latest assets across 385 issuers found zero values above 10^18 after the fix. Of 78 assets without parsed `total_assets`, 54 contain a raw total-assets line with a value and 24 do not.
- `assess_gil()` now emits `WARNING` with `REVIEW_REQUIRED` when latest denominators, accounting scope, or sector policy are incomplete; `PARTIAL` data cannot produce a clean `PASS`.

## 2026-09-21 - Production Pilot and Scope Selection Fix

- GIL policy `v50` now accepts validated manifest facts that carry accounting scope and taxonomy provenance; production facts are no longer incorrectly treated as missing just because they lack the deterministic-parser source label.
- Any unresolved relationship share now keeps `data_quality=PARTIAL` and prevents a clean `PASS`.
- Market sector metadata was backfilled for 402 SAG issuers from `public.stocks`; 398 source rows had sector values.
- The upstream document selector now enforces the GIL input contract: annual/latest financial documents are selected as `SEPARATE`; explicit consolidated candidates are excluded. Governance remains a separate role, and reporting scope is propagated through URL/R2/ZIP ingestion.
- The 21-issuer live pilot completed without runtime errors: 19 `WARNING`, 2 `DATA_INSUFFICIENT`, 0 `PASS` with partial data. FPT was reprocessed through the consolidated annual/latest sources and now has verified consolidated denominators; the latest `financial_income` line was recovered by correcting the VAS code-22/code-23 mapping, while 8.1% of relationship evidence remains unresolved, so the result is still `WARNING/PARTIAL`.
- Scope inference now trusts an explicit document title before body mentions, and GIL historical points prefer the active replacement for the same role/reporting period; this prevents separate/consolidated mixing and duplicate temporal points after reprocessing.

## 2026-09-21 — Production Pilot Closure Audit

- Fixed the shared scope handoff: `metadata.report_scope` is read from the canonical `DocumentAsset` during extraction and becomes the explicit accounting scope for every persisted fact/relation; OCR/body inference remains the fallback.
- Fixed three shared parser failure modes found by the live audit: OCR-glued date/unit headers such as `30/6/2026Triệu VND`, two-column RPT tables whose first cell is the transaction description, and ownership/cash-deposit labels incorrectly falling back to receivables. The accounting-scale and RPT regressions have focused tests.
- RPT relationship labels are now preserved in the graph. `công ty con`, `công ty liên kết`, and insider labels can therefore be classified when the source states them; generic `related_party` remains unresolved when the document does not state a stronger relationship. Accounting labels such as `Tổng công nợ`, `Giá vốn`, and `Báo cáo...` are rejected as counterparties.
- Reprocessed all 42 active annual/latest documents for the 21-issuer pilot after the final shared fixes. The final pass completed 42/42 without runtime or validation failures.
- Final live GIL audit: 2 `PASS` (MWG, VCB), 17 `WARNING/PARTIAL`, and 2 `DATA_INSUFFICIENT` (DGC, TIX). No completed assessment has a missing latest denominator. DGC/TIX are insufficient because the active corpus has no quantified related-party transaction/guarantee/financing evidence, not because a parsed denominator was silently substituted. GAS retains a fail-closed ratio invariant warning for overlapping aggregate/detail source rows; no forced PASS was applied.
- This is a production-safe pilot gate, not a market-wide certification: downstream use may consume `PASS` only, route `WARNING/PARTIAL` to analyst review, and block `DATA_INSUFFICIENT` until source coverage is expanded.

## 2026-09-22 — Parser Root-Cause Fixes & DGC/TIX Re-audit

- Added a pre-persist source-completeness gate for annual/latest financial OCR: Balance Sheet, Income Statement, Cash Flow, and Notes must be present in the canonical OCR source. Missing sections now produce `INCOMPLETE` with an explicit missing-section list; they are not converted into an empty but `COMPLETE` extraction.
- `RptTableParser` now extracts quantified aggregate related-party receivable/payable rows outside the dedicated VAS-26 note, while preserving them as `RELATED_PARTIES_AGGREGATE` rather than inventing a counterparty. Remaining relationship-unresolved share is therefore a source limitation when the report does not name entities, not a forced classification.
- `FrameSlotFiller` scans table rows for explicit guarantee disclosures, and `StatementTableParser` supports English statement labels plus coded rows split into OCR table fragments. The fallback is restricted to coded statement rows and excludes cash-flow context.
- Fiscal-period alignment now corrects a statement date that is older than a supplied title period and derives the correct quarter start. The TIX Q3 title/source mismatch was corrected from `2026-09-30` to the evidenced statement date `2026-06-30` / Q2.
- Governance period alignment now prefers the reporting horizon in the document title over signature/publication dates; DGC and TIX governance records were reprocessed back to `2026-06-30` instead of the later July dates found on their cover pages.
- Runtime dependency contract is pinned to `zleap-sag==0.7.1`, matching the imported `zleap.sag.modules.*` API; the previous `0.13.x` lock was incompatible with this codebase.
- Fresh full OCR source runs completed for DGC and TIX. After reprocessing the active documents with the shared fixes: DGC is `WARNING` with quantified related-party/guarantee evidence and no missing denominator; TIX is `WARNING` with no missing denominator after recovering `total_assets`, while its annual source is correctly blocked as `INCOMPLETE` because OCR contains only the balance-sheet portion. Both remain analyst-review outputs; neither is eligible for a clean `PASS` under the fail-closed policy.

## 2026-09-22 — GIL Contract Reset: Risk, Evidence, Decision

- GIL is an evidence-to-flow layer for verified ownership, related-party and capital-flow signals; risk interpretation and investment decisions are downstream concerns.
- The canonical v2 output now separates `flow_signal` (`NO_ABNORMAL_FLOW_OBSERVED`, `ABNORMAL_FLOW`, `CIRCULAR_FLOW`, or `NOT_ASSESSED`) from `evidence_status` (`VERIFIED`, `PARTIAL`, `INSUFFICIENT`) and downstream `decision` (`CLEAR`, `ALLOW_WITH_REVIEW`, `BLOCK`). `risk_signal` and `gil_flag` remain backward-compatible projections of observed flow findings.
- Aggregate-only or unresolved relationships reduce evidence confidence and require review when exposure exists; they are not scored as observed relationship risk and do not by themselves force `WARNING`.
- `INSUFFICIENT_HISTORY` suppresses temporal trend scoring but is not itself a hard flow finding. Missing denominators, unverifiable accounting scope, or ratio invariants reduce evidence coverage and mark the affected checks `NOT_EVALUATED`; they do not lower an observed positive flow signal.
- The comparison target is now grounded output quality: source-grounded facts, correct period/denominator alignment, high-precision hard-risk detection, stable reruns, and explicit uncertainty—not the number of issuers labeled `PASS`.
- Live smoke verification after the v55 contract reset: DGC and TIX return `risk_signal=PASS`, `evidence_status=PARTIAL`, `review_status=REVIEW_REQUIRED`, and `decision.action=ALLOW_WITH_REVIEW`; selector scope changes are assessed separately from denominator/source-quality blockers.
- Temporal bootstrap is intentionally excluded from `evidence_status`: a first-run issuer with fewer than three comparable periods can remain `VERIFIED` when its current source/relationship evidence is complete; only `temporal_history=INSUFFICIENT_HISTORY` and a null trend score are emitted.
- Cleaning is a mandatory canonicalization boundary for every Markdown/PDF path: English mirror sections are removed only when a Vietnamese counterpart exists, headers/boilerplate are normalized, statement tables and legal names are preserved, then source completeness and deterministic extraction run on that cleaned Markdown. Raw bytes remain traceable through `raw_content_sha256`.

## 2026-09-22 — Full Active-Corpus Clean/Extract/GIL Audit

- Audited 1,187 canonical Markdown assets: canonical hashes are consistent, clean is idempotent for 1,187/1,187, and the shared cleaner now removes uppercase LaTeX noise (`\\Delta`, `\\Omega`, etc.) without damaging statement tables. The remaining English-only sources are document-quality cases, not Vietnamese parser failures.
- Expanded statement completeness markers for English securities headings (`STATEMENT OF INCOME`, `STATEMENT OF COMPREHENSIVE INCOME`, and `NOTES TO THE FINANCIAL STATEMENTS`). Historical/inactive disclosure-only fixtures remain `INCOMPLETE`; active replacements are not downgraded because of stale fixtures.
- Replayed all 56 active documents with assets through clean → structure → deterministic extraction → persistence. All 56 ended `READY` with extraction `COMPLETE`; facts carry accounting scope/taxonomy provenance and evidence lines validate.
- Fixed RPT balance aggregation at the shared denominator boundary: when an aggregate subtotal exists, GIL uses the aggregate and keeps entity rows for graph context; non-related subtotals are excluded. This removed the GAS `related-party receivables > total receivables` invariant violation without a ticker-specific force.
- Pre-selector-correction audit: VCB was `VERIFIED/CLEAR`; 13 issuers were `PASS/ALLOW_WITH_REVIEW`; VIC was `WARNING/ALLOW_WITH_REVIEW`; six issuers were blocked because the selector scope had not reached the active facts. Three VCB records had no recoverable SAG asset at that point.
- The selector contract was then corrected to `SEPARATE`-only for annual/latest financial inputs. Replaying 63 selector artifacts from existing R2 Markdown produced 57 `READY/COMPLETE` results; the remaining five source failures are English-only or missing R2, and TIX annual remains `INCOMPLETE` because its source is incomplete. BMI, DHG, GMD, and HCM now have verified separate scope; ORS and VCI remain blocked on rejected English source quality, not selector ambiguity. VCB's three source assets were restored from R2.
- Follow-up source replay corrected the apparent English/R2 blockers: TIX annual OCR from source produced a new complete artifact and GIL `PASS`; ORS annual/latest, TVS annual/latest, and VCI latest succeeded after ZIP-candidate/source fallback, with Vietnamese artifacts persisted and GIL scope verified. The multipart PDF upload path now carries `report_scope`; the OCR cache gate verifies the R2 object exists before period-locking, so stale `r2_md_uploaded` flags cannot silently skip missing artifacts. FPT `total_assets`, HPG ratio invariant, and MWG `revenue` remain data-quality audit items, not GIL flow blockers.

## 2026-09-22 — OCR Table-Column Quality Gate

- FPT's original PDF contains the complete separate balance-sheet values, including total assets `33,157,714,945,685`. MinerU raw Markdown drops the numeric right-hand column before cleaning; the cleaner and deterministic statement parser are not the source of this loss. The consolidated FPT artifact does not show this failure.
- `assess_financial_document_completeness()` now detects a balance-sheet total-assets label/code whose nearby numeric values are absent and returns `INCOMPLETE` with `data_issues=["balance_sheet.total_assets_value"]`. The extraction path records this quality issue but does not block grounded relation/fact persistence; it never reconstructs or forces values from a visual PDF.

## 2026-09-22 - GIL v58 Flow-Only Contract

- GIL now separates `flow_signal` (`NO_ABNORMAL_FLOW_OBSERVED`, `ABNORMAL_FLOW`, `CIRCULAR_FLOW`, `NOT_ASSESSED`) from `evidence_status`. Missing optional denominators, history, scope, or ratio invariants are data-quality coverage findings, not observed flow risk.
- Extraction persists grounded facts and relations when required statement sections exist even if an optional numeric denominator is missing. GIL can evaluate observed flow evidence while reporting the affected ratio as `NOT_EVALUATED`.
- Only the absence of any active `COMPLETE` extraction remains `NOT_ASSESSED`; missing data no longer changes an observed `PASS` flow signal into `UNKNOWN` or blocks the GIL decision. `CIRCULAR_FLOW` is the hard block and `ABNORMAL_FLOW` is review-only.
- Cycle detection consumes all validated flow relations, keeps paths inside the same reporting period, and requires quantified financing/capital evidence. Ordinary trade reciprocity is not treated as circular flow.

## 2026-09-22 — Core Deployment Preparation & Reverse Proxy Gateway

- **Full Containerization & Standalone Build**:
  - `front-end`: Enabled `output: "standalone"` in `next.config.ts` (Turbopack + Next.js 16.2.6), reducing container image footprint to ~150MB with non-root security (`nextjs:nodejs`). Created `.dockerignore` and multi-stage `Dockerfile`.
  - `back-end`: Created `.dockerignore`, updated `Dockerfile` to copy Prisma CLI from builder, and added `docker-entrypoint.sh` to auto-apply pending Prisma migrations (`npx prisma migrate deploy`) before launching Express API.
  - `SAG`: Added `alembic` directory, `alembic.ini`, and `docker-entrypoint.sh` to `SAG/apps/api/Dockerfile` ensuring `alembic upgrade head` executes automatically on `sag-postgres` on container boot.
  - `ai-engine`: Hardened `Dockerfile` with `curl` dependency and active `/health` polling healthcheck.
- **Zone 1 DMZ Reverse Proxy & Network Hardening**:
  - Created production `nginx/nginx.conf` and `nginx/Dockerfile`: unified routing for SSR frontend (`/`), Backend API (`/api/v1/`), and real-time WebSockets (`/socket.io/` with `Upgrade` and `Connection: upgrade` headers).
  - Secured `docker-compose.yml`: retracted public host binds for all persistent databases (`postgres`, `sag-postgres`, `redis`, `rabbitmq`) and computational services (`ai-engine`, `sag-api`, `backend`), binding local admin ports exclusively to `127.0.0.1` and exposing only public ports `80`/`443` via Nginx.
  - Healthcheck topology covers the seven default long-running core services; SAG remains behind the `research` profile.
  - Log rotation policies (`json-file`, `max-size: 50m`, `max-file: 5`) added to prevent host disk exhaustion.
- **Secrets & CI/CD Hardening**:
  - Updated `.env.example` with all production-required variables and created `.env.production.example` with cryptographic token generation directives (`openssl rand -hex 32`).
  - Upgraded `.github/workflows/ci.yml` to a 5-tier monorepo CI testing Backend (Node 22), Frontend (Next.js 16 standalone), AI Engine (Python 3.11), SAG (Python 3.12), and Docker Compose config validation.
  - Authored comprehensive operational runbook: `docs/deployment/DEPLOYMENT_GUIDE.md`.

## 2026-09-22 - Temporary SAG Analysis Hold

- SAG is temporarily paused for research and is not certified for production abnormal-capital-flow decisions.
- `ai-engine` must not connect to SAG at all while the hold is active: no OCR/reprocess/extraction/embedding/activation/GIL/assessment/document-status/Markdown read or write, and no use of stale SAG-derived flags as current input.
- Any future ai-engine change touching a SAG boundary must include the exact caller/contract, source-document evidence, state/rollback impact, observability, and normal plus fail-closed tests before implementation.
- `ai-engine` enforces the hold at the shared `SAGConnector` boundary with `SAG_ANALYSIS_HOLD=true` by default. All SAG attempts return terminal `SAG_CLOSED`/`BLOCKED` without network, local-source fallback, retry, or SAG-derived PASS side effects; dependent workflows are deferred with explicit `SAG_CLOSED` state and must not be relabeled as `DATA_INSUFFICIENT`.

## 2026-09-22 — SAG Runtime Separation

- SAG is frozen and isolated as a research service. It is not registered as an Agent and is not a runtime dependency of `ai-engine`; Compose starts it only under the explicit `research` profile.
- The production ETL and `DailyInvestmentPipeline` no longer dispatch SAG ingestion, query SAG evidence, or pass forensic/GIL payloads to decision Agents.
- Decision Agents now use only independent financial, market, audit, Beneish, liquidity and thesis evidence. The old GIL veto, stale-flag reads, fake PASS defaults and universal forensic BUY gate were removed.
- Counter Thesis removed the SAG/GIL/Graph-RPT 25% share and recalibrated the remaining weights by preserving their relative prior: Beneish 13.33%, receivables 13.33%, macro 20%, liquidity 26.67%, missing/staleness 26.67%. Its Beneish+receivables interaction remains independent.
- Legacy `gil_flag`/SAG columns and research pipeline code remain only for historical compatibility and isolated research; active runtime paths never read them. Because the deployed legacy `universe_securities.gil_flag` column is `NOT NULL`, new Universe upserts write the explicit non-evidence sentinel `SAG_HOLD` and never update an existing GIL value.
- Future SAG/MOAT/GIL redesigns must be integrated as a new research-to-runtime contract after separate validation; they must not be threaded back through individual Agents.

## 2026-09-22 — Database Schema Synchronization & Second Normal Form (2NF) Normalization

- **Universal Schema Reconciliation (PostgreSQL 16 `aiinvest` & `schema.prisma`)**:
  - Reconciled live PostgreSQL schema with master `back-end/prisma/schema.prisma` across all 74 Prisma models and 75 database tables with exact zero column/type discrepancies.
- **Second Normal Form (2NF) Normalization on `reactions`**:
  - Replaced legacy non-relational polymorphic fields (`target_type`, `target_id`) with 2NF foreign keys (`post_id`, `comment_id`) referencing `posts(id)` and `comments(id)` with cascading deletes.
  - Enforced mutual exclusivity check constraint `reaction_exactly_one_target`: `((post_id IS NOT NULL AND comment_id IS NULL) OR (post_id IS NULL AND comment_id IS NOT NULL))`.
  - Added composite candidate key uniqueness: `UNIQUE(user_id, post_id)` and `UNIQUE(user_id, comment_id)`.
- **Entity Integrity Enforcement on `positions`**:
  - Added `UNIQUE(user_id, symbol)` (`positions_user_id_symbol_key`) constraint to enforce 1-to-1 position ownership per symbol per user, eliminating duplicate position insertion anomalies.
- **SAG v2 Business Quality Model Alignment (retired)**:
  - The former `business_quality_profiles`/`moat_profiles` model was retired from the core schema and Agent repository when SAG was frozen; future SAG research must define a new isolated contract.
  - Synchronized `log_equity_research.business_quality_evidence` in `schema.prisma` replacing obsolete `moat_citations_evidence`.
- **BCTC Pipeline & Governance Alignment**:
  - Added `ocr_cache_version` (`VarChar(32)`) and `source_document_id` (`BigInt`) to `bctc_pipeline_records` in `schema.prisma`.
  - Harmonized `cio_strategic_directives.status` (`VarChar(32)`) and `strategic_cash_target_pct` (`Decimal(6, 2)`).
  - Added missing GIL tracking columns (`gil_analysis_status`, `gil_analysis_version`, `gil_assessed_at`) to PostgreSQL tables `stocks` and `universe_securities`.
- **Zero Waste & Debris Purge**:
  - Dropped un-normalized, dead temporary snapshot tables (`bctc_pipeline_records_backup_20260915` and `knowledge_documents_cafef_backup_20260915`).
- **Prisma Migration State Synchronization**:
  - Marked migration `20260919020000_portfolio_integrity` as resolved/applied in `_prisma_migrations`.
  - Regenerated Prisma Client v5.22.0. Verified `back-end` build (`tsc`) passes with zero errors, and `ai-engine` test suite passes.

## 2026-09-23 — Core Deployment Safety Gate

- Portfolio automation starts disabled on a fresh core deployment. It requires an existing `MULTI_AGENT_ACCOUNT_ID` before being enabled; market ETL can still start independently.
- The backend health endpoint can report an open AI Engine circuit without throwing or failing its own liveness check.
- The migration retiring SAG/MOAT quality tables refuses to drop either table while it contains rows. Existing deployments must archive and verify those rows before retrying the migration.
- A clean-volume Compose smoke test verified migration and the core containers, but it does not certify live market data, LLM decisions, trading, SAG, or production-host operations.

## 2026-09-23 — Shadow Execution Boundary

- Manual and autonomous paper fills now require a timestamped live order book no more than 10 seconds old, a continuous HOSE session, and enough displayed depth at the permitted price. Missing or stale data blocks ledger mutations. The backend no longer manufactures order-book levels from historical closes, and the trade page labels orders as Shadow.
- Manual LO/MP orders debit or credit modeled fees and sell tax in the same serializable transaction as cash, position, and order updates. ATO/ATC are explicitly unsupported. Autonomous Agent-08 and Standalone ML no longer record synthetic close/slippage-based fills.
- Shadow execution remains a conservative displayed-depth model. It does not establish exchange queue priority, partial-fill lifecycle, market holidays, or real broker reconciliation and is not certification for live trading.
- AI-approved Shadow BUY orders now use the approved morning price as a day limit. They remain in the existing `orders` table as `PENDING_SHADOW`; one sequential worker scans every 1 second after a pass when orders are pending, and every 5 seconds while idle. It checks fresh displayed depth, atomically fills the existing order with cash/position updates, or expires it at session end. Standalone ML Shadow orders use the same pending lifecycle and isolated account.
- No recommendation, allocation, risk-gate, or agent ordering logic changes for pending Shadow execution. Current orders table fields are sufficient; no table migration is required.
- Widened `portfolio_account.account_id` to 64 characters: the previous 32-character column rejected 36-character user UUIDs and rolled back paper fills while synchronizing account NAV.
