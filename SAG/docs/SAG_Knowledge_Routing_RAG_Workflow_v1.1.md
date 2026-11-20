**SAG**

Knowledge Routing RAG Workflow

Thiết kế toàn bộ workflow Upload → Extract → Dedup/Version → Knowledge Tree → Hybrid Retrieval → LLM → Knowledge Gap → Research Agent

**Architecture & Implementation Specification • Version 1.1**

Ngày: 23/09/2026

**Mục tiêu tài liệu —** Chuyển hệ thống từ “classic RAG” sang kiến trúc Knowledge Routing: chi phí nặng được trả ở ingestion, query hằng ngày chủ yếu đi theo cây tri thức + local hybrid search; graph expansion và LLM reasoning chỉ chạy khi cần.

# Thông tin tài liệu

| **Trường**         | **Giá trị**                                                                                                |
|--------------------|------------------------------------------------------------------------------------------------------------|
| Tên                | SAG Knowledge Routing RAG Workflow                                                                         |
| Phiên bản          | 1.1                                                                                                        |
| Trạng thái         | Production-oriented algorithm & implementation specification                                                              |
| Phạm vi            | Document ingestion, search indexing, knowledge organization, retrieval, generation, agent knowledge growth |
| Source of truth    | PostgreSQL                                                                                                 |
| Search accelerator | Qdrant                                                                                                     |
| Query router       | Laya Local (coarse intent only)                                                                            |
| Generation         | LLM cấu hình độc lập trong Settings                                                                        |

# Mục lục

> 1\. Executive Summary
>
> 2\. Kiến trúc mục tiêu và nguyên tắc thiết kế
>
> 3\. End-to-End Workflow
>
> 4\. Mô hình dữ liệu theo tầng
>
> 5\. Workflow Upload và Source Registration
>
> 6\. Canonical Extraction
>
> 7\. Deduplication và Temporal Versioning
>
> 8\. Search Unit và Search Index
>
> 9\. Knowledge Unit, L4 Extraction và Multi-signal Graph
>
> 10\. Xây dựng Knowledge Routing Tree có ràng buộc
>
> 11\. Incremental Update, Tree Stability và Blue-Green Versioning
>
> 12\. PostgreSQL và Qdrant
>
> 13\. Online Retrieval Engine và Query Strategy Planner
>
> 14\. Laya, Agent Service và LLM
>
> 15\. Citation, Grounding và Context Builder
>
> 16\. Knowledge Quality, Gap Detection và Research Agent
>
> 17\. Job State Machine, Idempotency và Failure Recovery
>
> 18\. Security, ACL và Multi-tenancy
>
> 19\. Cache Layer
>
> 20\. Observability, Evaluation và Latency Budget
>
> 21\. Kế hoạch triển khai theo Phase
>
> 22\. Definition of Done
>
> 23\. Rủi ro và quyết định kiến trúc
>
> Phụ lục A–H: Schema, payload, API contract, pseudocode, test corpus, cấu hình, hyper-parameters và tài liệu tham khảo

# 1. Executive Summary

SAG được thiết kế lại theo nguyên tắc “build expensive once, query cheap many times”. Hệ thống không xem chunk là đơn vị tri thức cao nhất và không dùng LLM như công cụ mặc định cho mọi bước. Thay vào đó, ingestion chuẩn hóa nguồn, loại trùng, quản lý phiên bản, tạo search units, knowledge units, multi-signal similarity graph và một Knowledge Routing Tree ổn định. Online retrieval dùng cây để thu hẹp search space, sau đó dùng Qdrant hybrid search để lấy evidence; graph traversal và LLM reasoning là đường bổ sung có kiểm soát.

**Architectural rule —** Algorithm first → model second → LLM last. Bất kỳ bước nào có thể giải quyết ổn định bằng hash, SQL, ANN, BM25/sparse search, clustering, ranking hay graph traversal giới hạn thì không mặc định giao cho LLM.

| **Mục tiêu**                 | **Cách đạt được**                                                                             |
|------------------------------|-----------------------------------------------------------------------------------------------|
| Latency query ổn định        | Tree routing → branch-local hybrid retrieval → bounded rerank.                                |
| Chi phí production thấp      | LLM không nằm trong candidate generation mặc định; enrichment nặng chạy offline/asynchronous. |
| Dữ liệu thay đổi mỗi ngày    | Base tree + delta update + subtree rebuild khi drift, không full rebuild.                     |
| Không mất lịch sử            | Document/version/claim/tree đều có temporal metadata và lineage.                              |
| Recall không bị khóa bởi cây | Tree là strong routing prior; luôn có global escape retrieval budget.                         |
| Sẵn sàng cho Research Agent  | Knowledge tree đồng thời là retrieval index, knowledge map và planning structure.             |

# 2. Kiến trúc mục tiêu và nguyên tắc thiết kế

## 2.1. Kiến trúc logic

SOURCES  
│  
▼  
SOURCE REGISTRATION ── checksum / provenance / version  
│  
▼  
CANONICAL EXTRACTION ── page / heading / paragraph / table / anchor  
│  
▼  
DEDUP + TEMPORAL VERSIONING  
│  
├─────────────── SEARCH PATH ─────────────────┐  
│ │  
▼ ▼  
SEARCH UNITS KNOWLEDGE UNITS  
│ │  
▼ ▼  
Dense + Sparse Index Multi-signal Graph  
│ │  
▼ ▼  
QDRANT Hierarchical Clustering  
│  
▼  
KNOWLEDGE ROUTING TREE  
│  
▼  
PostgreSQL  
│  
════════════════════════ ONLINE QUERY ══════════╪══════════════  
▼  
Tree Beam Routing / Query Plan  
│  
candidate branches + filters  
│  
▼  
Qdrant Local Hybrid Search  
│  
RRF / dedup / MMR  
│  
optional bounded graph expansion  
│  
rerank  
│  
Context Builder  
│  
grounded LLM  
│  
Answer + citation + retrieval trace

## 2.2. Các nguyên tắc bắt buộc

- PostgreSQL là source of truth cho metadata, hierarchy, versioning, provenance, claims, relations, ACL, job state và tree lineage.

- Qdrant là search accelerator, không phải hệ thống nghiệp vụ chính.

- Knowledge Tree là semantic routing index, không chỉ là taxonomy để hiển thị UI.

- Tree không được là hard filter: retrieval luôn giữ một escape budget cho global search.

- Graph trong PostgreSQL là sparse cross-link graph; không cố sao chép đầy đủ hành vi của graph-native database.

- Online graph traversal phải bounded theo hop, node count và latency budget.

- Document có thể SEARCH_READY trước khi KNOWLEDGE_READY.

- Enrichment/summary bằng LLM phải retry độc lập và không làm mất khả năng search cơ bản.

- Mọi dữ liệu sinh ra phải có provenance và version để truy nguyên và rollback.

# 3. End-to-End Workflow

UPLOAD  
│  
├─ validate file / MIME / size / permission  
├─ compute source checksum  
├─ detect exact duplicate  
└─ create Document + DocumentVersion + IngestionRun  
│  
▼  
CANONICAL EXTRACT  
│  
├─ blocks / layout / heading / table / page anchors  
├─ normalize text  
└─ persist canonical blocks  
│  
▼  
DEDUP + VERSION  
│  
├─ exact block hash  
├─ near-duplicate candidates  
├─ source/version lineage  
└─ evidence references  
│  
▼  
SEARCH INDEX  
│  
├─ search chunks  
├─ dense vectors  
├─ sparse representation  
└─ Qdrant upsert  
│  
└────────────► SEARCH_READY  
│  
▼  
KNOWLEDGE ENRICHMENT (async)  
│  
├─ knowledge units  
├─ entities / aliases / claims / relations  
├─ multi-signal graph  
├─ tree assignment / local rebuild  
└─ representative prototypes / summaries  
│  
└────────────► KNOWLEDGE_READY  
  
QUERY  
│  
├─ Laya coarse intent  
├─ deterministic query analyzer  
├─ tree routing  
├─ branch-local hybrid retrieval + escape retrieval  
├─ fusion → dedup → MMR → optional graph expansion → rerank  
├─ context packing + citations  
└─ grounded LLM answer

## 3.1. Hai mốc readiness

| **Readiness**   | **Điều kiện**                                                        | **Ý nghĩa**                                                                             |
|-----------------|----------------------------------------------------------------------|-----------------------------------------------------------------------------------------|
| SEARCH_READY    | Parse + canonicalize + dedup + search indexing hoàn tất              | Người dùng có thể hỏi ngay; retrieval dùng hybrid search và metadata hiện có.           |
| KNOWLEDGE_READY | Knowledge units + entity/claim + tree assignment/enrichment hoàn tất | Cho phép tree routing đầy đủ, global/topic query, graph-assisted query và gap analysis. |

# 4. Mô hình dữ liệu theo tầng

| **Layer** | **Đơn vị**                    | **Vai trò**                                                    |
|-----------|-------------------------------|----------------------------------------------------------------|
| L0        | Raw Source                    | File/URL/API payload nguyên bản, checksum, provenance.         |
| L1        | Canonical Block               | Paragraph, heading, table, list, caption, page/layout anchor.  |
| L2        | Search Unit                   | Đơn vị tối ưu cho dense/sparse retrieval.                      |
| L3        | Knowledge Unit                | Đơn vị tri thức ổn định hơn chunk, dùng cho clustering/tree.   |
| L4        | Entity / Claim / Relation     | Tri thức cấu trúc, có evidence và validity.                    |
| L5        | Topic / Community / Tree Node | Hierarchy dùng routing, summarization và knowledge navigation. |
| L6        | Summary / Gap / Question      | Tri thức tổng hợp, thiếu hụt, conflict và research planning.   |

**Không đồng nhất Search Unit và Knowledge Unit —** Search unit có thể được tối ưu cho embedding/lexical retrieval; knowledge unit phải tối ưu cho tính nhất quán khái niệm, clustering và tree stability.

# 5. Workflow Upload và Source Registration

## 5.1. Request flow

1.  Frontend gửi multipart request cùng project/source scope và optional metadata.

