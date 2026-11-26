"""Deterministic financial fact layer built on Core PG `financial_statements`.

Normalizes vendor statement rows (two key schemas: Vietnamese VAS labels and English
`is_*` / `bs_*` / `cf_*` keys) into canonical, period-typed, PIT-gated facts with
explicit provenance and quality flags. Zero LLM calls.
"""

from sag_api.research.facts.catalog import (
    CATALOG,
    CanonicalItem,
    ItemKind,
    SignPolicy,
    Template,
    items_for_template,
    resolve_template,
)
from sag_api.research.facts.store import (
    FactLookup,
    FactSet,
    FinancialFact,
    LookupStatus,
    build_fact_set,
    load_fact_set,
)

__all__ = [
    "CATALOG",
    "CanonicalItem",
    "FactLookup",
    "FactSet",
    "FinancialFact",
    "ItemKind",
    "LookupStatus",
    "SignPolicy",
    "Template",
    "build_fact_set",
    "items_for_template",
    "load_fact_set",
    "resolve_template",
]
