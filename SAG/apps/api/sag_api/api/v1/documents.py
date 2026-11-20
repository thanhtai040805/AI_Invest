from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    Header,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.db import SessionLocal, get_session
from sag_api.core.deps import (
    get_current_user,
    get_current_user_or_connector,
    get_engine_manager,
    get_job_queue,
    require_path_source_access,
)
from sag_api.core.error_taxonomy import ErrorLayer, ErrorStage
from sag_api.core.errors import ApiError, ConflictError, ForbiddenError, NotFoundError, ValidationError
from sag_api.core.identity import VerifiedPrincipal as LocalVerifiedPrincipal
from sag_api.core.logging import get_logger
from sag_api.core.principal_assertion import (
    VerifiedPrincipal,
    require_principal_assertion,
    require_project_bff_principal,
    require_project_read_principal,
)
from sag_api.db.models import Document, User
from sag_api.enums import DocumentStatus
from sag_api.jobs import JobQueue
from sag_api.parsing.text import (
    TextDecodingError,
    is_text_preview,
    read_text_file,
)
from sag_api.sag import EngineManager
from sag_api.schemas.common import Ok
from sag_api.schemas.document import DocumentOut, IngestRequest
from sag_api.schemas.job import JobOut
from sag_api.schemas.routing_rag import (
    DocumentObjectIngestRequest,
    DocumentReprocessRequest,
    DocumentRevokeRequest,
    DocumentRevokeResponse,
    DocumentUploadResponse,
    DocumentVersionChunksResponse,
    DocumentVersionStatusResponse,
    ProjectDocumentReadinessBatchRequest,
    ProjectDocumentReadinessBatchResponse,
)
from sag_api.services.document_service import (
    delete_document,
    get_document_version_chunks,
    get_document_version_status,
    get_or_create_project_source,
    get_project_document_readiness_batch,
    get_public_document,
    handle_document_object_ingest,
    handle_document_upload,
    ingest_content,
    pause_document,
    reprocess_document,
    resolve_project_document,
    resume_document,
    revoke_project_document,
)
from sag_api.services.source_operation_service import source_document_mutation
from sag_api.services.source_service import (
    get_authorized_source,
    get_authorized_source_project,
    get_source,
    list_authorized_documents,
)

router = APIRouter(
    prefix="/sources/{source_id}/documents",
    tags=["documents"],
    dependencies=[Depends(require_path_source_access)],
)
project_router = APIRouter(prefix="/projects/{project_id}/documents", tags=["documents"])
log = get_logger("documents")


def _check_extension(filename: str | None) -> None:
    """Validate upload extension against whitelist (empty whitelist = no limit)."""
    allowed = settings.allowed_upload_exts
    if not allowed:
        return
    name = (filename or "").lower()
    if "." not in name or ("." + name.rsplit(".", 1)[1]) not in allowed:
        pretty = ", ".join(sorted(e.lstrip(".") for e in allowed))
        raise ValidationError(f"Unsupported file type. Allowed extensions: {pretty}")


def _upload_filename(filename: str | None) -> str:
    """Strip client paths from multipart filenames on POSIX and Windows."""
    return (filename or "upload").replace("\\", "/").rsplit("/", maxsplit=1)[-1] or "upload"


async def _source_upload_scope(
    session: AsyncSession,
    *,
    principal: VerifiedPrincipal,
    source_id: str,
    project_id: str | None,
    partition_id: str | None,
) -> tuple[str, str, str]:
    project_id = await get_authorized_source_project(
        session,
        principal=principal,
        source_id=source_id,
        project_id=project_id,
    )
    if not partition_id or not partition_id.strip():
        if len(principal.allowed_partition_ids) == 1:
            partition_id = next(iter(principal.allowed_partition_ids))
        else:
            raise ValidationError("X-Continuum-Security-Partition is required for this principal")
    if partition_id not in principal.allowed_partition_ids:
        raise ForbiddenError("The requested security partition is outside the principal scope")
    return principal.tenant_id, project_id, partition_id


