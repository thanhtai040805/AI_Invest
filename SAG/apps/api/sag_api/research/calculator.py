"""Sandboxed AST-Whitelist Python Financial Calculator Tool (Zero Mental Math, Zero eval()).

Enforces:
- AST-only expression evaluation over a strict node & function whitelist (ZERO `eval()`/`exec()`/`compile()`).
- Fail-closed rejection of attribute access, subscripts, lambdas, comprehensions, dunders, and unknown symbols.
- Fail-closed rejection of `TABLE_UNVERIFIED` inputs and cross-currency arithmetic (e.g. VND + USD).
- Automatic unit-scale alignment when inputs carry mixed `unit_multiplier` values (e.g. tỷ đồng vs triệu đồng).
- Structured `CalculatorTrace` output matching `PROJECT.md` Contract 4.
"""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

MAX_AST_DEPTH = 32
MAX_EXPONENT = 20.0


class CalculatorError(ValueError):
    """Base error for deterministic financial calculation failures."""


class UnsafeFormulaError(CalculatorError):
    """Raised when a formula uses disallowed syntax, functions, or AST nodes."""


class UnverifiedTableInputError(CalculatorError):
    """Raised when an input originates from a `TABLE_UNVERIFIED` table."""


class CurrencyMismatchError(CalculatorError):
    """Raised when arithmetic mixes incompatible currencies (e.g. VND and USD)."""


@dataclass(frozen=True, slots=True)
class CalcInput:
    value: float
    table_id: str | None = None
    row: str | None = None
    col: str | None = None
    currency: str | None = None
    unit_raw: str | None = None
    unit_multiplier: float = 1.0
    verification_status: str = "VERIFIED"

    def to_provenance_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"value": self.value}
        if self.table_id is not None:
            out["table_id"] = self.table_id
        if self.row is not None:
            out["row"] = self.row
        if self.col is not None:
            out["col"] = self.col
        if self.currency is not None:
            out["currency"] = self.currency
        if self.unit_raw is not None:
            out["unit_raw"] = self.unit_raw
        if self.unit_multiplier != 1.0:
            out["unit_multiplier"] = self.unit_multiplier
        if self.verification_status != "VERIFIED":
            out["verification_status"] = self.verification_status
        return out


@dataclass(frozen=True, slots=True)
class CalculatorTrace:
    calc_id: str
    formula: str
    description: str
    inputs: dict[str, dict[str, Any]]
    result: float
    formatted: str
    unit_multiplier: float = 1.0
    currency: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "calc_id": self.calc_id,
            "formula": self.formula,
            "description": self.description,
            "inputs": dict(self.inputs),
            "result": self.result,
            "formatted": self.formatted,
        }


def _ensure_finite(val: float, context: str = "result") -> float:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise CalculatorError(f"Non-numeric {context}: {val!r}")
    f = float(val)
    if not math.isfinite(f):
        raise CalculatorError(f"Non-finite {context}: {f}")
    return f


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    num = _ensure_finite(numerator, "numerator")
    den = _ensure_finite(denominator, "denominator")
    if abs(den) < 1e-12:
        return _ensure_finite(default, "default")
    return _ensure_finite(num / den, "safe_div")


def yoy(current: float, previous: float) -> float:
    cur = _ensure_finite(current, "current")
    prev = _ensure_finite(previous, "previous")
    if abs(prev) < 1e-12:
        raise CalculatorError("YoY division by zero (previous == 0)")
    return _ensure_finite((cur - prev) / abs(prev), "yoy")


def pct_change(new_val: float, old_val: float) -> float:
    return yoy(new_val, old_val)


def cagr(end_val: float, start_val: float, years: float) -> float:
    end_f = _ensure_finite(end_val, "end_val")
    start_f = _ensure_finite(start_val, "start_val")
    yrs_f = _ensure_finite(years, "years")
    if start_f <= 0 or end_f <= 0 or yrs_f <= 0:
        raise CalculatorError("CAGR requires positive start_val, end_val, and years")
    return _ensure_finite((end_f / start_f) ** (1.0 / yrs_f) - 1.0, "cagr")


def margin(profit: float, revenue: float) -> float:
    p = _ensure_finite(profit, "profit")
    r = _ensure_finite(revenue, "revenue")
    if abs(r) < 1e-12:
        raise CalculatorError("Margin division by zero (revenue == 0)")
    return _ensure_finite(p / r, "margin")


def bps_diff(new_ratio: float, old_ratio: float) -> float:
    return _ensure_finite((_ensure_finite(new_ratio) - _ensure_finite(old_ratio)) * 10000.0)


def roe(net_income: float, equity: float) -> float:
    return margin(net_income, equity)