2.  API kiểm tra quyền, extension, MIME signature, kích thước và policy.

3.  API stream file vào storage tạm; tính SHA-256/BLAKE3 trong khi stream để tránh đọc lại toàn file.

4.  Tra cứu source checksum trong phạm vi tenant/project; nếu exact duplicate thì áp dụng duplicate policy.

5.  Tạo Document, DocumentVersion, SourceSnapshot và IngestionRun trong transaction.

6.  Tạo idempotency key để frontend retry không sinh thêm Document/Job.

7.  Worker nhận job và chuyển trạng thái stage theo pipeline.

## 5.2. Duplicate policy khi upload

| **Trường hợp**                   | **Xử lý đề xuất**                                                           |
|----------------------------------|-----------------------------------------------------------------------------|
| Cùng hash + cùng source identity | Không parse lại; trả về version hiện có hoặc tạo link reference tùy policy. |
| Cùng hash + nguồn khác           | Tái sử dụng content object; thêm provenance/source reference mới.           |
| Khác hash + cùng source identity | Tạo DocumentVersion mới và giữ lineage với version trước.                   |
| Khác hash + nguồn mới            | Tạo document mới; near-duplicate xử lý sau canonicalization.                |

# 6. Canonical Extraction

Mục tiêu của extraction không phải chỉ “lấy text”. Nó phải tạo một representation ổn định, có vị trí nguồn và có thể tái sử dụng cho retrieval, citation, dedup, tree building và audit.

| **Block type**    | **Metadata tối thiểu**                                         |
|-------------------|----------------------------------------------------------------|
| Heading           | text, level, page, section_path, bbox/anchor nếu có            |
| Paragraph         | text, page, section_path, ordinal, parent_heading              |
| Table             | cells/markdown/json representation, page, caption, header info |
| List              | items, nesting level, parent heading                           |
| Caption           | text, referenced object, page                                  |
| Code/Preformatted | raw text, language nếu phát hiện được, page/anchor             |

## 6.1. Normalization rules

- Unicode normalize; sửa invalid code points và encoding artifacts.

- Whitespace normalize nhưng không phá bảng/code.

- Giữ heading hierarchy, page number và source anchor.

- Không xóa punctuation/key identifiers vì lexical search cần chúng.

- Tách boilerplate lặp như header/footer nhưng giữ dấu vết để audit.

- Không dùng LLM để sửa text mặc định; chỉ dùng model/LLM cho layout khó hoặc extraction ambiguity có giá trị.

# 7. Deduplication và Temporal Versioning

## 7.1. Bốn tầng dedup

| **Tầng**           | **Thuật toán**                            | **Quy tắc**                                                                         |
|--------------------|-------------------------------------------|-------------------------------------------------------------------------------------|
| File exact         | SHA-256/BLAKE3                            | Có thể kết luận duplicate vật lý.                                                   |
| Block exact        | Hash(normalized_text)                     | Tái sử dụng content; giữ nhiều evidence/provenance.                                 |
| Near duplicate     | Shingling + MinHash/SimHash + LSH/Jaccard | Sinh candidate cluster; cần threshold theo loại dữ liệu.                            |
| Semantic duplicate | Embedding similarity                      | Chỉ là candidate; không tự merge vì câu phủ định/cập nhật có thể rất gần embedding. |

## 7.2. Quan hệ semantic sau dedup

- EQUIVALENT: cùng nội dung về mặt ngữ nghĩa.

- SUPPORTS: hai evidence hỗ trợ cùng claim.

- CONTRADICTS: nội dung mâu thuẫn.

- SUPERSEDES: bản mới thay thế bản cũ.

- RELATED: cùng chủ đề nhưng không thể merge.

## 7.3. Temporal model

| **Field**                           | **Ý nghĩa**                                             |
|-------------------------------------|---------------------------------------------------------|
| source_published_at                 | Thời điểm nguồn tự công bố.                             |
| observed_at                         | Thời điểm SAG nhìn thấy nguồn.                          |
| ingested_at                         | Thời điểm dữ liệu được ingest.                          |
| valid_from / valid_to               | Khoảng thời gian claim/version được xem là có hiệu lực. |
| supersedes_id                       | Liên kết với bản/claim bị thay thế.                     |
| tree_version_from / tree_version_to | Validity của membership/tree node trong routing tree.   |

**Nguyên tắc —** Không overwrite lịch sử khi source thay đổi. Phiên bản cũ phải còn truy vấn được nếu câu hỏi có time constraint hoặc cần audit.

# 8. Search Unit và Search Index

## 8.1. Chunking/Search Unit

- Ưu tiên boundary theo heading/paragraph/table trước khi dùng token window.

- Search unit phải giữ document_version_id, canonical block range, page range, section path, content hash và source reference.

- Overlap chỉ dùng khi boundary thực sự cần; tránh tạo hàng loạt duplicate vectors.

- Có thể tạo nhiều representation cho table/code thay vì ép về một text blob duy nhất.

## 8.2. Dense + Sparse

| **Index**                 | **Mục tiêu**                              | **Ghi chú**                                              |
|---------------------------|-------------------------------------------|----------------------------------------------------------|
| Dense                     | Paraphrase/semantic retrieval             | Embedding model tách khỏi LLM generation.                |
| Sparse/BM25-style         | Identifier, từ khóa chính xác, rare terms | Quan trọng với mã lỗi, tên biến, thuật ngữ chuyên ngành. |
| Metadata filters          | ACL, project, source, version, time, node | Phải filter trước/đồng thời với ANN khi có thể.          |
| Optional late interaction | Rerank candidate nhỏ                      | Không chạy toàn corpus.                                  |

# 9. Knowledge Unit, L4 Extraction và Multi-signal Graph

Knowledge Unit là đơn vị dùng để tổ chức miền tri thức. Nó có thể là một atomic claim group, một semantic passage hoặc một concept-bound unit, nhưng phải đủ ổn định để cluster, version, gắn provenance và tham gia routing. Search Unit tối ưu cho retrieval; Knowledge Unit tối ưu cho organization/reasoning. Hai loại này có thể ánh xạ nhiều-nhiều.

## 9.1. Runtime L4: Entity / Alias / Claim / Relation Extraction

Tầng L4 không chạy một chiến lược duy nhất. SAG dùng **hybrid extraction 3-tier** để giữ throughput ingestion ổn định và chỉ trả chi phí LLM cho phần thực sự cần reasoning.

### 9.1.1. Tier E0 — Deterministic extraction

Chạy trên mọi tài liệu, CPU rẻ, idempotent:

- Regex/lexer cho URL, email, IP, port, UUID, hash, version, API path, env var, package/module/class/function, error code và mã định danh dự án.
- Dictionary/alias table cho entity đã biết trong project.
- Heading/section/table/header structure dùng để tạo structural relation.
- Sentence segmentation và heuristic atomic-statement splitting.
- Temporal expression parser cho ngày, version, validity marker.

Output của E0 luôn giữ `evidence_block_ids` và `source_anchor`.

### 9.1.2. Tier E1 — Small Specialized Model

Chạy mặc định sau `SEARCH_READY`, có thể CPU/GPU tùy môi trường:

- Multilingual NER / span classifier kiểu GLiNER hoặc model NER chuyên miền.
- Vietnamese/domain model như PhoBERT-family hoặc model tương đương nếu benchmark cho thấy tốt hơn.
- spaCy/rule dependency pattern cho quan hệ đơn giản.
- Entity linking bằng alias + lexical + embedding candidate matching.
- Relation candidate bằng co-occurrence, dependency pattern và lightweight classifier.

E1 tạo **candidate**, không tự động biến mọi quan hệ thành fact có độ tin cậy cao. Mỗi output có `extractor_version`, `confidence`, `evidence_ids`.

### 9.1.3. Tier E2 — Selective LLM Enrichment

LLM **không** chạy trên mọi Search Unit. Nó chỉ được enqueue khi thỏa ít nhất một điều kiện:

1. `local_confidence < L4_LOW_CONF` nhưng unit có `importance_score >= IMPORTANCE_MIN`.
2. Có contradiction candidate hoặc relation cần semantic interpretation.
3. Node có query demand cao nhưng graph/claim coverage thấp.
4. Tài liệu thuộc allowlist yêu cầu graph fidelity cao.
5. Batch off-peak còn budget token/cost.

LLM job chạy asynchronous/off-peak, trả structured JSON schema cố định: `entities`, `aliases`, `claims`, `relations`, `temporal_scope`, `evidence_refs`, `confidence`. Không có evidence anchor thì output không được promote thành canonical claim/relation.

**Backpressure bắt buộc:** `SEARCH_READY` không phụ thuộc E2. Queue E2 có daily token budget, max queue age, concurrency limit và priority. Khi queue lag tăng, hệ thống giảm enrichment rate thay vì chặn upload/search.

> Microsoft GraphRAG hiện cũng phân biệt standard graph extraction bằng LLM với FastGraphRAG dùng NLP truyền thống để giảm chi phí; tài liệu của họ lưu ý graph extraction chiếm phần lớn chi phí indexing của standard pipeline. SAG áp dụng ý tưởng hybrid này nhưng thêm selective escalation theo confidence/demand thay vì chọn một mode cố định cho toàn corpus.

## 9.2. Candidate edge generation

Không tính pairwise similarity `O(N^2)`. Với mỗi Knowledge Unit `u`, chỉ sinh candidate neighborhood:

```text
C(u) = union(
  dense_ann(u, K_dense),
  sparse_topk(u, K_sparse),
  entity_neighbors(u, K_entity),
  structural_neighbors(u),
  citation_neighbors(u),
  temporal_neighbors(u)
)
```

Sau đó mới tính composite edge score cho `v in C(u)`. Graph cuối giữ sparse bằng `top-K`, mutual-kNN và edge threshold.

Default khởi điểm:

