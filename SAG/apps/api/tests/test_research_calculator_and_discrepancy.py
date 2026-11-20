"""Unit tests for Worker Track 2: Sandboxed Financial Calculator & Deterministic Discrepancy Detector."""

from __future__ import annotations

import pytest

from sag_api.research import (
    CalcInput,
    CalculatorError,
    CurrencyMismatchError,
    FinancialCalculator,
    FinancialTrendSnapshot,
    UnsafeFormulaError,
    UnverifiedTableInputError,
    detect_discrepancies,
)

# ── Sandboxed Financial Calculator Tests ─────────────────────────────────────


def test_calculator_trace_and_financial_functions():
    calc = FinancialCalculator()

    # YoY revenue growth with full provenance
    tr_yoy = calc.evaluate(
        formula="(rev_2025 - rev_2024) / rev_2024",
        inputs={
            "rev_2024": CalcInput(
                value=1000.0,
                table_id="tbl-1",
                row="Doanh thu thuần",
                col="2024",
                currency="VND",
                unit_raw="tỷ đồng",
                unit_multiplier=1e9,
            ),
            "rev_2025": CalcInput(
                value=1200.0,
                table_id="tbl-1",
                row="Doanh thu thuần",
                col="2025",
                currency="VND",
                unit_raw="tỷ đồng",
                unit_multiplier=1e9,
            ),
        },
        description="Tăng trưởng doanh thu YoY",
        format_style="signed_percent",
    )
    assert tr_yoy.calc_id == "CALC-1"
    assert tr_yoy.result == pytest.approx(0.2)
    assert tr_yoy.formatted == "+20.00%"
    assert tr_yoy.inputs["rev_2024"]["table_id"] == "tbl-1"

    # Whitelisted financial helpers
    assert calc.evaluate(
        formula="cagr(end_v, start_v, yrs)",
        inputs={"end_v": 161.051, "start_v": 100.0, "yrs": 5},
        description="CAGR 5Y",
        format_style="percent",
    ).result == pytest.approx(0.10, abs=1e-4)

    assert calc.evaluate(
        formula="npl_ratio(g3, g4, g5, total)",
        inputs={"g3": 10.0, "g4": 15.0, "g5": 25.0, "total": 2500.0},
        description="NPL ratio",
    ).result == pytest.approx(0.02)

    assert calc.evaluate(
        formula="casa_ratio(demand, total)",
        inputs={"demand": 300.0, "total": 1000.0},
        description="CASA",
    ).result == pytest.approx(0.3)

    assert calc.evaluate(
        formula="safe_div(a, b, 0.0)",
        inputs={"a": 100.0, "b": 0.0},
        description="Safe zero div",
    ).result == 0.0

    assert calc.evaluate(
        formula="bps_diff(m_new, m_old)",
        inputs={"m_new": 0.1406, "m_old": 0.1529},
        description="Margin bps delta",
        format_style="bps",
    ).formatted == "-123 bps"

    assert calc.evaluate(
        formula="round(max(abs(a), min(b, c)), 2)",
        inputs={"a": -3.14159, "b": 2.0, "c": 5.0},
        description="Builtin whitelist",
    ).result == pytest.approx(3.14)


def test_calculator_mixed_units_and_currency_isolation():
    calc = FinancialCalculator()

    # Mixed VND units (tỷ đồng vs triệu đồng) MUST auto-normalize to base scale
    tr_mixed = calc.evaluate(
        formula="rev_ty - rev_trieu",
        inputs={
            "rev_ty": CalcInput(value=10.0, currency="VND", unit_raw="tỷ đồng", unit_multiplier=1e9),
            "rev_trieu": CalcInput(value=2500.0, currency="VND", unit_raw="triệu đồng", unit_multiplier=1e6),
        },
        description="Chênh lệch đơn vị hỗn hợp",
    )
    assert tr_mixed.result == pytest.approx(7_500_000_000.0)

    # Mixing VND and USD MUST fail closed
    with pytest.raises(CurrencyMismatchError):
        calc.evaluate(
            formula="vnd_val + usd_val",
            inputs={
                "vnd_val": CalcInput(value=100.0, currency="VND", unit_multiplier=1e9),
                "usd_val": CalcInput(value=5.0, currency="USD", unit_multiplier=1e6),
            },
            description="Unsafe currency mix",
        )