async def _read_upload_file_bounded(file: UploadFile, max_bytes: int) -> bytes:
    """Read upload stream in chunks, aborting immediately if limit is exceeded."""
    chunk_size = 64 * 1024  # 64 KB
    chunks: list[bytes] = []
    total_bytes = 0

    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            raise ValidationError(
                f"File size exceeds maximum allowed ({max_bytes} bytes / {settings.max_upload_mb}MB)",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )
        chunks.append(chunk)

    if total_bytes == 0:
        raise ValidationError("Uploaded file cannot be empty", layer=ErrorLayer.CLIENT, stage=ErrorStage.UPLOAD)

    return b"".join(chunks)


def _verify_mime_signature(filename: str, file_bytes: bytes) -> None:
    """Xác thực chữ ký nhị phân (magic bytes) đối chiếu với phần mở rộng tệp.

    Ngăn chặn việc giả mạo phần mở rộng file (extension deception) hoặc chèn mã độc.
    """
    if not file_bytes:
        return
    name = filename.lower()
    ext = ("." + name.rsplit(".", 1)[1]) if "." in name else ""

    if ext == ".pdf":
        if not file_bytes.startswith(b"%PDF-"):
            raise ValidationError(
                f"Định dạng nội dung không khớp phần mở rộng: tệp '{filename}' thiếu chữ ký PDF hợp lệ (%PDF-)",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )
    elif ext in {".docx", ".pptx", ".xlsx", ".epub", ".odt", ".ods", ".odp"}:
        if not (
            file_bytes.startswith(b"PK\x03\x04")
            or file_bytes.startswith(b"PK\x05\x06")
            or file_bytes.startswith(b"PK\x07\x08")
        ):
            raise ValidationError(
                f"Định dạng nội dung không khớp phần mở rộng: tệp '{filename}' ({ext}) "
                "thiếu chữ ký ZIP container hợp lệ (PK)",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )
    elif ext in {".xls", ".doc", ".ppt"}:
        is_ole2 = file_bytes.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
        is_rtf_doc = ext == ".doc" and file_bytes.lstrip().startswith(b"{\\rtf")
        if not (is_ole2 or is_rtf_doc):
            raise ValidationError(
                f"Định dạng nội dung không khớp phần mở rộng: tệp '{filename}' thiếu chữ ký OLE2 container hợp lệ",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )
    elif ext == ".rtf":
        if not file_bytes.lstrip().startswith(b"{\\rtf"):
            raise ValidationError(
                f"Định dạng nội dung không khớp phần mở rộng: tệp '{filename}' thiếu chữ ký RTF hợp lệ ({{\\rtf)",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )
    elif ext == ".png":
        if not file_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValidationError(
                f"Định dạng nội dung không khớp phần mở rộng: tệp '{filename}' thiếu chữ ký PNG hợp lệ",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )
    elif ext in {".jpg", ".jpeg"}:
        if not file_bytes.startswith(b"\xff\xd8\xff"):
            raise ValidationError(
                f"Định dạng nội dung không khớp phần mở rộng: tệp '{filename}' thiếu chữ ký JPEG hợp lệ",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )
    elif ext == ".webp":
        if not (len(file_bytes) >= 12 and file_bytes.startswith(b"RIFF") and file_bytes[8:12] == b"WEBP"):
            raise ValidationError(
                f"Định dạng nội dung không khớp phần mở rộng: tệp '{filename}' thiếu chữ ký WEBP hợp lệ",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )
    elif ext in {
        ".txt",
        ".md",
        ".markdown",
        ".text",
        ".csv",
        ".tsv",
        ".json",
        ".html",
        ".htm",
        ".yaml",
        ".yml",
        ".xml",
        ".drawio",
        ".sql",
    }:
        if (
            file_bytes.startswith(b"MZ")
            or file_bytes.startswith(b"\x7fELF")
            or file_bytes.startswith(b"\x89PNG\r\n\x1a\n")
            or file_bytes.startswith(b"\xff\xd8\xff")
            or (b"\x00" in file_bytes)
        ):
            raise ValidationError(
                f"Tệp nhị phân hoặc hình ảnh trá hình dưới phần mở rộng văn bản '{ext}' "
                f"không được phép cho '{filename}'",
                layer=ErrorLayer.CLIENT,
                stage=ErrorStage.UPLOAD,
            )


