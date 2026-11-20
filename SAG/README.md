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
docker compose up -d db qdrant

cd apps/api
uv sync --extra dev --extra postgres
uvicorn sag_api.main:app --reload --host 0.0.0.0 --port 8000
```

Run tests:

```bash
cd SAG/apps/api
pytest -q -m "not ai_smoke"
```
