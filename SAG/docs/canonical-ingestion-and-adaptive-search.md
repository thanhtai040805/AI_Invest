# Canonical ingestion and adaptive retrieval

Versioned uploads publish `SEARCH_READY` after canonical parsing, dedup/lineage,
embedding and verified SearchUnit indexing. They do not invoke the legacy
Event/Entity extractor by default. Canonical embedding uses the existing SDK
embedding client and runtime governor directly, with its configured timeout,
retry, concurrency and shared cache; it does not initialize Event storage.

FOUNDATION discovery accepts active, search-ready versions while a document is
pending/loading/extracting or ready. It still checks readiness time, validity,
confirmed project/source mapping and canonical provenance/checksums. Paused,
deleting and failed documents are excluded. FOUNDATION owns E0/E1 and its
readiness independently of the optional Event pipeline. By default it does not
borrow legacy engine entities. E2 remains a separate selective queue; disabling
E2 does not prevent ordinary indexing or E0/E1.

`SAG_DOCUMENT_EVENT_EXTRACTION_ENABLED=false` is the default. Operators needing
legacy Event/Entity features can explicitly enable it. Legacy unversioned
processing also skips `engine.extract` unless enabled. SDK batch extraction and
its retry/checkpoint behavior are retained for that opt-in path.

## Retrieval

- The shared retrieval path fixes `top_k` at 5, including Search, Agent, MCP and
  Dify. Positive request values are accepted for compatibility but do not change
  retrieval breadth; MCP and Dify may return fewer if their clients request fewer.
- `search_top_k` is initial candidate breadth, not the number of final results.
  Canonical retrieval doubles candidate breadth while retrievers are saturated
  and anchors/count coverage still needs expansion. Existing relevance gates,
  deduplication and MMR determine the eligible evidence returned.
- The shared query deadline covers all retrieval waves. Authorized manifests,
  project/tenant/partition checks and snapshot verification are repeated; an
  earlier wave is not returned after a later authorization failure.
- `search_unit_candidate_limit` is a configurable work/memory budget (default
  1024, no schema ceiling of 1024). It is not an assertion of answer completeness.
  Responses expose effective K, wave count, stop reason
  and budget exhaustion. Relevance plateau is not semantic answerability.
- Source selection defaults to all authorized sources. A positive optional
  `search_source_candidate_limit` rejects an over-budget scope explicitly;
  it never silently selects the first 16 sources. Source concurrency remains
  independently bounded.
- Query deadlines, model context packing, upload validation and authorization
  boundaries remain enforced. Returning more evidence does not bypass citation
  or context validation.

The canonical path no longer depends on a fixed two-document sibling limit,
three-seed graph expansion or two-section expansion per version. These are
bounded by the current retrieval work budget instead.

## Validation

Regression tests cover canonical ingestion without an Event LLM call, failed
manifest rejection, FOUNDATION before legacy completion, optional extraction,
standalone SDK embedding lifecycle, source scopes beyond 16, fixed Top-5,
candidate-budget reporting and frontend search state.

Dataset runners use the same API/runtime and fixed Top-5 retrieval. Live smoke
tests and complete benchmark runs are separate artifacts; a smoke test is not
a full quality or latency benchmark.

## FOUNDATION, graph and routing execution

Document jobs no longer rebuild FOUNDATION inline. The durable knowledge worker
is the single producer for each version/input checksum; it commits E0/E1 and
dense KnowledgeUnits as DATA_READY without rebuilding a whole-project graph.
Completed knowledge jobs retain a durable tree-delivery timestamp. Discovery
retries missing deliveries after enqueue failures or restarts, independently of
the completed extraction transaction.

The existing project tree job computes graph and routing together. Queued
requests coalesce; requests arriving while a rebuild runs retain a queued
follow-up. A PostgreSQL session advisory lock serializes builders for a project
across API processes without holding a write transaction on the lock connection.
Before publishing, the candidate's KnowledgeUnit inventory/checksums are checked
again under the project-input lock shared by FOUNDATION and E2 writes.

Graph math precomputes dense normalization/norms and LSH signs while preserving
the existing arithmetic order, graph calibration and output checksum. CPU work
runs in spawned child processes; cancellation/deadline kills and joins the owned
child before releasing admission. Database sessions and provider clients are
never passed to CPU children. Windows desktop startup retains freeze_support.

Graph input fingerprints are indexed and checked before expensive computation;
cache hits still verify evidence/output integrity. Evidence checksum validation
uses bounded SQL batches. Migration 0007 adds nullable fingerprint and delivery
columns without dropping existing graphs, documents or benchmark results. Local
startup performs the same additive upgrades.

Retrieval pins one graph build from the active project snapshot and invalidates
its scope cache on KnowledgeUnit checksum changes. Legacy unpinned graphs remain
eligible only when their stored inventory covers and matches the authorized
current scope. Historical edges are not unioned across graph builds.

E2 keeps its own queue timeout. The default FOUNDATION budget covers configured
embedding attempts, admission wait and a commit margin, rather than the former
60-second E2 timeout. An explicit override is supported. CPU graph timeout is
separate. Transient HTTP/transport causes survive domain wrapping; retry attempts
refresh persisted document state before returning it to PENDING. Fatal validation
and explicit non-retryable decisions remain fail-closed.

Dataset query timing runs in a process separate from the API event loop. Its
first-result/idle deadlines remain the frontend's 30/45 seconds, and recorded
latency excludes the independent judge and process startup. Ingestion still
uses the actual upload service and job queue with authenticated scope; no API
authorization dependency is replaced for the benchmark.

Canonical dedup also uses the CPU boundary. MinHash retains the same 128 MD5
permutations and five-token shingles; persisted signatures include an algorithm,
text digest and signature checksum. Invalid derivatives are recomputed. Migration
0008 adds only a nullable cache column. Previously indexed blocks are handled
without a destructive backfill. The process cache is bounded; eviction affects
performance, never the set of prior blocks considered for deduplication.

Project rebuilds defer while document ingestion or FOUNDATION jobs in the scope
are still active. This scheduling wait does not consume failure attempts. CPU
deadlines include admission wait and release the slot only after reaping the
child. Deleting a document/source writes the rebuild request in the deletion
transaction; an empty project clears its active snapshot pointer.

Dynamic Top-K's MMR selector retains the exact Jaccard score and deterministic
selection rules. Each candidate compares only with evidence selected since its
previous scoring round, avoiding repeated pair comparisons as the pool grows.
This changes computation reuse, not retrieval breadth or relevance thresholds.
Selection runs outside the API loop under bounded admission; it checks request
cancellation and its work finishes before releasing admission. Scope, mapping
version and index checksums are revalidated before returning selected evidence.

Routing metadata checksumming and publish-candidate preparation also run in the
CPU process. Canonical JSON encoding/checksums remain unchanged; manifest readers
use the installed fast JSON decoder for validated routing values. Background
query-demand recording uses the configured source-operation timeout instead of
a hardcoded 100 ms deadline, and remains outside response timing.
