"""Shared upsert policy for provider financial statements."""
from __future__ import annotations

from datetime import date, timedelta

MIN_STATEMENT_FIELDS = 8


def upsert_financial_statement(
    cur,
    symbol: str,
    period_end: date,
    statement_type: str,
    frequency: str,
    data,
    source: str,
    published_date: date | None,
    *,
    fill_only: bool = False,
    allow_historical_updates: bool = False,
) -> None:
    """Apply the shared period-aware write policy for statement crawls.

    Normal refreshes insert or update only the recent 90-day window. An explicit
    backfill may replace historical keys from the provider response, so it can
    repair wrong stored statements. A fill-only fallback replaces an AlphaStock
    row only when that row is sparse and the incoming payload has more non-null
    fields. Alias-heavy fallback JSON must not replace a usable primary row.
    An explicit backfill also
    refreshes existing rows from the same fallback provider.
    """
    non_null_count = "(SELECT count(*) FROM jsonb_each({data}) AS metric WHERE metric.value <> 'null'::jsonb)"
    if fill_only:
        conflict_guard = f"""financial_statements.source = 'alphastock'
            AND {non_null_count.format(data='financial_statements.data')} < {MIN_STATEMENT_FIELDS}
            AND {non_null_count.format(data='EXCLUDED.data')} >
                {non_null_count.format(data='financial_statements.data')}"""
        if allow_historical_updates:
            conflict_guard = f"({conflict_guard} OR financial_statements.source = EXCLUDED.source)"
    else:
        conflict_guard = "TRUE"
    insert_guard = "TRUE" if allow_historical_updates else "incoming.period_end >= %s::date"
    update_guard = "TRUE" if allow_historical_updates else "financial_statements.period_end >= %s::date"

    query = f"""INSERT INTO financial_statements
                   (symbol, period_end, statement_type, frequency, data, source, published_date)
               SELECT incoming.symbol, incoming.period_end, incoming.statement_type,
                      incoming.frequency, incoming.data, incoming.source, incoming.published_date
               FROM (VALUES (%s::text, %s::date, %s::text, %s::text, %s::jsonb, %s::text, %s::date))
                    AS incoming(symbol, period_end, statement_type, frequency, data, source, published_date)
               WHERE {insert_guard}
               ON CONFLICT (symbol, period_end, statement_type, frequency)
               DO UPDATE SET data = EXCLUDED.data,
                             source = EXCLUDED.source,
                             fetched_at = NOW(),
                             published_date = COALESCE(EXCLUDED.published_date, financial_statements.published_date)
               WHERE {conflict_guard}
                 AND {update_guard}
                 AND (financial_statements.data IS DISTINCT FROM EXCLUDED.data
                      OR financial_statements.source IS DISTINCT FROM EXCLUDED.source)"""
    params = (symbol, period_end, statement_type, frequency, data, source, published_date)
    if not allow_historical_updates:
        cutoff = date.today() - timedelta(days=90)
        params += (cutoff, cutoff)
    cur.execute(query, params)
