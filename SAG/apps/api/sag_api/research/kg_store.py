"""3-Layer Knowledge Graph Persistence & Strict Point-in-Time Query Store.

Aligns with PROJECT.md Contract 1 (`kg_nodes`, `kg_edges`, `macro_sensitivity_snapshots`)
with both:
- `SQLAlchemyKGStore`: async PostgreSQL persistence & PIT query (`effective_from <= as_of_date < valid_to`)
- `InMemoryKGStore`: deterministic in-memory store for offline unit tests and fast replay
- `load_core_db_snapshot`: strictly read-only Core DB loader (`<= as_of_date`, zero credential logging)
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Protocol

import pandas as pd
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

_MAX_VALID_TO = date(9999, 12, 31)
_VERSION_SUFFIX = "@v:"
_VERSION_DATE_LEN = 10


def _canonical_node_id(node_id: str) -> str:
    return node_id.split(_VERSION_SUFFIX, 1)[0]


def _canonical_id_from_row(node_id: str, meta: Mapping[str, Any] | None) -> str:
    if isinstance(meta, Mapping):
        stored = meta.get("_canonical_node_id")
        if isinstance(stored, str) and stored.strip():
            return stored.strip()
    return _canonical_node_id(node_id)


def _versioned_node_prefix(node_id: str) -> str:
    canonical = _canonical_node_id(node_id)
    max_prefix = max(1, 128 - len(_VERSION_SUFFIX) - _VERSION_DATE_LEN)
    return f"{canonical[:max_prefix]}{_VERSION_SUFFIX}"


def _versioned_node_id(node_id: str, eff: date) -> str:
    return f"{_versioned_node_prefix(node_id)}{eff.isoformat()}"


def _escape_like(val: str) -> str:
    return val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _clean_node_metadata(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in dict(meta or {}).items() if k != "_canonical_node_id"}


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
            nid = _canonical_node_id(str(row["node_id"]))
            new_eff = _coerce_date(row.get("effective_from"), default=date.today())
            new_vto = _coerce_date(row.get("valid_to"), default=_MAX_VALID_TO)
            new_meta = _clean_node_metadata(row.get("metadata_json"))
            new_rec = {
                "node_id": nid,
                "node_type": str(row.get("node_type") or "COMPANY"),
                "label": str(row.get("label") or nid),
                "ticker": row.get("ticker"),
                "metadata_json": new_meta,
                "effective_from": new_eff.isoformat(),
                "valid_to": new_vto.isoformat(),
            }
            existing = self.nodes.get(nid)
            if existing is None:
                self.nodes[nid] = new_rec
            else:
                ex_eff = _coerce_date(existing.get("effective_from"))
                changed = (
                    existing.get("label") != new_rec["label"]
                    or existing.get("node_type") != new_rec["node_type"]
                    or existing.get("ticker") != new_rec["ticker"]
                    or _clean_node_metadata(existing.get("metadata_json")) != new_meta
                )
                if new_eff > ex_eff:
                    if changed:
                        hist_id = _versioned_node_id(nid, ex_eff)
                        hist = dict(existing)
                        hist["node_id"] = hist_id
                        hist["valid_to"] = new_eff.isoformat()
                        hist["metadata_json"] = {
                            **_clean_node_metadata(existing.get("metadata_json")),
                            "_canonical_node_id": nid,
                        }
                        self.nodes[hist_id] = hist
                        self.nodes[nid] = new_rec
                    else:
                        # Unchanged metadata: keep earliest effective_from so historical PIT never breaks
                        ex_vto = _coerce_date(existing.get("valid_to"), default=_MAX_VALID_TO)
                        existing["valid_to"] = max(ex_vto, new_vto).isoformat()
                elif new_eff == ex_eff:
                    self.nodes[nid] = new_rec
                else:
                    # Out-of-order historical backfill: maintain disjoint bi-temporal chain
                    # across ALL sibling revisions without mutating current canonical row.
                    hist_id = _versioned_node_id(nid, new_eff)
                    bounded_vto = min(new_vto, ex_eff)
                    for sib_id, sib in list(self.nodes.items()):
                        if sib_id == hist_id:
                            continue
                        if _canonical_id_from_row(sib_id, sib.get("metadata_json")) != nid:
                            continue
                        sib_eff = _coerce_date(sib.get("effective_from"))
                        sib_vto = _coerce_date(sib.get("valid_to"), default=_MAX_VALID_TO)
                        if sib_eff < new_eff < sib_vto:
                            sib["valid_to"] = new_eff.isoformat()
                        elif sib_eff > new_eff and sib_eff < bounded_vto:
                            bounded_vto = sib_eff
                    hist = dict(new_rec)
                    hist["node_id"] = hist_id
                    hist["valid_to"] = bounded_vto.isoformat()
                    hist["metadata_json"] = {**new_meta, "_canonical_node_id": nid}
                    self.nodes[hist_id] = hist
            count += 1
        return count

    async def upsert_edges(self, rows: Sequence[Mapping[str, Any]]) -> int:
        count = 0
        for row in rows:
            src = str(row.get("src_node_id") or row.get("source_node_id") or "")
            dst = str(row.get("dst_node_id") or row.get("target_node_id") or "")
            rel = str(row.get("relation_type") or "")
            layer = str(row.get("layer") or "VALUE_CHAIN")
            new_eff = _coerce_date(row.get("effective_from"), default=date.today())
            new_vto = _coerce_date(row.get("valid_to"), default=_MAX_VALID_TO)

            same_key_edges = [
                e
                for e in self.edges.values()
                if e.get("src_node_id") == src
                and e.get("dst_node_id") == dst
                and e.get("relation_type") == rel
                and e.get("layer") == layer
            ]
            exact_same_eff = next(
                (e for e in same_key_edges if _coerce_date(e.get("effective_from")) == new_eff),
                None,
            )
            if exact_same_eff is not None:
                eid = str(exact_same_eff["edge_id"])
            else:
                for ex in same_key_edges:
                    ex_eff = _coerce_date(ex.get("effective_from"))
                    ex_vto = _coerce_date(ex.get("valid_to"), default=_MAX_VALID_TO)
                    if ex_eff < new_eff < ex_vto:
                        ex["valid_to"] = new_eff.isoformat()
                    elif ex_eff > new_eff and ex_eff < new_vto:
                        new_vto = ex_eff
                raw_eid = str(row.get("edge_id") or "").strip()
                if raw_eid and (
                    raw_eid not in self.edges
                    or _coerce_date(self.edges[raw_eid].get("effective_from")) == new_eff
                ):
                    eid = raw_eid
                else:
                    eid = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_DNS,
                            f"{src}:{dst}:{rel}:{layer}:{new_eff.isoformat()}",
                        )
                    )
            rec = dict(row)
            rec["edge_id"] = eid
            rec["src_node_id"] = src
            rec["dst_node_id"] = dst
            rec["layer"] = layer
            rec.setdefault("verification_tier", "DOC_ONLY")
            rec.setdefault("evidence_json", {})
            rec["effective_from"] = new_eff.isoformat()
            rec["valid_to"] = new_vto.isoformat()
            self.edges[eid] = rec
            count += 1
        return count

    async def upsert_macro_snapshots(self, rows: Sequence[Mapping[str, Any]]) -> int:
        count = 0
        for row in rows:
            target_id = str(row.get("target_id") or row.get("entity_node_id") or "")
            target_level = str(row.get("target_level") or row.get("entity_level") or "COMPANY")
            macro_factor = str(row.get("macro_factor") or row.get("indicator_name") or "")
            new_eff = _coerce_date(row.get("effective_from") or row.get("as_of_date"), default=date.today())
            new_vto = _coerce_date(row.get("valid_to"), default=_MAX_VALID_TO)
            meta = dict(row.get("metadata_json") or {})
            if row.get("audited_note_ref"):
                meta.setdefault("audited_note_ref", row["audited_note_ref"])
            if row.get("channel"):
                meta.setdefault("channel", row["channel"])
            if row.get("target_metric"):
                meta.setdefault("target_metric", row["target_metric"])
            ch = str(meta.get("channel") or "")
            metric = str(meta.get("target_metric") or "")

            same_key_snaps = [
                s
                for s in self.macro_snapshots.values()
                if s.get("target_id") == target_id
                and s.get("target_level") == target_level
                and s.get("macro_factor") == macro_factor
                and str((s.get("metadata_json") or {}).get("channel") or "") == ch
                and str((s.get("metadata_json") or {}).get("target_metric") or "") == metric
            ]
            exact_same_eff = next(
                (s for s in same_key_snaps if _coerce_date(s.get("effective_from")) == new_eff),
                None,
            )
            if exact_same_eff is not None:
                sid = str(exact_same_eff["snapshot_id"])
            else:
                for ex in same_key_snaps:
                    ex_eff = _coerce_date(ex.get("effective_from"))
                    ex_vto = _coerce_date(ex.get("valid_to"), default=_MAX_VALID_TO)
                    if ex_eff < new_eff < ex_vto:
                        ex["valid_to"] = new_eff.isoformat()
                    elif ex_eff > new_eff and ex_eff < new_vto:
                        new_vto = ex_eff
                raw_sid = str(row.get("snapshot_id") or "").strip()
                if raw_sid and (
                    raw_sid not in self.macro_snapshots
                    or _coerce_date(self.macro_snapshots[raw_sid].get("effective_from")) == new_eff
                ):
                    sid = raw_sid
                else:
                    sid = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_DNS,
                            f"{target_id}:{target_level}:{macro_factor}:{ch}:{metric}:{new_eff.isoformat()}",
                        )
                    )
            rec = {
                "snapshot_id": sid,
                "target_id": target_id,
                "target_level": target_level,
                "macro_factor": macro_factor,
                "as_of_date": str(row.get("as_of_date") or new_eff.isoformat())[:10],
                "best_lag_q": int(row.get("best_lag_q", row.get("lag_periods", 0)) or 0),
                "beta": float(row.get("beta", row.get("beta_value", 0.0)) or 0.0),
                "hac_t_stat": row.get("hac_t_stat", row.get("t_stat_hac")),
                "p_value": row.get("p_value"),
                "fdr_q_value": row.get("fdr_q_value", row.get("fdr_q")),
                "tercile_stable": bool(row.get("tercile_stable", False)),
                "doc_stance": row.get("doc_stance"),
                "verification_tier": str(row.get("verification_tier") or "QUANT_ONLY"),
                "effective_from": new_eff.isoformat(),
                "valid_to": new_vto.isoformat(),
                "sample_n": row.get("sample_n", row.get("n_obs")),
                "metadata_json": meta,
            }
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

    def _resolve_node_pit(self, canonical_id: str, as_of_date: date) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        best_eff: date | None = None
        for rec in self.nodes.values():
            if _canonical_id_from_row(str(rec.get("node_id") or ""), rec.get("metadata_json")) != canonical_id:
                continue
            if not self._is_pit_valid(rec, as_of_date):
                continue
            eff = _coerce_date(rec.get("effective_from"))
            if best_eff is None or eff >= best_eff:
                best = {
                    **rec,
                    "node_id": canonical_id,
                    "metadata_json": _clean_node_metadata(rec.get("metadata_json")),
                }
                best_eff = eff
        return best

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
            has_any_node = any(
                _canonical_node_id(str(k)) == other for k in self.nodes
            )
            other_node = self._resolve_node_pit(other, as_of_date)
            if has_any_node and other_node is None:
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
        company_node = self._resolve_node_pit(node_id, as_of_date)
        sector_name = ""
        if company_node:
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
            resolved
            for nid in sorted(touched_node_ids)
            if (resolved := self._resolve_node_pit(nid, as_of_date)) is not None
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

    async def get_tickers_by_macro_factor(
        self,
        macro_factor: str,
        as_of_date: date,
        *,
        allowed_tickers: Iterable[str] | None = None,
        direction_filter: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """PIT & ACL-scoped lookup of tickers sensitive to `macro_factor` (supports DOC_ONLY `stated_sign`)."""
        needle = macro_factor.strip().lower()
        if not needle:
            return []
        allowed_set = (
            {t.strip().upper() for t in allowed_tickers if t and t.strip()}
            if allowed_tickers is not None
            else None
        )
        tier_rank = {"CONFIRMED_BOTH": 0, "CONFLICT_FLAG": 1, "QUANT_DOC_NEUTRAL": 2, "QUANT_ONLY": 3, "DOC_ONLY": 4}
        out: list[dict[str, Any]] = []
        for s in self.macro_snapshots.values():
            if not self._is_pit_valid(s, as_of_date):
                continue
            if _coerce_date(s.get("as_of_date"), default=as_of_date) > as_of_date:
                continue
            mf = str(s.get("macro_factor") or "").strip().lower()
            if needle not in mf and mf not in needle:
                continue
            tid = str(s.get("target_id") or "")
            if not tid.startswith("TICKER:"):
                continue
            sym = tid.split(":", 1)[1].strip().upper()
            if allowed_set is not None and "*" not in allowed_set and sym not in allowed_set:
                continue
            meta = s.get("metadata_json") if isinstance(s.get("metadata_json"), Mapping) else {}
            note_ref = meta.get("audited_note_ref") if isinstance(meta.get("audited_note_ref"), Mapping) else {}
            beta = float(s.get("beta") or 0.0)
            stated_sign = int(note_ref.get("stated_sign") or 0) if note_ref else 0
            eff_sign = (1 if beta > 0 else -1) if abs(beta) > 1e-12 else stated_sign
            if direction_filter is not None and direction_filter != 0 and eff_sign != direction_filter:
                continue
            out.append(
                {
                    "ticker": sym,
                    "macro_factor": str(s.get("macro_factor") or ""),
                    "beta": beta,
                    "direction_sign": eff_sign,
                    "p_value": s.get("p_value"),
                    "fdr_q_value": s.get("fdr_q_value"),
                    "verification_tier": str(s.get("verification_tier") or "QUANT_ONLY"),
                    "doc_stance": s.get("doc_stance"),
                    "effective_from": str(s.get("effective_from") or ""),
                    "metadata_json": dict(meta),
                }
            )
        out.sort(
            key=lambda x: (
                tier_rank.get(x["verification_tier"], 9),
                float(x["p_value"]) if x.get("p_value") is not None else 999.0,
                -abs(float(x.get("beta") or 0.0)),
                x["ticker"],
            )
        )
        return out[: max(1, limit)]


class SQLAlchemyKGStore:
    """Async PostgreSQL/SQLite bi-temporal repository for `kg_nodes`, `kg_edges`, `macro_sensitivity_snapshots`.

    Never overwrites historical `effective_from`/`valid_to` in place: closes prior rows'
    `valid_to = new_effective_from` and appends new PIT-versioned records so historical
    Point-in-Time replay remains 100% faithful.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_nodes(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        from sag_api.db.models.stock_research import KgNode

        for row in rows:
            nid = _canonical_node_id(str(row["node_id"]))
            new_type = str(row["node_type"])
            new_label = str(row["label"])
            new_ticker = row.get("ticker")
            new_meta = _clean_node_metadata(row.get("metadata_json"))
            new_eff = _coerce_date(row.get("effective_from"), default=date.today())
            new_vto = _coerce_date(row.get("valid_to"), default=_MAX_VALID_TO)

            existing = await self._session.get(KgNode, nid)
            if existing is None:
                self._session.add(
                    KgNode(
                        node_id=nid,
                        node_type=new_type,
                        label=new_label,
                        ticker=new_ticker,
                        metadata_json=new_meta,
                        effective_from=new_eff,
                        valid_to=new_vto,
                    )
                )
            else:
                changed = (
                    existing.node_type != new_type
                    or existing.label != new_label
                    or existing.ticker != new_ticker
                    or _clean_node_metadata(existing.metadata_json) != new_meta
                )
                if new_eff > existing.effective_from:
                    if changed:
                        hist_id = _versioned_node_id(nid, existing.effective_from)
                        hist_existing = await self._session.get(KgNode, hist_id)
                        hist_meta = {
                            **_clean_node_metadata(existing.metadata_json),
                            "_canonical_node_id": nid,
                        }
                        if hist_existing is None:
                            self._session.add(
                                KgNode(
                                    node_id=hist_id,
                                    node_type=existing.node_type,
                                    label=existing.label,
                                    ticker=existing.ticker,
                                    metadata_json=hist_meta,
                                    effective_from=existing.effective_from,
                                    valid_to=new_eff,
                                )
                            )
                        else:
                            hist_existing.valid_to = min(hist_existing.valid_to, new_eff)
                        existing.node_type = new_type
                        existing.label = new_label
                        existing.ticker = new_ticker
                        existing.metadata_json = new_meta
                        existing.effective_from = new_eff
                        existing.valid_to = new_vto
                    else:
                        # Preserve earliest effective_from so historical queries remain valid
                        existing.valid_to = max(existing.valid_to, new_vto)
                elif new_eff == existing.effective_from:
                    existing.node_type = new_type
                    existing.label = new_label
                    existing.ticker = new_ticker
                    existing.metadata_json = new_meta
                    existing.valid_to = new_vto
                else:
                    # Out-of-order historical backfill: maintain disjoint bi-temporal chain
                    # across ALL sibling revisions without mutating current canonical row.
                    hist_id = _versioned_node_id(nid, new_eff)
                    bounded_vto = min(new_vto, existing.effective_from)
                    prefix_like = f"{_escape_like(_versioned_node_prefix(nid))}%"
                    sibling_rows = list(
                        (
                            await self._session.scalars(
                                select(KgNode).where(
                                    or_(
                                        KgNode.node_id == nid,
                                        KgNode.node_id.like(prefix_like, escape="\\"),
                                    )
                                )
                            )
                        ).all()
                    )
                    for sib in sibling_rows:
                        if sib.node_id == hist_id:
                            continue
                        if _canonical_id_from_row(sib.node_id, sib.metadata_json) != nid:
                            continue
                        if sib.effective_from < new_eff < sib.valid_to:
                            sib.valid_to = new_eff
                        elif sib.effective_from > new_eff and sib.effective_from < bounded_vto:
                            bounded_vto = sib.effective_from
                    hist_meta = {**new_meta, "_canonical_node_id": nid}
                    hist_existing = await self._session.get(KgNode, hist_id)
                    if hist_existing is None:
                        self._session.add(
                            KgNode(
                                node_id=hist_id,
                                node_type=new_type,
                                label=new_label,
                                ticker=new_ticker,
                                metadata_json=hist_meta,
                                effective_from=new_eff,
                                valid_to=bounded_vto,
                            )
                        )
                    else:
                        hist_existing.node_type = new_type
                        hist_existing.label = new_label
                        hist_existing.ticker = new_ticker
                        hist_existing.metadata_json = hist_meta
                        hist_existing.valid_to = bounded_vto
            await self._session.flush()
        return len(rows)

    async def upsert_edges(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        from sag_api.db.models.stock_research import KgEdge

        for row in rows:
            src = _canonical_node_id(str(row.get("src_node_id") or row.get("source_node_id")))
            dst = _canonical_node_id(str(row.get("dst_node_id") or row.get("target_node_id")))
            rel = str(row["relation_type"])
            layer = str(row.get("layer") or "VALUE_CHAIN")
            new_eff = _coerce_date(row.get("effective_from"), default=date.today())
            new_vto = _coerce_date(row.get("valid_to"), default=_MAX_VALID_TO)

            prior_edges = list(
                (
                    await self._session.scalars(
                        select(KgEdge).where(
                            KgEdge.src_node_id == src,
                            KgEdge.dst_node_id == dst,
                            KgEdge.relation_type == rel,
                            KgEdge.layer == layer,
                        )
                    )
                ).all()
            )
            exact = next((e for e in prior_edges if e.effective_from == new_eff), None)
            if exact is not None:
                exact.direction = row.get("direction")
                exact.weight = row.get("weight")
                exact.beta = row.get("beta")
                exact.p_value = row.get("p_value")
                exact.fdr_q_value = row.get("fdr_q_value")
                exact.tercile_stable = row.get("tercile_stable")
                exact.verification_tier = str(row.get("verification_tier") or "DOC_ONLY")
                exact.evidence_json = dict(row.get("evidence_json") or {})
                exact.valid_to = new_vto
            else:
                for ex in prior_edges:
                    if ex.effective_from < new_eff < ex.valid_to:
                        ex.valid_to = new_eff
                    elif ex.effective_from > new_eff and ex.effective_from < new_vto:
                        new_vto = ex.effective_from
                raw_eid = str(row.get("edge_id") or "").strip()
                existing_by_id = await self._session.get(KgEdge, raw_eid) if raw_eid else None
                if raw_eid and existing_by_id is None:
                    eid = raw_eid
                else:
                    eid = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_DNS,
                            f"{src}:{dst}:{rel}:{layer}:{new_eff.isoformat()}",
                        )
                    )
                    if await self._session.get(KgEdge, eid) is not None:
                        eid = str(
                            uuid.uuid5(
                                uuid.NAMESPACE_DNS,
                                f"{src}:{dst}:{rel}:{layer}:{new_eff.isoformat()}:{new_vto.isoformat()}",
                            )
                        )
                self._session.add(
                    KgEdge(
                        edge_id=eid,
                        src_node_id=src,
                        dst_node_id=dst,
                        relation_type=rel,
                        layer=layer,
                        direction=row.get("direction"),
                        weight=row.get("weight"),
                        beta=row.get("beta"),
                        p_value=row.get("p_value"),
                        fdr_q_value=row.get("fdr_q_value"),
                        tercile_stable=row.get("tercile_stable"),
                        verification_tier=str(row.get("verification_tier") or "DOC_ONLY"),
                        evidence_json=dict(row.get("evidence_json") or {}),
                        effective_from=new_eff,
                        valid_to=new_vto,
                    )
                )
            await self._session.flush()
        return len(rows)

    async def upsert_macro_snapshots(self, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        from sag_api.db.models.stock_research import MacroSensitivitySnapshot

        for row in rows:
            target_id = str(row.get("target_id") or row.get("entity_node_id"))
            target_level = str(row.get("target_level") or row.get("entity_level") or "COMPANY")
            macro_factor = str(row.get("macro_factor") or row.get("indicator_name"))
            eff_d = _coerce_date(row.get("effective_from") or row.get("as_of_date"), default=date.today())
            as_of_d = _coerce_date(row.get("as_of_date"), default=eff_d)
            new_vto = _coerce_date(row.get("valid_to"), default=_MAX_VALID_TO)
            meta = dict(row.get("metadata_json") or {})
            if row.get("audited_note_ref"):
                meta.setdefault("audited_note_ref", row["audited_note_ref"])
            if row.get("channel"):
                meta.setdefault("channel", row["channel"])
            if row.get("target_metric"):
                meta.setdefault("target_metric", row["target_metric"])
            ch = str(meta.get("channel") or "")
            metric = str(meta.get("target_metric") or "")

            prior_snaps = list(
                (
                    await self._session.scalars(
                        select(MacroSensitivitySnapshot).where(
                            MacroSensitivitySnapshot.target_id == target_id,
                            MacroSensitivitySnapshot.target_level == target_level,
                            MacroSensitivitySnapshot.macro_factor == macro_factor,
                        )
                    )
                ).all()
            )
            matching_snaps = [
                s
                for s in prior_snaps
                if str((s.metadata_json or {}).get("channel") or "") == ch
                and str((s.metadata_json or {}).get("target_metric") or "") == metric
            ]
            exact = next((s for s in matching_snaps if s.effective_from == eff_d), None)
            if exact is not None:
                exact.as_of_date = as_of_d
                exact.best_lag_q = int(row.get("best_lag_q", row.get("lag_periods", 0)) or 0)
                exact.beta = float(row.get("beta", row.get("beta_value", 0.0)) or 0.0)
                exact.hac_t_stat = row.get("hac_t_stat", row.get("t_stat_hac"))
                exact.p_value = row.get("p_value")
                exact.fdr_q_value = row.get("fdr_q_value", row.get("fdr_q"))
                exact.tercile_stable = bool(row.get("tercile_stable", False))
                exact.doc_stance = row.get("doc_stance")
                exact.verification_tier = str(row.get("verification_tier") or "QUANT_ONLY")
                exact.valid_to = new_vto
                exact.sample_n = row.get("sample_n", row.get("n_obs"))
                exact.metadata_json = meta
            else:
                for ex in matching_snaps:
                    if ex.effective_from < eff_d < ex.valid_to:
                        ex.valid_to = eff_d
                    elif ex.effective_from > eff_d and ex.effective_from < new_vto:
                        new_vto = ex.effective_from
                raw_sid = str(row.get("snapshot_id") or "").strip()
                existing_by_id = (
                    await self._session.get(MacroSensitivitySnapshot, raw_sid) if raw_sid else None
                )
                if raw_sid and existing_by_id is None:
                    sid = raw_sid
                else:
                    sid = str(
                        uuid.uuid5(
                            uuid.NAMESPACE_DNS,
                            f"{target_id}:{target_level}:{macro_factor}:{ch}:{metric}:{eff_d.isoformat()}",
                        )
                    )
                self._session.add(
                    MacroSensitivitySnapshot(
                        snapshot_id=sid,
                        target_id=target_id,
                        target_level=target_level,
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
                        valid_to=new_vto,
                        sample_n=row.get("sample_n", row.get("n_obs")),
                        metadata_json=meta,
                    )
                )
            await self._session.flush()
        return len(rows)

    async def _load_pit_nodes_map(
        self,
        canonical_ids: Sequence[str],
        as_of_date: date,
    ) -> dict[str, Any]:
        from sag_api.db.models.stock_research import KgNode

        clean_ids = sorted({_canonical_node_id(cid) for cid in canonical_ids if cid})
        if not clean_ids:
            return {}
        wanted = set(clean_ids)
        id_clauses = [KgNode.node_id.in_(clean_ids)] + [
            KgNode.node_id.like(f"{_escape_like(_versioned_node_prefix(cid))}%", escape="\\")
            for cid in clean_ids
        ]
        rows = list(
            (
                await self._session.scalars(
                    select(KgNode)
                    .where(
                        or_(*id_clauses),
                        KgNode.effective_from <= as_of_date,
                        KgNode.valid_to > as_of_date,
                    )
                    .order_by(KgNode.effective_from.asc())
                )
            ).all()
        )
        out: dict[str, Any] = {}
        for n in rows:
            cid = _canonical_id_from_row(n.node_id, n.metadata_json)
            if cid in wanted:
                out[cid] = n
        return out

    async def _load_existing_canonical_node_ids(
        self,
        canonical_ids: Sequence[str],
    ) -> set[str]:
        from sag_api.db.models.stock_research import KgNode

        clean_ids = sorted({_canonical_node_id(cid) for cid in canonical_ids if cid})
        if not clean_ids:
            return set()
        wanted = set(clean_ids)
        id_clauses = [KgNode.node_id.in_(clean_ids)] + [
            KgNode.node_id.like(f"{_escape_like(_versioned_node_prefix(cid))}%", escape="\\")
            for cid in clean_ids
        ]
        rows = (
            await self._session.execute(
                select(KgNode.node_id, KgNode.metadata_json).where(or_(*id_clauses))
            )
        ).all()
        found: set[str] = set()
        for raw_nid, meta_j in rows:
            cid = _canonical_id_from_row(str(raw_nid), meta_j)
            if cid in wanted:
                found.add(cid)
        return found

    async def get_true_peers(
        self,
        ticker: str,
        as_of_date: date,
        *,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        from sag_api.db.models.stock_research import KgEdge

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
        nodes_by_id = await self._load_pit_nodes_map(other_ids, as_of_date) if other_ids else {}
        existing_other_ids = (
            await self._load_existing_canonical_node_ids(other_ids) if other_ids else set()
        )

        peers: list[dict[str, Any]] = []
        for e in edge_rows:
            other = e.dst_node_id if e.src_node_id == node_id else e.src_node_id
            other_node = nodes_by_id.get(other)
            if other in existing_other_ids and other_node is None:
                continue
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
        from sag_api.db.models.stock_research import KgEdge, MacroSensitivitySnapshot

        sym = ticker.strip().upper()
        node_id = f"TICKER:{sym}"

        node_row = (await self._load_pit_nodes_map([node_id], as_of_date)).get(node_id)

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
            n_map = await self._load_pit_nodes_map(sorted(touched_ids), as_of_date)
            nodes = [
                {
                    "node_id": cid,
                    "node_type": n.node_type,
                    "label": n.label,
                    "ticker": n.ticker,
                    "metadata_json": _clean_node_metadata(n.metadata_json),
                    "effective_from": str(n.effective_from),
                    "valid_to": str(n.valid_to),
                }
                for cid, n in sorted(n_map.items())
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

    async def get_tickers_by_macro_factor(
        self,
        macro_factor: str,
        as_of_date: date,
        *,
        allowed_tickers: Iterable[str] | None = None,
        direction_filter: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """PIT & ACL-scoped lookup of tickers sensitive to `macro_factor` (supports DOC_ONLY `stated_sign`)."""
        from sag_api.db.models.stock_research import MacroSensitivitySnapshot

        needle = macro_factor.strip().lower()
        if not needle:
            return []
        allowed_set = (
            {t.strip().upper() for t in allowed_tickers if t and t.strip()}
            if allowed_tickers is not None
            else None
        )
        stmt = (
            select(MacroSensitivitySnapshot)
            .where(
                MacroSensitivitySnapshot.target_id.like("TICKER:%"),
                MacroSensitivitySnapshot.effective_from <= as_of_date,
                MacroSensitivitySnapshot.valid_to > as_of_date,
                MacroSensitivitySnapshot.as_of_date <= as_of_date,
            )
            .order_by(MacroSensitivitySnapshot.p_value.asc().nulls_last())
            .limit(400)
        )
        rows = list((await self._session.scalars(stmt)).all())
        tier_rank = {"CONFIRMED_BOTH": 0, "CONFLICT_FLAG": 1, "QUANT_DOC_NEUTRAL": 2, "QUANT_ONLY": 3, "DOC_ONLY": 4}
        out: list[dict[str, Any]] = []
        for s in rows:
            mf = str(s.macro_factor or "").strip().lower()
            if needle not in mf and mf not in needle:
                continue
            sym = str(s.target_id or "").split(":", 1)[1].strip().upper()
            if allowed_set is not None and "*" not in allowed_set and sym not in allowed_set:
                continue
            meta = s.metadata_json if isinstance(s.metadata_json, Mapping) else {}
            note_ref = meta.get("audited_note_ref") if isinstance(meta.get("audited_note_ref"), Mapping) else {}
            beta = float(s.beta or 0.0)
            stated_sign = int(note_ref.get("stated_sign") or 0) if note_ref else 0
            eff_sign = (1 if beta > 0 else -1) if abs(beta) > 1e-12 else stated_sign
            if direction_filter is not None and direction_filter != 0 and eff_sign != direction_filter:
                continue
            out.append(
                {
                    "ticker": sym,
                    "macro_factor": str(s.macro_factor or ""),
                    "beta": beta,
                    "direction_sign": eff_sign,
                    "p_value": s.p_value,
                    "fdr_q_value": s.fdr_q_value,
                    "verification_tier": str(s.verification_tier or "QUANT_ONLY"),
                    "doc_stance": s.doc_stance,
                    "effective_from": str(s.effective_from),
                    "metadata_json": dict(meta),
                }
            )
        out.sort(
            key=lambda x: (
                tier_rank.get(x["verification_tier"], 9),
                float(x["p_value"]) if x.get("p_value") is not None else 999.0,
                -abs(float(x.get("beta") or 0.0)),
                x["ticker"],
            )
        )
        return out[: max(1, limit)]


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


async def _load_document_pages_for_kg(
    session: AsyncSession,
    *,
    tickers: Sequence[str],
    as_of_date: date,
    tenant_id: str | None = None,
) -> list[Any]:
    """Load PIT-eligible canonical blocks grouped by `(version, page)` into `DocumentPageInput` rows."""
    from collections import defaultdict
    from datetime import UTC, datetime

    from sag_api.db.models import CanonicalBlock, Document, DocumentVersion
    from sag_api.research.kg_entity_linker import DocumentPageInput

    norm_tickers = sorted({t.strip().upper() for t in tickers if t and t.strip()})
    if not norm_tickers:
        return []
    as_of_dt = datetime(as_of_date.year, as_of_date.month, as_of_date.day, 23, 59, 59, tzinfo=UTC)
    filters = [
        Document.project_id.in_(norm_tickers),
        Document.is_active.is_(True),
        DocumentVersion.valid_from <= as_of_dt,
        DocumentVersion.valid_to > as_of_dt,
        DocumentVersion.status != "REVOKED",
        CanonicalBlock.block_type != "table_card",
    ]
    if tenant_id:
        filters.append(Document.tenant_id == tenant_id)
    stmt = (
        select(
            Document.project_id,
            Document.filename,
            DocumentVersion.id,
            DocumentVersion.source_published_at,
            DocumentVersion.valid_from,
            DocumentVersion.metadata_json,
            CanonicalBlock.page_from,
            CanonicalBlock.normalized_text,
        )
        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .join(CanonicalBlock, CanonicalBlock.document_version_id == DocumentVersion.id)
        .where(*filters)
        .order_by(DocumentVersion.id.asc(), CanonicalBlock.page_from.asc(), CanonicalBlock.ordinal.asc())
    )
    rows = (await session.execute(stmt)).all()
    grouped: dict[tuple[str, str, str, str, date, int], list[str]] = defaultdict(list)
    for pid, fname, vid, src_pub, vf, meta, pno, txt in rows:
        dt_val = src_pub or vf
        pub_d = dt_val.date() if isinstance(dt_val, datetime) else _coerce_date(dt_val, default=as_of_date)
        if pub_d > as_of_date:
            continue
        slot = str((meta or {}).get("sub_doc_slot") or "BCTN_CORE") if isinstance(meta, Mapping) else "BCTN_CORE"
        key = (str(pid).upper(), str(fname or vid), str(vid), slot, pub_d, int(pno or 1))
        if txt and str(txt).strip():
            grouped[key].append(str(txt).strip())

    return [
        DocumentPageInput(
            ticker=tk,
            document_name=fname,
            document_version_id=vid,
            sub_doc_slot=slot,
            published_date=pub_d,
            page_no=pno,
            text="\n".join(parts),
        )
        for (tk, fname, vid, slot, pub_d, pno), parts in grouped.items()
    ]


async def _load_extracted_tables_for_kg(
    session: AsyncSession,
    *,
    tickers: Sequence[str],
    as_of_date: date,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    """Load PIT-valid `SagExtractedTable` rows for subsidiary/associate ownership extraction."""
    from datetime import UTC, datetime

    from sag_api.db.models import Document, DocumentVersion
    from sag_api.db.models.stock_research import SagExtractedTable

    norm_tks = [t.strip().upper() for t in tickers if t and t.strip()]
    if not norm_tks:
        return []
    cutoff_dt = datetime(as_of_date.year, as_of_date.month, as_of_date.day, 23, 59, 59, tzinfo=UTC)
    stmt = (
        select(
            SagExtractedTable.table_id,
            SagExtractedTable.project_id,
            SagExtractedTable.document_version_id,
            SagExtractedTable.sub_doc_slot,
            SagExtractedTable.table_title,
            SagExtractedTable.section_path,
            SagExtractedTable.page_from,
            SagExtractedTable.headers_json,
            SagExtractedTable.rows_json,
            SagExtractedTable.valid_from,
        )
        .join(DocumentVersion, DocumentVersion.id == SagExtractedTable.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            SagExtractedTable.project_id.in_(norm_tks),
            SagExtractedTable.valid_from <= cutoff_dt,
            Document.is_active.is_(True),
            DocumentVersion.status != "REVOKED",
            DocumentVersion.valid_from <= cutoff_dt,
            DocumentVersion.valid_to > cutoff_dt,
        )
    )
    if tenant_id:
        stmt = stmt.where(Document.tenant_id == tenant_id)
    rows = (await session.execute(stmt)).all()
    return [
        {
            "table_id": r[0],
            "project_id": r[1],
            "document_version_id": r[2],
            "sub_doc_slot": r[3],
            "table_title": r[4],
            "section_path": r[5],
            "page_from": r[6],
            "headers_json": r[7],
            "rows_json": r[8],
            "valid_from": r[9],
        }
        for r in rows
    ]


async def refresh_research_knowledge_graph(
    session: AsyncSession,
    *,
    tickers: Sequence[str],
    as_of_date: date,
    core_database_url: str | None = None,
    core_snapshot: Mapping[str, Any] | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Production 3-Layer KG refresher wiring Entity Linker, Macro HAC/FDR, and True-Peer into `SQLAlchemyKGStore`."""
    import asyncio

    from sag_api.research.kg_entity_linker import (
        extract_corporate_structure_from_tables,
        extract_document_micro_edges_from_pages,
    )
    from sag_api.research.kg_macro_coupling import (
        reconcile_macro_with_audited_docs,
        run_cross_sector_coupling,
        run_fundamental_macro_channel,
        run_market_excess_macro_channel,
    )
    from sag_api.research.kg_true_peer import (
        build_financial_structure_matrix,
        build_true_peer_clusters,
        compute_biz_vectors,
        compute_multimodal_similarity,
    )

    norm_tickers = sorted({t.strip().upper() for t in tickers if t and t.strip()})
    snapshot: dict[str, Any]
    if core_snapshot is not None:
        snapshot = dict(core_snapshot)
    elif core_database_url:
        snapshot = await asyncio.to_thread(load_core_db_snapshot, core_database_url, as_of_date)
    else:
        snapshot = {
            "as_of_date": as_of_date,
            "stocks": pd.DataFrame(
                [{"symbol": tk, "name": tk, "sector": "HOSE", "industry": ""} for tk in norm_tickers]
            ),
            "macro": pd.DataFrame(),
            "ratios": pd.DataFrame(),
            "market": pd.DataFrame(),
            "statements": [],
        }

    stocks_df: pd.DataFrame = snapshot.get("stocks") if isinstance(snapshot.get("stocks"), pd.DataFrame) else (
        pd.DataFrame(columns=["symbol", "name", "sector", "industry"])
    )
    macro_df: pd.DataFrame = snapshot.get("macro") if isinstance(snapshot.get("macro"), pd.DataFrame) else (
        pd.DataFrame()
    )
    ratios_df: pd.DataFrame = snapshot.get("ratios") if isinstance(snapshot.get("ratios"), pd.DataFrame) else (
        pd.DataFrame()
    )
    mkt_df: pd.DataFrame = snapshot.get("market") if isinstance(snapshot.get("market"), pd.DataFrame) else (
        pd.DataFrame()
    )
    stmt_rows = list(snapshot.get("statements") or ())

    # Ensure every newly ingested ticker is present in `stocks_df` even without Core DB metadata
    existing_syms = (
        {str(s).strip().upper() for s in stocks_df["symbol"].dropna().tolist()}
        if not stocks_df.empty and "symbol" in stocks_df.columns
        else set()
    )
    missing_rows = [
        {"symbol": tk, "name": tk, "sector": "HOSE", "industry": ""}
        for tk in norm_tickers
        if tk not in existing_syms
    ]
    if missing_rows:
        stocks_df = pd.concat([stocks_df, pd.DataFrame(missing_rows)], ignore_index=True)

    macro_indicators: list[str] = (
        sorted({str(v) for v in macro_df["indicator_name"].dropna().unique().tolist()})
        if not macro_df.empty and "indicator_name" in macro_df.columns
        else []
    )
    pages = await _load_document_pages_for_kg(
        session,
        tickers=norm_tickers,
        as_of_date=as_of_date,
        tenant_id=tenant_id,
    )
    struct_tables = await _load_extracted_tables_for_kg(
        session,
        tickers=norm_tickers,
        as_of_date=as_of_date,
        tenant_id=tenant_id,
    )

    def _compute_all_layers() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
        nodes = build_kg_nodes_from_universe(stocks_df, macro_indicators, as_of_date)
        edges: list[dict[str, Any]] = []
        macro_snaps: list[dict[str, Any]] = []
        peer_clusters = 0

        corp_nodes, corp_edges = extract_corporate_structure_from_tables(
            struct_tables,
            stocks_df=stocks_df,
            as_of_date=as_of_date,
        )
        nodes.extend(corp_nodes)
        edges.extend(corp_edges)

        micro_edges, sector_edges, doc_disclosures = extract_document_micro_edges_from_pages(
            stocks_df,
            pages,
            as_of_date,
            available_macro_indicators=macro_indicators or None,
        )
        edges.extend(micro_edges)
        edges.extend(sector_edges)

        macro_beta_matrix: dict[str, dict[str, float]] = {}
        macro_cols: list[str] = []
        quant_snaps: list[dict[str, Any]] = []
        if not macro_df.empty and not ratios_df.empty:
            fund_snaps, _ = run_fundamental_macro_channel(stocks_df, macro_df, ratios_df, as_of_date)
            quant_snaps.extend(fund_snaps)
        if not macro_df.empty and not mkt_df.empty:
            mkt_snaps, macro_beta_matrix, macro_cols = run_market_excess_macro_channel(
                stocks_df, macro_df, mkt_df, as_of_date
            )
            quant_snaps.extend(mkt_snaps)
        if quant_snaps or doc_disclosures:
            macro_snaps = reconcile_macro_with_audited_docs(quant_snaps, doc_disclosures, as_of_date)
            edges.extend(snapshots_to_kg_edges(macro_snaps, as_of_date))
        if not ratios_df.empty or not mkt_df.empty:
            cross_sec_edges = run_cross_sector_coupling(stocks_df, ratios_df, mkt_df, as_of_date)
            edges.extend(cross_sec_edges)

        if len(stocks_df) >= 2:
            biz_vecs, _ = compute_biz_vectors(stocks_df, use_local_fallback=True)
            fin_z, has_fin_map, _ = build_financial_structure_matrix(
                stocks_df, ratios_df, stmt_rows, as_of_date
            )
            symbols = [str(s) for s in stocks_df["symbol"].tolist()]
            sim_peer, sim_biz, sim_macro, sim_fin, _ = compute_multimodal_similarity(
                symbols,
                biz_vecs,
                fin_z,
                macro_beta_matrix,
                macro_cols,
                has_fin_map=has_fin_map,
            )
            peer_res = build_true_peer_clusters(
                stocks_df, sim_peer, sim_biz, sim_macro, sim_fin, as_of_date
            )
            edges.extend(peer_res.get("kg_edges") or ())
            peer_clusters = int(peer_res.get("num_clusters", 0) or 0)

        return nodes, edges, macro_snaps, peer_clusters

    nodes, edges, macro_snaps, peer_clusters = await asyncio.to_thread(_compute_all_layers)
    store = SQLAlchemyKGStore(session)
    nodes_upserted = await store.upsert_nodes(nodes)
    edges_upserted = await store.upsert_edges(edges)
    snaps_upserted = await store.upsert_macro_snapshots(macro_snaps)
    return {
        "as_of_date": as_of_date.isoformat(),
        "tickers_refreshed": norm_tickers,
        "pages_scanned": len(pages),
        "nodes_upserted": nodes_upserted,
        "edges_upserted": edges_upserted,
        "macro_snapshots_upserted": snaps_upserted,
        "true_peer_clusters": peer_clusters,
    }