| Tham số | Default ban đầu | Ghi chú |
|---|---:|---|
| `K_dense` | 32 | ANN candidate, tune 16–64. |
| `K_sparse` | 24 | Rare-term/identifier candidate. |
| `K_entity` | 24 | Chỉ khi unit có entity. |
| `MAX_DEGREE` | 32 | Sau fusion/pruning. |
| `EDGE_MIN_SCORE` | 0.55 | Phải tune bằng clustering/routing eval. |
| mutual-kNN | ưu tiên | Edge 2 chiều được boost; không bắt buộc với structural/citation edge. |

## 9.3. Chuẩn hóa Multi-signal Score

Không cộng trực tiếp raw cosine, BM25 và Jaccard. Mọi signal phải qua calibration về `[0,1]` theo `graph_build_version` trước khi nhân trọng số.

### 9.3.1. Semantic

Với cosine `c in [-1,1]`:

```text
semantic_norm = clip((c + 1) / 2, 0, 1)
```

Nếu embedding model được benchmark cho thấy cosine chỉ nằm trong dải hẹp dương, có thể thay bằng percentile calibration nhưng phải version hóa calibration parameters.

### 9.3.2. Lexical

BM25 không có upper bound cố định. Dùng robust percentile scaling theo corpus/build window:

```text
lexical_norm = clip((bm25 - q05_bm25) / max(q95_bm25 - q05_bm25, eps), 0, 1)
```

`q05_bm25` và `q95_bm25` được lưu trong `graph_signal_calibrations`. Với incremental delta nhỏ, giữ calibration của active graph build; chỉ recalibrate khi full/local rebuild vượt ngưỡng drift. Có thể thay percentile scaling bằng robust sigmoid `sigmoid((x-median)/(1.4826*MAD+eps))` nếu tail quá nặng.

Vì BM25 có hướng query-document, pairwise lexical similarity dùng:

```text
lexical_pair(u,v) = 0.5 * BM25(u -> v) + 0.5 * BM25(v -> u)
```

sau đó mới normalize.

### 9.3.3. Entity

Dùng weighted Jaccard, entity phổ biến bị giảm trọng số bằng IDF:

```text
entity_norm = sum(min(w_u(e), w_v(e))) / sum(max(w_u(e), w_v(e)))
```

đã nằm trong `[0,1]`.

### 9.3.4. Structure

Default discrete score:

| Quan hệ cấu trúc | Score |
|---|---:|
| same canonical block / explicit cross-ref | 1.00 |
| same section | 0.85 |
| adjacent section | 0.65 |
| same document | 0.45 |
| same source family/version lineage | 0.25 |
| không liên hệ | 0.00 |

### 9.3.5. Citation/reference

Binary/weighted score trong `[0,1]`: explicit cross-reference = 1.0; shared citation/source anchor = 0.7; weak co-reference = 0.3.

### 9.3.6. Temporal compatibility

Temporal signal không đơn thuần ưu tiên mới. Nó đo hai unit có cùng validity regime hay không:

```text
if validity_intervals_overlap: 1.0
elif same_version_lineage and adjacent_period: 0.6
elif explicit_supersedes_relation: 0.4
else: 0.0
```

Recency boost dùng ở ranking query-time, không trộn lẫn với historical compatibility.

### 9.3.7. Missing-signal policy

Signal bị thiếu **không được mặc định bằng 0** nếu thiếu do extractor chưa chạy. Chỉ tổng hợp trên signal có quan sát và renormalize trọng số:

```text
W(u,v) = sum(w_i * s_i * m_i) / sum(w_i * m_i)
```

với `m_i=1` nếu signal có dữ liệu, ngược lại `0`.

## 9.4. Composite Edge Weight và default hyper-parameters

Default ban đầu cho technical/project corpus:

| Signal | Weight |
|---|---:|
| semantic | `0.40` |
| lexical | `0.20` |
| entity | `0.15` |
| structure | `0.10` |
| temporal compatibility | `0.10` |
| citation/reference | `0.05` |

```text
W(u,v) = normalized_weighted_mean(signals)
```

Các weight này chỉ là **starting point**, không phải constant kiến trúc. Tuning dùng objective hỗn hợp: clustering quality + routing Recall@K + escape-win-rate + latency. Weight/config phải có `graph_config_version` để A/B và rollback.

# 10. Xây dựng Knowledge Routing Tree có ràng buộc

Knowledge Routing Tree là **learned routing index**, không phải taxonomy do LLM tạo. Primary algorithm của SAG là **Constrained Hierarchical Leiden** trên sparse multi-signal graph, kèm capacity repair để chống Giant Component và skewed tree.

Microsoft GraphRAG dùng hierarchical Leiden để tạo community hierarchy; SAG dùng Leiden vì khả năng tối ưu community graph và connectivity tốt hơn Louvain, nhưng bổ sung ràng buộc kích thước/depth vì mục tiêu của SAG là beam routing production, không chỉ community discovery.

## 10.1. Thuật toán primary: Constrained Hierarchical Leiden

Mỗi node/subgraph được partition đệ quy:

```text
partition(subgraph G, depth):
    if stop_condition(G, depth):
        emit_leaf(G)
        return

    P = leiden(G, metric=CPM, resolution=gamma(depth), seed=stable_seed)
    P = repair_small_clusters(P)
    P = split_oversized_clusters(P)

    if degenerate(P):
        P = balanced_fallback_split(G)

    for community C in P:
        partition(G[C], depth + 1)
```

**Default metric:** CPM (Constant Potts Model) nếu library/backend hỗ trợ; fallback modularity khi không có. Resolution tăng nhẹ theo depth để tạo finer communities.

## 10.2. Ràng buộc cấu trúc cây

Default production baseline:

| Constraint | Default | Mục tiêu |
|---|---:|---|
| `N_min` | `5` Knowledge Units | Tránh cụm vụn không có giá trị routing. |
| `N_target` | `20–30` | Vùng tối ưu ban đầu cho leaf/micro-topic. |
| `N_max` | `50` | Leaf vượt ngưỡng bắt buộc split/repair. |
| `Depth_max` | `4` dưới Project root | Giữ beam traversal ngắn và dễ cache. |
| `MAX_CHILDREN` | `12` | Tránh node quá rộng. |
| `GIANT_RATIO_MAX` | `0.60` | Không child nào >60% parent sau repair nếu parent > N_max. |
| `SMALL_CLUSTER_MERGE_MAX` | `< N_min` | Merge vào neighbor phù hợp nhất. |
| deterministic seed | bắt buộc | Rebuild reproducible. |

Các giá trị này là **default khởi điểm** và phải benchmark theo corpus; config được version hóa theo project/domain.

## 10.3. Giant Component Guard

Một partition bị coi là degenerate nếu một trong các điều kiện đúng:

```text
largest_child_size / parent_size > GIANT_RATIO_MAX
child_count == 1
num_singleton_or_tiny_children / child_count > 0.40
normalized_child_size_entropy < H_CHILD_MIN
```

Repair sequence:

1. **Prune hub/noise edges**: giảm/loại edge từ entity quá phổ biến, boilerplate, generic heading, max-degree outlier.
2. **Increase Leiden resolution** theo schedule, ví dụ `gamma *= 1.35`, tối đa `MAX_RESOLUTION_RETRIES = 3`.
3. **Recursive split oversized child** độc lập với siblings.
4. Nếu vẫn oversized/degenerate, gọi `balanced_fallback_split()`.

## 10.4. Balanced fallback split

Fallback ưu tiên graph-aware balanced k-way partition. Interface:

```text
k = ceil(|C| / N_target)
k = clamp(k, 2, MAX_CHILDREN)
```

Ưu tiên implementation:

1. METIS/PyMetis balanced graph partition nếu dependency được chấp nhận.
2. Spectral bisection/k-way trên induced sparse graph nếu cluster đủ nhỏ.
3. Cuối cùng mới dùng embedding k-means constrained như emergency fallback.

Fallback phải giữ `edge_cut`, `balance_ratio`, `cohesion` trong metrics để phát hiện partition chất lượng kém.

## 10.5. Repair cụm nhỏ

Cụm `< N_min` không tự động thành leaf độc lập. Với mỗi small cluster `S`, tìm candidate sibling `C` tối đa hóa:

```text
merge_score(S,C) =
  0.50 * normalized_inter_cluster_edge_weight
+ 0.25 * prototype_similarity
+ 0.15 * entity_overlap
+ 0.10 * structural_affinity
```

Chỉ merge nếu `|S| + |C| <= N_max` hoặc parent đã chạm `Depth_max`. Nếu không có merge an toàn, giữ `OUTLIER_BUCKET` có giới hạn và đánh dấu để incremental monitor xử lý.

## 10.6. Stop criteria

Dừng chia khi:

```text
depth >= Depth_max
OR N_min <= |C| <= N_max and cohesion >= COHESION_MIN
OR split_gain < MIN_SPLIT_GAIN
OR graph_too_sparse_for_stable_split
```

Nếu `|C| > N_max`, không được stop chỉ vì cohesion cao; capacity constraint ưu tiên để bảo vệ beam routing.

## 10.7. Node prototype

Mỗi node chứa **routing representation**, không chỉ `name/parent`:

| Thuộc tính | Vai trò |
|---|---|
| `centroid_embedding` | Dense routing prototype. |
| `medoid_unit_ids[]` | Representative evidence thực, chống centroid blur. |
| `top_terms / sparse_signature` | Lexical routing. |
| `entity_profile` | Entity-aware routing. |
| `source_profile` | Phát hiện node bị một nguồn chi phối. |
| `time_min/time_max` | Temporal pruning. |
| `unit_count / child_count` | Capacity/routing stats. |
| `cohesion / density / edge_cut` | Quality và rebuild trigger. |
| `representative_evidence[]` | Extractive representation. |
| `summary_optional` | Chỉ tạo theo security partition và nhu cầu global query. |

## 10.8. Tree quality gates trước khi publish

Tree candidate không được activate nếu vi phạm một trong các gate:

- `max_leaf_size > N_max` (trừ explicit outlier bucket có cap).
- routing Recall@K trên validation set thấp hơn active tree quá `MAX_RECALL_REGRESSION`.
- giant ratio/branch skew vượt threshold.
- số branch không-accessible cao bất thường với ACL benchmark.
- `escape_win_rate` tăng mạnh trên shadow queries.

