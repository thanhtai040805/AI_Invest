"""
triage_engine.py — AI-powered Document Intelligence & Financial News Analysis.
Uses Groq/Qwen models to classify news events, extract symbols, and compute multi-dimensional investment vectors.
"""
import json
import logging
import asyncio
import math
import re
from typing import List, Dict, Any
from datetime import datetime, timezone

from app.config.settings import get_settings, get_async_evomap_client
from app.infrastructure.database.pg_pool import get_cursor

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a senior Vietnamese financial news intelligence analyst specializing in equity research and investment analysis.

Your task is NOT to summarize the article.

Your task is to evaluate the investment significance of a financial news article for the Vietnamese stock market.

The output will be consumed by quantitative models, GraphRAG, AI investment agents, and behavioral finance models.

Always analyze from the perspective of an institutional investor.

--------------------------------------------------
GENERAL PRINCIPLES
--------------------------------------------------

Evaluate the article along MULTIPLE independent dimensions.

Do NOT confuse:
• Sentiment
• Investment Impact
• Materiality
• Surprise
• News Scope

These represent different concepts.

--------------------------------------------------
1. SENTIMENT SCORE
--------------------------------------------------
Measure the emotional polarity of the news (expected investor reaction, not purely the text's tone).
Range: -1.0 to +1.0
-1.0 = Extremely Negative
0.0 = Neutral
+1.0 = Extremely Positive

Examples of POSITIVE expected reaction:
• earnings beat
• large contract / order win
• dividend increase
• policy/subsidy approval

Examples of NEGATIVE expected reaction:
• fraud
• earnings miss
• legal violation
• corporate governance failure

Neutral expected reaction:
• routine meeting announcement
• standard periodic disclosures (unless containing unexpected surprises)

--------------------------------------------------
2. INVESTMENT IMPACT (DIRECTIONS & MAGNITUDES)
--------------------------------------------------
Measure expected impact on the company's intrinsic value, cash flows, or core business operations.
Return a structured combination:
• direction: "POSITIVE" | "NEGATIVE" | "NEUTRAL"
• magnitude: integer from 1 (negligible) to 5 (extreme/transformative)

Examples of POSITIVE direction with HIGH magnitude (4-5):
• major CAPEX / factory construction (e.g. HPG Dung Quất 2)
• monopoly / regulatory approvals for new core segments
• huge multi-year contracts

Examples of NEGATIVE direction with HIGH magnitude (4-5):
• accounting fraud / criminal prosecution of top officers
• audit disclaimer / adverse opinion
• bankruptcy / bond defaults

PR, marketing, or corporate social responsibility (CSR) news usually has NEUTRAL direction and magnitude = 1.

--------------------------------------------------
3. MATERIALITY
--------------------------------------------------
Estimate how economically important the news is to the business value.
Options: "HIGH" | "MEDIUM" | "LOW"
• HIGH: Major valuation driver (e.g. merger, huge CAPEX, regulatory halt).
• MEDIUM: Operational significance (e.g. senior management change, moderate sales increase).
• LOW: Marketing/PR fluff, CSR, corporate awards, conferences, routine announcements.

--------------------------------------------------
4. SURPRISE
--------------------------------------------------
Estimate how unexpected the news is to the market.
Options: "HIGH" | "MEDIUM" | "LOW"
• HIGH: far beyond market expectations, unexpected CEO resignation, sudden state policy, major earnings beat.
• LOW: scheduled dividends, routine AGMs, regular disclosures.

--------------------------------------------------
5. NEWS SCOPE
--------------------------------------------------
Choose exactly one: "COMPANY" | "MULTI_COMPANY" | "SECTOR" | "MARKET" | "MACRO" | "GLOBAL"

--------------------------------------------------
6. EVENT TYPE
--------------------------------------------------
Choose exactly one:
"EARNINGS", "GUIDANCE", "CAPEX", "PROJECT", "DIVIDEND", "BUYBACK", "ESOP", "M&A", "LEGAL", "AUDIT", "CONTRACT", "ORDER_WIN", "ORDER_LOSS", "RATING_CHANGE", "POLICY", "MACRO", "INDUSTRY", "LEADERSHIP", "SUPPLY_CHAIN", "CUSTOMER", "COMPETITOR", "OTHER"

--------------------------------------------------
7. AFFECTED ENTITIES
--------------------------------------------------
Return listed companies, industries, or macro assets mentioned in the article as a list of objects containing:
• id: The ticker code (e.g., FPT, HPG, SGS) or industry/macro symbol.
• type: "COMPANY" | "INDUSTRY" | "MACRO" | "GLOBAL"

--------------------------------------------------
8. APPARENT NOVELTY
--------------------------------------------------
Estimate whether this appears to be new/novel news: "HIGH" | "MEDIUM" | "LOW"
(Estimate standalone, do NOT compare against historical news).

--------------------------------------------------
9. EVIDENCE STRENGTH
--------------------------------------------------
Evaluate how factual the source and facts are: "HIGH" | "MEDIUM" | "LOW"
• HIGH: Official filings, audited financial statements, state/government decrees, official corporate disclosures.
• LOW: Opinion, rumor, unverified gossip, speculative market commentary.

--------------------------------------------------
10. TIME HORIZONS & PERSISTENCE
--------------------------------------------------
• business_horizon: expected duration of business impact: "INTRADAY" | "SHORT" | "MEDIUM" | "LONG"
• pricing_horizon: expected time for market price to reflect this news: "IMMEDIATE" | "SHORT" | "MEDIUM" | "LONG"
• persistence: how long the sentiment impact will persist before decaying: "LOW" | "MEDIUM" | "HIGH"

--------------------------------------------------
11. REVERSIBILITY
--------------------------------------------------
reversibility: true | false (whether the business event can be reversed easily; e.g. factory fire/lawsuit is usually reversible/rectified over time (true), while new tax/law or permanent license revocation is irreversible (false)).

--------------------------------------------------
INVESTMENT DECISION TREE (Follow this step-by-step logic)
--------------------------------------------------
Step 1: Determine whether the article changes intrinsic value / business fundamentals. If not, investment_impact is neutral, materiality is LOW.
Step 2: Determine expected investor reaction.
Step 3: Estimate materiality.
Step 4: Estimate persistence.
Step 5: Estimate investment impact (direction & magnitude).
Step 6: Estimate sentiment.

--------------------------------------------------
PR / NON-INVESTMENT NEGATIVE EXAMPLES
--------------------------------------------------
The following items should ALWAYS receive Neutral/0 impact, LOW materiality, and LOW surprise:
- Awards (e.g. "Top Employer", "Top 10 Brand")
- CSR / Charity / Sponsorships
- Office relocations
- Conferences / Seminars / Event attendances
- Routine/regular board meetings (unless major announcements occur)
- Holiday greetings / anniversaries

--------------------------------------------------
CORE RULE
--------------------------------------------------
Never reward optimistic wording. Never punish pessimistic wording. Score only based on expected change in future business fundamentals and investor behavior. Ignore promotional language.

--------------------------------------------------
OUTPUT FORMAT
--------------------------------------------------
Return ONLY valid JSON. Do not output markdown code blocks (such as ```json).
{
  "document_type": "NEWS" | "AnnualReport" | "FinancialStatement" | "OfficialDisclosure" | "BrokerReport" | "Interview" | "Other",
  "primary_event": "EVENT_TYPE_MAPPED",
  "scope": "SCOPE_MAPPED",
  "affected_entities": [
    {"id": "TICKER_OR_NAME", "type": "COMPANY" | "INDUSTRY" | "MACRO" | "GLOBAL"}
  ],
  "sentiment_score": float (-1.0 to 1.0),
  "direction": "POSITIVE" | "NEGATIVE" | "NEUTRAL",
  "magnitude": int (1 to 5),
  "materiality": "HIGH" | "MEDIUM" | "LOW",
  "surprise_score": float (0.0 to 1.0),
  "apparent_novelty": "HIGH" | "MEDIUM" | "LOW",
  "evidence_strength": "HIGH" | "MEDIUM" | "LOW",
  "business_horizon": "INTRADAY" | "SHORT" | "MEDIUM" | "LONG",
  "pricing_horizon": "IMMEDIATE" | "SHORT" | "MEDIUM" | "LONG",
  "persistence": "LOW" | "MEDIUM" | "HIGH",
  "reversibility": boolean,
  "summary": "1-2 sentence brief explanation",
  "reason": "1-2 sentence explanation of logic"
}
"""

def get_tf_vector(text: str) -> Dict[str, int]:
    words = re.findall(r'\w+', text.lower())
    vector = {}
    for w in words:
        vector[w] = vector.get(w, 0) + 1
    return vector

def tf_cosine_similarity(vec1: Dict[str, int], vec2: Dict[str, int]) -> float:
    intersection = set(vec1.keys()) & set(vec2.keys())
    numerator = sum(vec1[x] * vec2[x] for x in intersection)
    sum1 = sum(vec1[x]**2 for x in vec1.keys())
    sum2 = sum(vec2[x]**2 for x in vec2.keys())
    denominator = math.sqrt(sum1) * math.sqrt(sum2)
    if not denominator:
        return 0.0
    return float(numerator) / denominator


class DocumentIntelligenceEngine:
    def __init__(self):
        self.settings = get_settings()
        self.use_evomap = bool(getattr(self.settings, "evomap_api_key", None))

    # ── Public Static Helpers (also called internally & testable directly) ──

    @staticmethod
    def _compute_credibility(source: str, evidence_strength: str, doc_type: str) -> float:
        """Compute credibility score from source, evidence strength, and document type.
        
        Formula: source_weight × evidence_score × official_confirmation
        """
        source_lower = str(source or "").lower()
        if any(k in source_lower for k in ["ubcknn", "ssc", "hose", "hnx"]):
            source_weight = 1.0
        elif any(k in source_lower for k in ["reuters", "bloomberg"]):
            source_weight = 0.95
        elif any(k in source_lower for k in ["cafef", "vneconomy", "vietstock"]):
            source_weight = 0.90
        elif any(k in source_lower for k in ["broker", "ctck", "report"]):
            source_weight = 0.80
        elif any(k in source_lower for k in ["facebook", "social", "f319"]):
            source_weight = 0.20
        else:
            source_weight = 0.40

        ev = str(evidence_strength or "MEDIUM").upper()
        evidence_score = 1.0 if ev == "HIGH" else 0.6 if ev == "MEDIUM" else 0.2 if ev == "LOW" else 0.4

        dt = str(doc_type or "OTHER").upper()
        if dt in ["ANNUALREPORT", "FINANCIALSTATEMENT", "OFFICIALDISCLOSURE"]:
            official_conf = 1.0
        elif dt in ["NEWS", "BROKERREPORT"]:
            official_conf = 0.8
        elif dt == "INTERVIEW":
            official_conf = 0.7
        else:
            official_conf = 0.5

        return round(source_weight * evidence_score * official_conf, 4)

    @staticmethod
    def _compute_investment_impact(direction: str, magnitude: int, affected_entities: list = None) -> float:
        """Compute signed investment impact.

        Returns 0 if no COMPANY is directly affected (macro/policy news
        doesn't change intrinsic value of any listed firm).
        Otherwise: sign(direction) × (magnitude / 5.0).
        """
        if affected_entities:
            has_company = any(
                e.get("type") == "COMPANY" for e in affected_entities if isinstance(e, dict)
            )
            if not has_company:
                return 0.0
        d = str(direction or "NEUTRAL").upper()
        sign = 1.0 if d == "POSITIVE" else -1.0 if d == "NEGATIVE" else 0.0
        return round(sign * (int(magnitude) / 5.0), 2)

    async def triage_article(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze a single article using Groq/Evomap and compute News Intelligence Vector."""
        title = article.get("title", "")
        content = article.get("article_content", "") or article.get("article_pdf_text", "") or ""
        
        # Truncate content to avoid exceeding context window
        if len(content) > 6000:
            content = content[:6000] + "...(truncated)"

        prompt = f"Title: {title}\n\nContent:\n{content}"
        
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
        
        try:
            if self.use_evomap:
                completion = await self.evomap_client.chat.completions.create(
                    model="evomap-deepseek-v4-flash",
                    messages=messages,
                    temperature=0.1,
                )
                raw_content = completion.choices[0].message.content.strip()
            else:
                res = await self.agent.chat(messages, temperature=0.1)
                raw_content = res.get("content", "").strip()
            
            # Clean up markdown JSON wrappers if present
            if raw_content.startswith("```json"):
                raw_content = raw_content[7:]
            if raw_content.startswith("```"):
                raw_content = raw_content[3:]
            if raw_content.endswith("```"):
                raw_content = raw_content[:-3]
                
            parsed = json.loads(raw_content.strip())
            
            # --- Python-driven calculations ---
            
            # 1. Credibility Calculation
            source = parsed.get("source") or article.get("source") or "cafef"
            source = str(article.get("source") or "")
            evidence_strength = str(parsed.get("evidence_strength") or "MEDIUM").upper()
            doc_type = str(parsed.get("document_type") or "Other")
            credibility = DocumentIntelligenceEngine._compute_credibility(source, evidence_strength, doc_type)

            # 2. Novelty Calculation (against last 20 articles of same symbol)
            prev_docs = []
            sym = None
            affected = parsed.get("affected_entities") or []
            if affected:
                for ent in affected:
                    if ent.get("type") == "COMPANY":
                        sym = ent.get("id")
                        break
            if not sym:
                sym = article.get("symbol")
            
            if sym:
                try:
                    with get_cursor() as cur:
                        cur.execute("""
                            SELECT title, article_content 
                            FROM knowledge_documents 
                            WHERE symbol = %s AND triaged_at IS NOT NULL
                            ORDER BY published_date DESC
                            LIMIT 20
                        """, (sym.upper(),))
                        prev_docs = [{"title": r[0], "content": r[1] or ""} for r in cur.fetchall()]
                except Exception as e:
                    logger.debug("Failed to fetch prev documents for novelty check: %s", e)

            max_sim = 0.0
            if prev_docs:
                curr_text = f"{title} {parsed.get('summary', '')}"
                curr_vec = get_tf_vector(curr_text)
                for pd in prev_docs:
                    pd_text = f"{pd['title']} {pd['content'][:500]}"
                    pd_vec = get_tf_vector(pd_text)
                    sim = tf_cosine_similarity(curr_vec, pd_vec)
                    if sim > max_sim:
                        max_sim = sim

            apparent_novelty = str(parsed.get("apparent_novelty") or "MEDIUM").upper()
            if apparent_novelty == "HIGH":
                apparent_novelty_score = 1.0
            elif apparent_novelty == "MEDIUM":
                apparent_novelty_score = 0.5
            elif apparent_novelty == "LOW":
                apparent_novelty_score = 0.1
            else:
                apparent_novelty_score = 0.5
                
            novelty = round(apparent_novelty_score * (1.0 - max_sim), 4)

            # 3. Investment Impact Calculation
            direction = str(parsed.get("direction") or "NEUTRAL").upper()
            magnitude = int(parsed.get("magnitude") or 1)
            investment_impact = DocumentIntelligenceEngine._compute_investment_impact(direction, magnitude, affected)

            # 4. Materiality & Persistence Score Mappings
            materiality = str(parsed.get("materiality") or "LOW").upper()
            if materiality == "HIGH":
                materiality_score = 1.0
            elif materiality == "MEDIUM":
                materiality_score = 0.6
            else:
                materiality_score = 0.2

            persistence = str(parsed.get("persistence") or "LOW").upper()
            if persistence == "HIGH":
                persistence_score = 1.0
            elif persistence == "MEDIUM":
                persistence_score = 0.6
            else:
                persistence_score = 0.2

            surprise_score = float(parsed.get("surprise_score") if parsed.get("surprise_score") is not None else 0.5)

            # ── Invariant enforcement layer ──────────────────────────────
            # If no listed company or industry is mentioned, the news
            # does not affect intrinsic value or investor sentiment.
            has_relevant_entity = any(
                e.get("type") in ("COMPANY", "INDUSTRY")
                for e in affected if isinstance(e, dict)
            )
            if not has_relevant_entity:
                sentiment_score = 0.0
                investment_impact = 0.0
                direction = "NEUTRAL"
                magnitude = 1
                materiality = "LOW"
                materiality_score = 0.2
                surprise_score = 0.0
            # ──────────────────────────────────────────────────────────────

            # Assemble clean enriched payload
            return {
                "document_type": parsed.get("document_type", "Other"),
                "primary_event": parsed.get("primary_event") or "OTHER",
                "scope": parsed.get("scope", "COMPANY"),
                "affected_entities": affected,
                
                # Dynamic Python metrics
                "sentiment_score": float(parsed.get("sentiment_score") or 0.0),
                "direction": direction,
                "magnitude": magnitude,
                "investment_impact": investment_impact,
                "materiality": materiality,
                "materiality_score": materiality_score,
                "surprise_score": surprise_score,
                "business_horizon": parsed.get("business_horizon", "SHORT"),
                "pricing_horizon": parsed.get("pricing_horizon", "IMMEDIATE"),
                "persistence": persistence,
                "persistence_score": persistence_score,
                "reversibility": bool(parsed.get("reversibility", True)),
                "apparent_novelty": apparent_novelty,
                "novelty": novelty,
                "evidence_strength": evidence_strength,
                "credibility": credibility,
                "summary": parsed.get("summary") or title,
                "reason": parsed.get("reason") or "",
                
                # Backward-compatible fields
                "event_type": parsed.get("primary_event") or "OTHER",
                "severity": parsed.get("materiality") or "INFO",
                "ai_sentiment_score": float(parsed.get("sentiment_score") or 0.0),
                "ai_summary": parsed.get("summary") or title,
                "symbols": [e.get("id") for e in affected if e.get("type") == "COMPANY"] or [sym] or []
            }
        except Exception as e:
            logger.error("Triage failed for article '%s': %s", title, e)
            # Safe default fallback
            fallback_sym = article.get("symbol")
            return {
                "document_type": "Other",
                "primary_event": "OTHER",
                "scope": "COMPANY",
                "affected_entities": [{"id": fallback_sym, "type": "COMPANY"}] if fallback_sym else [],
                "sentiment_score": 0.0,
                "direction": "NEUTRAL",
                "magnitude": 1,
                "investment_impact": 0.0,
                "materiality": "LOW",
                "materiality_score": 0.2,
                "surprise_score": 0.0,
                "business_horizon": "SHORT",
                "pricing_horizon": "IMMEDIATE",
                "persistence": "LOW",
                "persistence_score": 0.2,
                "reversibility": True,
                "apparent_novelty": "MEDIUM",
                "novelty": 0.5,
                "evidence_strength": "LOW",
                "credibility": 0.4,
                "summary": title,
                "reason": f"Fallback error: {str(e)}",
                
                # Backward compatibility
                "event_type": "OTHER",
                "severity": "INFO",
                "ai_sentiment_score": 0.0,
                "ai_summary": f"Failed to parse: {str(e)}",
                "symbols": [fallback_sym] if fallback_sym else []
            }

    async def triage_batch(self, articles: List[Dict[str, Any]], concurrency: int = 5) -> List[Dict[str, Any]]:
        """Triage multiple articles concurrently."""
        sem = asyncio.Semaphore(concurrency)
        
        async def process(art):
            async with sem:
                result = await self.triage_article(art)
                art.update(result)
                # Keep sentiment_score on article root level
                art["sentiment_score"] = result["sentiment_score"]
                return art
                
        tasks = [process(art) for art in articles]
        return await asyncio.gather(*tasks)


# Backward compatible alias for crawlers
NewsTriageEngine = DocumentIntelligenceEngine

_triage_engine = None

def get_triage_engine() -> DocumentIntelligenceEngine:
    global _triage_engine
    if _triage_engine is None:
        _triage_engine = DocumentIntelligenceEngine()
    return _triage_engine
