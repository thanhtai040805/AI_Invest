"""Shared upsert policy for provider financial statements."""
from __future__ import annotations

from datetime import date, timedelta

from app.domain.services import financial_fields as fields

MIN_STATEMENT_FIELDS = 8


def stage_financial_reset(cur) -> None:
    """Build a provider batch independently of retained financial rows."""
    for table in ("financial_statements", "financial_ratios"):
        cur.execute(f"CREATE TEMP TABLE {table} (LIKE public.{table} INCLUDING ALL) ON COMMIT DROP")


def publish_financial_reset(cur, symbols: list[str]) -> dict[str, int]:
    """Validate the staged scope and replace it; the caller owns commit/rollback."""
    if not symbols:
        raise ValueError("Financial reset requires a nonempty symbol scope")
    cur.execute("""SELECT s.symbol FROM unnest(%s::text[]) s(symbol)
        WHERE NOT EXISTS (SELECT 1 FROM pg_temp.financial_statements f
                          WHERE f.symbol=s.symbol AND f.statement_type='BS')
           OR NOT EXISTS (SELECT 1 FROM pg_temp.financial_statements f
                          WHERE f.symbol=s.symbol AND f.statement_type='IS')
           OR NOT EXISTS (SELECT 1 FROM pg_temp.financial_ratios r WHERE r.symbol=s.symbol)
        ORDER BY s.symbol""", (symbols,))
    missing = [row[0] for row in cur.fetchall()]
    if missing:
        raise ValueError(f"Incomplete financial reset inputs for: {missing}")
    # Reset must not silently erase reported valuation inputs. Reject the
    # batch, rather than carrying old fields into a supposedly fresh crawl.
    cur.execute("""SELECT old.symbol,old.period_end,old.frequency,old.data,new.data
        FROM public.financial_statements old
        JOIN pg_temp.financial_statements new
          USING(symbol,period_end,statement_type,frequency)
        WHERE old.symbol=ANY(%s) AND old.statement_type='ratios'""", (symbols,))
    lost = []
    for symbol, period, frequency, before, after in cur.fetchall():
        for name, aliases in (("EPS", fields.EPS_KEYS), ("BVPS", fields.BVPS_KEYS)):
            if (fields.extract_financial_value(before, aliases) is not None
                    and fields.extract_financial_value(after, aliases) is None):
                lost.append(f"{symbol}/{period}/{frequency}/{name}")
    if lost:
        raise ValueError(f"Financial reset would erase {len(lost)} reported per-share inputs: {lost[:10]}")
    counts = {}
    for table, day in (("financial_statements", "period_end"), ("financial_ratios", "ratio_date")):
        cur.execute(f"SELECT count(*) FROM pg_temp.{table} WHERE NOT(symbol=ANY(%s)) OR {day}>%s",
                    (symbols, date.today()))
        if cur.fetchone()[0]:
            raise ValueError(f"Financial reset contains out-of-scope/future rows: {table}")
        cur.execute(f"SELECT count(*) FROM pg_temp.{table}")
        counts[table] = cur.fetchone()[0]
    cur.execute("SET LOCAL lock_timeout='5s'")
    cur.execute("LOCK TABLE public.financial_statements,public.financial_ratios IN SHARE ROW EXCLUSIVE MODE")
    # Every historical financial input in this scope is replaced. Cached factors
    # derived from the previous batch must disappear in the same transaction.
    cur.execute("""DELETE FROM public.factor_scores WHERE symbol=ANY(%s)
                   OR factor_details->'source'->>'producer'='VN_FACTOR_SCORES'""", (symbols,))
    counts["factor_scores_invalidated"] = cur.rowcount
    for table in ("financial_ratios", "financial_statements"):
        cur.execute(f"DELETE FROM public.{table} WHERE symbol=ANY(%s)", (symbols,))
        cur.execute(f"INSERT INTO public.{table} SELECT * FROM pg_temp.{table}")
    return counts


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
    publication_value = ("EXCLUDED.published_date" if allow_historical_updates else
                         "COALESCE(EXCLUDED.published_date, financial_statements.published_date)")
    publication_changed = ("financial_statements.published_date IS DISTINCT FROM EXCLUDED.published_date"
                           if allow_historical_updates else
                           "EXCLUDED.published_date IS NOT NULL AND financial_statements.published_date IS DISTINCT FROM EXCLUDED.published_date")

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
                             published_date = {publication_value}
               WHERE {conflict_guard}
                 AND {update_guard}
                 AND (financial_statements.data IS DISTINCT FROM EXCLUDED.data
                      OR financial_statements.source IS DISTINCT FROM EXCLUDED.source
                      OR ({publication_changed}))"""
    params = (symbol, period_end, statement_type, frequency, data, source, published_date)
    if not allow_historical_updates:
        cutoff = date.today() - timedelta(days=90)
        params += (cutoff, cutoff)
    cur.execute(query, params)
