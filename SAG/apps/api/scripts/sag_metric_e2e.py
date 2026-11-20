"""Exercise an isolated BE account/project for SAG AI research metrics.

The full run uses the real SAG API, database, worker, embedding/LLM providers,
and Qdrant through an in-process ASGI client. SAG stores a local user mirror and
source mapping for the BE account/project; no auth bypass is added to a service.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import secrets
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ORIGINAL_CWD = Path.cwd()
API_ROOT = Path(__file__).resolve().parents[1]
SAG_ROOT = API_ROOT.parent.parent
WORKSPACE_ROOT = SAG_ROOT.parent.parent
DEFAULT_FILE = (
    WORKSPACE_ROOT
    / "Document"
    / "research-docs"
    / "Continuum_AI_Graduation_Project_Specification_v1.0.docx.md"
)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")

# Settings reads SAG/.env relative to cwd; scope the DB before importing db.py.
os.chdir(SAG_ROOT)
sys.path.insert(0, str(API_ROOT))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from sag_api.core.config import settings  # noqa: E402

USER_ID = os.getenv("SAG_METRIC_BE_USER_ID", "").strip()
USER_EMAIL = f"continuum-{USER_ID}@sag.invalid" if USER_ID else ""
LEGACY_USER_EMAIL = f"sag-metric-e2e+{USER_ID}@local.invalid" if USER_ID else ""
USER_NAME = "SAG Metric E2E"
TENANT_ID = os.getenv("SAG_METRIC_BE_ORGANIZATION_ID", "").strip()
PROJECT_ID = os.getenv("SAG_METRIC_BE_PROJECT_ID", "").strip()
# The Continuum SSO session uses the selected Project ID as its security partition.
PARTITION_ID = PROJECT_ID
LOGICAL_SOURCE_ID_PREFIX = "sag-metric-ai-research-spec-v1"
QUERY = (
    "Trong tài liệu, những rủi ro chính khi dùng AI trong quản lý tri thức tổ chức là gì? "
    "Các biện pháp nào giúp giảm hallucination và giữ nguồn gốc, phiên bản, quyền sở hữu "
    "cũng như trạng thái đã kiểm chứng của thông tin?"
)

if PROJECT_ID and settings.environment == "dev":
    _database = make_url(settings.database_url)
    if _database.drivername.startswith("postgresql") and _database.database == "sag":
        settings.database_url = str(
            _database.update_query_dict({"search_path": f"sag_metric_e2e_{PROJECT_ID[-8:].lower()}"})
        )

from sag_api.core.db import SessionLocal, engine, init_db  # noqa: E402
from sag_api.core.deps import (  # noqa: E402
    get_current_user,
    get_current_user_or_connector,
    require_path_source_access,
)
from sag_api.core.principal_assertion import (  # noqa: E402
    VerifiedPrincipal,
    require_principal_assertion,
    require_project_bff_principal,
    require_project_read_principal,
)
from sag_api.core.security import hash_password  # noqa: E402
from sag_api.db.base import new_id  # noqa: E402
from sag_api.db.models import (  # noqa: E402
    CanonicalBlock,
    Document,
    DocumentVersion,
    Job,
    SearchUnit,
    Source,
    SourceProjectMapping,
    User,
)
from sag_api.db.models.knowledge import KnowledgeEvidence, KnowledgeJob, KnowledgeUnit  # noqa: E402
from sag_api.db.models.routing_rag import ProjectSearchState, TreeManifest, TreeRoutingProfile  # noqa: E402
from sag_api.services.document_service import get_or_create_project_source  # noqa: E402


def _check_target_database() -> None:
    database = make_url(settings.database_url)
    expected_schema = f"sag_metric_e2e_{PROJECT_ID[-8:].lower()}"
    if (
        settings.environment != "dev"
        or database.database != "sag"
        or database.query.get("search_path") != expected_schema
    ):
        raise RuntimeError(
            f"Chỉ cho phép chạy trong schema development riêng '{expected_schema}' "
            "trên PostgreSQL DB 'sag'."
        )
    if not database.drivername.startswith("postgresql"):
        raise RuntimeError("Scope SAG metric chỉ được seed vào PostgreSQL development.")


def _check_be_scope() -> None:
    values = {
        "SAG_METRIC_BE_USER_ID": USER_ID,
        "SAG_METRIC_BE_ORGANIZATION_ID": TENANT_ID,
        "SAG_METRIC_BE_PROJECT_ID": PROJECT_ID,
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(
            "Cần ID account/organization/project đã tạo trong BE qua các biến: "
            + ", ".join(missing)
        )
    ids = (USER_ID, TENANT_ID, PROJECT_ID)
    valid_object_ids = all(
        len(value) == 24 and all(c in "0123456789abcdefABCDEF" for c in value)
        for value in ids
    )
    if not valid_object_ids:
        raise RuntimeError("BE user, organization và project ID phải là Mongo ObjectId 24 ký tự.")


async def _ensure_target_schema() -> None:
    schema = f"sag_metric_e2e_{PROJECT_ID[-8:].lower()}"
    if not schema.replace("_", "").isalnum():
        raise RuntimeError("Schema SAG metric có tên không hợp lệ.")
    async with engine.begin() as connection:
        await connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')


async def _prepare_scope() -> tuple[str, User]:
    _check_be_scope()
    _check_target_database()
    await _ensure_target_schema()
    await init_db()

    async with SessionLocal() as session:
        user = await session.get(User, USER_ID)
        email_owner = await session.scalar(select(User).where(User.email == USER_EMAIL))
        if email_owner is not None and email_owner.id != USER_ID:
            raise RuntimeError("SAG metric email đã thuộc account khác; không sửa account đó.")
        if user is None:
            user = User(
                id=USER_ID,
                email=USER_EMAIL,
                password_hash=hash_password(secrets.token_urlsafe(32)),
                name=USER_NAME,
                is_active=True,
            )
            session.add(user)
            await session.flush()
        elif user.email != USER_EMAIL:
            if user.email == LEGACY_USER_EMAIL:
                user.email = USER_EMAIL
            else:
                raise RuntimeError("SAG metric user ID đã được dùng bởi account khác.")
        elif not user.is_active:
            user.is_active = True

        source = await get_or_create_project_source(session, TENANT_ID, PROJECT_ID)
        config = source.config if isinstance(source.config, dict) else {}
        if config.get("tenant_id") != TENANT_ID or config.get("project_id") != PROJECT_ID:
            raise RuntimeError("Project source hiện có không khớp scope SAG metric.")

        mapping = await session.scalar(
            select(SourceProjectMapping).where(SourceProjectMapping.source_id == source.id)
        )
        if mapping is not None:
            if mapping.organization_id != TENANT_ID or mapping.project_id != PROJECT_ID:
                raise RuntimeError("Project source đã được gán vào scope khác; không ghi đè.")
            if mapping.state == "REVOKED":
                raise RuntimeError("Project mapping đã bị revoke; không tự khôi phục.")
            if mapping.state == "PENDING":
                mapping.state = "CONFIRMED"
                mapping.mapping_version += 1
                mapping.confirmed_at = datetime.now(UTC)
                mapping.confirmed_by = USER_ID
                mapping.approval_ref = "user-request:sag-metric-e2e"
        else:
            session.add(
                SourceProjectMapping(
                    id=new_id(),
                    source_id=source.id,
                    organization_id=TENANT_ID,
                    project_id=PROJECT_ID,
                    state="CONFIRMED",
                    mapping_version=1,
                    confirmed_at=datetime.now(UTC),
                    confirmed_by=USER_ID,
                    approval_ref="user-request:sag-metric-e2e",
                )
            )

        await session.commit()
        await session.refresh(user)
        source_id = source.id
        return source_id, user


async def _check_qdrant() -> None:
    import httpx

    if settings.sag_vector_provider != "qdrant":
        raise RuntimeError("SAG_SAG_VECTOR_PROVIDER không đặt là qdrant.")
    if not settings.sag_qdrant_url or not settings.sag_qdrant_api_key:
        raise RuntimeError("Thiếu URL hoặc API key Qdrant trong cấu hình SAG.")
    url = f"{settings.sag_qdrant_url.rstrip('/')}/collections"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=10.0)) as client:
            response = await client.get(url, headers={"api-key": settings.sag_qdrant_api_key})
    except httpx.RequestError as error:
        raise RuntimeError(f"Qdrant không kết nối được ({type(error).__name__}).") from None
    if response.status_code != 200:
        raise RuntimeError(f"Qdrant preflight trả HTTP {response.status_code}; kiểm tra endpoint/quyền truy cập.")


async def _pipeline_state(document_id: str) -> tuple[str, str, float, int, str, str]:
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        version = await session.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_no.desc())
            .limit(1)
        )
        job = await session.scalar(
            select(Job)
            .where(Job.document_id == document_id)
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        )
        if document is None or version is None or job is None:
            raise RuntimeError("SAG metric ingest chưa tạo đủ document/version/job trong database.")
        unit_count = await session.scalar(
            select(func.count(SearchUnit.id)).where(SearchUnit.document_version_id == version.id)
        )
        return (
            str(getattr(document.status, "value", document.status)).lower(),
            str(getattr(job.status, "value", job.status)).lower(),
            float(job.progress or 0.0),
            int(unit_count or 0),
            version.id,
            str(version.knowledge_status or "NOT_STARTED").upper(),
        )


async def _knowledge_tree_state(version_id: str) -> dict:
    async with SessionLocal() as session:
        version = await session.get(DocumentVersion, version_id)
        if version is None:
            raise RuntimeError("Không tìm thấy document version khi kiểm tra knowledge/tree.")

        knowledge_units = int(
            await session.scalar(
                select(func.count(KnowledgeUnit.id)).where(
                    KnowledgeUnit.document_version_id == version_id,
                    KnowledgeUnit.is_current.is_(True),
                )
            )
            or 0
        )
        evidence_rows = (
            await session.execute(
                select(KnowledgeEvidence.tier, func.count(KnowledgeEvidence.id))
                .join(KnowledgeUnit, KnowledgeUnit.id == KnowledgeEvidence.unit_id)
                .where(
                    KnowledgeUnit.document_version_id == version_id,
                    KnowledgeUnit.is_current.is_(True),
                )
                .group_by(KnowledgeEvidence.tier)
            )
        ).all()
        evidence_by_tier = {str(tier): int(count) for tier, count in evidence_rows}
        extraction_jobs = (
            await session.execute(
                select(KnowledgeJob.kind, KnowledgeJob.status, func.count(KnowledgeJob.id))
                .where(KnowledgeJob.document_version_id == version_id)
                .group_by(KnowledgeJob.kind, KnowledgeJob.status)
            )
        ).all()

        tree_state = await session.get(ProjectSearchState, PROJECT_ID)
        active_tree_version = tree_state.active_tree_version if tree_state else None
        active_slot = tree_state.active_routing_slot if tree_state else None
        manifest = (
            await session.get(TreeManifest, active_tree_version) if active_tree_version else None
        )
        profile_count = 0
        node_count = 0
        if manifest is not None:
            profile_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(TreeRoutingProfile)
                    .where(
                        TreeRoutingProfile.project_id == PROJECT_ID,
                        TreeRoutingProfile.tree_version == active_tree_version,
                        TreeRoutingProfile.document_version_id == version_id,
                        TreeRoutingProfile.partition_id == PARTITION_ID,
                    )
                )
                or 0
            )
            # Active published trees store their authoritative node count in
            # TreeManifest. KnowledgeTreeNode belongs to the legacy candidate
            # persistence path and is not populated by tree_publish_service.
            node_count = int(manifest.node_count)

        return {
            "knowledge_status": str(version.knowledge_status or "NOT_STARTED").upper(),
            "knowledge_units": knowledge_units,
            "evidence_by_tier": evidence_by_tier,
            "extraction_jobs": [
                {"kind": kind, "status": status, "count": int(count)}
                for kind, status, count in extraction_jobs
            ],
            "tree_version": active_tree_version,
            "tree_status": manifest.status if manifest else None,
            "tree_slot": active_slot,
            "tree_nodes": node_count,
            "tree_profiles_for_version": profile_count,
        }


async def _qdrant_points_for_version(version_id: str) -> int:
    import httpx

    collection = f"search_units_{PROJECT_ID}"
    url = f"{settings.sag_qdrant_url.rstrip('/')}/collections/{collection}/points/count"
    body = {
        "filter": {
            "must": [{"key": "document_version_id", "match": {"value": version_id}}]
        },
        "exact": True,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=12.0)) as client:
            response = await client.post(url, headers={"api-key": settings.sag_qdrant_api_key}, json=body)
    except httpx.RequestError as error:
        raise RuntimeError(f"Không đếm được SearchUnit trong Qdrant ({type(error).__name__}).") from None
    if response.status_code != 200:
        raise RuntimeError(f"Qdrant count SearchUnit trả HTTP {response.status_code}.")
    try:
        return int(response.json()["result"]["count"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Qdrant trả kết quả count SearchUnit không hợp lệ.") from error


async def _qdrant_tree_points_for_version(version_id: str, tree_version: str, routing_slot: str) -> int:
    import httpx

    slot = "a" if routing_slot == "SLOT_A" else "b" if routing_slot == "SLOT_B" else None
    if slot is None:
        return 0
    collection = f"search_units_{PROJECT_ID}"
    url = f"{settings.sag_qdrant_url.rstrip('/')}/collections/{collection}/points/count"
    body = {
        "filter": {
            "must": [
                {"key": "document_version_id", "match": {"value": version_id}},
                {"key": f"tree_version_{slot}", "match": {"value": tree_version}},
            ]
        },
        "exact": True,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=12.0)) as client:
            response = await client.post(url, headers={"api-key": settings.sag_qdrant_api_key}, json=body)
    except httpx.RequestError as error:
        raise RuntimeError(f"Không đếm được SearchUnit theo tree trong Qdrant ({type(error).__name__}).") from None
    if response.status_code != 200:
        raise RuntimeError(f"Qdrant count SearchUnit theo tree trả HTTP {response.status_code}.")
    try:
        return int(response.json()["result"]["count"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("Qdrant trả kết quả count tree SearchUnit không hợp lệ.") from error


async def _rebuild_qdrant_search_units(document_id: str, engine_manager: object) -> int:
    import httpx

    from sag_api.services.search_index_service import index_search_units_to_qdrant

    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        version = await session.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_no.desc())
            .limit(1)
        )
        if document is None or version is None:
            raise RuntimeError("Không tìm thấy document/version để dựng lại Qdrant index.")
        if document.project_id != PROJECT_ID or document.tenant_id != TENANT_ID:
            raise RuntimeError("Từ chối dựng lại Qdrant index ngoài project/tenant SAG metric.")
        source = await session.get(Source, document.source_id) if document.source_id else None
        units = list(
            (
                await session.scalars(
                    select(SearchUnit)
                    .where(SearchUnit.document_version_id == version.id)
                    .order_by(SearchUnit.id)
                )
            ).all()
        )
        blocks = list(
            (
                await session.scalars(
                    select(CanonicalBlock)
                    .where(CanonicalBlock.document_version_id == version.id)
                    .order_by(CanonicalBlock.ordinal)
                )
            ).all()
        )
        if source is None or not units or not blocks:
            raise RuntimeError("Canonical DB thiếu source/SearchUnit/block để dựng lại Qdrant index.")
        project_id = document.project_id
        tenant_id = document.tenant_id
        source_id = source.id
        config_id = source.sag_source_config_id
        blocks_by_id = {block.id: block for block in blocks}

    await engine_manager.provision(config_id, source)
    embedder = await engine_manager.get_sag_embedding(config_id, source)
    async with httpx.AsyncClient(
        base_url=settings.sag_qdrant_url.rstrip("/"),
        headers={"api-key": settings.sag_qdrant_api_key},
        timeout=httpx.Timeout(120.0, connect=10.0),
    ) as client:
        return await index_search_units_to_qdrant(
            client,
            project_id=project_id,
            units=units,
            version=version,
            tenant_id=tenant_id,
            embedder=embedder,
            blocks_by_id=blocks_by_id,
            source_id=source_id,
            document_id=document_id,
        )


def _principal() -> VerifiedPrincipal:
    now = int(time.time())
    return VerifiedPrincipal(
        subject=USER_ID,
        organization_id=TENANT_ID,
        allowed_project_ids=frozenset({PROJECT_ID}),
        issuer="local-sag-metric-e2e",
        key_id="local-test-only",
        token_id="local-sag-metric-e2e",
        issued_at=now,
        expires_at=now + 300,
        tenant_id=TENANT_ID,
        allowed_partition_ids=frozenset({PARTITION_ID}),
    )


async def _run_e2e(*, file_path: Path, timeout_seconds: int, search_after_tree: bool) -> int:
    import httpx

    from sag_api.main import app

    source_id, user = await _prepare_scope()
    if not file_path.is_file():
        raise RuntimeError(f"Không tìm thấy tài liệu: {file_path}")
    raw = file_path.read_bytes()
    if not raw:
        raise RuntimeError("Tài liệu đầu vào rỗng.")

    await _check_qdrant()
    principal = _principal()

    async def scoped_principal() -> VerifiedPrincipal:
        return principal

    async def scoped_user() -> User:
        return user

    async def scoped_source() -> Source:
        async with SessionLocal() as session:
            source = await session.get(Source, source_id)
            if source is None:
                raise RuntimeError("SAG metric source không còn trong database.")
            return source

    app.dependency_overrides[require_principal_assertion] = scoped_principal
    app.dependency_overrides[require_project_bff_principal] = scoped_principal
    app.dependency_overrides[require_project_read_principal] = scoped_principal
    app.dependency_overrides[get_current_user] = scoped_user
    app.dependency_overrides[get_current_user_or_connector] = scoped_user
    app.dependency_overrides[require_path_source_access] = scoped_source

    digest = hashlib.sha256(raw).hexdigest()
    idempotency_key = f"sag-metric-{digest[:40]}"
    logical_source_id = f"{LOGICAL_SOURCE_ID_PREFIX}-{digest[:20]}"
    filename = file_path.name

    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://sag-metric.local") as client:
                uploaded = await client.post(
                    f"/api/v1/projects/{PROJECT_ID}/documents/upload",
                    headers={
                        "Idempotency-Key": idempotency_key,
                        "X-Continuum-Security-Partition": PARTITION_ID,
                    },
                    data={"logical_source_id": logical_source_id},
                    files={"file": (filename, raw, "text/markdown")},
                    timeout=60.0,
                )
                if uploaded.status_code not in {200, 201}:
                    body = (
                        uploaded.json()
                        if uploaded.headers.get("content-type", "").startswith("application/json")
                        else {}
                    )
                    error_code = ((body.get("error") or {}).get("code") if isinstance(body, dict) else None)
                    print(f"Upload thất bại: HTTP {uploaded.status_code}; code={error_code or 'unknown'}")
                    return 1

                upload_result = uploaded.json()
                document_id = upload_result.get("document_id")
                version_no = upload_result.get("version_no", 1)
                if not document_id:
                    raise RuntimeError("Upload response thiếu document_id.")
                print(
                    f"Upload: document={document_id}, version={version_no}, "
                    f"duplicate={upload_result.get('is_duplicate')}"
                )

                deadline = time.monotonic() + timeout_seconds
                status_body: dict = {}
                reprocess_attempted = False
                last_progress_report = 0.0
                tracked_version_no = int(version_no)
                qdrant_count = 0
                while time.monotonic() < deadline:
                    status_response = await client.get(
                        f"/api/v1/projects/{PROJECT_ID}/documents/{document_id}/versions/{tracked_version_no}/status",
                        timeout=15.0,
                    )
                    if status_response.status_code != 200:
                        print(f"Kiểm tra trạng thái thất bại: HTTP {status_response.status_code}")
                        return 1
                    status_body = status_response.json()
                    (
                        document_status,
                        job_status,
                        job_progress,
                        unit_count,
                        version_id,
                        knowledge_status,
                    ) = await _pipeline_state(document_id)
                    tracked_version_no = int(
                        status_body.get("version_no") or tracked_version_no
                    )
                    failed = (
                        document_status == "failed"
                        or job_status == "failed"
                        or str(status_body.get("status", "")).upper() in {"FAILED", "ERROR"}
                    )
                    if failed:
                        error = status_body.get("error") or {}
                        if not reprocess_attempted:
                            retry_response = await client.post(
                                f"/api/v1/sources/{source_id}/documents/{document_id}/reprocess",
                                timeout=30.0,
                            )
                            if retry_response.status_code == 200:
                                reprocess_attempted = True
                                deadline = time.monotonic() + timeout_seconds
                                print("Ingest lỗi; đã xếp một lần reprocess trong project SAG metric.")
                                await asyncio.sleep(2)
                                continue
                        print(
                            "Ingest thất bại: "
                            f"document_status={document_status}, job_status={job_status}, "
                            f"stage={status_body.get('current_stage')}, "
                            f"code={error.get('code', 'unknown')}"
                        )
                        return 1

                    if document_status in {"paused", "pausing"} or job_status == "paused":
                        print("Ingest đang paused; resume document/job trước khi chạy tiếp E2E.")
                        return 1

                    if status_body.get("search_ready"):
                        qdrant_count = await _qdrant_points_for_version(version_id)
                        if unit_count > 0 and qdrant_count != unit_count:
                            rebuilt_count = await _rebuild_qdrant_search_units(
                                document_id,
                                app.state.engine_manager,
                            )
                            qdrant_count = await _qdrant_points_for_version(version_id)
                            print(
                                "Qdrant SearchUnit index đã lệch; dựng lại từ canonical DB: "
                                f"upserted={rebuilt_count}, qdrant={qdrant_count}, database={unit_count}."
                            )
                        if unit_count > 0 and qdrant_count != unit_count:
                            print(
                                "SearchUnit trong Qdrant chưa đồng bộ: "
                                f"qdrant={qdrant_count}, database={unit_count}."
                            )
                            return 1
                        if (
                            unit_count > 0
                            and qdrant_count == unit_count
                            and document_status == "ready"
                            and job_status == "succeeded"
                        ):
                            break

                    now = time.monotonic()
                    if now - last_progress_report >= 20:
                        print(
                            "Đang chờ ingest hoàn tất: "
                            f"document={document_status}, job={job_status}, "
                            f"job_progress={job_progress:.0%}, "
                            f"search_ready={bool(status_body.get('search_ready'))}, "
                            f"knowledge={knowledge_status}, "
                            f"qdrant={qdrant_count}/{unit_count}"
                        )
                        last_progress_report = now
                    await asyncio.sleep(2)
                else:
                    print(
                        "Hết thời gian chờ ingest: "
                        f"document={document_status}, job={job_status}, "
                        f"stage={status_body.get('current_stage')}"
                    )
                    return 1

                print(
                    "Ingest hoàn tất và SearchUnit đã đồng bộ: "
                    f"status={status_body.get('status')}, "
                    f"search_status={status_body.get('search_status')}, "
                    f"knowledge_status={status_body.get('knowledge_status')}, "
                    f"document_status={document_status}, job_status={job_status}, "
                    f"search_units={unit_count}, qdrant_points={qdrant_count}"
                )

                knowledge_deadline = time.monotonic() + timeout_seconds
                last_knowledge_report = 0.0
                knowledge_state: dict = {}
                tree_point_count = 0
                tree_rebuild_requested = False
                while time.monotonic() < knowledge_deadline:
                    knowledge_state = await _knowledge_tree_state(version_id)
                    has_evidence = sum(knowledge_state["evidence_by_tier"].values()) > 0
                    tree_ready = (
                        knowledge_state["tree_status"] == "ACTIVE"
                        and knowledge_state["tree_nodes"] > 0
                        and knowledge_state["tree_profiles_for_version"] > 0
                        and bool(knowledge_state["tree_version"])
                    )
                    if (
                        knowledge_state["knowledge_status"] == "DATA_READY"
                        and not tree_ready
                        and not tree_rebuild_requested
                    ):
                        rebuild_response = await client.post(
                            "/api/v1/universe/rebuild",
                            timeout=30.0,
                        )
                        if rebuild_response.status_code not in {200, 202}:
                            print(
                                "Không xếp được project tree rebuild: "
                                f"HTTP {rebuild_response.status_code}."
                            )
                            return 1
                        rebuild_job = rebuild_response.json()
                        print(
                            "Đã xác nhận/đưa vào queue tree rebuild: "
                            f"job={rebuild_job.get('id')}, status={rebuild_job.get('status')}"
                        )
                        tree_rebuild_requested = True
                    if tree_ready:
                        tree_point_count = await _qdrant_tree_points_for_version(
                            version_id,
                            knowledge_state["tree_version"],
                            knowledge_state["tree_slot"],
                        )
                    if (
                        knowledge_state["knowledge_status"] == "DATA_READY"
                        and knowledge_state["knowledge_units"] > 0
                        and has_evidence
                        and tree_ready
                        and tree_point_count == unit_count
                    ):
                        break
                    if knowledge_state["knowledge_status"] == "FAILED":
                        print("Knowledge extraction thất bại; kiểm tra knowledge_jobs theo document version.")
                        return 1

                    now = time.monotonic()
                    if now - last_knowledge_report >= 20:
                        print(
                            "Đang chờ extraction/tree: "
                            f"knowledge={knowledge_state['knowledge_status']}, "
                            f"units={knowledge_state['knowledge_units']}, "
                            f"evidence={sum(knowledge_state['evidence_by_tier'].values())}, "
                            f"tree={knowledge_state['tree_status'] or 'pending'}, "
                            f"tree_profiles={knowledge_state['tree_profiles_for_version']}, "
                            f"qdrant_tree_points={tree_point_count}/{unit_count}"
                        )
                        last_knowledge_report = now
                    await asyncio.sleep(2)
                else:
                    print(
                        "Hết thời gian chờ extraction/tree: "
                        f"knowledge={knowledge_state.get('knowledge_status')}, "
                        f"units={knowledge_state.get('knowledge_units')}, "
                        f"tree={knowledge_state.get('tree_status')}, "
                        f"profiles={knowledge_state.get('tree_profiles_for_version')}, "
                        f"qdrant_tree_points={tree_point_count}/{unit_count}"
                    )
                    return 1

                print(
                    "Extraction và tree đã sẵn sàng: "
                    f"knowledge_units={knowledge_state['knowledge_units']}, "
                    f"evidence={knowledge_state['evidence_by_tier']}, "
                    f"tree_version={knowledge_state['tree_version']}, "
                    f"tree_nodes={knowledge_state['tree_nodes']}, "
                    f"profiles={knowledge_state['tree_profiles_for_version']}, "
                    f"qdrant_tree_points={tree_point_count}/{unit_count}"
                )

                if not search_after_tree:
                    print("Đã xác nhận full pipeline đến extraction/tree; bỏ qua truy vấn search cuối.")
                    return 0

                searched = await client.post(
                    f"/api/v1/sources/{source_id}/search",
                    json={"query": QUERY, "top_k": 8},
                    timeout=120.0,
                )
                if searched.status_code != 200:
                    body = (
                        searched.json()
                        if searched.headers.get("content-type", "").startswith("application/json")
                        else {}
                    )
                    error_code = ((body.get("error") or {}).get("code") if isinstance(body, dict) else None)
                    print(f"Search thất bại: HTTP {searched.status_code}; code={error_code or 'unknown'}")
                    return 1

                result = searched.json()
                print(f"\nCâu trả lời AI ({result.get('answer_status', 'unknown')}):")
                print(result.get("summary") or "Không tạo được câu trả lời có trích dẫn.")
                citations = result.get("citations") or []
                if citations:
                    print("\nNguồn trích dẫn:")
                    for item in citations[:8]:
                        print(
                            f"- {item.get('document_name') or filename}, "
                            f"v{item.get('version_no') or version_no}, "
                            f"{item.get('heading') or item.get('section_path') or 'đoạn nguồn'}"
                        )
                if not citations:
                    print("Search trả HTTP 200 nhưng chưa có citation để kiểm chứng kết quả trích xuất.")
                    return 1
                print(
                    "\nSearch metrics: "
                    f"sections={len(result.get('sections') or [])}, "
                    f"answer_status={result.get('answer_status')}, "
                    f"fallback={result.get('stats', {}).get('fallback_used', False)}"
                )
                return 0
    finally:
        app.dependency_overrides.clear()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Trước --prepare-only hoặc full E2E, đặt SAG_METRIC_BE_USER_ID, "
            "SAG_METRIC_BE_ORGANIZATION_ID và SAG_METRIC_BE_PROJECT_ID theo các ID trong BE."
        ),
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Chỉ tạo SAG user mirror/source mapping cho account/project đã có trong BE.",
    )
    parser.add_argument("--qdrant-only", action="store_true", help="Chỉ kiểm tra Qdrant REST bằng cấu hình hiện tại.")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE, help="Tài liệu AI research dạng Markdown.")
    parser.add_argument(
        "--through-extraction",
        action="store_true",
        help="Kết thúc sau khi xác nhận ingestion, extraction và active tree; không gọi search cuối.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument(
        "--confirm-siliconflow-key-rotated",
        action="store_true",
        help="Xác nhận key SiliconFlow đã bị lộ trước đó đã được thu hồi/thay mới.",
    )
    return parser.parse_args()


async def _main(args: argparse.Namespace) -> int:
    if args.prepare_only:
        source_id, _user = await _prepare_scope()
        print(
            "Đã chuẩn bị SAG mirror/mapping cho scope BE: "
            f"user={USER_ID}, organization={TENANT_ID}, project={PROJECT_ID}, "
            f"mapping=CONFIRMED, source={source_id}"
        )
        return 0

    if args.qdrant_only:
        await _check_qdrant()
        print("Qdrant REST preflight thành công.")
        return 0

    if not args.confirm_siliconflow_key_rotated:
        raise RuntimeError(
            "Chưa chạy AI: cần thu hồi/thay key SiliconFlow đã lộ, cập nhật SAG/.env, "
            "rồi chạy lại với --confirm-siliconflow-key-rotated."
        )
    if args.timeout_seconds < 30 or args.timeout_seconds > 3600:
        raise RuntimeError("--timeout-seconds phải nằm trong khoảng 30–3600.")
    file_path = args.file if args.file.is_absolute() else ORIGINAL_CWD / args.file
    return await _run_e2e(
        file_path=file_path.resolve(),
        timeout_seconds=args.timeout_seconds,
        search_after_tree=not args.through_extraction,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main(_parse_args())))
    except RuntimeError as error:
        print(f"Không thể chạy SAG metric E2E: {error}", file=sys.stderr)
        raise SystemExit(2) from None