Tree build phải sinh `tree_quality_report` và `tree_manifest` trước khi chuyển sang blue-green publish ở Mục 11.

# 11. Incremental Update, Tree Stability và Blue-Green Versioning

Dữ liệu thay đổi hằng ngày nên tree phải hỗ trợ incremental assignment và **subtree rebuild**, không full rebuild theo mỗi ingestion batch.

## 11.1. Base tree + delta assignment

```text
NEW KNOWLEDGE UNIT
        |
        v
ANN against accessible leaf/node prototypes
        |
        +-- score >= T_high --> attach directly
        +-- T_low..T_high --> borderline queue/local refine
        `-- score < T_low --> outlier/new-topic candidate
        |
        v
update inactive/delta statistics
        |
        v
Tree Stability Monitor
        |
        +-- stable --> publish delta metadata
        `-- drift --> rebuild affected subtree only
```

Default starting thresholds: `T_high=0.78`, `T_low=0.58`; tune bằng assignment accuracy. Incremental centroid/profile update chỉ áp dụng cho statistics; canonical lineage và active routing snapshot vẫn tuân theo publish protocol ở 11.4.

## 11.2. Drift signals

| Metric | Ý nghĩa / trigger |
|---|---|
| Centroid drift | `1 - cosine(old,new)` vượt threshold. |
| Outlier ratio | Nhiều unit mới không fit leaf hiện tại. |
| New-unit ratio | Delta lớn so với node size. |
| Cohesion drop | Node ngày càng không đồng nhất. |
| Edge churn | Neighborhood thay đổi mạnh. |
| Query miss rate | Escape retrieval liên tục thắng tree-guided retrieval. |
| Branch entropy shift | Query distribution thay đổi đáng kể. |
| ACL blackhole rate | Router chọn branch nhưng accessible result = 0. |

Trigger rebuild dùng hysteresis, ví dụ cần ít nhất 2/3 window liên tiếp vượt threshold để tránh rebuild do spike.

## 11.3. Stable node lineage

- Match old community <-> new community bằng weighted overlap: member Jaccard + medoid similarity + entity profile.
- Split/merge ghi lineage: `SPLIT_FROM`, `MERGED_FROM`, `SUPERSEDES_TREE_NODE`.
- Stable `knowledge_node_id` được giữ nếu overlap >= `NODE_ID_INHERIT_THRESHOLD`.
- Tree nodes dùng validity range `valid_from_tree_version`, `valid_to_tree_version`.
- Cache key chứa tree version nên version switch tự invalidates logic cache mà không cần global delete.

## 11.4. Blue-Green Tree Publish Protocol

Qdrant không có cross-system ACID transaction với PostgreSQL. SAG không cập nhật trực tiếp active membership của hàng nghìn point. Thay vào đó dùng **dual routing slot A/B**.

### 11.4.1. PostgreSQL control state

```text
project_search_state
- project_id
- active_tree_version
- active_routing_slot      # A | B
- active_search_epoch
- previous_tree_version
- updated_at
```

Tree mới được build vào version `V_next` ở các bảng versioned trong PostgreSQL nhưng chưa active.

### 11.4.2. Qdrant dual-slot payload

Mỗi Search Unit giữ hai bộ routing membership ổn định:

```json
{
  "primary_node_a": "node-...",
  "secondary_node_ids_a": ["..."],
  "tree_version_a": 103,

  "primary_node_b": "node-...",
  "secondary_node_ids_b": ["..."],
  "tree_version_b": 104
}
```

Nếu slot A đang active, rebuild chỉ update slot B. Query trong thời gian rebuild vẫn dùng A, vì vậy không nhìn thấy half-migrated payload.

### 11.4.3. Publish sequence

1. Lock `tree_publish(project_id)` ở PostgreSQL.
2. Build `V_next` + node lineage + ACL routing profiles vào inactive version.
3. Update Qdrant **inactive slot** theo batch với `wait=true`/acknowledged writes và conditional version guard khi cần.
4. Verify exact point counts, sample membership, checksum/manifest và ACL smoke test.
5. Shadow-route một tập query regression trên `V_next`.
6. Trong **một PostgreSQL transaction**, switch `active_tree_version=V_next`, `active_routing_slot=inactive_slot`, increment `active_search_epoch`.
7. Query mới đọc control state một lần và dùng đúng tree version + slot trong toàn request.
8. Giữ slot/version cũ trong rollback window; sau đó mới cho phép overwrite ở rebuild kế tiếp.

### 11.4.4. Consistency rule tại query-time

Mọi retrieval request phải chụp snapshot:

```text
SearchSnapshot = {
  project_id,
  tree_version,
  routing_slot,
  search_epoch,
  acl_epoch
}
```

Snapshot không thay đổi giữa beam routing, Qdrant search và citation build, kể cả khi active pointer đổi giữa chừng.

### 11.4.5. Major vector/index migration

Dual-slot giải quyết tree membership. Với thay đổi lớn như embedding model/vector dimension/collection schema, dùng **blue-green Qdrant collection** và atomic collection alias switch. Qdrant hỗ trợ build collection mới rồi atomically đổi alias, tránh request concurrent thấy trạng thái chuyển dở.

## 11.5. Failure và rollback

- Qdrant batch fail: active slot không đổi; retry inactive batch theo manifest.
- Verification fail: mark `V_next=REJECTED`, không publish.
- Publish xong nhưng quality regression: transaction switch về `previous_tree_version` + previous slot trong rollback window.
- PostgreSQL/Qdrant mismatch: query fail closed sang global escape retrieval trên active search index, không dùng tree version chưa verified.

# 12. PostgreSQL và Qdrant

## 12.1. Trách nhiệm PostgreSQL

- Documents, versions, source snapshots, provenance.

- Canonical blocks, knowledge units và evidence mappings.

- Entities, aliases, claims, relations và validity.

- Knowledge nodes, primary hierarchy, secondary memberships, sparse cross-links.

- Ingestion runs, stage runs, errors, retries, idempotency.

- ACL/project/tenant policy và tree lineage.

## 12.2. Trách nhiệm Qdrant

- Dense vector search và sparse/hybrid retrieval.

- Payload filtering theo project/tenant/document/version/time/node.

- Candidate retrieval trong branch đã được tree router chọn.

- Global escape retrieval trong budget nhỏ.

- Optional multi-stage candidate/rerank representations.

## 12.3. Không coi PostgreSQL là Neo4j replacement

**Thiết kế —** Hierarchy được tối ưu như tree (adjacency/ltree/materialized path tùy benchmark); graph chỉ giữ sparse cross-links. Online traversal giới hạn 1–3 hop hoặc node budget cụ thể.

# 13. Online Retrieval Engine và Query Strategy Planner

## 13.1. Query flow

```text
USER QUERY
   |
   +-- Laya coarse intent: CHAT / KNOWLEDGE / COMMAND / AMBIGUOUS
   |
   v
Deterministic Query Feature Extractor
   |
   +-- exact identifiers / quotes / code symbols
   +-- entities / relation cues
   +-- date/version/time constraints
   +-- broad/global cues
   +-- decomposition/multi-hop cues
   |
   v
Query Strategy Planner
   |
   v
ACL-AWARE TREE BEAM ROUTING
   |
   +-- route only through nodes with accessible_unit_count > 0
   +-- score dense + sparse + entity + time + prior + ACL coverage
   +-- retain top-B branches
   `-- entropy/margin decides descend vs broad retrieval
   |
   +--------------------------+
   v                          v
Branch-local Retrieval   Global Escape Retrieval
   80-90% budget             10-20% budget
   |                          |
   +------------+-------------+
                v
              Fusion
                v
           Dedup + MMR
                v
          Coverage check
           /          \
        enough        weak
          |            |
          |       bounded graph expand
          |            |
          +------+- ----+
                 v
          optional rerank
                 v
          Context Builder
                 v
           Grounded LLM
```

## 13.2. Node routing score

Node score chỉ sử dụng **ACL-safe routing profile** của caller:

```text
Score(q,n,p) =
  wd * dense_similarity(q, profile(n,p).centroid_or_medoids)
