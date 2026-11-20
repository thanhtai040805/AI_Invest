# AIInvest SAG — Knowledge Routing RAG Engine

Clean SAG backend adopted from the benchmarked Knowledge Routing RAG architecture (`zleap-sag==0.12.0`, PostgreSQL 16 + Qdrant), focused exclusively on **Stock Research & Competitive Advantage (MOAT)** for AIInvest.

All legacy **GIL (Graph Intelligence Layer)** extractors, forensic tables, `/api/v1/gil`, and `/api/v2/financial` pipelines have been removed.

## Core Architecture

**"Build expensive once (deterministically), query cheap many times"** — *Algorithm first -> model second -> LLM last.*

1. **Multi-Lane Ingestion & Canonical Extraction**: R2/file upload -> Smart Hybrid MinerU / isolated local worker -> `CanonicalBlock` (heading/paragraph/table/page anchors) -> 4-layer deduplication (SHA-256 + MinHash) & temporal versioning (`DocumentVersion`).
2. **Search Path (`SEARCH_READY`)**: `SearchUnit` -> shared embedding cache -> Qdrant dense + sparse hybrid index (`SAG_DOCUMENT_EVENT_EXTRACTION_ENABLED=false`, **0 LLM calls during ingest**).
3. **Knowledge Foundation (`KNOWLEDGE_READY`)**: Deterministic `E0/E1` extraction -> `KnowledgeUnit` -> `DenseGraph` (`KnowledgeUnitEdge`) -> Constrained Hierarchical Leiden `Knowledge Routing Tree` -> Blue-Green dual-slot (`A/B`) snapshot publish.
4. **Adaptive Online Retrieval**: Tree beam routing + ACL-scoped global escape budget -> branch-local hybrid search -> RRF + dedup + MMR + bounded HippoRAG 2 multi-hop expansion -> grounded citations with exact provenance & token usage accounting (`sag_api/core/token_usage.py`).

### Reference Benchmarks (RetrievalCI Bench v0 — 81 Documents, 50 Queries)

See [`docs/adr-remove-e2-llm-enrichment.md`](docs/adr-remove-e2-llm-enrichment.md) for full A/B benchmark evidence:

- **RetrievalCI Overall Score**: `93.64`
- **Source Recall**: `99.17%` (`100.0%` Single-Hop, `97.5%` Multi-Hop, `100.0%` Contradiction)
- **Abstention Correctness**: `100.0%` (`5/5`)
- **Ingest LLM Calls**: `0` (`100%` embedding + deterministic `E0/E1` + `DenseGraph` + Leiden tree)

## Directory Layout

```text
SAG/
├── apps/api/          FastAPI backend (sag_api + sag_agent), SQL migrations, tests
├── docs/              Core architecture specs & E2-removal benchmark ADR
├── compose.yaml       Standalone PostgreSQL 16 + Qdrant + sag-api stack
└── .env.example       Default environment configuration
```

## Quick Start

```bash
cd SAG
cp .env.example .env
docker compose up -d db qdrant

cd apps/api
uv sync --extra dev --extra postgres
uvicorn sag_api.main:app --reload --host 0.0.0.0 --port 8000
```

### Standalone Project-Scoped Auth (No External JWKS Required)

Evidence, source, document, and search routes fail closed by default. For standalone AIInvest Stock Research / MOAT usage, keep `SAG_AUTH_MODE=continuum` and configure a `>= 32`-character `SAG_BFF_SERVICE_TOKEN`:

1. **Ingest documents per ticker/project** (`POST /api/v1/projects/{ticker}/documents/upload` or `/ingest-object`) using `X-SAG-BFF-Service-Token` + scoped headers (`X-Continuum-User-Id`, `X-Continuum-Organization-Id`, `X-Continuum-Project-Id`, `X-Continuum-Security-Partition`, `Idempotency-Key`). This automatically provisions the ticker-scoped `Source`.
2. **Query/read sources & search** by minting a short-lived scoped session JWT via `POST /api/v1/auth/continuum-login` (with `X-SAG-BFF-Service-Token`) and passing `Authorization: Bearer <token>` to `GET /api/v1/sources`, `POST /api/v1/search`, `/api/v1/knowledge/*`, and `/api/v1/universe/*`.

Run tests:

```bash
cd SAG/apps/api
pytest -q -m "not ai_smoke"
```
