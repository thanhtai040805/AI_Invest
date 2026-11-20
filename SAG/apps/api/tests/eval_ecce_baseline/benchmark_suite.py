"""12-Scenario Vietnamese & Multi-Step Benchmark + Ablation Evaluator (V0 -> V5) for SAG-ECCE."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sag_api.services.decision_router import infer_ecce_routing_signals
from sag_api.services.session_context.models import AblationMode
from sag_api.services.session_context.optimizer import build_ecce_context_pack
from sag_api.services.session_context.substrate import IncrementalSessionIndex

ALL_ABLATION_MODES: tuple[AblationMode, ...] = (
    "V0_legacy",
    "V1_hybrid",
    "V2_graph_ppr",
    "V3_dual_hierarchy",
    "V4_closure_temporal",
    "V5_ecce_full",
)


@dataclass(frozen=True)
class BenchmarkScenario:
    case_id: str
    category: str
    query: str
    gold_turn_ids: tuple[str, ...]
    forbidden_stale_turn_ids: tuple[str, ...] = ()
    budget_tokens: int = 620


def build_shared_long_horizon_corpus() -> list[dict[str, Any]]:
    """Construct a realistic 236-turn multi-episode Vietnamese engineering session."""
    t0 = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)
    msgs: list[dict[str, Any]] = []

    def add(turn_id: str, role: str, content: str, minutes_offset: int) -> None:
        msgs.append(
            {
                "id": turn_id,
                "role": role,
                "content": content,
                "created_at": t0 + timedelta(minutes=minutes_offset),
            }
        )

    # ── Episode 0 (Early Kickoff — >220 turns before end, beyond legacy LIMIT 200!) ──
    add(
        "gold_budget_t1",
        "user",
        "Chốt nguyên tắc tài chính ngay đầu dự án: hạn mức ngân sách hạ tầng đám mây tối đa là 1450 USD mỗi tháng.",
        1,
    )
    add(
        "ack_budget_t2",
        "assistant",
        "Đã ghi nhận hạn mức ngân sách đám mây tối đa 1450 USD/tháng cho toàn bộ môi trường.",
        2,
    )
    add(
        "stale_db_mongo",
        "user",
        "Về database lưu trữ giao dịch phân tán, mình đề xuất thử dùng MongoDB để linh hoạt schema ban đầu.",
        3,
    )
    add(
        "gold_lan_owner",
        "user",
        "Trong buổi họp đầu phiên hôm nay, thống nhất giao chị Lan nhận phụ trách mảng kiểm thử bảo mật OWASP.",
        4,
    )
    add(
        "gold_hop1_gateway",
        "assistant",
        "Theo thiết kế bảo mật, dịch vụ payment-gateway-v2 bắt buộc xác thực qua cụm khóa Vault-HSM-Prod.",
        5,
    )
    add(
        "gold_minh_fe",
        "user",
        "Thống nhất giao cho anh Minh FE phụ trách tối ưu bảng điều khiển biểu đồ doanh thu trên trình duyệt.",
        6,
    )

    # Filler Episode A (UI & Styling discussions)
    for i in range(40):
        add(
            f"fill_ui_{i}",
            "user" if i % 2 == 0 else "assistant",
            (
                f"Trao đổi giao diện bước {i}: rà soát độ tương phản màu chữ 4.5:1, "
                "căn lề bảng số liệu tabular-nums và responsive mobile 375px."
            ),
            45 + i,
        )

    # ── Episode 2 (Architecture Debate & Mid-Session Proposals) ──
    add(
        "stale_queue_pg_v1",
        "user",
        "Ban đầu cho hàng đợi sự kiện nội bộ, chúng ta chốt dùng PostgreSQL LISTEN/NOTIFY cho gọn nhẹ.",
        130,
    )
    add(
        "gold_golive_date",
        "assistant",
        "Biên bản thống nhất lịch golive bản thử nghiệm Continuum giai đoạn 1 ấn định vào ngày 15/11/2026.",
        132,
    )
    add(
        "stale_db_cockroach",
        "user",
        (
            "Đối với database lưu trữ giao dịch hệ thống chính, team đang cân nhắc "
            "dùng CockroachDB vì hỗ trợ giao dịch phân tán."
        ),
        134,
    )
    add(
        "gold_tuan_prop",
        "user",
        (
            "Anh Tuấn vừa gửi 2 lựa chọn kho vector: (1) tự vận hành cụm Milvus riêng, "
            "hoặc (2) dùng Qdrant đồng bộ theo từng project."
        ),
        136,
    )
    add(
        "gold_tuan_confirm",
        "user",
        "Đồng ý luôn, chốt phương án 2 như anh Tuấn vừa đề xuất nhé!",
        137,
    )
    add(
        "gold_minh_be",
        "user",
        "Còn phía backend, thống nhất anh Minh BE sẽ phụ trách tích hợp cổng đối soát thanh toán VNPAY.",
        139,
    )

    # Filler Episode B (Parsing & Ingestion formatting)
    for i in range(60):
        add(
            f"fill_ingest_{i}",
            "user" if i % 2 == 0 else "assistant",
            (
                f"Kiểm tra luồng đọc tài liệu PDF lần {i}: chuẩn hóa bảng biểu Markdown, "
                "giữ nguyên tiêu đề cột khi ngắt trang."
            ),
            200 + i,
        )

    # ── Episode 4 (Security Vault & Queue Switch -> Failure -> Revert) ──
    add(
        "gold_hop2_vault",
        "assistant",
        "Quy chuẩn vận hành của cụm khóa Vault-HSM-Prod bắt buộc chu kỳ xoay vòng chứng chỉ mTLS là 72 giờ một lần.",
        310,
    )
    add(
        "stale_queue_redis",
        "user",
        "Thống nhất chuyển hàng đợi sự kiện sang dùng Redis Streams thay cho LISTEN/NOTIFY để tăng thông lượng.",
        312,
    )
    add(
        "gold_queue_issue",
        "assistant",
        "Cảnh báo kiểm thử tải: Redis Streams đang bị lỗi mất thứ tự gói tin khi consumer khởi động lại đột ngột.",
        314,
    )
    add(
        "gold_queue_revert_pg",
        "user",
        (
            "Do lỗi mất thứ tự gói tin trên Redis Streams, quyết định bỏ Redis Streams "
            "và chốt quay lại dùng PostgreSQL LISTEN/NOTIFY."
        ),
        316,
    )

    # Filler Episode C (QA & Logging)
    for i in range(60):
        add(
            f"fill_qa_{i}",
            "user" if i % 2 == 0 else "assistant",
            (
                f"Rà soát nhật ký kiểm thử đơn vị đợt {i}: kiểm tra mã trạng thái HTTP, "
                "giới hạn tốc độ truy cập và định dạng trace log."
            ),
            380 + i,
        )

    # ── Episode 6 (Final DB Confirmation, SSO Decision & Redundant Deployment Spam) ──
    add(
        "gold_db_pg16",
        "user",
        "Sau khi đo hiệu năng thực tế, bỏ CockroachDB vì tốn RAM; chốt dùng PostgreSQL 16 làm database chính thức.",
        490,
    )
    add(
        "gold_sso_keycloak",
        "user",
        (
            "Về cơ chế xác thực người dùng doanh nghiệp, chúng ta chốt dùng "
            "Keycloak OIDC SSO kết hợp JWT ngắn hạn 15 phút."
        ),
        492,
    )
    # 5 near-duplicate verbose restatements of Docker Ubuntu 24.04 deployment + 1 distinct backup RPO requirement
    for dup_i in range(5):
        add(
            f"dup_deploy_{dup_i}" if dup_i > 0 else "gold_deploy_primary",
            "assistant",
            (
                "Ghi chú triển khai máy chủ sản phẩm: hệ thống triển khai bằng Docker Compose trên máy chủ "
                "Ubuntu 24.04 LTS tiêu chuẩn với cấu hình giám sát container tự động khởi động lại khi gặp sự cố."
            ),
            530 + dup_i,
        )
    add(
        "gold_backup_rpo",
        "user",
        (
            "Yêu cầu sao lưu đi kèm khi triển khai máy chủ sản phẩm: bắt buộc "
            "cấu hình pgBackRest đảm bảo RPO tối đa 15 phút."
        ),
        536,
    )

    # Final 55 filler turns at the tail so even mid-session turns fall outside small tail budgets
    for i in range(55):
        add(
            f"fill_tail_{i}",
            "user" if i % 2 == 0 else "assistant",
            (
                f"Cập nhật tiến độ cuối ngày mục {i}: hoàn thiện tài liệu hướng dẫn "
                "vận hành và kiểm tra danh mục bàn giao."
            ),
            580 + i,
        )

    return msgs


def build_benchmark_scenarios() -> list[BenchmarkScenario]:
    return [
        BenchmarkScenario(
            case_id="C01_long_horizon_early_budget",
            category="long_horizon_recall",
            query="Đầu dự án chúng ta đã chốt hạn mức ngân sách hạ tầng đám mây tối đa mỗi tháng là bao nhiêu?",
            gold_turn_ids=("gold_budget_t1",),
            budget_tokens=520,
        ),
        BenchmarkScenario(
            case_id="C02_decision_drift_database",
            category="temporal_superseding",
            query="Đối với database lưu trữ giao dịch hệ thống chính, hiện tại chúng ta đã chốt dùng hệ quản trị nào?",
            gold_turn_ids=("gold_db_pg16",),
            forbidden_stale_turn_ids=("stale_db_mongo", "stale_db_cockroach"),
            budget_tokens=520,
        ),
        BenchmarkScenario(
            case_id="C03_decision_revert_queue",
            category="temporal_revert_A_B_A",
            query="Hiện tại hàng đợi sự kiện nội bộ đang chốt dùng giải pháp nào?",
            gold_turn_ids=("gold_queue_revert_pg",),
            forbidden_stale_turn_ids=("stale_queue_redis",),
            budget_tokens=520,
        ),
        BenchmarkScenario(
            case_id="C04_decision_lineage_why",
            category="decision_lineage",
            query="Vì sao chúng ta chuyển hàng đợi sự kiện từ Redis Streams quay lại dùng PostgreSQL LISTEN/NOTIFY?",
            gold_turn_ids=("stale_queue_redis", "gold_queue_issue", "gold_queue_revert_pg"),
            budget_tokens=680,
        ),
        BenchmarkScenario(
            case_id="C05_vi_anaphoric_confirmation",
            category="vietnamese_coreference",
            query="Chúng ta đã chốt phương án kho vector nào mà anh Tuấn đề xuất?",
            gold_turn_ids=("gold_tuan_prop", "gold_tuan_confirm"),
            budget_tokens=520,
        ),
        BenchmarkScenario(
            case_id="C06_vi_homonym_role_disambig",
            category="vietnamese_entity_disambig",
            query="Anh Minh BE được giao phụ trách hạng mục thanh toán nào?",
            gold_turn_ids=("gold_minh_be",),
            forbidden_stale_turn_ids=("gold_minh_fe",),
            budget_tokens=520,
        ),
        BenchmarkScenario(
            case_id="C07_temporal_filter_owner",
            category="temporal_filter_vs_target",
            query="Trong buổi họp đầu phiên, chị Lan nhận phụ trách mảng nào?",
            gold_turn_ids=("gold_lan_owner",),
            budget_tokens=520,
        ),
        BenchmarkScenario(
            case_id="C08_temporal_target_golive",
            category="temporal_target",
            query="Lịch golive bản thử nghiệm Continuum giai đoạn 1 được ấn định vào ngày nào?",
            gold_turn_ids=("gold_golive_date",),
            budget_tokens=520,
        ),
        BenchmarkScenario(
            case_id="C09_multihop_entity_bridge",
            category="graph_multihop",
            query="Dịch vụ payment-gateway-v2 bắt buộc tuân theo chu kỳ xoay vòng chứng chỉ mTLS bao lâu một lần?",
            gold_turn_ids=("gold_hop1_gateway", "gold_hop2_vault"),
            budget_tokens=560,
        ),
        BenchmarkScenario(
            case_id="C10_hybrid_session_and_adr",
            category="hybrid_routing",
            query="Đối chiếu cơ chế xác thực SSO chúng ta chốt trong cuộc chat với quy định tại tài liệu ADR-004.",
            gold_turn_ids=("gold_sso_keycloak",),
            budget_tokens=520,
        ),
        BenchmarkScenario(
            case_id="C11_redundant_spam_submodular",
            category="budget_optimization",
            query="Triển khai máy chủ sản phẩm dùng hệ điều hành gì và yêu cầu sao lưu pgBackRest RPO tối đa bao lâu?",
            gold_turn_ids=("gold_deploy_primary", "gold_backup_rpo"),
            budget_tokens=360,
        ),
        BenchmarkScenario(
            case_id="C12_full_session_audit",
            category="full_session_synthesis",
            query="Tổng hợp tất cả các quyết định kỹ thuật đã chốt từ đầu cuộc trò chuyện đến giờ.",
            gold_turn_ids=(
                "gold_db_pg16",
                "gold_queue_revert_pg",
                "gold_tuan_confirm",
                "gold_sso_keycloak",
            ),
            budget_tokens=980,
        ),
    ]


async def run_ablation_benchmark() -> dict[str, dict[str, Any]]:
    """Run all 6 ablation modes (V0 -> V5) on the 12-scenario long-horizon benchmark."""
    corpus = build_shared_long_horizon_corpus()
    scenarios = build_benchmark_scenarios()
    index = IncrementalSessionIndex("bench-thread-main")
    await index.append_messages(corpus)

    summary_by_mode: dict[str, dict[str, Any]] = {}
    for mode in ALL_ABLATION_MODES:
        recalls: list[float] = []
        full_hits = 0
        stale_contaminations = 0
        stale_eligible = 0
        tokens_used: list[int] = []
        llm_calls_total = 0
        latencies_ms: list[float] = []
        per_case: dict[str, dict[str, Any]] = {}

        for sc in scenarios:
            mem_scope, temp_mode = infer_ecce_routing_signals(sc.query, coarse_intent="KNOWLEDGE")
            route_dec = {
                "coarse_intent": "KNOWLEDGE",
                "memory_scope": mem_scope,
                "temporal_mode": temp_mode,
                "model": "deterministic_benchmark",
                "fallback_used": False,
            }
            pack = await build_ecce_context_pack(
                index,
                query=sc.query,
                budget_tokens=sc.budget_tokens,
                ablation=mode,
                route_decision=route_dec,
            )
            chosen_set = set(pack.selected_turn_ids)
            # Treat any of the 5 identical deployment restatements as satisfying `gold_deploy_primary`
            effective_chosen = set(chosen_set)
            if any(tid.startswith("dup_deploy_") for tid in chosen_set):
                effective_chosen.add("gold_deploy_primary")

            gold_hits = sum(1 for gid in sc.gold_turn_ids if gid in effective_chosen)
            recall = gold_hits / len(sc.gold_turn_ids)
            recalls.append(recall)
            if gold_hits == len(sc.gold_turn_ids):
                full_hits += 1

            contaminated = False
            if sc.forbidden_stale_turn_ids:
                stale_eligible += 1
                if any(fid in chosen_set for fid in sc.forbidden_stale_turn_ids):
                    stale_contaminations += 1
                    contaminated = True

            toks = pack.audit_trace.total_output_history_tokens
            tokens_used.append(toks)
            llm_calls_total += pack.audit_trace.llm_memory_calls
            latencies_ms.append(pack.audit_trace.latency_ms)
            per_case[sc.case_id] = {
                "recall": round(recall, 4),
                "contaminated": contaminated,
                "tokens": toks,
                "selected_turn_ids": pack.selected_turn_ids,
                "memory_scope": mem_scope,
                "temporal_mode": temp_mode,
            }

        avg_recall = statistics.mean(recalls)
        avg_tokens = statistics.mean(tokens_used)
        total_gold_found = sum(r * len(s.gold_turn_ids) for r, s in zip(recalls, scenarios, strict=True))
        total_toks_sum = max(1, sum(tokens_used))
        density_per_1k = (total_gold_found / total_toks_sum) * 1000.0
        sorted_lat = sorted(latencies_ms)
        p95_idx = min(len(sorted_lat) - 1, int(round(0.95 * (len(sorted_lat) - 1))))

        summary_by_mode[mode] = {
            "total_turns_in_session": len(corpus),
            "episode_count": len(index.episodes),
            "mean_recall": round(avg_recall, 4),
            "exact_all_gold_cases": f"{full_hits}/{len(scenarios)}",
            "full_hit_count": full_hits,
            "stale_contamination_rate": round(stale_contaminations / max(1, stale_eligible), 4),
            "stale_contaminations": stale_contaminations,
            "avg_history_tokens": round(avg_tokens, 1),
            "evidence_density_per_1k_tokens": round(density_per_1k, 2),
            "llm_memory_calls": llm_calls_total,
            "p50_latency_ms": round(statistics.median(sorted_lat), 2),
            "p95_latency_ms": round(sorted_lat[p95_idx], 2),
            "cases": per_case,
        }

    return summary_by_mode
