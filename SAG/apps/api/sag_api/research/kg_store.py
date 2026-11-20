"""3-Layer Knowledge Graph Persistence & Strict Point-in-Time Query Store.

Aligns with PROJECT.md Contract 1 (`kg_nodes`, `kg_edges`, `macro_sensitivity_snapshots`)
with both:
- `SQLAlchemyKGStore`: async PostgreSQL persistence & PIT query (`effective_from <= as_of_date < valid_to`)
- `InMemoryKGStore`: deterministic in-memory store for offline unit tests and fast replay
- `load_core_db_snapshot`: strictly read-only Core DB loader (`<= as_of_date`, zero credential logging)
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Protocol

import pandas as pd
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

_MAX_VALID_TO = date(9999, 12, 31)


def _coerce_date(val: Any, default: date | None = None) -> date:
    if isinstance(val, date):
        return val
    if val is None:
        if default is not None:
            return default
        raise ValueError("Date value is required")
    return date.fromisoformat(str(val)[:10])


def build_kg_nodes_from_universe(
    stocks_df: pd.DataFrame,
    macro_indicators: Sequence[str],
    as_of_date: date,
) -> list[dict[str, Any]]:
    """Build normalized `kg_nodes` payloads matching Contract 1 schema."""
    nodes: list[dict[str, Any]] = []
    valid_to = _MAX_VALID_TO.isoformat()
    eff = as_of_date.isoformat()

    for ind in sorted({str(m) for m in macro_indicators if m}):
        nodes.append(
            {
                "node_id": f"MACRO:{ind}",
                "node_type": "MACRO_FACTOR",
                "label": ind,
                "ticker": None,
                "metadata_json": {"indicator_name": ind},
                "effective_from": eff,
                "valid_to": valid_to,
            }
        )

    if not stocks_df.empty:
        if "sector" in stocks_df.columns:
            for sec in sorted({str(s) for s in stocks_df["sector"].dropna() if str(s).strip()}):
                nodes.append(
                    {
                        "node_id": f"SECTOR:{sec}",
                        "node_type": "SECTOR",
                        "label": sec,
                        "ticker": None,
                        "metadata_json": {"sector": sec},
                        "effective_from": eff,
                        "valid_to": valid_to,
                    }
                )
        for row in stocks_df.itertuples(index=False):
            sym = str(getattr(row, "symbol", "")).strip().upper()
            if not sym:
                continue
            nodes.append(
                {
                    "node_id": f"TICKER:{sym}",
                    "node_type": "COMPANY",
                    "label": str(getattr(row, "name", sym) or sym),
                    "ticker": sym,
                    "metadata_json": {
                        "sector": str(getattr(row, "sector", "") or ""),
                        "industry": str(getattr(row, "industry", "") or ""),
                    },
                    "effective_from": eff,
                    "valid_to": valid_to,
                }
            )
    return nodes


def snapshots_to_kg_edges(
    macro_snapshots: Sequence[Mapping[str, Any]],
    as_of_date: date,
) -> list[dict[str, Any]]:
    """Convert reconciled Engine 2 macro snapshots into Contract 1 `kg_edges` records."""
    edges: list[dict[str, Any]] = []
    for snap in macro_snapshots:
        ent = str(snap.get("entity_node_id") or "")
        ind = str(snap.get("indicator_name") or "")
        if not ent or not ind:
            continue
        src = f"MACRO:{ind}"
        dst = ent
        eff = str(snap.get("effective_from") or as_of_date.isoformat())[:10]
        valid_to = str(snap.get("valid_to") or _MAX_VALID_TO.isoformat())[:10]
        beta = snap.get("beta_value")
        p_val = snap.get("p_value")
        fdr_q = snap.get("fdr_q")
        channel = str(snap.get("channel") or "FUNDAMENTAL")
        metric = str(snap.get("target_metric") or "GPM")
        edge_id = str(
            uuid.uuid5(uuid.NAMESPACE_DNS, f"MACRO:{src}:{dst}:{channel}:{metric}:{eff}")
        )
        edges.append(
            {
                "edge_id": edge_id,
                "src_node_id": src,
                "dst_node_id": dst,
                "relation_type": "MACRO_SENSITIVE_TO",
                "layer": "MACRO",
                "direction": "DIRECTED",
                "weight": round(abs(float(snap.get("corr_value") or beta or 0.0)), 4),
                "beta": float(beta) if beta is not None else None,
                "p_value": float(p_val) if p_val is not None else None,
                "fdr_q_value": float(fdr_q) if fdr_q is not None else None,
                "tercile_stable": bool(snap.get("tercile_stable", False)),
                "verification_tier": str(snap.get("verification_tier") or "QUANT_ONLY"),
                "evidence_json": {
                    "channel": channel,
                    "target_metric": metric,
                    "lag_periods": snap.get("lag_periods", 0),
                    "audited_note_ref": snap.get("audited_note_ref"),
                },
                "effective_from": eff,
                "valid_to": valid_to,
            }
        )
    return edges


class KGStoreProtocol(Protocol):
    """Protocol for 3-layer KG persistence & PIT subgraph queries."""

    async def get_subgraph_for_ticker(
        self,
        ticker: str,
        as_of_date: date,
    ) -> dict[str, Any]:
        ...

    async def get_true_peers(
        self,
        ticker: str,
        as_of_date: date,
        *,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        ...


@dataclass
class InMemoryKGStore:
    """In-memory PIT-enforcing KG repository for unit tests & offline evaluation."""

    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: dict[str, dict[str, Any]] = field(default_factory=dict)
    macro_snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def upsert_nodes(self, rows: Sequence[Mapping[str, Any]]) -> int:
        count = 0
        for row in rows:
            nid = str(row["node_id"])
            rec = dict(row)
            rec.setdefault("effective_from", date.today().isoformat())
            rec.setdefault("valid_to", _MAX_VALID_TO.isoformat())
            rec.setdefault("metadata_json", {})
            self.nodes[nid] = rec
            count += 1
        return count

    async def upsert_edges(self, rows: Sequence[Mapping[str, Any]]) -> int:
        count = 0
        for row in rows:
            src = str(row.get("src_node_id") or row.get("source_node_id") or "")
            dst = str(row.get("dst_node_id") or row.get("target_node_id") or "")
            rel = str(row.get("relation_type") or "")
            eid = str(row.get("edge_id") or uuid.uuid5(uuid.NAMESPACE_DNS, f"{src}:{dst}:{rel}"))
            rec = dict(row)
            rec["edge_id"] = eid
            rec["src_node_id"] = src
            rec["dst_node_id"] = dst
            rec.setdefault("layer", "VALUE_CHAIN")
            rec.setdefault("verification_tier", "DOC_ONLY")
            rec.setdefault("evidence_json", {})
            rec.setdefault("effective_from", date.today().isoformat())
            rec.setdefault("valid_to", _MAX_VALID_TO.isoformat())
            self.edges[eid] = rec
            count += 1
        return count

    async def upsert_macro_snapshots(self, rows: Sequence[Mapping[str, Any]]) -> int:
        count = 0
        for row in rows:
            target_id = str(row.get("target_id") or row.get("entity_node_id") or "")
            target_level = str(row.get("target_level") or row.get("entity_level") or "COMPANY")
            macro_factor = str(row.get("macro_factor") or row.get("indicator_name") or "")
            eff = str(row.get("effective_from") or row.get("as_of_date") or date.today().isoformat())[:10]
            sid = str(
                row.get("snapshot_id")
                or uuid.uuid5(
                    uuid.NAMESPACE_DNS,
                    f"{target_id}:{macro_factor}:{row.get('channel', '')}:{row.get('target_metric', '')}:{eff}",
                )
            )
            rec = {
                "snapshot_id": sid,
                "target_id": target_id,
                "target_level": target_level,
                "macro_factor": macro_factor,
                "as_of_date": str(row.get("as_of_date") or eff)[:10],
                "best_lag_q": int(row.get("best_lag_q", row.get("lag_periods", 0)) or 0),
                "beta": float(row.get("beta", row.get("beta_value", 0.0)) or 0.0),
                "hac_t_stat": row.get("hac_t_stat", row.get("t_stat_hac")),
                "p_value": row.get("p_value"),
                "fdr_q_value": row.get("fdr_q_value", row.get("fdr_q")),
                "tercile_stable": bool(row.get("tercile_stable", False)),
                "doc_stance": row.get("doc_stance"),
                "verification_tier": str(row.get("verification_tier") or "QUANT_ONLY"),
                "effective_from": eff,
                "valid_to": str(row.get("valid_to") or _MAX_VALID_TO.isoformat())[:10],
                "sample_n": row.get("sample_n", row.get("n_obs")),
                "metadata_json": dict(row.get("metadata_json") or {}),
            }
            if row.get("audited_note_ref"):
                rec["metadata_json"].setdefault("audited_note_ref", row["audited_note_ref"])
            if row.get("channel"):
                rec["metadata_json"].setdefault("channel", row["channel"])
            if row.get("target_metric"):
                rec["metadata_json"].setdefault("target_metric", row["target_metric"])
            self.macro_snapshots[sid] = rec
            count += 1
        return count

    @staticmethod
    def _is_pit_valid(rec: Mapping[str, Any], as_of_date: date) -> bool:
        if not rec.get("effective_from"):
            return False
        eff = _coerce_date(rec.get("effective_from"))
        vto = _coerce_date(rec.get("valid_to"), default=_MAX_VALID_TO)
        return eff <= as_of_date < vto

    async def get_true_peers(
        self,
        ticker: str,
        as_of_date: date,
        *,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        sym = ticker.strip().upper()
        node_id = f"TICKER:{sym}"
        peers: list[dict[str, Any]] = []
        for edge in self.edges.values():
            if not self._is_pit_valid(edge, as_of_date):
                continue
            if edge.get("relation_type") != "TRUE_PEER_OF":
                continue
            src = edge["src_node_id"]
            dst = edge["dst_node_id"]
            if src == node_id:
                other = dst
            elif dst == node_id:
                other = src
            else:
                continue
            peer_ticker = other.split(":", 1)[1] if ":" in other else other
            if peer_ticker == sym:
                continue
            other_node = self.nodes.get(other)
            if other_node is not None and not self._is_pit_valid(other_node, as_of_date):
                continue
            peers.append(
                {
                    "ticker": peer_ticker,
                    "node_id": other,
                    "label": other_node["label"] if other_node else peer_ticker,
                    "weight": float(edge.get("weight") or 0.0),
                    "effective_from": edge["effective_from"],
                    "evidence_json": edge.get("evidence_json", {}),
                }
            )
        peers.sort(key=lambda x: (-x["weight"], x["ticker"]))
        return peers[:limit]

    async def get_subgraph_for_ticker(
        self,
        ticker: str,
        as_of_date: date,
    ) -> dict[str, Any]:
        sym = ticker.strip().upper()
        node_id = f"TICKER:{sym}"

        valid_edges: list[dict[str, Any]] = []
        touched_node_ids: set[str] = {node_id}
        for edge in self.edges.values():
            if not self._is_pit_valid(edge, as_of_date):
                continue
            if edge["src_node_id"] == node_id or edge["dst_node_id"] == node_id:
                valid_edges.append(dict(edge))
                touched_node_ids.add(edge["src_node_id"])
                touched_node_ids.add(edge["dst_node_id"])

        # Include sector-level edges for the company's sector if known
        company_node = self.nodes.get(node_id)
        sector_name = ""
        if company_node and self._is_pit_valid(company_node, as_of_date):
            sector_name = str(company_node.get("metadata_json", {}).get("sector") or "")
        sector_node_id = f"SECTOR:{sector_name}" if sector_name else ""
        if sector_node_id:
            for edge in self.edges.values():
                if not self._is_pit_valid(edge, as_of_date):
                    continue
                if edge["src_node_id"] == sector_node_id or edge["dst_node_id"] == sector_node_id:
                    if edge["edge_id"] not in {e["edge_id"] for e in valid_edges}:
                        valid_edges.append(dict(edge))
                        touched_node_ids.add(edge["src_node_id"])
                        touched_node_ids.add(edge["dst_node_id"])

        valid_nodes = [
            dict(self.nodes[nid])
            for nid in sorted(touched_node_ids)
            if nid in self.nodes and self._is_pit_valid(self.nodes[nid], as_of_date)
        ]

        valid_snaps = [
            dict(s)
            for s in self.macro_snapshots.values()
            if self._is_pit_valid(s, as_of_date)
            and _coerce_date(s.get("as_of_date"), default=as_of_date) <= as_of_date
            and s["target_id"] in {node_id, sector_node_id}
        ]
        tier_rank = {"CONFIRMED_BOTH": 0, "CONFLICT_FLAG": 1, "QUANT_DOC_NEUTRAL": 2, "QUANT_ONLY": 3, "DOC_ONLY": 4}
        valid_snaps.sort(key=lambda x: (tier_rank.get(x["verification_tier"], 9), x.get("p_value") or 1.0))

        true_peers = await self.get_true_peers(sym, as_of_date, limit=5)
        return {
            "ticker": sym,
            "as_of_date": as_of_date.isoformat(),
            "nodes": valid_nodes,
            "edges": valid_edges,
            "macro_snapshots": valid_snaps,
            "true_peers": true_peers,
        }


class SQLAlchemyKGStore:
    """Async PostgreSQL/SQLite repository for `kg_nodes`, `kg_edges`, `macro_sensitivity_snapshots`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _is_postgres(self) -> bool:
        bind = self._session.get_bind()
        return bind is not None and bind.dialect.name == "postgresql"

    async def upsert_nodes(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        if not self._is_postgres():
            from sag_api.db.models.stock_research import KgNode

            for row in rows:
                await self._session.merge(
                    KgNode(
                        node_id=str(row["node_id"]),
                        node_type=str(row["node_type"]),
                        label=str(row["label"]),
                        ticker=row.get("ticker"),
                        metadata_json=dict(row.get("metadata_json") or {}),
                        effective_from=_coerce_date(row.get("effective_from"), default=date.today()),
                        valid_to=_coerce_date(row.get("valid_to"), default=_MAX_VALID_TO),
                    )
                )
            await self._session.flush()
            return len(rows)

        stmt = text(
            """
            INSERT INTO kg_nodes (node_id, node_type, label, ticker, metadata_json, effective_from, valid_to)
            VALUES (:node_id, :node_type, :label, :ticker, CAST(:metadata_json AS jsonb), :effective_from, :valid_to)
            ON CONFLICT (node_id) DO UPDATE SET
                node_type = EXCLUDED.node_type,
                label = EXCLUDED.label,
                ticker = EXCLUDED.ticker,
                metadata_json = EXCLUDED.metadata_json,
                effective_from = EXCLUDED.effective_from,
                valid_to = EXCLUDED.valid_to
            """
        )
        for row in rows:
            await self._session.execute(
                stmt,
                {
                    "node_id": str(row["node_id"]),
                    "node_type": str(row["node_type"]),
                    "label": str(row["label"]),
                    "ticker": row.get("ticker"),
                    "metadata_json": json.dumps(row.get("metadata_json") or {}, ensure_ascii=False),
                    "effective_from": _coerce_date(row.get("effective_from"), default=date.today()),
                    "valid_to": _coerce_date(row.get("valid_to"), default=_MAX_VALID_TO),
                },
            )
        return len(rows)

    async def upsert_edges(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        if not self._is_postgres():
            from sag_api.db.models.stock_research import KgEdge

            for row in rows:
                src = str(row.get("src_node_id") or row.get("source_node_id"))
                dst = str(row.get("dst_node_id") or row.get("target_node_id"))
                rel = str(row["relation_type"])
                eid = str(row.get("edge_id") or uuid.uuid5(uuid.NAMESPACE_DNS, f"{src}:{dst}:{rel}"))
                await self._session.merge(
                    KgEdge(
                        edge_id=eid,
                        src_node_id=src,
                        dst_node_id=dst,
                        relation_type=rel,
                        layer=str(row.get("layer") or "VALUE_CHAIN"),
                        direction=row.get("direction"),
                        weight=row.get("weight"),
                        beta=row.get("beta"),
                        p_value=row.get("p_value"),
                        fdr_q_value=row.get("fdr_q_value"),
                        tercile_stable=row.get("tercile_stable"),
                        verification_tier=str(row.get("verification_tier") or "DOC_ONLY"),
                        evidence_json=dict(row.get("evidence_json") or {}),
                        effective_from=_coerce_date(row.get("effective_from"), default=date.today()),
                        valid_to=_coerce_date(row.get("valid_to"), default=_MAX_VALID_TO),
                    )
                )
            await self._session.flush()
            return len(rows)

        stmt = text(
            """
            INSERT INTO kg_edges (
                edge_id, src_node_id, dst_node_id, relation_type, layer, direction,
                weight, beta, p_value, fdr_q_value, tercile_stable, verification_tier,
                evidence_json, effective_from, valid_to
            )
            VALUES (
                :edge_id, :src_node_id, :dst_node_id, :relation_type, :layer, :direction,
                :weight, :beta, :p_value, :fdr_q_value, :tercile_stable, :verification_tier,
                CAST(:evidence_json AS jsonb), :effective_from, :valid_to
            )
            ON CONFLICT (edge_id) DO UPDATE SET
                weight = EXCLUDED.weight,
                beta = EXCLUDED.beta,
                p_value = EXCLUDED.p_value,
                fdr_q_value = EXCLUDED.fdr_q_value,
                tercile_stable = EXCLUDED.tercile_stable,
                verification_tier = EXCLUDED.verification_tier,
                evidence_json = EXCLUDED.evidence_json,
                effective_from = EXCLUDED.effective_from,
                valid_to = EXCLUDED.valid_to
            """
        )
        for row in rows:
            src = str(row.get("src_node_id") or row.get("source_node_id"))
            dst = str(row.get("dst_node_id") or row.get("target_node_id"))
            rel = str(row["relation_type"])
            eid = str(row.get("edge_id") or uuid.uuid5(uuid.NAMESPACE_DNS, f"{src}:{dst}:{rel}"))
            await self._session.execute(
                stmt,
                {
                    "edge_id": eid,
                    "src_node_id": src,
                    "dst_node_id": dst,
                    "relation_type": rel,
                    "layer": str(row.get("layer") or "VALUE_CHAIN"),
                    "direction": row.get("direction"),
                    "weight": row.get("weight"),
                    "beta": row.get("beta"),
                    "p_value": row.get("p_value"),
                    "fdr_q_value": row.get("fdr_q_value"),
                    "tercile_stable": row.get("tercile_stable"),
                    "verification_tier": str(row.get("verification_tier") or "DOC_ONLY"),
                    "evidence_json": json.dumps(row.get("evidence_json") or {}, ensure_ascii=False),
                    "effective_from": _coerce_date(row.get("effective_from"), default=date.today()),
                    "valid_to": _coerce_date(row.get("valid_to"), default=_MAX_VALID_TO),
                },
            )
        return len(rows)

    async def upsert_macro_snapshots(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        if not self._is_postgres():
            from sag_api.db.models.stock_research import MacroSensitivitySnapshot

            for row in rows:
                target_id = str(row.get("target_id") or row.get("entity_node_id"))
                macro_factor = str(row.get("macro_factor") or row.get("indicator_name"))
                eff_d = _coerce_date(row.get("effective_from") or row.get("as_of_date"), default=date.today())
                as_of_d = _coerce_date(row.get("as_of_date"), default=eff_d)
                sid = str(
                    row.get("snapshot_id")
                    or uuid.uuid5(uuid.NAMESPACE_DNS, f"{target_id}:{macro_factor}:{eff_d}")
                )
                meta = dict(row.get("metadata_json") or {})
                if row.get("audited_note_ref"):
                    meta.setdefault("audited_note_ref", row["audited_note_ref"])
                await self._session.merge(
                    MacroSensitivitySnapshot(
                        snapshot_id=sid,
                        target_id=target_id,
                        target_level=str(row.get("target_level") or row.get("entity_level") or "COMPANY"),
                        macro_factor=macro_factor,
                        as_of_date=as_of_d,
                        best_lag_q=int(row.get("best_lag_q", row.get("lag_periods", 0)) or 0),
                        beta=float(row.get("beta", row.get("beta_value", 0.0)) or 0.0),
                        hac_t_stat=row.get("hac_t_stat", row.get("t_stat_hac")),
                        p_value=row.get("p_value"),
                        fdr_q_value=row.get("fdr_q_value", row.get("fdr_q")),
                        tercile_stable=bool(row.get("tercile_stable", False)),
                        doc_stance=row.get("doc_stance"),
                        verification_tier=str(row.get("verification_tier") or "QUANT_ONLY"),
                        effective_from=eff_d,
                        valid_to=_coerce_date(row.get("valid_to"), default=_MAX_VALID_TO),
                        sample_n=row.get("sample_n", row.get("n_obs")),
                        metadata_json=meta,
                    )
                )
            await self._session.flush()
            return len(rows)

        stmt = text(
            """
            INSERT INTO macro_sensitivity_snapshots (
                snapshot_id, target_id, target_level, macro_factor, as_of_date,
                best_lag_q, beta, hac_t_stat, p_value, fdr_q_value, tercile_stable,
                doc_stance, verification_tier, effective_from, valid_to, sample_n, metadata_json
            )
            VALUES (
                :snapshot_id, :target_id, :target_level, :macro_factor, :as_of_date,
                :best_lag_q, :beta, :hac_t_stat, :p_value, :fdr_q_value, :tercile_stable,
                :doc_stance, :verification_tier, :effective_from, :valid_to, :sample_n,
                CAST(:metadata_json AS jsonb)
            )
            ON CONFLICT (snapshot_id) DO UPDATE SET
                beta = EXCLUDED.beta,
                hac_t_stat = EXCLUDED.hac_t_stat,
                p_value = EXCLUDED.p_value,
                fdr_q_value = EXCLUDED.fdr_q_value,
                tercile_stable = EXCLUDED.tercile_stable,
                doc_stance = EXCLUDED.doc_stance,
                verification_tier = EXCLUDED.verification_tier,
                effective_from = EXCLUDED.effective_from,
                valid_to = EXCLUDED.valid_to,
                metadata_json = EXCLUDED.metadata_json
            """
        )
        for row in rows:
            target_id = str(row.get("target_id") or row.get("entity_node_id"))
            macro_factor = str(row.get("macro_factor") or row.get("indicator_name"))
            eff_d = _coerce_date(row.get("effective_from") or row.get("as_of_date"), default=date.today())
            as_of_d = _coerce_date(row.get("as_of_date"), default=eff_d)
            sid = str(
                row.get("snapshot_id")
                or uuid.uuid5(uuid.NAMESPACE_DNS, f"{target_id}:{macro_factor}:{eff_d}")
            )
            meta = dict(row.get("metadata_json") or {})
            if row.get("audited_note_ref"):
                meta.setdefault("audited_note_ref", row["audited_note_ref"])
            await self._session.execute(
                stmt,
                {
                    "snapshot_id": sid,
                    "target_id": target_id,
                    "target_level": str(row.get("target_level") or row.get("entity_level") or "COMPANY"),
                    "macro_factor": macro_factor,
                    "as_of_date": as_of_d,
                    "best_lag_q": int(row.get("best_lag_q", row.get("lag_periods", 0)) or 0),
                    "beta": float(row.get("beta", row.get("beta_value", 0.0)) or 0.0),
                    "hac_t_stat": row.get("hac_t_stat", row.get("t_stat_hac")),
                    "p_value": row.get("p_value"),
                    "fdr_q_value": row.get("fdr_q_value", row.get("fdr_q")),
                    "tercile_stable": bool(row.get("tercile_stable", False)),
                    "doc_stance": row.get("doc_stance"),
                    "verification_tier": str(row.get("verification_tier") or "QUANT_ONLY"),
                    "effective_from": eff_d,
                    "valid_to": _coerce_date(row.get("valid_to"), default=_MAX_VALID_TO),
                    "sample_n": row.get("sample_n", row.get("n_obs")),
                    "metadata_json": json.dumps(meta, ensure_ascii=False),
                },
            )
        return len(rows)

    async def get_true_peers(
        self,
        ticker: str,
        as_of_date: date,
        *,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        from sag_api.db.models.stock_research import KgEdge, KgNode

        sym = ticker.strip().upper()
        node_id = f"TICKER:{sym}"
        edge_stmt = (
            select(KgEdge)
            .where(
                KgEdge.relation_type == "TRUE_PEER_OF",
                or_(KgEdge.src_node_id == node_id, KgEdge.dst_node_id == node_id),
                KgEdge.effective_from <= as_of_date,
                KgEdge.valid_to > as_of_date,
            )
            .order_by(KgEdge.weight.desc())
        )
        edge_rows = list((await self._session.scalars(edge_stmt)).all())
        other_ids = sorted(
            {
                e.dst_node_id if e.src_node_id == node_id else e.src_node_id
                for e in edge_rows
            }
        )
        nodes_by_id: dict[str, KgNode] = {}
        if other_ids:
            n_stmt = select(KgNode).where(
                KgNode.node_id.in_(other_ids),
                KgNode.effective_from <= as_of_date,
                KgNode.valid_to > as_of_date,
            )
            for n in (await self._session.scalars(n_stmt)).all():
                nodes_by_id[n.node_id] = n

        peers: list[dict[str, Any]] = []
        for e in edge_rows:
            other = e.dst_node_id if e.src_node_id == node_id else e.src_node_id
            other_node = nodes_by_id.get(other)
            peer_ticker = (
                other_node.ticker
                if (other_node and other_node.ticker)
                else (other.split(":", 1)[1] if ":" in other else other)
            )
            if peer_ticker == sym:
                continue
            peers.append(
                {
                    "ticker": peer_ticker,
                    "node_id": other,
                    "label": other_node.label if other_node else peer_ticker,
                    "weight": float(e.weight or 0.0),
                    "effective_from": str(e.effective_from),
                    "evidence_json": e.evidence_json or {},
                }
            )
        peers.sort(key=lambda x: (-x["weight"], x["ticker"]))
        return peers[:limit]

    async def get_subgraph_for_ticker(
        self,
        ticker: str,
        as_of_date: date,
    ) -> dict[str, Any]:
        from sag_api.db.models.stock_research import KgEdge, KgNode, MacroSensitivitySnapshot

        sym = ticker.strip().upper()
        node_id = f"TICKER:{sym}"

        node_row = (
            await self._session.scalars(
                select(KgNode).where(
                    KgNode.node_id == node_id,
                    KgNode.effective_from <= as_of_date,
                    KgNode.valid_to > as_of_date,
                )
            )
        ).first()

        sector_name = ""
        if node_row and isinstance(node_row.metadata_json, Mapping):
            sector_name = str(node_row.metadata_json.get("sector") or "")
        sector_node_id = f"SECTOR:{sector_name}" if sector_name else "__NONE__"
        target_ids = [node_id, sector_node_id]

        edge_rows = list(
            (
                await self._session.scalars(
                    select(KgEdge)
                    .where(
                        or_(KgEdge.src_node_id.in_(target_ids), KgEdge.dst_node_id.in_(target_ids)),
                        KgEdge.effective_from <= as_of_date,
                        KgEdge.valid_to > as_of_date,
                    )
                    .order_by(KgEdge.weight.desc())
                    .limit(100)
                )
            ).all()
        )
        edges = [
            {
                "edge_id": e.edge_id,
                "src_node_id": e.src_node_id,
                "dst_node_id": e.dst_node_id,
                "relation_type": e.relation_type,
                "layer": e.layer,
                "direction": e.direction,
                "weight": e.weight,
                "beta": e.beta,
                "p_value": e.p_value,
                "fdr_q_value": e.fdr_q_value,
                "tercile_stable": e.tercile_stable,
                "verification_tier": e.verification_tier,
                "evidence_json": e.evidence_json or {},
                "effective_from": str(e.effective_from),
                "valid_to": str(e.valid_to),
            }
            for e in edge_rows
        ]

        touched_ids = {node_id}
        for e in edges:
            touched_ids.add(e["src_node_id"])
            touched_ids.add(e["dst_node_id"])

        nodes: list[dict[str, Any]] = []
        if touched_ids:
            n_rows = list(
                (
                    await self._session.scalars(
                        select(KgNode).where(
                            KgNode.node_id.in_(sorted(touched_ids)),
                            KgNode.effective_from <= as_of_date,
                            KgNode.valid_to > as_of_date,
                        )
                    )
                ).all()
            )
            nodes = [
                {
                    "node_id": n.node_id,
                    "node_type": n.node_type,
                    "label": n.label,
                    "ticker": n.ticker,
                    "metadata_json": n.metadata_json or {},
                    "effective_from": str(n.effective_from),
                    "valid_to": str(n.valid_to),
                }
                for n in n_rows
            ]

        snap_rows = list(
            (
                await self._session.scalars(
                    select(MacroSensitivitySnapshot)
                    .where(
                        MacroSensitivitySnapshot.target_id.in_(target_ids),
                        MacroSensitivitySnapshot.effective_from <= as_of_date,
                        MacroSensitivitySnapshot.valid_to > as_of_date,
                        MacroSensitivitySnapshot.as_of_date <= as_of_date,
                    )
                    .order_by(MacroSensitivitySnapshot.p_value.asc())
                    .limit(50)
                )
            ).all()
        )
        snaps = [
            {
                "snapshot_id": s.snapshot_id,
                "target_id": s.target_id,
                "target_level": s.target_level,
                "macro_factor": s.macro_factor,
                "as_of_date": str(s.as_of_date),
                "best_lag_q": s.best_lag_q,
                "beta": s.beta,
                "hac_t_stat": s.hac_t_stat,
                "p_value": s.p_value,
                "fdr_q_value": s.fdr_q_value,
                "tercile_stable": s.tercile_stable,
                "doc_stance": s.doc_stance,
                "verification_tier": s.verification_tier,
                "effective_from": str(s.effective_from),
                "valid_to": str(s.valid_to),
                "sample_n": s.sample_n,
                "metadata_json": s.metadata_json or {},
            }
            for s in snap_rows
        ]

        true_peers = await self.get_true_peers(sym, as_of_date, limit=5)
        return {
            "ticker": sym,
            "as_of_date": as_of_date.isoformat(),
            "nodes": nodes,
            "edges": edges,
            "macro_snapshots": snaps,
            "true_peers": true_peers,
        }


def load_core_db_snapshot(core_database_url: str, as_of_date: date) -> dict[str, Any]:
    """Read-only Point-in-Time loader from AIInvest Core PostgreSQL (`<= as_of_date`).

    Never logs connection strings or credentials.
    """
    import psycopg2

    conn = psycopg2.connect(core_database_url)
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.symbol, COALESCE(NULLIF(im.name, ''), s.name) AS full_name,
                       s.sector, s.industry
                FROM stocks s
                LEFT JOIN instrument_master im ON im.symbol = s.symbol
                ORDER BY s.symbol
                """
            )
            stocks_df = pd.DataFrame(cur.fetchall(), columns=["symbol", "name", "sector", "industry"])

            cur.execute(
                """
                SELECT indicator_date, indicator_name, value, unit
                FROM macro_indicators
                WHERE indicator_date <= %s
                  AND value IS NOT NULL
                  AND indicator_name NOT LIKE 'vnindex%%'
                ORDER BY indicator_name, indicator_date
                """,
                (as_of_date,),
            )
            macro_df = pd.DataFrame(
                cur.fetchall(), columns=["indicator_date", "indicator_name", "value", "unit"]
            )
            macro_df["indicator_date"] = pd.to_datetime(macro_df["indicator_date"])

            cur.execute(
                """
                SELECT symbol, ratio_date,
                       CASE
                         WHEN published_date IS NOT NULL
                              AND published_date >= ratio_date
                              AND published_date <= (ratio_date + INTERVAL '180 days')::date
                         THEN published_date
                         ELSE (ratio_date + INTERVAL '45 days')::date
                       END AS effective_published_date,
                       gross_margin, net_margin, yoy_revenue_growth, debt_equity, roe, roa, frequency
                FROM financial_ratios
                WHERE frequency IN ('quarterly', 'yearly')
                  AND ratio_date <= %s
                  AND (
                    CASE
                      WHEN published_date IS NOT NULL
                           AND published_date >= ratio_date
                           AND published_date <= (ratio_date + INTERVAL '180 days')::date
                      THEN published_date
                      ELSE (ratio_date + INTERVAL '45 days')::date
                    END
                  ) <= %s
                  AND ratio_date >= %s
                ORDER BY symbol, ratio_date
                """,
                (as_of_date, as_of_date, as_of_date - timedelta(days=365 * 7)),
            )
            ratios_df = pd.DataFrame(
                cur.fetchall(),
                columns=[
                    "symbol",
                    "ratio_date",
                    "published_date",
                    "gross_margin",
                    "net_margin",
                    "yoy_revenue_growth",
                    "debt_equity",
                    "roe",
                    "roa",
                    "frequency",
                ],
            )
            ratios_df["ratio_date"] = pd.to_datetime(ratios_df["ratio_date"])

            mkt_start = as_of_date - timedelta(days=365 * 3 + 30)
            cur.execute(
                """
                SELECT ticker, date, close_adj
                FROM market_data_daily
                WHERE date >= %s AND date <= %s AND close_adj IS NOT NULL AND close_adj > 0
                ORDER BY date
                """,
                (mkt_start, as_of_date),
            )
            mkt_df = pd.DataFrame(cur.fetchall(), columns=["ticker", "date", "close_adj"])
            mkt_df["date"] = pd.to_datetime(mkt_df["date"])

            cur.execute(
                """
                SELECT symbol, period_end, statement_type, data, published_date
                FROM financial_statements
                WHERE frequency = 'yearly'
                  AND statement_type IN ('BS', 'IS', 'CF')
                  AND period_end >= %s AND period_end <= %s
                  AND (
                    CASE
                      WHEN published_date IS NOT NULL
                           AND published_date >= period_end
                           AND published_date <= (period_end + INTERVAL '180 days')::date
                      THEN published_date
                      ELSE (period_end + INTERVAL '90 days')::date
                    END
                  ) <= %s
                ORDER BY symbol, period_end
                """,
                (as_of_date - timedelta(days=365 * 5 + 30), as_of_date, as_of_date),
            )
            stmt_rows = [
                {
                    "symbol": row[0],
                    "period_end": row[1],
                    "statement_type": row[2],
                    "data": row[3],
                    "published_date": row[4],
                }
                for row in cur.fetchall()
            ]
    finally:
        conn.close()

    return {
        "as_of_date": as_of_date,
        "stocks": stocks_df,
        "macro": macro_df,
        "ratios": ratios_df,
        "market": mkt_df,
        "statements": stmt_rows,
    }