@router.get("", response_model=list[DocumentOut])
async def list_(
    source_id: str,
    _user: User = Depends(get_current_user_or_connector),
    principal: VerifiedPrincipal = Depends(require_project_read_principal),
    session: AsyncSession = Depends(get_session),
) -> list[DocumentOut]:
    source = await get_source(session, source_id)
    documents = await list_authorized_documents(session, principal=principal, source_ids=[source.id])
    return [DocumentOut.model_validate(d) for d in documents]


@router.post("", response_model=DocumentOut, status_code=201)
async def upload(
    source_id: str,
    response: Response,
    request: Request,
    file: UploadFile = File(...),
    project_id: str | None = Form(None),
    logical_source_id: str | None = Form(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_continuum_security_partition: str | None = Header(None, alias="X-Continuum-Security-Partition"),
    _user: User = Depends(get_current_user_or_connector),
    principal: VerifiedPrincipal = Depends(require_principal_assertion),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue | None = Depends(get_job_queue),
) -> DocumentOut:
    folder_import_header = request.headers.get("X-SAG-Folder-Import-Id") if request else None
    filename = _upload_filename(file.filename)
    request_id = getattr(request.state, "request_id", None) if request else None
    folder_import_id: str | None = None
    if folder_import_header is not None:
        try:
            folder_import_id = str(uuid.UUID(folder_import_header))
        except ValueError:
            log.warning(
                "folder_import_upload operation=upload outcome=rejected reason=invalid_batch_id "
                "batch_id=invalid source_id=%s filename=%r byte_count=unknown request_id=%s",
                source_id,
                filename,
                request_id,
            )
            raise ValidationError("Invalid folder import batch identifier") from None

    data: bytes | None = None
    try:
        _check_extension(filename)
        max_upload_bytes = settings.max_upload_mb * 1024 * 1024
        data = await _read_upload_file_bounded(file, max_upload_bytes)
        _verify_mime_signature(filename, data)
        if folder_import_id is not None:
            log.info(
                "folder_import_upload operation=upload outcome=started batch_id=%s source_id=%s "
                "filename=%r byte_count=%s request_id=%s",
                folder_import_id,
                source_id,
                filename,
                len(data),
                request_id,
            )
        async with source_document_mutation(SessionLocal, source_id, "document-upload"):
            source = await get_source(session, source_id)
            tenant_id, authorized_project_id, partition_id = await _source_upload_scope(
                session,
                principal=principal,
                source_id=source.id,
                project_id=project_id,
                partition_id=x_continuum_security_partition,
            )
            result = await handle_document_upload(
                session,
                tenant_id=tenant_id,
                project_id=authorized_project_id,
                owner_id=principal.subject,
                security_partition_id=partition_id,
                client_token=idempotency_key or f"source-upload-{uuid.uuid4().hex}",
                file_bytes=data,
                original_filename=filename,
                content_type=file.content_type or "application/octet-stream",
                logical_source_id=logical_source_id or f"source:{source.id}:{filename}",
                job_queue=job_queue,
                source_id=source.id,
            )
            document = await session.get(Document, result.document_id)
            if document is None:
                raise NotFoundError("Document not found after ingestion")
            if response is not None and result.is_duplicate:
                response.status_code = status.HTTP_200_OK
    except ApiError as exc:
        if folder_import_id is not None:
            log.warning(
                "folder_import_upload operation=upload outcome=rejected batch_id=%s source_id=%s "
                "filename=%r byte_count=%s request_id=%s error_class=%s",
                folder_import_id,
                source_id,
                filename,
                len(data) if data is not None else "unknown",
                request_id,
                type(exc).__name__,
            )
        raise

    if folder_import_id is not None:
        log.info(
            "folder_import_upload operation=upload outcome=accepted batch_id=%s source_id=%s "
            "filename=%r byte_count=%s document_id=%s request_id=%s",
            folder_import_id,
            source_id,
            filename,
            len(data),
            document.id,
            request_id,
        )
    return DocumentOut.model_validate(document)


@router.post("/ingest", response_model=DocumentOut, status_code=201)
async def ingest(
    source_id: str,
    body: IngestRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_continuum_security_partition: str | None = Header(None, alias="X-Continuum-Security-Partition"),
    _user: User = Depends(get_current_user_or_connector),
    principal: VerifiedPrincipal = Depends(require_principal_assertion),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue | None = Depends(get_job_queue),
) -> DocumentOut:
    """Unified ingestion endpoint: external systems continuously push text or messages into source."""
    async with source_document_mutation(SessionLocal, source_id, "document-ingest"):
        source = await get_source(session, source_id)
        tenant_id, project_id, partition_id = await _source_upload_scope(
            session,
            principal=principal,
            source_id=source.id,
            project_id=body.project_id,
            partition_id=x_continuum_security_partition,
        )
        document = await ingest_content(
            session,
            source,
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=principal.subject,
            security_partition_id=partition_id,
            client_token=idempotency_key or f"source-ingest-{uuid.uuid4().hex}",
            text=body.text,
            title=body.title,
            messages=[m.model_dump() for m in body.messages] if body.messages else None,
            logical_source_id=body.logical_source_id,
            job_queue=job_queue,
        )
    return DocumentOut.model_validate(document)


@router.get("/{document_id}", response_model=DocumentOut)
async def get_(
    source_id: str,
    document_id: str,
    _user: User = Depends(get_current_user_or_connector),
    principal: VerifiedPrincipal = Depends(require_project_read_principal),
    session: AsyncSession = Depends(get_session),
) -> DocumentOut:
    source = await get_source(session, source_id)
    return DocumentOut.model_validate(
        await get_public_document(session, source, document_id, principal=principal)
    )


@router.get("/{document_id}/file")
async def get_file(
    source_id: str,
    document_id: str,
    _user: User = Depends(get_current_user),
    principal: VerifiedPrincipal = Depends(require_project_read_principal),
    session: AsyncSession = Depends(get_session),
):
    """Original file (preview/download). Returns 404 when file has been cleaned up."""
    import os

    from fastapi.responses import FileResponse

    from sag_api.core.errors import NotFoundError

    source = await get_source(session, source_id)
    document = await get_public_document(session, source, document_id, principal=principal)
    if document.octx_installation_id:
        raise NotFoundError("OCTX bundle does not contain the original file; inspect parsed content instead")
    if not document.storage_path or not os.path.isfile(document.storage_path):
        raise NotFoundError("Original file does not exist or has been cleaned up")
    return FileResponse(
        document.storage_path,
        media_type=document.content_type or "application/octet-stream",
        filename=document.filename,
        content_disposition_type="inline",
    )


@router.get("/{document_id}/preview")
async def get_preview(
    source_id: str,
    document_id: str,
    _user: User = Depends(get_current_user),
    principal: VerifiedPrincipal = Depends(require_project_read_principal),
    session: AsyncSession = Depends(get_session),
):
    """Return preview directly consumable by browser; text is normalized to UTF-8 while download preserves raw bytes."""
    import os

    from fastapi.responses import FileResponse, Response

    source = await get_source(session, source_id)
    document = await get_public_document(session, source, document_id, principal=principal)
    if document.octx_installation_id:
        raise NotFoundError("OCTX bundle does not contain the original file; inspect parsed content instead")
    if not document.storage_path or not os.path.isfile(document.storage_path):
        raise NotFoundError("Original file does not exist or has been cleaned up")
    if is_text_preview(document.filename, document.content_type):
        try:
            decoded = await asyncio.to_thread(read_text_file, document.storage_path)
        except TextDecodingError as error:
            raise ValidationError(f"Failed to identify text preview encoding: {error}") from error
        return Response(
            content=decoded.text,
            media_type="text/plain; charset=utf-8",
            headers={"X-Muse-Source-Encoding": decoded.encoding},
        )
    return FileResponse(
        document.storage_path,
        media_type=document.content_type or "application/octet-stream",
        filename=document.filename,
        content_disposition_type="inline",
    )


@router.get("/{document_id}/parsed")
async def get_parsed(
    source_id: str,
    document_id: str,
    _user: User = Depends(get_current_user),
    principal: VerifiedPrincipal = Depends(require_project_read_principal),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
):
    """Return full Markdown saved upon successful ingestion without triggering re-parse on read."""
    from fastapi.responses import Response

    source = await get_source(session, source_id)
    document = await get_public_document(session, source, document_id, principal=principal)
    if document.status != DocumentStatus.READY:
        if document.status == DocumentStatus.FAILED:
            raise ConflictError(document.error or "Document parsing failed, no parsed content available")
        raise ConflictError("Document parsing is still in progress")
    if not document.sag_source_id:
        raise NotFoundError("Parsed content not found, please reprocess the document")

    markdown = await engine_manager.get_document_markdown(
        source.sag_source_config_id,
        document.sag_source_id,
        source=source,
    )
    if not markdown:
        raise NotFoundError("Parsed content not found, please reprocess the document")
    return Response(content=markdown, media_type="text/markdown")


@router.post("/{document_id}/reprocess", response_model=JobOut)
async def reprocess(
    source_id: str,
    document_id: str,
    _user: User = Depends(get_current_user_or_connector),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue = Depends(get_job_queue),
) -> JobOut:
    async with source_document_mutation(SessionLocal, source_id, "document-reprocess"):
        source = await get_source(session, source_id)
        job = await reprocess_document(
            session,
            source,
            document_id,
            job_queue=job_queue,
        )
    return JobOut.model_validate(job)


@router.post("/{document_id}/pause", response_model=JobOut)
async def pause(
    source_id: str,
    document_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JobOut:
    # Pause is a control-plane signal to the worker that currently owns the
    # source lease. Requiring a second lease here would make cooperative pause
    # impossible; the worker observes this flag before its next chunk.
    source = await get_source(session, source_id)
    job = await pause_document(session, source, document_id)
    return JobOut.model_validate(job)


@router.post("/{document_id}/resume", response_model=JobOut)
async def resume(
    source_id: str,
    document_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue = Depends(get_job_queue),
) -> JobOut:
    async with source_document_mutation(SessionLocal, source_id, "document-resume"):
        source = await get_source(session, source_id)
        job = await resume_document(session, source, document_id, job_queue=job_queue)
    return JobOut.model_validate(job)


@router.delete("/{document_id}", response_model=Ok)
async def delete_(
    source_id: str,
    document_id: str,
    _user: User = Depends(get_current_user_or_connector),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue = Depends(get_job_queue),
) -> Ok:
    async with source_document_mutation(SessionLocal, source_id, "document-delete"):
        source = await get_source(session, source_id)
        await delete_document(
            session,
            source,
            document_id,
            job_queue=job_queue,
        )
    return Ok(detail="Document deleted")


# ============================================================================
# Knowledge Routing RAG: Project-Scoped Document Upload & Status Endpoints
# ============================================================================


@project_router.get(
    "/token-usage",
    summary="Aggregate per-file, per-component, and per-token-type usage and cost for a project",
)
async def get_project_token_usage_endpoint(
    project_id: str,
    principal: VerifiedPrincipal = Depends(require_project_read_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if project_id not in principal.allowed_project_ids or not principal.tenant_id:
        raise ForbiddenError(
            f"Principal '{principal.subject}' is not authorized to access project '{project_id}'"
        )
    from sag_api.core.token_usage import build_project_token_usage_report

    return await build_project_token_usage_report(
        session,
        tenant_id=principal.tenant_id,
        project_id=project_id,
    )
# ============================================================================


@project_router.post(
    "/upload",
    response_model=DocumentUploadResponse,
    summary="Upload a document version with strict idempotency and ACL partition",
)
async def upload_document_version(
    project_id: str,
    response: Response,
    file: UploadFile = File(...),
    source_published_at: datetime | None = Form(None),
    logical_source_id: str | None = Form(None),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    x_continuum_user_id: str | None = Header(None, alias="X-Continuum-User-Id"),
    x_continuum_security_partition: str | None = Header(None, alias="X-Continuum-Security-Partition"),
    x_continuum_tenant_id: str | None = Header(None, alias="X-Continuum-Tenant-Id"),
    principal: VerifiedPrincipal = Depends(require_project_bff_principal),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue | None = Depends(get_job_queue),
) -> DocumentUploadResponse:
    # Scope check 1: Project membership boundary
    if project_id not in principal.allowed_project_ids:
        raise ForbiddenError(
            f"Principal '{principal.subject}' is not authorized to access project '{project_id}'"
        )

    # The signed assertion is authoritative; compatibility headers may only echo it.
    if x_continuum_user_id and x_continuum_user_id != principal.subject:
        raise ForbiddenError("X-Continuum-User-Id does not match the signed principal")
    if not principal.tenant_id:
        raise ForbiddenError("The signed principal assertion has no tenant scope")
    if x_continuum_tenant_id and x_continuum_tenant_id != principal.tenant_id:
        raise ForbiddenError("X-Continuum-Tenant-Id does not match the signed principal")
    owner_id = principal.subject
    tenant_id = principal.tenant_id

    # Scope check 2: Security partition authorization
    partition = x_continuum_security_partition
    if not partition or not partition.strip():
        if len(principal.allowed_partition_ids) == 1:
            partition = next(iter(principal.allowed_partition_ids))
        else:
            raise ValidationError(
                "X-Continuum-Security-Partition header is required to enforce data isolation"
            )

    if partition not in principal.allowed_partition_ids:
        raise ForbiddenError(
            f"Principal '{principal.subject}' is not authorized for security partition '{partition}'"
        )

    filename = _upload_filename(file.filename)
    _check_extension(filename)

    max_upload_bytes = getattr(settings, "max_upload_size_bytes", None) or (settings.max_upload_mb * 1024 * 1024)
    file_bytes = await _read_upload_file_bounded(file, max_upload_bytes)
    _verify_mime_signature(filename, file_bytes)

    result = await handle_document_upload(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        owner_id=owner_id,
        security_partition_id=partition,
        client_token=idempotency_key,
        file_bytes=file_bytes,
        original_filename=filename,
        content_type=file.content_type or "application/octet-stream",
        logical_source_id=logical_source_id,
        source_published_at=source_published_at,
        job_queue=job_queue,
    )

    if result.is_duplicate:
        response.status_code = status.HTTP_200_OK
    else:
        response.status_code = status.HTTP_201_CREATED

    return result


@project_router.post(
    "/ingest-object",
    response_model=DocumentUploadResponse,
    summary="Zero-buffer ingest of a document version from an authorized R2/S3 presigned URL",
)
async def ingest_document_object_version(
    project_id: str,
    payload: DocumentObjectIngestRequest,
    response: Response,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    x_continuum_user_id: str | None = Header(None, alias="X-Continuum-User-Id"),
    x_continuum_security_partition: str | None = Header(None, alias="X-Continuum-Security-Partition"),
    x_continuum_tenant_id: str | None = Header(None, alias="X-Continuum-Tenant-Id"),
    principal: VerifiedPrincipal = Depends(require_project_bff_principal),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue | None = Depends(get_job_queue),
) -> DocumentUploadResponse:
    if project_id not in principal.allowed_project_ids:
        raise ForbiddenError(
            f"Principal '{principal.subject}' is not authorized to access project '{project_id}'"
        )
    if x_continuum_user_id and x_continuum_user_id != principal.subject:
        raise ForbiddenError("X-Continuum-User-Id does not match the signed principal")
    if not principal.tenant_id:
        raise ForbiddenError("The signed principal assertion has no tenant scope")
    if x_continuum_tenant_id and x_continuum_tenant_id != principal.tenant_id:
        raise ForbiddenError("X-Continuum-Tenant-Id does not match the signed principal")

    partition = x_continuum_security_partition
    if not partition or not partition.strip():
        if len(principal.allowed_partition_ids) == 1:
            partition = next(iter(principal.allowed_partition_ids))
        else:
            raise ValidationError(
                "X-Continuum-Security-Partition header is required to enforce data isolation"
            )
    if partition not in principal.allowed_partition_ids:
        raise ForbiddenError(
            f"Principal '{principal.subject}' is not authorized for security partition '{partition}'"
        )

    filename = _upload_filename(payload.original_filename)
    _check_extension(filename)

    result = await handle_document_object_ingest(
        session,
        tenant_id=principal.tenant_id,
        project_id=project_id,
        owner_id=principal.subject,
        security_partition_id=partition,
        client_token=idempotency_key,
        source_url=payload.source_url,
        original_filename=filename,
        expected_sha256=payload.expected_sha256,
        expected_size_bytes=payload.expected_size_bytes,
        content_type=payload.content_type or "application/octet-stream",
        logical_source_id=payload.logical_source_id,
        source_published_at=payload.source_published_at,
        job_queue=job_queue,
    )

    if result.is_duplicate:
        response.status_code = status.HTTP_200_OK
    else:
        response.status_code = status.HTTP_202_ACCEPTED

    return result


@project_router.get(
    "/{document_id}/versions/{version_no}/status",
    response_model=DocumentVersionStatusResponse,
    summary="Query fine-grained readiness and stage execution progress for a document version",
)
async def query_document_version_status(
    project_id: str,
    document_id: str,
    version_no: int,
    principal: VerifiedPrincipal = Depends(require_project_bff_principal),
    session: AsyncSession = Depends(get_session),
) -> DocumentVersionStatusResponse:
    if project_id not in principal.allowed_project_ids or not principal.tenant_id:
        raise ForbiddenError(
            f"Principal '{principal.subject}' is not authorized to access project '{project_id}'"
        )
    return await get_document_version_status(
        session,
        project_id=project_id,
        document_id=document_id,
        version_no=version_no,
        principal=LocalVerifiedPrincipal(
            user_id=principal.subject,
            tenant_id=principal.tenant_id,
            allowed_projects=principal.allowed_project_ids,
            allowed_partitions=principal.allowed_partition_ids,
        ),
    )


@project_router.post(
    "/readiness",
    response_model=ProjectDocumentReadinessBatchResponse,
    summary="Read live search readiness for a bounded batch of Project document versions",
)
async def project_document_readiness_batch_endpoint(
    project_id: str,
    payload: ProjectDocumentReadinessBatchRequest,
    principal: VerifiedPrincipal = Depends(require_project_bff_principal),
    session: AsyncSession = Depends(get_session),
) -> ProjectDocumentReadinessBatchResponse:
    if project_id not in principal.allowed_project_ids or not principal.tenant_id:
        raise ForbiddenError("The requested Project is outside the service scope")
    local_principal = LocalVerifiedPrincipal(
        user_id=principal.subject,
        tenant_id=principal.tenant_id,
        allowed_projects=principal.allowed_project_ids,
        allowed_partitions=principal.allowed_partition_ids,
    )
    return await get_project_document_readiness_batch(
        session,
        project_id=project_id,
        documents=payload.documents,
        principal=local_principal,
    )


@project_router.post(
    "/revoke",
    response_model=DocumentRevokeResponse,
    summary="Revoke a project document from search and knowledge indexing",
)
async def revoke_project_document_endpoint(
    project_id: str,
    payload: DocumentRevokeRequest,
    principal: VerifiedPrincipal = Depends(require_project_bff_principal),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue | None = Depends(get_job_queue),
) -> DocumentRevokeResponse:
    if project_id not in principal.allowed_project_ids or not principal.tenant_id:
        raise ForbiddenError(
            f"Principal '{principal.subject}' is not authorized to access project '{project_id}'"
        )
    revoke_result = await revoke_project_document(
        session,
        tenant_id=principal.tenant_id,
        project_id=project_id,
        logical_source_id=payload.logical_source_id,
        document_id=payload.document_id,
        reason=payload.reason,
    )
    if (
        (payload.reason or "").strip().upper() == "DOCUMENT_DELETED"
        and revoke_result.document_id
    ):
        try:
            doc, source = await _resolve_project_document_with_source(
                session,
                principal=principal,
                project_id=project_id,
                document_id=revoke_result.document_id,
            )
            async with source_document_mutation(SessionLocal, source.id, "document-delete"):
                await delete_document(
                    session,
                    source,
                    doc.id,
                    job_queue=job_queue,
                )
        except NotFoundError:
            pass
    return revoke_result


async def _resolve_project_document_with_source(
    session: AsyncSession,
    *,
    principal: VerifiedPrincipal,
    project_id: str,
    document_id: str,
):
    if project_id not in principal.allowed_project_ids or not principal.tenant_id:
        raise ForbiddenError(
            f"Principal '{principal.subject}' is not authorized to access project '{project_id}'"
        )
    doc = await resolve_project_document(
        session,
        tenant_id=principal.tenant_id,
        project_id=project_id,
        document_id_or_logical_id=document_id,
    )
    if not doc.source_id:
        source = await get_or_create_project_source(session, principal.tenant_id, project_id)
        doc.source_id = source.id
        await session.flush()
    else:
        source = await get_authorized_source(
            session, principal=principal, source_id=doc.source_id
        )
        if source is None:
            source = await get_or_create_project_source(
                session, principal.tenant_id, project_id
            )
            if doc.source_id != source.id:
                doc.source_id = source.id
                await session.flush()
    return doc, source


@project_router.post(
    "/{document_id}/pause",
    response_model=JobOut,
    summary="Pause an in-flight ingestion job for a project document",
)
async def pause_project_document_endpoint(
    project_id: str,
    document_id: str,
    principal: VerifiedPrincipal = Depends(require_project_bff_principal),
    session: AsyncSession = Depends(get_session),
) -> JobOut:
    doc, source = await _resolve_project_document_with_source(
        session, principal=principal, project_id=project_id, document_id=document_id
    )
    job = await pause_document(session, source, doc.id)
    return JobOut.model_validate(job)


@project_router.post(
    "/{document_id}/resume",
    response_model=JobOut,
    summary="Resume a paused ingestion job for a project document",
)
async def resume_project_document_endpoint(
    project_id: str,
    document_id: str,
    principal: VerifiedPrincipal = Depends(require_project_bff_principal),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue = Depends(get_job_queue),
) -> JobOut:
    doc, source = await _resolve_project_document_with_source(
        session, principal=principal, project_id=project_id, document_id=document_id
    )
    async with source_document_mutation(SessionLocal, source.id, "document-resume"):
        job = await resume_document(session, source, doc.id, job_queue=job_queue)
    return JobOut.model_validate(job)


@project_router.post(
    "/{document_id}/reprocess",
    response_model=JobOut,
    summary="Reprocess a project document from failed or ready state",
)
async def reprocess_project_document_endpoint(
    payload: DocumentReprocessRequest | None = Body(default=None),
    *,
    project_id: str,
    document_id: str,
    principal: VerifiedPrincipal = Depends(require_project_bff_principal),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue = Depends(get_job_queue),
) -> JobOut:
    doc, source = await _resolve_project_document_with_source(
        session, principal=principal, project_id=project_id, document_id=document_id
    )
    async with source_document_mutation(SessionLocal, source.id, "document-reprocess"):
        job = await reprocess_document(
            session,
            source,
            doc.id,
            job_queue=job_queue,
            remote_source_url=payload.source_url if payload else None,
            expected_sha256=payload.expected_sha256 if payload else None,
            expected_size_bytes=payload.expected_size_bytes if payload else None,
            original_filename=payload.original_filename if payload else None,
        )
    return JobOut.model_validate(job)



@project_router.get(
    "/{document_id}/versions/{version_no}/chunks",
    response_model=DocumentVersionChunksResponse,
    summary="List extracted canonical chunks for an indexed project document version",
)
async def query_document_version_chunks(
    project_id: str,
    document_id: str,
    version_no: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: VerifiedPrincipal = Depends(require_project_bff_principal),
    session: AsyncSession = Depends(get_session),
) -> DocumentVersionChunksResponse:
    if project_id not in principal.allowed_project_ids or not principal.tenant_id:
        raise ForbiddenError(
            f"Principal '{principal.subject}' is not authorized to access project '{project_id}'"
        )
    return await get_document_version_chunks(
        session,
        project_id=project_id,
        document_id=document_id,
        version_no=version_no,
        limit=limit,
        offset=offset,
        principal=LocalVerifiedPrincipal(
            user_id=principal.subject,
            tenant_id=principal.tenant_id,
            allowed_projects=principal.allowed_project_ids,
            allowed_partitions=principal.allowed_partition_ids,
        ),
    )
