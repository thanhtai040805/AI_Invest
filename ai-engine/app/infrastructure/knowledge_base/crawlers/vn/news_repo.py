"""
news_repo.py — Database repository for news events.
"""
import logging
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import psycopg2
import psycopg2.extras
from app.infrastructure.database.pg_pool import DB_URL, get_cursor

logger = logging.getLogger(__name__)


def batch_has_content(articles: list[dict]) -> list[bool]:
    """Check which articles already have content in DB. Returns same-length list.

    Each article must have 'url' key. Returns bool per article:
      True = already has content (skip deep crawl)
      False = needs deep crawl
    """
    if not articles:
        return []

    with get_cursor() as cur:
        urls = [art["url"] for art in articles]

        # Batch query: check all URLs at once
        cur.execute(
            """SELECT url FROM knowledge_documents
               WHERE url = ANY(%s)
               AND (article_content IS NOT NULL AND article_content != '')""",
            (urls,),
        )
        existing_urls = {row[0] for row in cur.fetchall()}

    return [art["url"] in existing_urls for art in articles]


def upsert_article(art: dict, source: str) -> bool:
    """Upsert one article into knowledge_documents. Returns True if inserted, False if existed."""
    pub_date = art.get("published_date") or datetime.now(timezone.utc)
    content = art.get("article_content", "")
    images = art.get("article_images") or []
    pdf_urls = art.get("article_pdf_urls") or []
    pdf_text = art.get("article_pdf_text", "")
    doc_type = art.get("doc_type", "news")

    has_data = bool(content or images or pdf_urls or pdf_text)
    fetched_at = datetime.now(timezone.utc) if has_data else None

    try:
        with get_cursor() as cur:
            # Phase 1: INSERT if not exists
            cur.execute(
                """INSERT INTO knowledge_documents
                   (symbol, published_date, title, url, source, doc_type,
                    article_content, article_images, article_pdf_urls,
                    article_pdf_text, content_fetched_at,
                    event_type, severity, ai_sentiment_score, ai_summary, triaged_at, sentiment_score,
                    direction, magnitude, investment_impact, materiality, materiality_score, surprise_score,
                    business_horizon, pricing_horizon, persistence, persistence_score, reversibility,
                    apparent_novelty, novelty, evidence_strength, credibility, affected_entities)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (symbol, url) DO NOTHING""",
                (art["symbol"], pub_date, art["title"], art["url"], source, doc_type,
                 content or None, images or None, pdf_urls or None,
                 pdf_text or None, fetched_at,
                 art.get("event_type"), art.get("severity"),
                 art.get("ai_sentiment_score"), art.get("ai_summary"),
                 datetime.now(timezone.utc) if art.get("event_type") or art.get("triaged_at") else None,
                 art.get("sentiment_score"),
                 art.get("direction"), art.get("magnitude"), art.get("investment_impact"),
                 art.get("materiality"), art.get("materiality_score"), art.get("surprise_score"),
                 art.get("business_horizon"), art.get("pricing_horizon"), art.get("persistence"),
                 art.get("persistence_score"), art.get("reversibility"), art.get("apparent_novelty"),
                 art.get("novelty"), art.get("evidence_strength"), art.get("credibility"),
                 json.dumps(art.get("affected_entities")) if art.get("affected_entities") is not None else None),
            )
            if cur.rowcount > 0:
                return True  # newly inserted

            # Phase 2: existed — update only if existing row is missing data
            if has_data:
                cur.execute(
                    """UPDATE knowledge_documents SET
                       doc_type = CASE WHEN %s::text IS NOT NULL AND knowledge_documents.doc_type = 'news' THEN %s ELSE knowledge_documents.doc_type END,
                       article_content = COALESCE(knowledge_documents.article_content, %s),
                       article_images = CASE
                         WHEN knowledge_documents.article_images IS NULL THEN %s
                         ELSE knowledge_documents.article_images END,
                       article_pdf_urls = CASE
                         WHEN knowledge_documents.article_pdf_urls IS NULL THEN %s
                         ELSE knowledge_documents.article_pdf_urls END,
                       article_pdf_text = COALESCE(knowledge_documents.article_pdf_text, %s),
                       content_fetched_at = COALESCE(knowledge_documents.content_fetched_at, %s),
                       event_type = COALESCE(knowledge_documents.event_type, %s),
                       severity = COALESCE(knowledge_documents.severity, %s),
                       ai_sentiment_score = COALESCE(knowledge_documents.ai_sentiment_score, %s),
                       ai_summary = COALESCE(knowledge_documents.ai_summary, %s),
                       triaged_at = CASE WHEN %s::text IS NOT NULL THEN NOW() ELSE knowledge_documents.triaged_at END,
                       sentiment_score = COALESCE(knowledge_documents.sentiment_score, %s),
                       direction = COALESCE(knowledge_documents.direction, %s),
                       magnitude = COALESCE(knowledge_documents.magnitude, %s),
                       investment_impact = COALESCE(knowledge_documents.investment_impact, %s),
                       materiality = COALESCE(knowledge_documents.materiality, %s),
                       materiality_score = COALESCE(knowledge_documents.materiality_score, %s),
                       surprise_score = COALESCE(knowledge_documents.surprise_score, %s),
                       business_horizon = COALESCE(knowledge_documents.business_horizon, %s),
                       pricing_horizon = COALESCE(knowledge_documents.pricing_horizon, %s),
                       persistence = COALESCE(knowledge_documents.persistence, %s),
                       persistence_score = COALESCE(knowledge_documents.persistence_score, %s),
                       reversibility = COALESCE(knowledge_documents.reversibility, %s),
                       apparent_novelty = COALESCE(knowledge_documents.apparent_novelty, %s),
                       novelty = COALESCE(knowledge_documents.novelty, %s),
                       evidence_strength = COALESCE(knowledge_documents.evidence_strength, %s),
                       credibility = COALESCE(knowledge_documents.credibility, %s),
                       affected_entities = COALESCE(knowledge_documents.affected_entities, %s)
                       WHERE symbol = %s AND url = %s
                       AND (article_content IS NULL OR article_content = '')""",
                    (doc_type if doc_type != "news" else None,
                     doc_type if doc_type != "news" else None,
                     content or None, images or None, pdf_urls or None,
                     pdf_text or None, fetched_at,
                     art.get("event_type"), art.get("severity"),
                     art.get("ai_sentiment_score"), art.get("ai_summary"),
                     art.get("event_type"), art.get("sentiment_score"),
                     art.get("direction"), art.get("magnitude"), art.get("investment_impact"),
                     art.get("materiality"), art.get("materiality_score"), art.get("surprise_score"),
                     art.get("business_horizon"), art.get("pricing_horizon"), art.get("persistence"),
                     art.get("persistence_score"), art.get("reversibility"), art.get("apparent_novelty"),
                     art.get("novelty"), art.get("evidence_strength"), art.get("credibility"),
                     json.dumps(art.get("affected_entities")) if art.get("affected_entities") is not None else None,
                     art["symbol"], art["url"]),
                )
                if cur.rowcount > 0:
                    return True  # updated missing content

    except Exception as e:
        logger.debug("Upsert skip [%s]: %s", art.get("symbol"), e)
    return False