def test_calculator_blocks_unverified_table_and_unsafe_ast():
    calc = FinancialCalculator()

    # TABLE_UNVERIFIED input MUST fail closed
    with pytest.raises(UnverifiedTableInputError):
        calc.evaluate(
            formula="a / b",
            inputs={
                "a": CalcInput(value=100.0, table_id="tbl-bad", verification_status="TABLE_UNVERIFIED"),
                "b": CalcInput(value=200.0),
            },
            description="Unverified input",
        )

    # Unsafe formulas MUST fail closed (zero eval/exec)
    unsafe_expressions = [
        "__import__('os').system('echo pwned')",
        "().__class__.__bases__[0]",
        "a.real",
        "a[0]",
        "(lambda x: x + 1)(a)",
        "[x for x in (1, 2)]",
        "open('secret.txt').read()",
        "unknown_var + 1",
        "2 ** 100",
        "abs(*[1, 2])",
    ]
    for expr in unsafe_expressions:
        with pytest.raises(UnsafeFormulaError):
            calc.evaluate(
                formula=expr,
                inputs={"a": 10.0},
                description="Security test",
            )

    # Division by zero in `/` operator MUST fail closed
    with pytest.raises(CalculatorError):
        calc.evaluate(formula="a / b", inputs={"a": 10.0, "b": 0.0}, description="Zero div")


# ── Deterministic Quant-vs-Narrative Discrepancy Detector Tests ──────────────


def test_discrepancy_detector_numeric_mismatch_and_unit_alignment():
    narrative = (
        "<!-- PAGE 12 -->\n"
        "Năm 2025, Doanh thu thuần đạt 6.000 tỷ đồng và Lợi nhuận sau thuế đạt 1.100 tỷ đồng."
    )
    verified_table = {
        "table_id": "tbl-s07",
        "sub_doc_slot": "S07_REV_COGS_DETAIL",
        "page_from": 168,
        "currency": "VND",
        "unit_raw": "triệu đồng",
        "unit_multiplier": 1e6,
        "verification_status": "VERIFIED",
        "rows": [
            {"label": "Doanh thu thuần", "values": [5_156_200.0, 1_712_400.0]},
            {"label": "Lợi nhuận sau thuế", "values": [1_095_000.0, 950_000.0]},
        ],
    }
    alerts = detect_discrepancies(
        narrative_text=narrative,
        narrative_slot="S01",
        hydrated_tables=[verified_table],
    )
    # Doanh thu thuần: 6,000 tỷ vs 5,156.2 tỷ -> delta ~16.36% (HIGH)
    # LNST: 1,100 tỷ vs 1,095 tỷ -> delta ~0.46% (< 1.5% tolerance -> NOT flagged)
    assert len(alerts) == 1
    assert alerts[0]["alert_id"] == "DISC-1"
    assert alerts[0]["severity"] == "HIGH"
    assert alerts[0]["metric"] == "Doanh thu thuần"
    assert alerts[0]["delta_pct"] == pytest.approx(16.36, abs=0.05)
    assert alerts[0]["citations"] == ["S01·P12", "S07·P168"]


def test_discrepancy_detector_ignores_unverified_tables_and_flags_margin_divergence():
    narrative = (
        "<!-- PAGE 5 -->\n"
        "Biên lợi nhuận gộp cải thiện mạnh nhờ mở rộng quy mô. "
        "Kết quả kinh doanh tăng trưởng vượt trội."
    )
    unverified_table = {
        "table_id": "tbl-bad",
        "sub_doc_slot": "S07_REV_COGS_DETAIL",
        "page_from": 40,
        "unit_multiplier": 1e9,
        "verification_status": "TABLE_UNVERIFIED",
        "rows": [{"label": "Doanh thu thuần", "values": [1.0]}],
    }
    trend = FinancialTrendSnapshot(
        fiscal_year=2025,
        gpm_yoy_bps=-617.0,
        cfo_value=-350.0,
        npat_value=420.0,
        plan_target_pbt=1000.0,
        actual_pbt=1298.0,
        operating_lease_yoy_pct=280.0,
        citations=["S07·P168", "CoreDB·FY2025"],
    )
    alerts = detect_discrepancies(
        narrative_text=narrative,
        narrative_slot="S01",
        hydrated_tables=[unverified_table],
        trend_snapshot=trend,
    )
    metrics = {a["metric"]: a for a in alerts}
    assert "Biên lợi nhuận gộp" in metrics
    assert metrics["Biên lợi nhuận gộp"]["severity"] == "HIGH"
    assert "Chất lượng dòng tiền (CFO vs LNST)" in metrics
    assert "Thực hiện vs Kế hoạch LNTT" in metrics
    assert "Cam kết thuê hoạt động ngoại bảng" in metrics