def roa(net_income: float, total_assets: float) -> float:
    return margin(net_income, total_assets)


def ev_ebitda(enterprise_value: float, ebitda: float) -> float:
    return margin(enterprise_value, ebitda)


def debt_to_equity(total_debt: float, equity: float) -> float:
    return margin(total_debt, equity)


def casa_ratio(demand_deposits: float, total_deposits: float) -> float:
    return margin(demand_deposits, total_deposits)


def npl_ratio(group3: float, group4: float, group5: float, total_loans: float) -> float:
    npl = _ensure_finite(group3) + _ensure_finite(group4) + _ensure_finite(group5)
    return margin(npl, total_loans)


def _safe_sum(*args: float) -> float:
    return _ensure_finite(sum(_ensure_finite(a) for a in args), "sum")


def _safe_min(*args: float) -> float:
    if not args:
        raise CalculatorError("min() requires at least 1 argument")
    return _ensure_finite(min(_ensure_finite(a) for a in args), "min")


def _safe_max(*args: float) -> float:
    if not args:
        raise CalculatorError("max() requires at least 1 argument")
    return _ensure_finite(max(_ensure_finite(a) for a in args), "max")


def _safe_round(val: float, ndigits: float = 0) -> float:
    v = _ensure_finite(val)
    n = int(_ensure_finite(ndigits))
    if abs(n) > 12:
        raise UnsafeFormulaError(f"round() ndigits out of bounds: {n}")
    return _ensure_finite(round(v, n))


SAFE_FUNCTIONS: dict[str, Callable[..., float]] = {
    "abs": lambda x: _ensure_finite(abs(_ensure_finite(x))),
    "min": _safe_min,
    "max": _safe_max,
    "round": _safe_round,
    "sum": _safe_sum,
    "safe_div": safe_div,
    "cagr": cagr,
    "yoy": yoy,
    "pct_change": pct_change,
    "margin": margin,
    "bps_diff": bps_diff,
    "roe": roe,
    "roa": roa,
    "ev_ebitda": ev_ebitda,
    "debt_to_equity": debt_to_equity,
    "casa_ratio": casa_ratio,
    "npl_ratio": npl_ratio,
}

_BINARY_OPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
}


def _eval_ast_node(node: ast.AST, env: dict[str, float], depth: int = 0) -> float:
    if depth > MAX_AST_DEPTH:
        raise UnsafeFormulaError(f"Formula AST exceeds max depth {MAX_AST_DEPTH}")

    if isinstance(node, ast.Expression):
        return _eval_ast_node(node.body, env, depth + 1)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise UnsafeFormulaError(f"Disallowed constant type: {type(node.value).__name__}")
        return _ensure_finite(float(node.value), "constant")

    if isinstance(node, ast.Name):
        if node.id.startswith("_"):
            raise UnsafeFormulaError(f"Disallowed dunder/private identifier: {node.id}")
        if node.id not in env:
            raise UnsafeFormulaError(f"Unknown or unprovided variable: {node.id}")
        return _ensure_finite(env[node.id], f"input:{node.id}")

    if isinstance(node, ast.UnaryOp):
        operand = _eval_ast_node(node.operand, env, depth + 1)
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.USub):
            return _ensure_finite(-operand)
        raise UnsafeFormulaError(f"Disallowed unary operator: {type(node.op).__name__}")

    if isinstance(node, ast.BinOp):
        left = _eval_ast_node(node.left, env, depth + 1)
        right = _eval_ast_node(node.right, env, depth + 1)
        if isinstance(node.op, ast.Div):
            if abs(right) < 1e-12:
                raise CalculatorError("Division by zero in formula")
            return _ensure_finite(left / right, "division")
        if isinstance(node.op, ast.Pow):
            if abs(right) > MAX_EXPONENT:
                raise UnsafeFormulaError(f"Exponent {right} exceeds safe bound {MAX_EXPONENT}")
            try:
                res = left**right
            except (OverflowError, ZeroDivisionError, ValueError) as exc:
                raise CalculatorError(f"Power evaluation failed: {exc}") from exc
            if isinstance(res, complex):
                raise CalculatorError("Complex numbers are not allowed")
            return _ensure_finite(res, "power")
        op_fn = _BINARY_OPS.get(type(node.op))
        if op_fn is None:
            raise UnsafeFormulaError(f"Disallowed binary operator: {type(node.op).__name__}")
        return _ensure_finite(op_fn(left, right), "binary_op")

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise UnsafeFormulaError("Only direct whitelisted function calls are allowed")
        fn_name = node.func.id
        if fn_name not in SAFE_FUNCTIONS:
            raise UnsafeFormulaError(f"Function '{fn_name}' is not in calculator whitelist")
        if node.keywords:
            raise UnsafeFormulaError("Keyword arguments are not supported in sandboxed formulas")
        args: list[float] = []
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                raise UnsafeFormulaError("Starred argument unpacking is disallowed")
            args.append(_eval_ast_node(arg, env, depth + 1))
        return _ensure_finite(SAFE_FUNCTIONS[fn_name](*args), f"call:{fn_name}")

    raise UnsafeFormulaError(f"Disallowed AST node: {type(node).__name__}")


