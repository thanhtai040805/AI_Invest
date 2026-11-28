"""Phase 10 & Checkpoint D Bounded Research Agent execution service.

Implements Discovery/Fetch with SSRF/allowlist enforcement, manual redirect revalidation,
streaming byte limits, cumulative caps, duplicate guards, partition scoping,
failure classification, and relay into normal ingestion and incremental tree update.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.connectors.web import extract_web_markdown, extract_web_title
from sag_api.core.logging import get_logger
from sag_api.db.models import (
    DocumentVersion,
    IngestionRun,
    KnowledgeGapQuestion,
    ResearchTask,
)
from sag_api.jobs import JobQueue
from sag_api.services.document_service import handle_document_upload
from sag_api.services.incremental_tree_service import (
    IncrementalTreeUpdateResult,
    update_tree_incrementally,
)
from sag_api.services.knowledge_gap_question_service import (
    PhraseAdapter,
    generate_question_for_gap,
)
from sag_api.services.research_planner_service import (
    DEFAULT_MIN_PRIORITY,
    ResearchBudget,
    ResearchPolicy,
    ResearchTaskStatus,
    create_or_get_research_task,
    detect_eligible_gaps,
    is_url_allowed,
    normalize_url,
    plan_research_task,
)
from sag_api.services.routing_tree_service import (
    KnowledgeEdgeInput,
    KnowledgeUnitInput,
    RoutingSnapshot,
    TreeBuildConfig,
)
from sag_api.tools.builtin import _validated_public_web_url

log = get_logger("research.agent")

_USER_AGENT = "sag-research-bot/0.1 (+https://github.com/Zleap-AI/SAG)"
MAX_REDIRECT_HOPS = 5


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    url: str
    success: bool
    status_code: int | None = None
    content_bytes: bytes = b""
    payload_hash: str = ""
    content_hash: str = ""
    title: str = ""
    markdown: str = ""
    error: str | None = None
    retry_count: int = 0
    is_duplicate: bool = False
    is_no_info: bool = False


FetchAdapter = Callable[
    [str, ResearchPolicy, ResearchBudget],
    Awaitable[FetchOutcome],
]


async def fetch_url_bounded(
    url: str,
    policy: ResearchPolicy,
    budget: ResearchBudget,
    *,
    client: httpx.AsyncClient | None = None,
) -> FetchOutcome:
    """Fetch URL with SSRF guards, allowlist enforcement, stream bounds, manual redirect revalidation and retries."""
    if not is_url_allowed(url, policy):
        return FetchOutcome(
            url=url,
            success=False,
            error=f"URL is not in allowed domains: {url}",
        )

    retries = 0
    last_error: str | None = None
    close_client = False
    active_client = client
    if active_client is None:
        active_client = httpx.AsyncClient(
            timeout=budget.timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": _USER_AGENT},
            trust_env=False,
        )
        close_client = True

    try:
        for attempt in range(budget.max_retries + 1):
            if attempt > 0:
                retries += 1
                backoff = 0.1 * (2 ** (attempt - 1))
                await asyncio.sleep(backoff)

            current_url = url
            hop_count = 0
            redirect_disallowed = False

            while True:
                if not is_url_allowed(current_url, policy):
                    last_error = f"Redirect target not in allowed domains: {current_url}"
                    redirect_disallowed = True
                    break

                try:
                    validated_url = await _validated_public_web_url(current_url)
                except RuntimeError as err:
                    last_error = f"SSRF or invalid URL guard rejected address: {err}"
                    redirect_disallowed = True
                    break

                try:
                    async with active_client.stream("GET", validated_url) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("Location")
                            if not location:
                                last_error = f"Redirect HTTP {response.status_code} missing Location header"
                                redirect_disallowed = True
                                break
                            hop_count += 1
                            if hop_count > MAX_REDIRECT_HOPS:
                                last_error = f"Exceeded maximum redirect hops ({MAX_REDIRECT_HOPS})"
                                redirect_disallowed = True
                                break
                            current_url = normalize_url(urljoin(current_url, location))
                            continue

                        if response.status_code >= 400:
                            last_error = f"HTTP status error: {response.status_code}"
                            if response.status_code in {401, 403, 404, 410}:
                                # Non-retryable HTTP client errors
                                return FetchOutcome(
                                    url=url,
                                    success=False,
                                    status_code=response.status_code,
                                    error=last_error,
                                    retry_count=retries,
                                )
                            # Retryable HTTP status error (e.g. 500, 502, 503)
                            break

                        # Stream response bytes bounded strictly by budget.max_bytes_per_page
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in response.aiter_bytes(chunk_size=16 * 1024):
                            remaining = budget.max_bytes_per_page - size
                            if remaining <= 0:
                                break
                            chunks.append(chunk[:remaining])
                            size += min(len(chunk), remaining)
                            if size >= budget.max_bytes_per_page:
                                break

                        content_bytes = b"".join(chunks)
                        encoding = response.encoding or "utf-8"
                        html_text = content_bytes.decode(encoding, errors="replace")

                        markdown_body = extract_web_markdown(html_text)
                        if not markdown_body or not markdown_body.strip():
                            return FetchOutcome(
                                url=url,
                                success=True,
                                status_code=response.status_code,
                                content_bytes=content_bytes,
                                is_no_info=True,
                                retry_count=retries,
                            )

                        title = extract_web_title(html_text) or urlsplit(current_url).netloc
                        full_markdown = f"# {title}\n\n> Source: {current_url}\n\n{markdown_body}\n"
                        payload_hash = hashlib.sha256(full_markdown.encode("utf-8")).hexdigest()
                        content_hash = hashlib.sha256(markdown_body.encode("utf-8")).hexdigest()

                        return FetchOutcome(
                            url=url,
                            success=True,
                            status_code=response.status_code,
                            content_bytes=content_bytes,
                            payload_hash=payload_hash,
                            content_hash=content_hash,
                            title=title,
                            markdown=full_markdown,
                            retry_count=retries,
                        )
                except (httpx.TimeoutException, httpx.NetworkError) as req_err:
                    last_error = f"Network or timeout error: {req_err}"
                    break
                except Exception as unk_err:  # noqa: BLE001
                    last_error = f"Fetch failed: {unk_err}"
                    break

            if redirect_disallowed:
                # SSRF or Allowlist violations are non-retryable
                return FetchOutcome(
                    url=url,
                    success=False,
                    error=last_error,
                    retry_count=retries,
                )
    finally:
        if close_client:
            await active_client.aclose()

    return FetchOutcome(
        url=url,
        success=False,
        error=last_error or "Fetch failed after retries",
        retry_count=retries,
    )


async def execute_research_task(
    session: AsyncSession,
    task_id: str,
    *,
    job_queue: JobQueue | None = None,
    fetch_adapter: FetchAdapter | None = None,
) -> tuple[ResearchTask, list[FetchOutcome]]:
    """Execute a persisted research task: fetch targets, enforce guards, and relay to normal upload."""
    task = await session.get(ResearchTask, task_id)
    if task is None:
        raise ValueError(f"Research task {task_id} not found")

    # Prevent re-executing terminal tasks
    if task.status in {
        ResearchTaskStatus.COMPLETED.value,
        ResearchTaskStatus.INGESTING.value,
        ResearchTaskStatus.STOPPED_NO_NEW_INFO.value,
        ResearchTaskStatus.FAILED.value,
        ResearchTaskStatus.CANCELLED.value,
    }:
        log.info("Task %s is in terminal state %s; skipping execution", task.id, task.status)
        return task, []

    task.status = ResearchTaskStatus.FETCHING.value
    await session.flush()

    policy = ResearchPolicy(**{k: v for k, v in task.policy_json.items() if k != "discovery_provenance"})
    budget = ResearchBudget(**task.budget_json)
    fetcher = fetch_adapter or fetch_url_bounded

    target_urls: list[str] = list(task.target_urls_json)
    outcomes: list[FetchOutcome] = []
    ingested_run_ids: list[str] = list(task.ingestion_run_ids_json)
    ingested_version_ids: list[str] = list(task.document_version_ids_json)
    has_new_information = False
    execution = dict(task.execution_json or {})
    total_bytes_fetched = max(0, int(execution.get("total_bytes_fetched", 0) or 0))
    attempted_urls = set(execution.get("attempted_urls", []))
    seen_content_hashes = set(execution.get("content_hashes", []))

    def mark_attempted(url: str) -> None:
        attempted_urls.add(url)
        execution["attempted_urls"] = sorted(attempted_urls)
        task.execution_json = execution

    def remember_ingestion(run_id: str | None, version_id: str | None) -> None:
        if run_id and run_id not in ingested_run_ids:
            ingested_run_ids.append(run_id)
        if version_id and version_id not in ingested_version_ids:
            ingested_version_ids.append(version_id)
        task.ingestion_run_ids_json = ingested_run_ids
        task.document_version_ids_json = ingested_version_ids

    for url in target_urls:
        if url in attempted_urls:
            continue
        if task.fetch_count >= budget.max_fetches:
            log.info("Task %s reached max_fetches budget cap (%s)", task.id, budget.max_fetches)
            break

        if total_bytes_fetched >= budget.max_total_bytes:
            log.info("Task %s reached max_total_bytes budget cap (%s)", task.id, budget.max_total_bytes)
            break

        remaining_bytes = budget.max_total_bytes - total_bytes_fetched
        fetch_budget = replace(budget, max_bytes_per_page=min(budget.max_bytes_per_page, remaining_bytes))
        outcome = await fetcher(url, policy, fetch_budget)
        task.fetch_count += 1
        task.retry_count += outcome.retry_count
        total_bytes_fetched += len(outcome.content_bytes)
        execution["total_bytes_fetched"] = total_bytes_fetched
        task.execution_json = execution
        await session.flush()

        if not outcome.success or outcome.is_no_info or not outcome.payload_hash:
            outcomes.append(outcome)
            mark_attempted(url)
            continue

        content_hash = outcome.content_hash or hashlib.sha256(outcome.markdown.encode("utf-8")).hexdigest()

        # Check duplicate within this task execution
        if content_hash in seen_content_hashes:
            outcome = FetchOutcome(
                url=outcome.url,
                success=True,
                status_code=outcome.status_code,
                content_bytes=outcome.content_bytes,
                payload_hash=outcome.payload_hash,
                content_hash=content_hash,
                title=outcome.title,
                markdown=outcome.markdown,
                retry_count=outcome.retry_count,
                is_duplicate=True,
            )
            outcomes.append(outcome)
            mark_attempted(url)
            continue

        # Check duplicate against existing IngestionRuns in this tenant, project AND partition
        existing_run_stmt = (
            select(IngestionRun)
            .join(DocumentVersion, DocumentVersion.id == IngestionRun.document_version_id)
            .where(
                IngestionRun.tenant_id == task.tenant_id,
                IngestionRun.project_id == task.project_id,
                or_(IngestionRun.content_hash == content_hash, IngestionRun.payload_hash == outcome.payload_hash),
                DocumentVersion.metadata_json["security_partition_id"].as_string() == task.security_partition_id,
            )
            .order_by(
                (IngestionRun.research_task_id == task.id).desc(),
                IngestionRun.created_at.desc(),
                IngestionRun.id.desc(),
            )
            .limit(1)
        )
        existing_run = (await session.execute(existing_run_stmt)).scalar_one_or_none()
        if existing_run is not None:
            if existing_run.research_task_id == task.id:
                has_new_information = True
                remember_ingestion(existing_run.id, existing_run.document_version_id)
            outcome = FetchOutcome(
                url=outcome.url,
                success=True,
                status_code=outcome.status_code,
                content_bytes=outcome.content_bytes,
                payload_hash=outcome.payload_hash,
                content_hash=content_hash,
                title=outcome.title,
                markdown=outcome.markdown,
                retry_count=outcome.retry_count,
                is_duplicate=True,
            )
            seen_content_hashes.add(content_hash)
            execution["content_hashes"] = sorted(seen_content_hashes)
            task.execution_json = execution
            outcomes.append(outcome)
            mark_attempted(url)
            continue

        # Relay to Normal Ingestion Pipeline via handle_document_upload
        client_token = f"research-task-{task.id}-{outcome.payload_hash[:16]}"
        file_bytes = outcome.markdown.encode("utf-8")
        safe_netloc = urlsplit(url).netloc.replace(":", "-").replace("/", "-") or "web"
        original_filename = f"research_{safe_netloc}_{task.node_id}_{outcome.payload_hash[:8]}.md"

        upload_res = await handle_document_upload(
            session,
            tenant_id=task.tenant_id,
            project_id=task.project_id,
            owner_id=f"research-agent:{task.id}",
            security_partition_id=task.security_partition_id,
            client_token=client_token,
            file_bytes=file_bytes,
            original_filename=original_filename,
            content_type="text/markdown",
            content_hash=content_hash,
            logical_source_id=f"web:{normalize_url(url)}",
            research_task_id=task.id,
            job_queue=job_queue,
        )

        if upload_res.is_duplicate:
            existing_run = await session.get(IngestionRun, upload_res.run_id) if upload_res.run_id else None
            if existing_run and existing_run.research_task_id == task.id:
                has_new_information = True
                remember_ingestion(existing_run.id, upload_res.version_id)
            outcome = FetchOutcome(
                url=outcome.url,
                success=True,
                status_code=outcome.status_code,
                content_bytes=outcome.content_bytes,
                payload_hash=outcome.payload_hash,
                content_hash=content_hash,
                title=outcome.title,
                markdown=outcome.markdown,
                retry_count=outcome.retry_count,
                is_duplicate=True,
            )
            seen_content_hashes.add(content_hash)
            execution["content_hashes"] = sorted(seen_content_hashes)
            task.execution_json = execution
            outcomes.append(outcome)
            mark_attempted(url)
            continue

        has_new_information = True
        outcomes.append(outcome)
        seen_content_hashes.add(content_hash)
        execution["content_hashes"] = sorted(seen_content_hashes)
        task.execution_json = execution
        mark_attempted(url)

        remember_ingestion(upload_res.run_id, upload_res.version_id)

    task.execution_json = execution
    task.ingestion_run_ids_json = ingested_run_ids
    task.document_version_ids_json = ingested_version_ids

    # Stop conditions & lifecycle state finalization
    if has_new_information:
        task.status = ResearchTaskStatus.INGESTING.value
        task.error_message = None
    elif any(o.success for o in outcomes):
        # All fetched targets were duplicates or empty markdown
        task.status = ResearchTaskStatus.STOPPED_NO_NEW_INFO.value
        task.error_message = None
    else:
        # All fetch attempts failed
        task.status = ResearchTaskStatus.FAILED.value
        errors = [o.error for o in outcomes if o.error]
        task.error_message = "; ".join(errors) if errors else "All fetch attempts failed"

    await session.flush()
    await finalize_research_task(session, task.id)
    return task, outcomes


async def finalize_research_task(session: AsyncSession, task_id: str) -> None:
    """Complete research only after every linked ingestion run reaches a terminal state."""
    task = await session.get(ResearchTask, task_id)
    if task is None or task.status != ResearchTaskStatus.INGESTING.value:
        return

    expected_run_ids = set(task.ingestion_run_ids_json or [])
    if not expected_run_ids:
        return

    rows = (
        await session.execute(
            select(IngestionRun, DocumentVersion)
            .join(DocumentVersion, DocumentVersion.id == IngestionRun.document_version_id)
            .where(
                IngestionRun.tenant_id == task.tenant_id,
                IngestionRun.project_id == task.project_id,
                or_(
                    IngestionRun.research_task_id == task.id,
                    and_(
                        IngestionRun.research_task_id.is_(None),
                        IngestionRun.id.in_(expected_run_ids),
                    ),
                ),
            )
        )
    ).all()
    runs_by_id = {run.id: (run, version) for run, version in rows}
    if not expected_run_ids.issubset(runs_by_id):
        return

    results = [runs_by_id[run_id] for run_id in expected_run_ids]
    if any(run.status not in {"SUCCEEDED", "FAILED"} for run, _ in results):
        return

    if all(run.status == "SUCCEEDED" and version.search_status == "SEARCH_READY" for run, version in results):
        task.status = ResearchTaskStatus.COMPLETED.value
        task.error_message = None
    else:
        task.status = ResearchTaskStatus.FAILED.value
        task.error_message = next(
            (run.error_message for run, _ in results if run.error_message),
            "One or more research documents failed to reach SEARCH_READY",
        )
    await session.flush()


def apply_incremental_tree_update_for_research(
    base_snapshot: RoutingSnapshot,
    delta_units: Sequence[KnowledgeUnitInput],
    delta_edges: Sequence[KnowledgeEdgeInput] = (),
    config: TreeBuildConfig | None = None,
) -> IncrementalTreeUpdateResult:
    """Connect new research units into Knowledge Tree via standard delta update."""
    return update_tree_incrementally(
        base_snapshot,
        list(delta_units),
        list(delta_edges),
        config=config,
    )


def complete_research_task_with_tree_update(
    task: ResearchTask,
    base_snapshot: RoutingSnapshot,
    delta_units: Sequence[KnowledgeUnitInput],
    delta_edges: Sequence[KnowledgeEdgeInput] = (),
    config: TreeBuildConfig | None = None,
) -> IncrementalTreeUpdateResult:
    """Connect new research units into Knowledge Tree and mark research task COMPLETED."""
    update_result = apply_incremental_tree_update_for_research(
        base_snapshot,
        delta_units,
        delta_edges,
        config=config,
    )
    task.status = ResearchTaskStatus.COMPLETED.value
    return update_result


async def run_bounded_research_flow(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    security_partition_id: str,
    tree_version: str,
    candidate_urls: Sequence[str] = (),
    policy: ResearchPolicy | None = None,
    budget: ResearchBudget | None = None,
    min_priority: float = DEFAULT_MIN_PRIORITY,
    phrase_adapter: PhraseAdapter | None = None,
    fetch_adapter: FetchAdapter | None = None,
    job_queue: JobQueue | None = None,
    base_snapshot: RoutingSnapshot | None = None,
    tree_config: TreeBuildConfig | None = None,
) -> tuple[ResearchTask | None, list[FetchOutcome], IncrementalTreeUpdateResult | None]:
    """Run bounded research through ingestion; indexing publishes real SearchUnits and tree updates."""
    eligible_gaps = await detect_eligible_gaps(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        security_partition_id=security_partition_id,
        tree_version=tree_version,
        min_priority=min_priority,
        limit=1,
    )
    if not eligible_gaps:
        return None, [], None

    gap, _ = eligible_gaps[0]

    # Generate or reuse grounded question from Phase 9
    question = await generate_question_for_gap(
        session,
        gap.id,
        phrase=phrase_adapter,
    )
    if question is None:
        q_stmt = select(KnowledgeGapQuestion).where(KnowledgeGapQuestion.gap_id == gap.id)
        question = (await session.execute(q_stmt)).scalars().first()

    plan = plan_research_task(
        gap,
        question,
        tenant_id=tenant_id,
        project_id=project_id,
        security_partition_id=security_partition_id,
        tree_version=tree_version,
        policy=policy,
        budget=budget,
        candidate_urls=candidate_urls,
    )

    task, _ = await create_or_get_research_task(session, plan)
    task, outcomes = await execute_research_task(
        session,
        task.id,
        job_queue=job_queue,
        fetch_adapter=fetch_adapter,
    )

    # The ingestion/index pipeline owns real SearchUnit embeddings and tree publication.
    # Do not publish fabricated vectors or mark the task complete from fetched Markdown.
    return task, outcomes, None