+ ws * sparse_score(q, profile(n,p).sparse_signature)
+ we * entity_overlap(q, profile(n,p).entity_profile)
+ wt * temporal_compatibility(q, profile(n,p).time_range)
+ wp * routing_prior(n, project)
+ wa * acl_coverage_prior(n, p)
```

`p` là security partition / effective access context. Node có `accessible_unit_count == 0` bị prune trước beam expansion.

## 13.3. Beam search và entropy

Default khởi điểm:

- `beam_width B = 3`, benchmark trong 2–5.
- `route_top_children = min(MAX_CHILDREN, 12)`.
- normalized entropy `H_norm in [0,1]`.
- broad-route candidate khi `H_norm > 0.72` **và** top1-top2 margin `< 0.10`.
- decisive leaf khi top1 margin `>= 0.18` và coverage/accessibility đủ.

Entropy không phải classifier duy nhất; nó được dùng sau deterministic query features để quyết định descend hay giữ nhiều branch.

## 13.4. Fusion, dedup và MMR

- Dense/sparse raw score không cộng trực tiếp nếu scale khác nhau.
- Candidate retrieval ưu tiên rank fusion (RRF/DBSF) hoặc calibrated normalized fusion.
- Exact content hash và near-duplicate cluster được collapse trước rerank.
- MMR/diversity giảm việc trả nhiều evidence cùng một ý.
- Reranker chỉ chạy candidate nhỏ, ví dụ 20–50 item tùy latency budget.

## 13.5. Retrieval strategy modes

Các mode là **primary strategy + modifiers**, không nhất thiết mutually exclusive. Ví dụ Exact + Temporal là hợp lệ.

| Mode | Trigger chính | Pipeline |
|---|---|---|
| `EXACT` | exact phrase/identifier/code/path/hash | exact/sparse first + metadata filter, dense hỗ trợ. |
| `LOCAL_FACTUAL` | câu hỏi cụ thể, single topic | leaf/subtree routing -> hybrid -> fusion -> MMR. |
| `ENTITY_RELATIONAL` | 2+ entity hoặc relation cue | entity-aware tree + bounded relation expansion. |
| `TEMPORAL` | date/version/historical cue | temporal SQL/Qdrant filter + version-aware ranking. |
| `GLOBAL_TOPIC` | broad cue hoặc route entropy cao | higher-level node representatives/safe summaries + child evidence. |
| `MULTI_HOP` | explicit chain/decomposition hoặc coverage yếu | staged retrieval + bounded graph expansion + rerank. |

## 13.6. Query Strategy Planner: bridge từ Laya sang Retrieval Modes

Laya **không** map trực tiếp sang 6 mode. Component đứng giữa là `QueryStrategyPlanner`.

### 13.6.1. Deterministic features

**Exact signals**:

- dấu ngoặc kép / exact phrase;
- UUID, SHA/hash, IP, port;
- error code (`ERR_*`, `HTTP 5xx`, mã nội bộ);
- env var dạng `UPPER_SNAKE_CASE`;
- file/API path (`/v1/...`, `C:\\...`, `foo.bar` code symbol);
- version/package identifier.

**Temporal signals**:

- regex ngày/tháng/năm/ISO datetime;
- từ khóa: `trước`, `sau`, `từ ... đến`, `khi`, `phiên bản`, `version`, `release`, `hiện tại`, `lúc đó`;
- explicit document/version filter.

**Entity/relational signals**:

- `>= 2` linked entities;
- relation verbs/cues: `liên quan`, `phụ thuộc`, `dùng bởi`, `kết nối`, `thuộc`, `gây ra`, `dẫn đến`, `khác gì`;
- comparison pattern `A vs B`, `so sánh A và B`.

**Global/topic signals**:

- `tổng quan`, `toàn bộ`, `các chủ đề`, `kiến trúc chung`, `những vấn đề chính`, `xu hướng`, `bức tranh`;
- no decisive entity/identifier + high routing entropy.

**Multi-hop signals**:

- nhiều relation clause trong một câu;
- decomposition tạo `>= 2` atomic subqueries phụ thuộc nhau;
- explicit causal/path query;
- hoặc escalation sau first-pass khi `coverage < COVERAGE_MIN` và relation evidence tồn tại.

### 13.6.2. Precedence và composition

```text
if Laya == CHAT and confidence >= CHAT_HIGH:
    NO_RETRIEVAL
elif user_requested_strategy != AUTO:
    validate_and_use_override()
else:
    features = deterministic_extract(query)
    modifiers = []

    if features.temporal: modifiers += TEMPORAL
    if features.exact:    primary = EXACT
    elif features.relational: primary = ENTITY_RELATIONAL
    elif features.global_cue: primary = GLOBAL_TOPIC
    else: primary = LOCAL_FACTUAL

    # routing may refine LOCAL_FACTUAL -> GLOBAL_TOPIC
    # first-pass coverage may escalate -> MULTI_HOP
```

`MULTI_HOP` nên là escalation mode khi có bằng chứng, không bật chỉ vì query dài.

### 13.6.3. Planner output contract

```json
{
  "coarse_intent": "KNOWLEDGE",
  "primary_strategy": "ENTITY_RELATIONAL",
  "modifiers": ["TEMPORAL"],
  "detected_entities": ["PostgreSQL", "Qdrant"],
  "time_filter": {"from": "2026-08-01", "to": "2026-09-30"},
  "exact_terms": [],
  "confidence": 0.91,
  "reason_codes": ["MULTI_ENTITY", "RELATION_CUE", "EXPLICIT_DATE"],
  "planner_version": "qsp-v1"
}
```

Reason codes phải log được để regression test và không phụ thuộc chain-of-thought của LLM.

## 13.7. Fallback/escalation policy

- Planner low confidence -> `LOCAL_FACTUAL + larger escape budget`, không bypass retrieval.
- Tree route low confidence -> tăng beam width có cap + escape retrieval.
- Branch returns 0 accessible evidence -> không coi là no-answer ngay; chạy global ACL-filtered escape.
- Coverage yếu -> optional relation expansion/multi-hop nếu latency budget còn.
- LLM planner chỉ là optional slow path cho query cực kỳ ambiguous, không thuộc fast path mặc định.

# 14. Laya, Agent Service và LLM

## 14.1. Laya Local

Laya chỉ thực hiện coarse intent/routing guard, không thay embedding, tree router hoặc search engine.

| **Output** | **Ý nghĩa**                                    |
|------------|------------------------------------------------|
| CHAT       | Chitchat confidence cao → có thể bỏ retrieval. |
| KNOWLEDGE  | Cho phép retrieval engine xử lý.               |
| COMMAND    | Agent/tool flow khác.                          |
| AMBIGUOUS  | Fallback về retrieval an toàn.                 |

- Lazy load singleton; model path/device từ environment.

- Init error được cache trong một khoảng hợp lý; không retry nặng mỗi request.

- Laya unavailable → fallback retrieval, không làm gián đoạn knowledge QA.

- Không để label sai/“null” làm mất query context.

## 14.2. Agent Service

- Nhận intent + query features + user scope.

- Chọn retrieval strategy nhưng không tự thực hiện search logic thấp cấp.

- Quản lý latency budget, tool invocation và fallback.

- Đưa retrieval trace + evidence vào generation.

## 14.3. Generation LLM

- Model trong Settings độc lập với Laya.

- Prompt phải yêu cầu dùng evidence và nêu rõ khi evidence không đủ.

- Không gửi toàn tài liệu khi context builder đã có evidence pack.

- Tách lỗi Laya / retrieval / rerank / LLM để quan sát chính xác.

# 15. Citation, Grounding và Context Builder

| **Context item**     | **Bắt buộc**           |
|----------------------|------------------------|
| Evidence ID          | Có                     |
| Document + version   | Có                     |
| Page/anchor/section  | Có nếu source hỗ trợ   |
| Knowledge node       | Có khi KNOWLEDGE_READY |
| Score components     | Nên lưu trong trace    |
| Validity/source time | Có với temporal data   |
| Content hash         | Nên có để audit/dedup  |

- Context builder ưu tiên coverage và diversity thay vì chỉ top score.

- Group các evidence cùng claim/topic để tránh token lặp.

- Giới hạn token theo model context window và reserved output budget.

- Citation phải map ngược được về canonical block/search unit/page.

- No-answer path phải là first-class response khi không đủ evidence.

# 16. Knowledge Quality, Gap Detection và Research Agent

## 16.1. Node quality signals

| **Signal**       | **Ví dụ**                                       |
|------------------|-------------------------------------------------|
| Coverage         | Tỷ lệ subtopic/claim có evidence.               |
| Source diversity | Bao nhiêu nguồn độc lập hỗ trợ.                 |
| Freshness        | Evidence có quá cũ so với domain requirement.   |
| Contradiction    | Có claims xung đột chưa resolve.                |
| Query demand     | Node được người dùng hỏi bao nhiêu.             |
| Uncertainty      | Confidence của extraction/claim/entity linking. |
| Growth rate      | Node đang tăng nhanh hay ổn định.               |

## 16.2. Gap priority

GapPriority =  
Importance  
× QueryDemand  
× (1 - EvidenceCoverage)  
× FreshnessNeed  
× Uncertainty  
× ContradictionFactor

## 16.3. Research Agent loop

Knowledge Tree / Graph  
│  
▼  
Gap Detector  
│  
▼  
Question Generator (LLM allowed here)  
│  
▼  
Research Planner  
│  
▼  
Source Discovery / Fetch  
│  
▼  
Normal Ingestion Pipeline  
│  
▼  
Dedup / Version / Tree Update  
│  
└──────────────► Gap Detector

**Nguyên tắc —** LLM không tự “nghĩ câu hỏi tiếp theo” vô điều kiện. Algorithm xác định gap/staleness/conflict trước; LLM chỉ chuyển gap có cấu trúc thành research questions và query plans.

# 17. Job State Machine, Idempotency và Failure Recovery

## 17.1. Document-level state

| **State**  | **Ý nghĩa**                                               |
|------------|-----------------------------------------------------------|
| PROCESSING | Có ít nhất một stage bắt buộc đang chạy.                  |
| READY      | Search path tối thiểu hoàn tất; UI có thể cho phép hỏi.   |
| FAILED     | Search path không thể hoàn thành; có stage/error rõ ràng. |

## 17.2. Capability/readiness flags

readiness = {  
parsed: true\|false,  
search: true\|false,  
knowledge: true\|false,  
summaries: true\|false  
}

## 17.3. Stage runs

| **Stage**         | **Retry semantics**                               |
|-------------------|---------------------------------------------------|
| REGISTER          | Idempotent theo source/version key.               |
| EXTRACT           | Có thể retry; output versioned/temp trước commit. |
| DEDUP             | Deterministic; retry an toàn.                     |
| SEARCH_INDEX      | Upsert theo stable point ID/index manifest.       |
| KNOWLEDGE_EXTRACT | Retry riêng; không hạ SEARCH_READY nếu fail.      |
| TREE_ASSIGN       | Idempotent theo tree version/membership.          |
| SUBTREE_REBUILD   | Transaction/version boundary rõ ràng.             |
| SUMMARY           | Best-effort/async; có thể bỏ qua nếu không cần.   |

# 18. Security, ACL và Multi-tenancy

ACL không chỉ là Qdrant post-filter. Nếu tree được build toàn Project nhưng routing prototype/summaries trộn tài liệu khác quyền, hệ thống vừa có nguy cơ information leakage vừa có routing blackhole. SAG vì vậy tách **tree topology** khỏi **ACL-safe routing profile**.

## 18.1. Tree scope

Default:

```text
Tenant
  -> Project Tree Topology
       -> Node Routing Profiles by Security Partition