def _coerce_input(raw: CalcInput | dict[str, Any] | int | float) -> CalcInput:
    if isinstance(raw, CalcInput):
        return raw
    if isinstance(raw, dict):
        if "value" not in raw:
            raise CalculatorError(f"Input dictionary missing 'value': {raw!r}")
        return CalcInput(
            value=_ensure_finite(raw["value"], "input_value"),
            table_id=raw.get("table_id"),
            row=raw.get("row"),
            col=raw.get("col"),
            currency=raw.get("currency"),
            unit_raw=raw.get("unit_raw"),
            unit_multiplier=float(raw.get("unit_multiplier") or 1.0),
            verification_status=str(raw.get("verification_status") or "VERIFIED"),
        )
    return CalcInput(value=_ensure_finite(raw, "input_value"))


FormatStyle = Literal["percent", "signed_percent", "bps", "number", "ratio"]


def format_calc_result(value: float, style: FormatStyle = "number", decimals: int = 2) -> str:
    if style == "percent":
        return f"{value * 100:.{decimals}f}%"
    if style == "signed_percent":
        return f"{value * 100:+.{decimals}f}%"
    if style == "bps":
        return f"{value:+.0f} bps"
    if style == "ratio":
        return f"{value:.{decimals}f}x"
    return f"{value:,.{decimals}f}"


class FinancialCalculator:
    """Sandboxed deterministic financial calculator emitting `CalculatorTrace`."""

    def __init__(self) -> None:
        self._counter = 0

    def evaluate(
        self,
        *,
        formula: str,
        inputs: dict[str, CalcInput | dict[str, Any] | int | float],
        description: str,
        calc_id: str | None = None,
        format_style: FormatStyle = "number",
        decimals: int = 2,
        precision: int = 6,
        normalize_to_base_units: bool = False,
        allow_mixed_currencies: bool = False,
    ) -> CalculatorTrace:
        if not formula or not formula.strip():
            raise UnsafeFormulaError("Empty formula")

        coerced: dict[str, CalcInput] = {}
        currencies: set[str] = set()
        multipliers: set[float] = set()

        for var_name, raw_val in inputs.items():
            if not var_name.isidentifier() or var_name.startswith("_"):
                raise UnsafeFormulaError(f"Invalid input variable name: {var_name!r}")
            ci = _coerce_input(raw_val)
            if ci.verification_status == "TABLE_UNVERIFIED":
                raise UnverifiedTableInputError(
                    f"Input '{var_name}' comes from TABLE_UNVERIFIED source ({ci.table_id})"
                )
            if ci.currency:
                currencies.add(ci.currency.upper())
            multipliers.add(ci.unit_multiplier)
            coerced[var_name] = ci

        if not allow_mixed_currencies and len(currencies) > 1:
            raise CurrencyMismatchError(
                f"Cannot combine inputs across currencies {sorted(currencies)} without explicit FX conversion"
            )

        mixed_units = len(multipliers) > 1
        use_base_scale = normalize_to_base_units or mixed_units
        env = {
            k: (ci.value * ci.unit_multiplier if use_base_scale else ci.value)
            for k, ci in coerced.items()
        }

        try:
            tree = ast.parse(formula.strip(), mode="eval")
        except SyntaxError as exc:
            raise UnsafeFormulaError(f"Invalid formula syntax: {exc}") from exc

        raw_result = _eval_ast_node(tree, env, depth=0)
        rounded = round(raw_result, precision)
        self._counter += 1
        cid = calc_id or f"CALC-{self._counter}"

        provenance_inputs = {k: ci.to_provenance_dict() for k, ci in coerced.items()}
        common_mult = 1.0 if use_base_scale else next(iter(multipliers), 1.0)
        common_curr = next(iter(currencies), None) if len(currencies) == 1 else None

        return CalculatorTrace(
            calc_id=cid,
            formula=formula.strip(),
            description=description,
            inputs=provenance_inputs,
            result=rounded,
            formatted=format_calc_result(rounded, style=format_style, decimals=decimals),
            unit_multiplier=common_mult,
            currency=common_curr,
        )
