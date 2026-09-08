# sag-api

FastAPI backend cho SAG v2 Financial Evidence Engine. SAG v2 chỉ phục vụ ba loại tài liệu theo ticker:

- `ANNUAL_BACKBONE`
- `LATEST_QUARTER`
- `GOVERNANCE_REPORT`

Luồng runtime chính:

```text
PDF/MD asset on R2
  -> Markdown canonical
  -> deterministic document tree
  -> full-document extraction manifest
  -> evidence spans, entities, facts, relations, moat signals
  -> node/evidence embeddings
  -> deterministic MOAT/GIL assessment
  -> API v2 / Web admin / ai-engine projection
```

## Runtime database

Runtime API/worker/E2E dùng PostgreSQL + pgvector.

```env
SAG_DATABASE_URL=postgresql+asyncpg://sag:sag@localhost:55432/sag
SAG_ALLOW_SQLITE_RUNTIME=false
```

SQLite không còn là runtime mặc định. Chỉ bật `SAG_ALLOW_SQLITE_RUNTIME=true` cho test cô lập hoặc kiểm tra legacy snapshot export.

## Local E2E environment

E2E để kiểm output MOAT/GIL nên chạy với:

- Docker/local PostgreSQL có `pgvector`, `unaccent`, `pg_trgm`.
- R2 thật nhưng write prefix tách biệt, ví dụ `dev/<machine>/...`, hoặc bucket test.
- LLM/extraction/embedding config thật nếu muốn kiểm extraction end-to-end; thiếu LLM/embedding phải fail stage, không sinh fallback.
- Ba tài liệu active cho cùng ticker: annual, latest quarter, governance report.

Embedding local hiện khuyến nghị dùng dimension `1536` để pgvector tạo được HNSW index:

```env
SAG_EMBEDDING_DIMENSIONS=1536
```

Nếu deployment dùng vector lớn hơn 2000 dimensions, SAG vẫn lưu được vector nhưng migration sẽ bỏ qua HNSW index vì giới hạn của pgvector.

Khởi động DB E2E cục bộ:

```bash
docker compose -f ../../docker-compose.e2e.yml up -d sag-postgres
alembic upgrade head
```

`SAG_DATABASE_URL` trong `.env` local mặc định khớp compose này:

```env
SAG_DATABASE_URL=postgresql+asyncpg://sag:sag@localhost:55432/sag
```

## Commands

```bash
pip install -e ".[dev,postgres]"
alembic upgrade head
uvicorn sag_api.main:app --reload --host 0.0.0.0 --port 8000
```

Worker:

```bash
sag-worker
```

Snapshot tooling:

```bash
sagctl snapshot-export --sqlite-path <legacy.db> --objects-root <objects-dir> --out <snapshot-dir>
sagctl snapshot-verify --snapshot <snapshot-dir>
sagctl snapshot-import --snapshot <snapshot-dir> --target postgresql+asyncpg://...
```