```

- Tree topology được build theo `tenant_id + project_id` để giữ cấu trúc miền tri thức ổn định.
- Không build tree per-user vì số combination ACL có thể bùng nổ.
- Document được gán `security_partition_id` dựa trên **ACL equivalence class** ổn định: workspace/team/source collection/classification boundary, không phải từng user ngẫu nhiên.
- Với môi trường đặc biệt nhạy cảm có thể cấu hình `TREE_SCOPE=SECURITY_PARTITION` để tách vật lý tree theo boundary.

## 18.2. ACL-safe Node Routing Profile

Không dùng một centroid/sparse signature chung cho tất cả user. Bảng:

```text
node_routing_profiles
- tree_version
- node_id
- security_partition_id
- centroid_ref
- medoid_unit_ids
- sparse_signature
- entity_profile
- time_min / time_max
- accessible_unit_count
- summary_ref_nullable
```

Khi principal có quyền trên nhiều partition, Query Router score union các profile tương ứng. Nếu principal không có quyền trên một profile thì profile đó hoàn toàn không tham gia beam score.

**Security rule:** `node.summary` có raw content chỉ được sinh/lưu theo security partition. Không có “project-global LLM summary” chứa nội dung từ nhiều ACL boundary rồi hiển thị cho user quyền thấp hơn.

## 18.3. ACL-aware Beam Search

Trước khi expand child:

```text
if accessible_unit_count(child, principal_scope) == 0:
    prune child
```

Routing score thêm `acl_coverage_prior`, ví dụ:

```text
acl_coverage_prior = log1p(accessible_unit_count) / log1p(node_total_count)
```

Không dùng prior này để làm rò thông tin count tuyệt đối ra response; nó chỉ là internal score.

## 18.4. Chống Routing Blackhole

Ngay cả ACL-aware profile có thể thiếu/delay do incremental update. Vì vậy:

1. Tree-guided branch retrieval luôn dùng **exact ACL filter** ở Qdrant.
2. Nếu local branch trả `0` hoặc coverage thấp, bắt buộc chạy ACL-filtered global escape retrieval.
3. `acl_blackhole_rate` = số query mà escape tìm được relevant evidence trong khi selected branch không có accessible evidence / tổng query.
4. Nếu metric vượt threshold, block publish tree version mới hoặc trigger profile rebuild.

## 18.5. Qdrant filter fields

Payload filter tối thiểu:

- `tenant_id`
- `project_id`
- `security_partition_id` hoặc ACL filter representation
- `document_id/document_version_id`
- active routing node field theo slot A/B
- validity/time fields khi query temporal

Các field filter thường xuyên phải có payload index. Qdrant khuyến nghị tạo payload index trước ingestion để filtered vector search hoạt động hiệu quả và filterable HNSW có thể tận dụng filter-aware edges.

## 18.6. Arbitrary document-level ACL

Nếu quyền là arbitrary allowlist per document và không thể gom thành partition ổn định:

- Không tạo user-specific tree.
- Beam routing dùng safe project topology + only non-content topology statistics, sau đó tính accessible child candidates bằng PostgreSQL/materialized ACL mapping.
- Không dùng mixed-ACL summary/medoid text ở routing layer.
- Tăng escape budget cho principal có ACL fragmentation cao.
- Qdrant vẫn là enforcement cuối cùng bằng exact document/security filter.

## 18.7. Audit requirements

Mỗi retrieval trace phải lưu:

```text
principal_scope_hash
acl_epoch
selected_security_partitions
selected_node_ids
filtered_candidate_count
acl_blackhole_fallback_used
```

Không log raw permission token hay secret.

# 19. Cache Layer

Cache mục tiêu là giảm CPU/model/search work lặp lại nhưng **không được làm yếu ACL, versioning hoặc freshness**. Mọi cache key đều phải mang đủ version/epoch để invalidation chủ yếu diễn ra bằng namespace change thay vì quét/xóa hàng loạt.

## 19.1. Cache hierarchy

| Cache | Nội dung | Key bắt buộc | Default policy |
|---|---|---|---|
| L0 model cache | Laya/embedding/sparse model singleton | model version + device | process lifetime |
| Query embedding cache | dense query vector | normalized query hash + embedding model version | LRU, 24h–7d |
| Sparse encoding cache | sparse query representation | query hash + sparse encoder version | LRU, 24h–7d |
| Exact routing cache | strategy + selected tree paths | tenant/project + ACL fingerprint + query hash + tree version + planner version | TTL 30–60m |
| Semantic routing cache | route cho query gần nghĩa | same scope + query vector + tree version | optional, threshold cao |
| Retrieval cache | evidence IDs/scores | query + ACL + search epoch + tree version + filters | TTL 5–15m |
| Answer cache | final answer | **disabled by default** | chỉ bật với evidence signature chặt |

## 19.2. Exact Routing Cache

Key mẫu:

```text
route:{tenant}:{project}:{acl_fp}:{tree_version}:{planner_version}:{embedding_version}:{query_hash}:{filter_hash}
```

Value:

```json
{
  "strategy": "LOCAL_FACTUAL",
  "selected_nodes": ["n1", "n2"],
  "route_scores": [0.88, 0.72],
  "entropy": 0.31
}
```

Khi active tree version đổi, key namespace đổi tự nhiên; không cần scan delete cache cũ.

## 19.3. Semantic Routing Cache

Semantic cache chỉ reuse **routing plan**, không reuse answer/evidence mặc định.

Lookup condition ban đầu:

```text
same tenant/project
same ACL fingerprint
same tree_version
same primary strategy/modifiers
same temporal/filter bucket
cosine(query_embedding, cached_query_embedding) >= 0.97
```

`0.97` là default conservative; tune bằng route-equivalence precision. Query có exact identifier, sensitive temporal filter hoặc `COMMAND` không dùng semantic route cache.

Implementation options:

- single replica/MVP: in-process ANN/LRU nhỏ;
- multi-replica: dedicated shared cache service;
- nếu muốn tránh thêm Redis/Valkey sớm, có thể dùng dedicated Qdrant collection cho route-cache embeddings, nhưng phải benchmark để không tự tạo thêm bottleneck.

## 19.4. Retrieval Cache

Retrieval cache được invalidated logic bởi `active_search_epoch`:

```text
retrieval:{project}:{acl_fp}:{search_epoch}:{tree_version}:{query_hash}:{filter_hash}:{retrieval_config_version}
```

Mỗi lần một ingestion batch làm corpus trở thành SEARCH_READY hoặc ACL mapping thay đổi đáng kể:

```text
active_search_epoch += 1
```

Không cần delete tất cả key cũ; chúng tự hết TTL.

## 19.5. ACL fingerprint

`acl_fp` là hash ổn định của effective security partitions/permission groups + `acl_epoch`, ví dụ:

```text
SHA256(sorted(security_partition_ids) + acl_epoch + policy_version)
```

Không đưa raw user ID vào semantic shared cache nếu không cần. Hai user chỉ share cache khi effective access context thực sự tương đương.

## 19.6. Answer cache policy

Disabled mặc định vì answer dễ stale và có nguy cơ citation/ACL leak. Nếu bật sau benchmark thì key phải chứa:

```text
acl_fp
search_epoch
tree_version
evidence_signature
prompt_version
generation_model_version
```

`evidence_signature` là hash của ordered evidence IDs + content/version hashes. Chỉ khi signature giống nhau mới reuse final answer.

## 19.7. Cache invalidation matrix

| Event | Embedding cache | Routing cache | Retrieval cache | Answer cache |
|---|---|---|---|---|
| New document SEARCH_READY | giữ | giữ nếu tree chưa đổi | invalidate bằng `search_epoch` | invalidate |
| Subtree/tree publish | giữ | invalidate bằng `tree_version` | invalidate bằng tree/search epoch | invalidate |
| ACL policy/membership change | giữ | invalidate bằng `acl_epoch/acl_fp` | invalidate | invalidate |
| Embedding model change | namespace mới | namespace mới | index migration required | invalidate |
| Planner rule change | giữ | `planner_version` mới | thường giữ nếu route bypassed; safest config version mới | invalidate |
| Prompt/LLM change | giữ | giữ | giữ | prompt/model version mới |

## 19.8. Cache observability

Theo dõi:

- exact route cache hit rate;
- semantic route cache hit rate và false-route rate;
- embedding cache hit rate;
- retrieval cache hit rate;
- stale-cache incident count;
- ACL fingerprint mismatch rejects;
- p95 latency saved per cache layer.

Cache chỉ được giữ nếu nó giảm latency/cost mà không làm giảm routing/retrieval accuracy đáng kể.

# 20. Observability, Evaluation và Latency Budget

## 20.1. Retrieval trace

query_analysis_ms  
laya_ms  
routing_ms  
branch_dense_ms  
branch_sparse_ms  
escape_search_ms  
fusion_ms  
dedup_mmr_ms  
graph_expand_ms  
rerank_ms  
context_build_ms  
llm_ms  
  
tree_version  
selected_node_ids  
retrieval_strategy  
fallback_used

## 20.2. Metrics theo layer

| **Layer**  | **Metrics**                                                            |
|------------|------------------------------------------------------------------------|
| Ingestion  | docs/min, blocks/sec, failures by stage, queue lag                     |
| Dedup      | duplicate rate, false merge sample rate, near-dup cluster size         |
| Tree       | cohesion, drift, outlier ratio, giant ratio, child-size entropy, edge-cut, rebuild frequency, node depth |
| Routing    | branch recall, escape win rate, routing entropy, selected branch count, ACL blackhole rate, strategy confusion matrix |
| Retrieval  | Recall@K, MRR, nDCG@K, context precision/recall                        |
| Citation   | citation precision/coverage                                            |
| Generation | groundedness, no-answer correctness                                    |
| Latency    | p50/p95/p99 per stage + end-to-end                                     |
| Cost       | embedding/indexing/LLM cost per document/query, L4 E2 escalation rate, queue lag     |

## 20.3. Latency budgets

Không hard-code một con số trước benchmark. Mỗi environment phải định nghĩa SLO cho routing, retrieval, rerank và generation; các stage có budget riêng. Agent Service phải có khả năng bỏ graph expansion/rerank khi budget còn lại không đủ.

# 21. Kế hoạch triển khai theo Phase

| **Phase**                           | **Kết quả chính**                                                                        |
|-------------------------------------|------------------------------------------------------------------------------------------|
| Phase 0 — Contracts & Foundations   | Schema, IDs, provenance, timestamps, readiness, Laya response contract, index manifests. |
| Phase 1 — Upload & Versioned Source | Upload API, idempotency, Document/Version/Job, status API, frontend progress.            |
| Phase 2A — Canonical Extraction     | Parsers, block model, normalization, anchors, tests theo PDF/DOCX/TXT.                   |
| Phase 2B — Dedup & Temporal         | Exact/near duplicate, lineage, version validity, reprocess semantics.                    |
| Phase 2C — Search Index             | Search units, dense+sparse indexing, Qdrant payload/filter design, SEARCH_READY.         |
| Phase 3 — Laya & Query Analyzer     | Coarse intent + deterministic entity/date/identifier parsing + fallback.                 |
| Phase 4 — Retrieval Engine v1       | Global hybrid baseline, fusion, dedup, MMR, citations, metrics.                          |
| Phase 5 — Knowledge Units & Graph   | E0/E1 local extraction, selective E2 queue, calibrated multi-signal sparse graph.         |
| Phase 6 — Knowledge Routing Tree    | Constrained Hierarchical Leiden, giant-component guard, capacity repair, node prototypes. |
| Phase 7 — Tree-guided Retrieval     | ACL-aware beam routing, Query Strategy Planner, escape retrieval, bounded graph expansion. |
| Phase 8 — Incremental Tree          | Delta assignment, drift monitor, subtree rebuild, dual-slot blue-green publish/rollback.   |
| Phase 9 — Knowledge Quality & Gap   | Coverage/freshness/conflict metrics, gap model, question generation.                     |
| Phase 10 — Research Agent           | Gap-driven discovery/fetch → normal ingestion loop.                                      |
| Phase 11 — Hardening                | Cache layer, load tests, ACL leakage/blackhole tests, SLOs, failure drills, migration.      |

## 21.1. Checkpoints

- Checkpoint A — SEARCH_READY: upload → parse → dedup → hybrid search → citation hoạt động độc lập với knowledge enrichment.

- Checkpoint B — ROUTING_READY: tree build ổn định, routing recall đạt ngưỡng, escape retrieval kiểm soát lỗi route.

- Checkpoint C — INCREMENTAL_READY: dữ liệu mới không yêu cầu full rebuild; subtree drift/rebuild có versioning.

- Checkpoint D — AGENT_READY: gap scoring sinh research tasks có căn cứ và vòng lặp ingest không tạo duplicate/unbounded crawl.

# 22. Definition of Done

- [ ] Upload hợp lệ tạo đúng một logical document/version và một ingestion workflow idempotent.

- [ ] Canonical blocks có source/page/section/anchor đủ để citation.

- [ ] Dedup exact/near-duplicate có test và không merge semantic contradiction một cách mù quáng.

- [ ] Document chỉ SEARCH_READY sau khi search index nhất quán hoàn tất.

- [ ] Reprocess không tạo duplicate chunk/vector và có index manifest/version rõ ràng.

- [ ] Hybrid retrieval tìm được evidence chuẩn trên regression corpus.

- [ ] Laya fail không làm knowledge query fail.

- [ ] Knowledge Routing Tree được xây bằng thuật toán và có prototypes/quality metrics.

- [ ] Tree-guided retrieval có escape path; branch routing error không làm recall rơi về 0.

- [ ] Delta ingestion cập nhật node/ancestors và chỉ rebuild subtree khi cần.

- [ ] PostgreSQL giữ history/provenance/tree lineage; Qdrant có thể rebuild từ source of truth.

- [ ] Citation map đúng về source/version/page/anchor.

- [ ] LLM trả lời grounded hoặc nêu rõ thiếu evidence.

- [ ] Retrieval trace phân biệt requested/effective strategy và fallback_used.

- [ ] Có p50/p95/p99 theo stage và regression evaluation Recall@K/MRR/nDCG.

- [ ] Frontend phân biệt processing/search-ready/knowledge-ready/failed và lỗi bằng tiếng Việt.

- [ ] Knowledge gap được tính từ metrics có cấu trúc trước khi dùng LLM sinh câu hỏi.

- [ ] Multi-signal scores được calibration về `[0,1]`; config/quantile parameters có version.

- [ ] Tree build đáp ứng `N_min/N_max/Depth_max`, giant-ratio gate và không publish nếu routing Recall regression vượt ngưỡng.

- [ ] ACL-safe routing profile ngăn mixed-permission centroid/summary leakage; branch không-accessible bị prune trước beam search.

- [ ] Subtree rebuild dùng inactive routing slot; active pointer chỉ switch sau manifest verification và có rollback.

- [ ] Query Strategy Planner có regression test cho Exact/Temporal/Relational/Global/Multi-hop và reason codes deterministic.

- [ ] L4 E2 LLM enrichment là selective async, có queue budget/backpressure và không chặn SEARCH_READY.

- [ ] Cache key chứa tree/search/ACL/model/planner version tương ứng; semantic cache không cross ACL context.

# 23. Rủi ro và quyết định kiến trúc

| **Rủi ro**                            | **Giảm thiểu**                                                       |
|---------------------------------------|----------------------------------------------------------------------|
| Tree route sai làm mất recall         | Escape retrieval + secondary membership + routing evaluation.        |
| Topic drift do dữ liệu hằng ngày      | Incremental assignment + drift monitor + subtree rebuild.            |
| Cluster ID thay đổi sau rebuild       | Stable node matching + lineage.                                      |
| Graph traversal chậm trong PostgreSQL | Sparse edges, bounded hop/node budget, tree-first routing.           |
| Enrichment LLM làm ingestion chậm     | SEARCH_READY trước; knowledge enrichment asynchronous.               |
| Semantic duplicate merge sai          | Embedding chỉ sinh candidate; giữ contradiction/supersede semantics. |
| Rerank làm p95 cao                    | Chỉ rerank candidate nhỏ và cho phép skip theo latency budget.       |
| Qdrant trở thành source of truth ngầm | Index manifest + rebuildability từ PostgreSQL.                       |
| Tree quá sâu hoặc quá rộng            | Depth/capacity/cohesion constraints + benchmark routing entropy.     |
| Research Agent crawl vô hạn           | Gap priority, domain/source policies, budget và duplicate guard.     |
| Giant component / skewed tree         | Constrained Leiden + capacity repair + giant-ratio quality gate.      |
| ACL leakage qua centroid/summary       | Security-partition routing profiles; không dùng mixed-ACL summaries. |
| ACL routing blackhole                  | Accessible-count pruning + mandatory global escape fallback.          |
| Tree rebuild race PG/Qdrant            | Dual routing slot A/B + immutable SearchSnapshot + rollback.           |
| Cache trả route/evidence stale          | Versioned cache keys: tree/search/ACL/model/planner epochs.            |

# Phụ lục A — PostgreSQL Schema gợi ý

```text
documents
  id, tenant_id, project_id, owner_id, logical_source_id, status, created_at

