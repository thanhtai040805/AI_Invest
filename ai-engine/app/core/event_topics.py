"""Official RabbitMQ Event Topics & Routing Keys for 12 Agents (IOS v5.1).

Structure:
  {domain}.{action/entity}.{status}
Exchange:
  aiinvest.events (Topic Exchange)
"""

from enum import Enum


class EventTopics:
    # 01. Market Surveillance
    MARKET_PULSE = "market.pulse"
    MARKET_ANOMALY = "market.anomaly.alert"
    REGIME_UPDATED = "market.regime.updated"

    # 02. Universe Discovery
    DISCOVERY_CANDIDATES = "discovery.candidates"
    UNIVERSE_SNAPSHOT = "universe.snapshot"

    # 03. Equity Research
    RESEARCH_REPORT = "research.report"
    FACTOR_SCORES_UPDATED = "research.factor_scores.updated"

    # 04. Investment Thesis
    THESIS_CREATED = "thesis.created"
    THESIS_INVALIDATED = "thesis.invalidated"

    # 05. Counter Thesis
    COUNTER_VERDICT = "counter.verdict"

    # 12. Strategy CIO
    CIO_RESOLUTION = "cio.resolution"

    # 06. Portfolio Risk
    RISK_APPROVED = "risk.approved"
    RISK_BREACH_ALERT = "risk.breach.alert"
    DRAWDOWN_TIER_CHANGED = "risk.drawdown.changed"

    # 07. Portfolio Allocation
    ORDER_INSTRUCTION = "portfolio.order.instruction"
    REBALANCE_PLANNED = "portfolio.rebalance.planned"

    # 08. Trade Execution
    TRADE_EXECUTED = "execution.trade.executed"
    TRADE_REJECTED = "execution.trade.rejected"

    # 09. Position Monitoring
    STOP_LOSS_EMERGENCY = "monitoring.stop_loss.emergency"
    POSITION_HEALTH_TICK = "monitoring.position.health"

    # 10. Reinforcement Learning
    POLICY_WEIGHTS = "learning.policy.weights"
    CDC_TRIGGERED = "learning.cdc.triggered"

    # 11. System Governance
    GOVERNANCE_FAILSAFE = "governance.failsafe.active"
    AUDIT_LOG_ENTRY = "governance.audit.logged"