def upsert_articles(articles: list[dict], source: str) -> int:
    """Upsert batch. Returns count of newly inserted."""
    count = 0
    for art in articles:
        if upsert_article(art, source):
            count += 1
    return count


def get_urls_to_crawl(limit: int = 500, doc_types: tuple = ("news", "analyst_report")) -> List[Dict[str, Any]]:
    """Get articles needing deep crawl (HTML content fetching).
    
    Excludes CafeF document types (BCTC, BCTN, Nghị quyết, governance_report)
    which are PDF-only and handled by a separate pipeline.
    """
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, symbol, url, title FROM knowledge_documents
                WHERE (article_content IS NULL OR article_content = '')
                  AND url IS NOT NULL
                  AND doc_type = ANY(%s)
                ORDER BY published_date DESC
                LIMIT %s
            """, (list(doc_types), limit))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_urls_missing_pdf_text(limit: int = 500) -> List[Dict[str, Any]]:
    """Get du-lieu articles that have HTML content but missing PDF text."""
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT id, symbol, url, title FROM knowledge_documents
                WHERE article_content IS NOT NULL
                  AND article_content != ''
                  AND (article_pdf_text IS NULL OR article_pdf_text = '')
                  AND url LIKE '%/du-lieu/%'
                ORDER BY published_date DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def update_content(rows: List[Dict[str, Any]]) -> int:
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            updated = 0
            for row_dict in rows:
                news_id = row_dict["id"]
                content = row_dict.get("article_content", "")
                pdf_text = row_dict.get("article_pdf_text", "")
                
                if content:
                    has_triage = bool(row_dict.get("event_type") or row_dict.get("triaged_at"))
                    triage_cols = ""
                    triage_params = []
                    if has_triage:
                        triage_cols = """, triaged_at = NOW(), event_type = %s, severity = %s, ai_sentiment_score = %s, ai_summary = %s, sentiment_score = %s,
                                         direction = %s, magnitude = %s, investment_impact = %s, materiality = %s, materiality_score = %s, surprise_score = %s,
                                         business_horizon = %s, pricing_horizon = %s, persistence = %s, persistence_score = %s, reversibility = %s,
                                         apparent_novelty = %s, novelty = %s, evidence_strength = %s, credibility = %s, affected_entities = %s"""
                        triage_params = [
                            row_dict.get("event_type"), row_dict.get("severity"),
                            row_dict.get("ai_sentiment_score"), row_dict.get("ai_summary"),
                            row_dict.get("sentiment_score"),
                            row_dict.get("direction"), row_dict.get("magnitude"), row_dict.get("investment_impact"),
                            row_dict.get("materiality"), row_dict.get("materiality_score"), row_dict.get("surprise_score"),
                            row_dict.get("business_horizon"), row_dict.get("pricing_horizon"), row_dict.get("persistence"),
                            row_dict.get("persistence_score"), row_dict.get("reversibility"), row_dict.get("apparent_novelty"),
                            row_dict.get("novelty"), row_dict.get("evidence_strength"), row_dict.get("credibility"),
                            json.dumps(row_dict.get("affected_entities")) if row_dict.get("affected_entities") is not None else None
                        ]
                    cur.execute(
                        """
                        UPDATE knowledge_documents
                        SET article_content = %s,
                            article_pdf_text = CASE WHEN %s != '' THEN %s ELSE article_pdf_text END,
                            content_fetched_at = NOW()
                            {triage_cols}
                        WHERE id = %s AND (article_content IS NULL OR article_content = '')
                        """.format(triage_cols=triage_cols),
                        (
                            content, pdf_text, pdf_text,
                            *triage_params,
                            news_id
                        )
                    )
                    updated += cur.rowcount
            conn.commit()
            return updated
    finally:
        conn.close()


def count_missing_content() -> int:
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM knowledge_documents
                WHERE (article_content IS NULL OR article_content = '')
                  AND url IS NOT NULL
            """)
            return cur.fetchone()[0]
    finally:
        conn.close()