document_versions
  id, document_id, version_no, file_hash,
  source_published_at, observed_at, ingested_at,
  valid_from, valid_to, supersedes_id

canonical_blocks
  id, document_version_id, block_type, ordinal,
  page_from, page_to, section_path, source_anchor,
  normalized_text, content_hash

search_units
  id, document_version_id, block_from_id, block_to_id,
  content_hash, token_count, page_from, page_to, section_path,
  security_partition_id

knowledge_units
  id, document_version_id, canonical_content_hash,
  primary_node_id, confidence, valid_from, valid_to,
  extractor_version

entities
entity_aliases
entity_mentions
claims
claim_evidence
relations
relation_evidence

knowledge_nodes
  id, parent_id, path, depth,
  valid_from_tree_version, valid_to_tree_version,
  unit_count, child_count, cohesion, density, edge_cut,
  time_min, time_max

node_routing_profiles
  tree_version, node_id, security_partition_id,
  centroid_ref, medoid_unit_ids,
  sparse_signature, entity_profile,
  source_profile, accessible_unit_count,
  time_min, time_max, summary_ref_nullable

knowledge_memberships
  unit_id, node_id, weight, membership_type,
  valid_from_tree_version, valid_to_tree_version

knowledge_edges
  from_id, to_id, relation_type, weight,
  valid_from, valid_to

graph_signal_calibrations
  graph_config_version, signal_name,
  method, q05, q50, q95, mad, created_at

tree_manifests
  tree_version, project_id, config_version,
  node_count, leaf_count, max_leaf_size,
  giant_ratio, routing_recall_at_k,
  escape_win_rate, acl_blackhole_rate,
  status, checksum, created_at

project_search_state
  project_id,
  active_tree_version,
  active_routing_slot,
  previous_tree_version,
  active_search_epoch,
  acl_epoch,
  updated_at

security_partitions
principal_partition_memberships
search_unit_acl_mappings   # optional for fragmented document ACL

