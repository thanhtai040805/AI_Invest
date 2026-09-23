"""Read normalized stock risk flags from the canonical stocks table."""

from typing import Any

from app.adapters.postgres_adapter import PostgresAdapter


def get_active_flags(symbol: str) -> list[dict[str, Any]]:
    rows = PostgresAdapter().fetch_all(
        """SELECT trading_status, beneish_status, audit_opinion
           FROM stocks WHERE symbol = %s LIMIT 1""",
        (symbol.upper().strip(),),
    )
    if not rows:
        return [{"code": "SYMBOL_NOT_FOUND", "severity": "HARD", "value": None}]

    trading, beneish, audit = (str(value or "").upper().strip() for value in rows[0])
    flags: list[dict[str, Any]] = []
    if trading != "NORMAL":
        flags.append({"code": "TRADING_STATUS", "severity": "HARD", "value": trading})
    if beneish not in {"PASS", "SAFE"}:
        flags.append({"code": "BENEISH_STATUS", "severity": "HARD" if beneish not in {"PENDING", "UNKNOWN"} else "SOFT", "value": beneish})
    if audit != "UNQUALIFIED":
        flags.append({"code": "AUDIT_OPINION", "severity": "HARD", "value": audit})
    return flags


def get_hard_blocked(symbol: str) -> bool:
    return any(flag["severity"] in {"HARD", "CLOSED"} for flag in get_active_flags(symbol))


def get_soft_flag_count(symbol: str) -> int:
    return sum(flag["severity"] == "SOFT" for flag in get_active_flags(symbol))
