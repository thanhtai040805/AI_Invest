"""Phase 10 & Checkpoint D Research Planner and Gap Detector service.

Pure deterministic planning, domain allowlist enforcement, budget bounding,
and idempotent persistence for research tasks.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.db.models import (
    KnowledgeGap,
    KnowledgeGapQuestion,
    KnowledgeQualityAssessment,
    ResearchTask,
)
from sag_api.services.knowledge_service import checksum

PLANNER_VERSION = "research-planner-v1"
DEFAULT_MIN_PRIORITY = 0.20


class ResearchTaskStatus(StrEnum):
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    FETCHING = "FETCHING"
    INGESTING = "INGESTING"
    COMPLETED = "COMPLETED"
    STOPPED_NO_NEW_INFO = "STOPPED_NO_NEW_INFO"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


ACTIVE_TASK_STATUSES = frozenset(
    {
        ResearchTaskStatus.PENDING,
        ResearchTaskStatus.PLANNING,
        ResearchTaskStatus.FETCHING,
        ResearchTaskStatus.INGESTING,
    }
)


@dataclass(frozen=True, slots=True)
class ResearchPolicy:
    allowed_domains: tuple[str, ...] = ("docs.python.org", "wikipedia.org", "example.com")
    allow_subdomains: bool = True
    require_https: bool = True


@dataclass(frozen=True, slots=True)
class ResearchBudget:
    max_fetches: int = 3
    max_bytes_per_page: int = 2 * 1024 * 1024  # 2 MB
    max_total_bytes: int = 6 * 1024 * 1024     # 6 MB
    timeout_seconds: float = 15.0
    max_retries: int = 2

    def __post_init__(self) -> None:
        if (
            not 1 <= self.max_fetches <= 100
            or not 1 <= self.max_bytes_per_page <= 8 * 1024 * 1024
            or not 1 <= self.max_total_bytes <= 100 * 1024 * 1024
            or not 0 < self.timeout_seconds <= 60
            or not 0 <= self.max_retries <= 5
        ):
            raise ValueError("Research budget exceeds supported bounds")


@dataclass(frozen=True, slots=True)
class ResearchPlan:
    gap_id: str
    question_id: str | None
    tenant_id: str
    project_id: str
    security_partition_id: str
    tree_version: str
    node_id: str
    policy: ResearchPolicy
    budget: ResearchBudget
    target_urls: tuple[str, ...]
    reason_codes: tuple[str, ...]
    checksum: str
    discovery_provenance: dict[str, object] = ()
    gap_checksum: str = ""


def discover_candidates_from_gap_or_question(
    gap: KnowledgeGap,
    question: KnowledgeGapQuestion | None,
    policy: ResearchPolicy,
    budget: ResearchBudget,
) -> tuple[tuple[str, ...], dict[str, object]]:
    """Deterministic discovery generating candidate URLs and provenance from question or gap and policy."""
    if question is not None and question.question_text:
        raw_query = question.question_text.strip()
        strategy = "grounded_question_discovery"
        question_id = question.id
    else:
        raw_query = gap.node_id.replace("node-", "").replace("-", " ").strip()
        strategy = "gap_node_discovery"
        question_id = None

    words = [w for w in "".join(c if c.isalnum() else " " for c in raw_query).split() if len(w) > 2]
    topic_query = "+".join(words[:4]) or "overview"

    candidates: list[str] = []
    for domain in policy.allowed_domains:
        d_clean = domain.lower().strip()
        if not d_clean:
            continue
        if "python" in d_clean:
            candidates.append(f"https://{d_clean}/3/search.html?q={topic_query}")
        elif "wikipedia" in d_clean:
            candidates.append(f"https://{d_clean}/wiki/Special:Search?search={topic_query}")
        else:
            candidates.append(f"https://{d_clean}/search?q={topic_query}")

    provenance: dict[str, object] = {
        "strategy": strategy,
        "question_id": question_id,
        "query_terms": list(words[:4]),
        "discovered_count": len(candidates),
    }
    return tuple(candidates[: budget.max_fetches]), provenance


def discover_candidates_from_question(
    question: KnowledgeGapQuestion,
    policy: ResearchPolicy,
    budget: ResearchBudget,
) -> tuple[tuple[str, ...], dict[str, object]]:
    dummy_gap = KnowledgeGap(id=question.gap_id, node_id="", eligible=True)
    return discover_candidates_from_gap_or_question(dummy_gap, question, policy, budget)


def normalize_url(raw_url: str) -> str:
    """Normalize URL by stripping fragments, lowercase scheme/host, and trailing slashes."""
    url = raw_url.strip()
    if not url:
        return ""
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    # Remove standard port if explicitly included
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]

    path = parsed.path
    if path and path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def is_url_allowed(url: str, policy: ResearchPolicy) -> bool:
    """Validate that a URL complies with scheme and domain allowlist policy."""
    normalized = normalize_url(url)
    if not normalized:
        return False
    parsed = urlsplit(normalized)
    if policy.require_https and parsed.scheme != "https":
        return False
    if parsed.scheme not in {"http", "https"}:
        return False

    host = parsed.hostname
    if not host or any(char.isspace() for char in host):
        return False

    # Block raw IPv4 loopback / private IP strings immediately
    if host in {"localhost", "127.0.0.1", "0.0.0.0"} or host.startswith(
        ("10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.", "172.2", "172.3")
    ):
        return False

    for allowed in policy.allowed_domains:
        allowed_clean = allowed.lower().strip()
        if not allowed_clean:
            continue
        if host == allowed_clean:
            return True
        if policy.allow_subdomains and host.endswith("." + allowed_clean):
            return True

    return False


def plan_research_task(
    gap: KnowledgeGap,
    question: KnowledgeGapQuestion | None,
    *,
    tenant_id: str,
    project_id: str,
    security_partition_id: str,
    tree_version: str,
    policy: ResearchPolicy | None = None,
    budget: ResearchBudget | None = None,
    candidate_urls: Sequence[str] = (),
) -> ResearchPlan:
    """Pure deterministic function planning a bounded research task from a scored gap."""
    if not gap.eligible or gap.priority is None:
        raise ValueError(f"Cannot plan research task for ineligible gap {gap.id}")

    active_policy = policy or ResearchPolicy()
    active_budget = budget or ResearchBudget()

    discovery_provenance: dict[str, object] = {}
    candidates_to_evaluate: list[str] = list(candidate_urls)
    if not candidates_to_evaluate:
        discovered_urls, discovery_provenance = discover_candidates_from_gap_or_question(
            gap, question, active_policy, active_budget
        )
        candidates_to_evaluate.extend(discovered_urls)
    else:
        discovery_provenance = {
            "strategy": "caller_supplied_candidates",
            "candidate_count": len(candidates_to_evaluate),
        }

    # Filter and normalize candidate URLs against the policy allowlist
    valid_urls: list[str] = []
    seen: set[str] = set()
    for candidate in candidates_to_evaluate:
        norm = normalize_url(candidate)
        if norm and norm not in seen and is_url_allowed(norm, active_policy):
            seen.add(norm)
            valid_urls.append(norm)

    # Strictly cap targets by budget.max_fetches
    bounded_urls = tuple(valid_urls[: active_budget.max_fetches])
    reason_codes = tuple(sorted(str(rc) for rc in gap.reason_codes_json))

    plan_checksum = checksum(
        {
            "planner_version": PLANNER_VERSION,
            "gap_id": gap.id,
            "gap_checksum": gap.checksum,
            "question_id": question.id if question else None,
            "question_checksum": question.checksum if question else None,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "security_partition_id": security_partition_id,
            "tree_version": tree_version,
            "node_id": gap.node_id,
            "policy": asdict(active_policy),
            "budget": asdict(active_budget),
            "target_urls": list(bounded_urls),
            "reason_codes": list(reason_codes),
            "discovery_provenance": discovery_provenance,
        }
    )

    return ResearchPlan(
        gap_id=gap.id,
        question_id=question.id if question else None,
        tenant_id=tenant_id,
        project_id=project_id,
        security_partition_id=security_partition_id,
        tree_version=tree_version,
        node_id=gap.node_id,
        policy=active_policy,
        budget=active_budget,
        target_urls=bounded_urls,
        reason_codes=reason_codes,
        checksum=plan_checksum,
        discovery_provenance=discovery_provenance,
        gap_checksum=gap.checksum,
    )


async def detect_eligible_gaps(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    security_partition_id: str,
    tree_version: str,
    min_priority: float = DEFAULT_MIN_PRIORITY,
    limit: int = 10,
) -> list[tuple[KnowledgeGap, KnowledgeQualityAssessment]]:
    """Exclude active and already-handled gap revisions before applying the limit."""
    if limit < 1:
        return []
    handled = select(ResearchTask.id).where(
        ResearchTask.gap_id == KnowledgeGap.id,
        or_(
            ResearchTask.status.in_([status.value for status in ACTIVE_TASK_STATUSES]),
            ResearchTask.execution_json["gap_checksum"].as_string() == KnowledgeGap.checksum,
            # Older tasks have no revision checkpoint; do not silently rerun them.
            ResearchTask.execution_json["gap_checksum"].as_string().is_(None),
        ),
    ).exists()
    stmt = (
        select(KnowledgeGap, KnowledgeQualityAssessment)
        .join(
            KnowledgeQualityAssessment,
            KnowledgeGap.assessment_id == KnowledgeQualityAssessment.id,
        )
        .where(
            KnowledgeQualityAssessment.tenant_id == tenant_id,
            KnowledgeQualityAssessment.project_id == project_id,
            KnowledgeQualityAssessment.security_partition_id == security_partition_id,
            KnowledgeQualityAssessment.tree_version == tree_version,
            KnowledgeGap.eligible.is_(True),
            KnowledgeGap.priority >= min_priority,
            ~handled,
        )
        .order_by(KnowledgeGap.priority.desc(), KnowledgeGap.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).all())


async def create_or_get_research_task(
    session: AsyncSession,
    plan: ResearchPlan,
) -> tuple[ResearchTask, bool]:
    """Idempotently persist or fetch a research task based on its plan checksum."""
    task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"sag:research-task:{plan.checksum}"))
    existing = await session.get(ResearchTask, task_id)
    if existing is not None:
        if existing.checksum != plan.checksum or existing.gap_id != plan.gap_id:
            raise ValueError("Persisted research task identity checksum mismatch")
        return existing, False

    task = ResearchTask(
        id=task_id,
        tenant_id=plan.tenant_id,
        project_id=plan.project_id,
        security_partition_id=plan.security_partition_id,
        tree_version=plan.tree_version,
        node_id=plan.node_id,
        gap_id=plan.gap_id,
        question_id=plan.question_id,
        status=ResearchTaskStatus.PENDING.value,
        reason_codes_json=list(plan.reason_codes),
        policy_json={
            **asdict(plan.policy),
            "discovery_provenance": dict(plan.discovery_provenance),
        },
        budget_json=asdict(plan.budget),
        execution_json={"gap_checksum": plan.gap_checksum},
        target_urls_json=list(plan.target_urls),
        fetch_count=0,
        retry_count=0,
        ingestion_run_ids_json=[],
        document_version_ids_json=[],
        checksum=plan.checksum,
        error_message=None,
    )
    session.add(task)
    await session.flush()
    return task, True