dedup_clusters
ingestion_runs
stage_runs
index_manifests
l4_enrichment_jobs
knowledge_gaps
research_questions
```

# Phụ lục B — Qdrant Payload gợi ý

```json
{
  "point_id": "stable-search-unit-id",
  "tenant_id": "...",
  "project_id": "...",
  "security_partition_id": "...",
  "document_id": "...",
  "document_version_id": "...",
  "search_unit_id": "...",
  "content_hash": "...",

  "primary_node_a": "node-123",
  "secondary_node_ids_a": ["node-456"],
  "tree_version_a": 103,

  "primary_node_b": "node-789",
  "secondary_node_ids_b": ["node-999"],
  "tree_version_b": 104,

  "page_from": 1,
  "page_to": 2,
  "published_at": "...",
  "valid_from": "...",
  "valid_to": null,
  "source_type": "..."
}
```

Payload index nên tạo trước ingestion cho các field filter thường xuyên: `tenant_id`, `project_id`, `security_partition_id`, `document_id`, `document_version_id`, `primary_node_a`, `primary_node_b`, temporal fields cần range filter. `secondary_node_ids_*` chỉ index nếu branch retrieval thực sự filter trên đó đủ thường xuyên để đáng chi phí.

**Không lưu `active_slot` trên từng point.** Active slot là control-plane state trong PostgreSQL; query chọn field A hoặc B dựa trên snapshot của request.

# Phụ lục C — Retrieval API Contract

Request:

```json
{
  "query": "...",
  "project_id": "...",
  "document_ids": [],
  "time_range": null,
  "latency_budget_ms": 1200,
  "requested_strategy": "AUTO"
}
```

Internal snapshot captured once:

```json
{
  "tree_version": 104,
  "routing_slot": "B",
  "search_epoch": 221,
  "acl_epoch": 17,
  "acl_fingerprint": "sha256:..."
}
```

Response:

```json
{
  "requested_strategy": "AUTO",
  "effective_strategy": "ENTITY_RELATIONAL",
  "strategy_modifiers": ["TEMPORAL"],
  "reason_codes": ["MULTI_ENTITY", "RELATION_CUE", "EXPLICIT_DATE"],
  "fallback_used": false,
  "tree_version": 104,
  "selected_nodes": ["..."],
  "evidence": [],
  "retrieval_trace": {
    "query_analysis_ms": 2,
    "routing_cache_hit": false,
    "routing_ms": 7,
    "branch_search_ms": 19,
    "escape_search_ms": 6,
    "fusion_ms": 2,
    "graph_expand_ms": 0,
    "rerank_ms": 28,
    "acl_blackhole_fallback_used": false
  }
}
```

# Phụ lục D — Pseudocode Tree-guided Retrieval

```python
def retrieve(query, principal, scope, budget):
    snap = read_search_snapshot(scope.project_id)  # one immutable request snapshot
    acl_ctx = resolve_acl_context(principal, snap.acl_epoch)

    laya = coarse_intent(query)
    if laya.intent == "CHAT" and laya.confidence >= CHAT_HIGH:
        return no_retrieval()

    features = deterministic_query_features(query)
    plan = query_strategy_planner(laya, features)

    q_dense = embedding_cache.get_or_compute(
        query, EMBEDDING_MODEL_VERSION, embed
    )
    q_sparse = sparse_cache.get_or_compute(
        query, SPARSE_ENCODER_VERSION, sparse_encode
    )

    route_key = make_route_cache_key(
        query=query,
        acl_fp=acl_ctx.fingerprint,
        tree_version=snap.tree_version,
        planner_version=PLANNER_VERSION,
        filters=features.filters,
    )

    nodes = route_cache.get(route_key)
    if nodes is None:
        profiles = load_acl_safe_profiles(
            tree_version=snap.tree_version,
            security_partitions=acl_ctx.security_partitions,
        )
        nodes = beam_route(
            q_dense=q_dense,
            q_sparse=q_sparse,
            entities=features.entities,
            time_filter=features.time_filter,
            profiles=profiles,
            beam_width=adaptive_beam(plan),
            tree_version=snap.tree_version,
        )
        route_cache.set(route_key, nodes)

    local = qdrant_hybrid_search(
        scope=scope,
        acl_filter=acl_ctx.qdrant_filter,
        routing_slot=snap.routing_slot,
        node_ids=nodes,
        strategy=plan,
        budget=LOCAL_BUDGET,
    )

    escape = []
    if should_run_escape(local, plan):
        escape = qdrant_hybrid_search(
            scope=scope,
            acl_filter=acl_ctx.qdrant_filter,
            routing_slot=snap.routing_slot,
            node_ids=None,
            strategy=plan,
            budget=ESCAPE_BUDGET,
        )

    candidates = fuse(local, escape)
    candidates = content_dedup(candidates)
    candidates = diversify_mmr(candidates)

    if coverage(candidates, features) < COVERAGE_MIN and budget.allow_graph:
        expanded = bounded_graph_expand(
            candidates,
            acl_ctx=acl_ctx,
            tree_version=snap.tree_version,
            hops=2,
            node_budget=MAX_GRAPH_NODES,
        )
        candidates = fuse(candidates, expanded)

    if budget.allow_rerank:
        candidates = rerank(candidates[:RERANK_INPUT_K])

    return build_evidence_pack(
        candidates,
        token_budget=budget.context_tokens,
        search_snapshot=snap,
    )
```

# Phụ lục E — Regression Query Taxonomy

| **Loại query**        | **Ví dụ mục tiêu**                       |
|-----------------------|------------------------------------------|
| Exact identifier      | port, error code, env var, API path      |
| Semantic paraphrase   | khác từ nhưng cùng ý                     |
| Vietnamese/domain mix | câu Việt có thuật ngữ kỹ thuật tiếng Anh |
| Entity                | hỏi một entity cụ thể                    |
| Relationship          | A liên quan gì đến B                     |
| Multi-hop             | A → B → C                                |
| Temporal              | trạng thái tại thời điểm/version         |
| Global                | tổng quan toàn domain/project            |
| Comparison            | so sánh hai version/approach             |
| Contradiction         | nguồn A và B khác nhau                   |
| No-answer             | corpus không có evidence                 |
| Duplicate-source      | nhiều nguồn copy cùng nội dung           |
| Cross-domain          | knowledge unit có secondary membership   |
| ACL split             | cùng query, user A/B có corpus khác nhau |
| Routing blackhole     | branch local rỗng nhưng escape có answer |
| Giant-cluster         | corpus sinh hub/generic entity mạnh       |
| Tree version race     | query chạy trong lúc blue-green switch    |
| Semantic-cache near   | query gần nghĩa nhưng filter/time khác    |

# Phụ lục F — Cấu hình môi trường tối thiểu

```dotenv
SAG_ENABLE_LAYA=true
SAG_LAYA_MODEL_PATH=C:\\laya-local
SAG_LAYA_DEVICE=cpu

SAG_UPLOAD_DIR=...
DATABASE_URL=postgresql+asyncpg://...
QDRANT_URL=...
QDRANT_API_KEY=...  # secret, never commit/log

# Tree defaults - benchmark before production lock
SAG_TREE_N_MIN=5
SAG_TREE_N_TARGET=25
SAG_TREE_N_MAX=50
SAG_TREE_DEPTH_MAX=4
SAG_TREE_MAX_CHILDREN=12
SAG_TREE_GIANT_RATIO_MAX=0.60
SAG_TREE_BEAM_WIDTH=3

# Retrieval
SAG_RETRIEVAL_ESCAPE_BUDGET_RATIO=0.15
SAG_ROUTE_GLOBAL_ENTROPY_THRESHOLD=0.72
SAG_ROUTE_TOP_MARGIN_GLOBAL_THRESHOLD=0.10

# L4 selective enrichment
SAG_L4_LLM_ENRICHMENT_ENABLED=true
SAG_L4_LLM_DAILY_TOKEN_BUDGET=...
SAG_L4_LLM_MAX_CONCURRENCY=...

# Cache
SAG_ROUTE_CACHE_TTL_SECONDS=3600
SAG_RETRIEVAL_CACHE_TTL_SECONDS=600
SAG_SEMANTIC_ROUTE_CACHE_THRESHOLD=0.97

# Embedding provider/model configured separately from generation LLM.
# Generation LLM remains configured in application Settings.
```

# Phụ lục G — Default Hyper-parameter Registry

| Group | Parameter | Initial default | Tune objective |
|---|---|---:|---|
| Graph | semantic weight | 0.40 | cluster purity + route recall |
| Graph | lexical weight | 0.20 | exact/rare-term recall |
| Graph | entity weight | 0.15 | relational queries |
| Graph | structure weight | 0.10 | local coherence |
| Graph | temporal weight | 0.10 | historical correctness |
| Graph | citation weight | 0.05 | evidence-linked cohesion |
| Graph | max degree | 32 | memory vs quality |
| Graph | edge threshold | 0.55 | sparsity vs connectivity |
| Tree | N_min | 5 | avoid tiny clusters |
| Tree | N_target | 25 | routing granularity |
| Tree | N_max | 50 | p95 branch search |
| Tree | Depth_max | 4 | route latency/recall |
| Tree | giant ratio | 0.60 | branch balance |
| Routing | beam width | 3 | Recall@K / latency |
| Routing | global entropy | 0.72 | local/global classification |
| Cache | semantic threshold | 0.97 | route equivalence precision |

Không freeze các số này trước regression benchmark. Mọi production config phải lưu trong `retrieval_config_version`/`graph_config_version` và có rollback.

# Phụ lục H — Tài liệu tham khảo kỹ thuật

- Microsoft GraphRAG — indexing, hierarchical Leiden, query modes: https://microsoft.github.io/graphrag/
- Microsoft GraphRAG — default dataflow / community detection: https://microsoft.github.io/graphrag/index/default_dataflow/
- Microsoft GraphRAG — FastGraphRAG vs standard graph extraction: https://microsoft.github.io/graphrag/index/methods/
- Qdrant — collection aliases và atomic switch: https://qdrant.tech/documentation/manage-data/collections/
- Qdrant — payload indexes/filter-aware search: https://qdrant.tech/documentation/manage-data/indexing/
- Qdrant — filtering: https://qdrant.tech/documentation/search/filtering/
- Qdrant — point updates / conditional update: https://qdrant.tech/documentation/manage-data/points/
- PostgreSQL — `ltree`: https://www.postgresql.org/docs/current/ltree.html
- PostgreSQL — recursive queries: https://www.postgresql.org/docs/current/queries-with.html
- NetworkX Leiden documentation: https://networkx.org/documentation/stable/reference/algorithms/community.html

---

**Kết luận kiến trúc —** SAG không còn được tối ưu như một “vector RAG application”. Target architecture là một knowledge system: PostgreSQL giữ truth + version + ACL-aware hierarchy + sparse graph; Qdrant tăng tốc filtered hybrid retrieval; Constrained Knowledge Routing Tree amortize chi phí tổ chức tri thức sang ingestion; selective L4 enrichment và cache giảm chi phí lặp; blue-green routing version đảm bảo rebuild không tạo race condition; Research Agent dùng tree/quality/gap signals để quyết định cần học thêm gì.
