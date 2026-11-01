"""Backfill must derive from its own DB, share ETL normalization, and reject stale reviews."""

from copy import deepcopy
from datetime import date
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from app.infrastructure.data_pipelines import financial_etl_cafef as cafef
from app.infrastructure.data_pipelines import financial_etl_alphastock as alpha

path = Path(__file__).resolve().parents[2] / "scripts/backfill_financials.py"
spec = importlib.util.spec_from_file_location("financial_backfill_command", path)
command = importlib.util.module_from_spec(spec)
spec.loader.exec_module(command)


def test_preview_repairs_target_rows_without_provider_or_writes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Preview attempted provider request or DB write")

    monkeypatch.setattr(cafef._CLIENT, "get", forbidden)
    pe = date(2026, 6, 30)
    inc = {
        "XIII. Lợi nhuận sau thuế": 10,
        "lợi nhuận sau thuế": 8,
        "18_loi_nhuan_sau_thue": 8,
    }
    original = deepcopy(inc)
    statements = [
        (pe, "quarterly", "IS", inc, None, "cafef"),
        (
            pe,
            "quarterly",
            "BS",
            {"Tổng tài sản": 1000, "Vốn chủ sở hữu": 100, "Nợ phải trả": 900},
            None,
            "cafef",
        ),
    ]

    class Cursor:
        def execute(self, query, params=None):
            assert query.startswith("SELECT"), query
            self.query = query

        def fetchall(self):
            if "SELECT DISTINCT" in self.query:
                return [("TEST",)]
            if "FROM financial_statements" in self.query:
                return statements
            return [(pe, "quarterly", *([None] * 12), None)]

    changes, summary = command.plan(Cursor())
    assert inc == original
    assert summary["statement_updates"] == summary["ratio_updates"] == 1
    fixed = next(
        row["after"] for row in changes if row["table"] == "financial_statements"
    )
    assert fixed["lợi nhuận sau thuế"] == fixed["18_loi_nhuan_sau_thue"] == 10
    assert cafef.normalize_statement("TEST", pe, "IS", "quarterly", fixed) == fixed
    ratio = next(row for row in changes if row["table"] == "financial_ratios")
    assert ratio["after"][command.COLS.index("roe")] == pytest.approx(0.1)
    assert ratio["after"][command.COLS.index("debt_equity")] == 9


@pytest.mark.parametrize("field", ["target", "source_code_sha256", "summary"])
def test_review_from_other_database_code_or_data_is_rejected(field):
    target = {"database": "prod_clone", "server_system_identifier": "123"}
    hashes = {"calculator": "original"}
    summary = {"input_sha256": "rows", "plan_sha256": "derived"}
    review = dict(
        status="PREVIEW", target=target, source_code_sha256=hashes, summary=summary
    )
    command.validate_review(review, target, hashes, summary)
    review = deepcopy(review)
    review[field]["unexpected_change"] = "different"
    with pytest.raises(ValueError, match=field):
        command.validate_review(review, target, hashes, summary)


def test_review_from_different_financial_schema_is_rejected():
    target = {
        "database": "clone",
        "statement_schema": "public",
        "ratio_schema": "public",
    }
    review = dict(status="PREVIEW", target=target, source_code_sha256={}, summary={})
    with pytest.raises(ValueError, match="target"):
        command.validate_review(
            review, {**target, "ratio_schema": "financial_current"}, {}, {}
        )


def test_alias_repair_preserves_final_cfo_and_is_idempotent():
    stage = "Lưu chuyển tiền thuần từ hoạt động kinh doanh trước những thay đổi về tài sản và vốn lưu động"
    raw = {
        stage: 9,
        "I. Lưu chuyển tiền thuần từ hoạt động kinh doanh": -42,
        "Lưu chuyển tiền thuần từ hoạt động kinh doanh": 9,
    }
    fixed = cafef.normalize_statement("TEST", date(2026, 6, 30), "CF", "quarterly", raw)
    assert raw["Lưu chuyển tiền thuần từ hoạt động kinh doanh"] == 9
    assert fixed["Lưu chuyển tiền thuần từ hoạt động kinh doanh"] == -42
    assert fixed[stage] == 9
    assert (
        cafef.normalize_statement("TEST", date(2026, 6, 30), "CF", "quarterly", fixed)
        == fixed
    )


def test_ratio_units_use_source_schema_for_backfill_and_future_ingestion():
    pe = date(2026, 3, 31)
    base = [
        (
            pe,
            "quarterly",
            "BS",
            {"Tổng tài sản": 1000, "Vốn chủ sở hữu": 100},
            None,
            "alphastock",
        ),
        (pe, "quarterly", "IS", {"Lợi nhuận sau thuế": 5}, None, "alphastock"),
    ]
    legacy = {"roe": 4.87, "roa": 0.31, "roe_trailling": 21.85, "roa_trailling": 1.38}
    modern = {"rt_prt_roe": 0.217608, "ROE %": 0.217608}
    for data, expected in [(legacy, 0.05), (modern, 0.05)]:
        rows = base + [(pe, "quarterly", "ratios", data, None, "alphastock")]
        assert cafef.derive_financial_ratios("CTG", rows)[0][5] == pytest.approx(
            expected
        )

        class Cursor:
            def execute(self, query, params):
                if query.lstrip().startswith("INSERT"):
                    self.values = params

            def fetchall(self):
                return rows

        cur = Cursor()
        cafef._store_derived_ratios("CTG", cur)
        assert cur.values[5] == pytest.approx(expected)
    assert legacy["roe"] == 4.87 and modern["ROE %"] == 0.217608
    assert command.fields.normalize_statement_ratio_units(legacy, "alphastock")[
        "roe"
    ] == pytest.approx(0.0487)
    missing_ni = [
        base[0],
        (pe, "quarterly", "IS", {"Doanh thu thuần": 100}, None, "alphastock"),
        (pe, "quarterly", "ratios", legacy, None, "alphastock"),
    ]
    assert cafef.derive_financial_ratios("CTG", missing_ni)[0][5] == pytest.approx(
        0.0487
    )
    missing_ni[-1] = (pe, "quarterly", "ratios", modern, None, "alphastock")
    assert cafef.derive_financial_ratios("CTG", missing_ni)[0][5] == pytest.approx(
        0.217608
    )


def test_alphastock_net_revenue_and_total_profit_reproduce_baseline_margins():
    pe = date(2026, 9, 30)
    rows = [
        (
            pe,
            "quarterly",
            "BS",
            {"Tổng tài sản": 1000, "Vốn chủ sở hữu": 100},
            None,
            "alphastock",
        ),
        (
            pe,
            "quarterly",
            "IS",
            {
                "3_net_revenue": 15820338560,
                "5_gross_profit": 1323356649,
                "18_net_profit_after_tax": 218360500,
                "profit_after_tax_for_shareholders_of_parent_company": 1,
            },
            None,
            "alphastock",
        ),
    ]
    result = cafef.derive_financial_ratios("AAM", rows)[0]
    assert result[9] == pytest.approx(0.0836490726150427)
    assert result[10] == pytest.approx(0.01380251751072513)


def test_modern_alphastock_pe_is_used_for_ratios():
    pe = date(2026, 3, 31)
    modern = {"rt_value_pe": 9.001927, "‣ P/E cơ bản": 9.001927}
    rows = [
        (
            pe,
            "quarterly",
            "BS",
            {"total_assets": 1000, "equity": 100},
            None,
            "alphastock",
        ),
        (pe, "quarterly", "IS", {"net_income": 5}, None, "alphastock"),
        (pe, "quarterly", "ratios", modern, None, "alphastock"),
    ]
    assert cafef.derive_financial_ratios("HPG", rows)[0][3] == pytest.approx(9.001927)


def test_alphastock_total_profit_and_equity_reproduce_baseline_roe():
    pe = date(2026, 9, 30)
    rows = [
        (
            pe,
            "quarterly",
            "BS",
            {
                "total_assets": 12441449364994,
                "b_owner_s_equity": 2675480317959,
                "i_owner_s_equity": 2675480317959,
            },
            None,
            "alphastock",
        ),
        (
            pe,
            "quarterly",
            "IS",
            {
                "34_profit_after_tax": 84364825830,
                "36_profit_after_tax_for_shareholders_of_the_parent_company": 1,
            },
            None,
            "alphastock",
        ),
    ]
    result = cafef.derive_financial_ratios("MIG", rows)[0]
    assert result[5] == pytest.approx(0.03153259071416306)
    assert result[6] == pytest.approx(0.006780948373054821)


@pytest.mark.parametrize(
    "revenue_key,revenue,profit,expected",
    [
        ("i_net_interest_income", 609777000000, 279115000000, 0.45773290891588236),
        (
            "7_total_net_revenue_from_insurance_business",
            1144993564820,
            84364825830,
            0.07368148470184867,
        ),
    ],
)
def test_provider_bank_and_insurance_revenue_reproduce_baseline_net_margin(
    revenue_key, revenue, profit, expected
):
    pe = date(2026, 9, 30)
    rows = [
        (
            pe,
            "quarterly",
            "BS",
            {"Tổng tài sản": 1000, "Vốn chủ sở hữu": 100},
            None,
            "alphastock",
        ),
        (
            pe,
            "quarterly",
            "IS",
            {revenue_key: revenue, "34_profit_after_tax": profit},
            None,
            "alphastock",
        ),
    ]
    assert cafef.derive_financial_ratios("TEST", rows)[0][10] == pytest.approx(expected)


def test_cafef_reported_percent_uses_baseline_basis_and_fraction_fallback():
    pe = date(2022, 12, 31)
    bs = {"Tổng tài sản": 1808429764000000, "Vốn chủ sở hữu": 108167657000000}
    income = {"Lợi nhuận sau thuế": 16834994000000}
    ratios = {"ROE": 15.51, "ROA": 0.93}
    rows = [
        (pe, "yearly", "BS", bs, None, "cafef"),
        (pe, "yearly", "IS", income, None, "cafef"),
        (pe, "yearly", "ratios", ratios, None, "cafef"),
    ]
    result = cafef.derive_financial_ratios("CTG", rows)[0]
    assert result[5] == pytest.approx(0.15563796486781625)
    assert result[6] == pytest.approx(0.009309177682833139)
    rows[1] = (pe, "yearly", "IS", {"Doanh thu thuần": 1}, None, "cafef")
    result = cafef.derive_financial_ratios("CTG", rows)[0]
    assert result[5] == pytest.approx(0.1551)
    assert result[6] == pytest.approx(0.0093)


def test_cafef_profit_margins_convert_all_aliases_and_keep_baseline_basis():
    pe = date(2024, 12, 31)
    ratios = {
        "GOS": 2.84,
        "tỷ suất lợi nhuận gộp biên": 2.84,
        "ROS": -4.1,
        "tỷ suất sinh lợi trên doanh thu thuần": -4.1,
    }
    rows = [
        (pe, "yearly", "BS", {"Tổng tài sản": 1000}, None, "cafef"),
        (
            pe,
            "yearly",
            "IS",
            {"Doanh thu thuần": 100, "gross_profit": 12, "Lợi nhuận sau thuế": 3},
            None,
            "cafef",
        ),
        (pe, "yearly", "ratios", ratios, None, "cafef"),
    ]
    result = cafef.derive_financial_ratios("AAM", rows)[0]
    assert result[9:11] == pytest.approx((0.12, 0.03))
    rows[1] = (pe, "yearly", "IS", {"unrelated": 1}, None, "cafef")
    result = cafef.derive_financial_ratios("AAM", rows)[0]
    assert result[9:11] == pytest.approx((0.0284, -0.041))
    with pytest.raises(command.fields.UnknownFinancialRatioSchema):
        command.fields.normalize_statement_ratio_units({"roe": 4.87}, "cafef")


def test_unknown_alphastock_rate_schema_stops_calculation_and_ingestion(monkeypatch):
    with pytest.raises(command.fields.UnknownFinancialRatioSchema):
        command.fields.normalize_statement_ratio_units({"roe": 4.87}, "alphastock")
    monkeypatch.setattr(alpha, "_get_workspace", lambda symbol: {"statements": {}})

    def fail(symbol, cur, **kwargs):
        raise command.fields.UnknownFinancialRatioSchema("unknown schema")

    monkeypatch.setattr(alpha, "_store_derived_ratios", fail)
    with pytest.raises(command.fields.UnknownFinancialRatioSchema):
        alpha.fetch_and_store_financials("CTG", object())

    class NoWrite:
        def execute(self, *args):
            raise AssertionError("unknown schema reached DB write")

    with pytest.raises(command.fields.UnknownFinancialRatioSchema):
        alpha._upsert(
            NoWrite(),
            "CTG",
            date(2026, 3, 31),
            "ratios",
            "quarterly",
            {"roe": 4.87},
            None,
        )

    class Cursor:
        def execute(self, *args):
            pass

        def fetchone(self):
            return None

    monkeypatch.setattr(cafef, "_fetch_cafef_endpoint", lambda *a, **k: None)
    monkeypatch.setattr(cafef, "_store_derived_ratios", fail)
    with pytest.raises(command.fields.UnknownFinancialRatioSchema):
        cafef.fetch_and_store_financials("CTG", Cursor())


@pytest.mark.parametrize("provider", ["alphastock", "cafef"])
def test_ingestion_propagates_any_ratio_rebuild_failure(monkeypatch, provider):
    def fail(*args, **kwargs):
        raise RuntimeError("ratio rebuild failed")

    if provider == "alphastock":
        monkeypatch.setattr(alpha, "_get_workspace", lambda symbol: {"statements": {}})
        monkeypatch.setattr(alpha, "_store_derived_ratios", fail)
        run = lambda: alpha.fetch_and_store_financials("CTG", object())
    else:

        class Cursor:
            def execute(self, *args):
                pass

            def fetchone(self):
                return None

        monkeypatch.setattr(cafef, "_fetch_cafef_endpoint", lambda *a, **k: None)
        monkeypatch.setattr(cafef, "_store_derived_ratios", fail)
        run = lambda: cafef.fetch_and_store_financials("CTG", Cursor())
    with pytest.raises(RuntimeError, match="ratio rebuild failed"):
        run()


def test_full_financial_refresh_rebuilds_after_both_sources(monkeypatch):
    calls = []

    class Cursor:
        def execute(self, query, params=None):
            pass

        def fetchall(self):
            return [("TEST",)]

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(alpha.psycopg2, "connect", lambda *a, **k: Connection())
    monkeypatch.setattr(
        alpha,
        "fetch_and_store_financials",
        lambda *a, **k: calls.append("alpha") or {"rows": 1},
    )
    monkeypatch.setattr(
        alpha, "_fill_missing_with_cafef", lambda *a, **k: calls.append("cafef") or 0
    )
    monkeypatch.setattr(
        alpha, "_store_derived_ratios", lambda *a, **k: calls.append("derive")
    )
    monkeypatch.setattr(
        cafef,
        "_fetch_vci_missing_quarter",
        lambda *a, **k: calls.append("vci") or {"rows": 0},
    )
    result = alpha.refresh_all()
    assert calls == ["alpha", "cafef", "derive", "vci"]
    assert result["errors"] == 0


def test_incremental_backfill_forwards_history_repair_to_cafef(monkeypatch):
    calls = []

    class Cursor:
        def execute(self, query, params=None):
            self.query = query

        def fetchall(self):
            return [("TEST",)] if "SELECT symbol FROM stocks" in self.query else []

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(alpha.psycopg2, "connect", lambda *a, **k: Connection())
    monkeypatch.setattr(
        alpha,
        "fetch_and_store_financials",
        lambda *a, **k: calls.append(("alpha", k)) or {"rows": 1},
    )
    monkeypatch.setattr(alpha, "_has_complete_latest_quarter", lambda *a: False)
    monkeypatch.setattr(
        alpha,
        "_fill_missing_with_cafef",
        lambda *a, **k: calls.append(("cafef", k)) or 0,
    )
    assert alpha.refresh_incremental(backfill=True)["errors"] == 0
    assert calls == [("alpha", {"backfill": True}), ("cafef", {"backfill": True})]


def test_cafef_full_refresh_enables_historical_statement_repairs(monkeypatch):
    calls = []

    class Cursor:
        def execute(self, query, params=None):
            pass

        def fetchall(self):
            return [("TEST",)]

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(cafef.psycopg2, "connect", lambda *a, **k: Connection())
    monkeypatch.setattr(
        cafef,
        "fetch_and_store_financials",
        lambda *a, **k: calls.append(k.get("backfill")) or {"rows": 1},
    )

    result = cafef.refresh_all(limit=1)

    assert calls == [True]
    assert result["errors"] == 0


@pytest.mark.parametrize("should_fail", [False, True])
def test_vci_new_statement_rebuilds_ratios_in_same_savepoint(monkeypatch, should_fail):
    calls = []

    class Cursor:
        rowcount = 0

        def execute(self, query, params=None):
            calls.append(query.strip().split()[0])
            self.rowcount = 1 if query.lstrip().startswith("INSERT") else 0

        def fetchall(self):
            return [("TEST", "IS")]

    class Frame:
        empty = False
        columns = ["2026-Q3"]

        def to_dict(self, orient):
            return [{"item_id": "net_revenue", "2026-Q3": 100}]

    class Finance:
        def __init__(self, **kwargs):
            pass

        def income_statement(self):
            return Frame()

    for name in ("vnstock", "vnstock.api", "vnstock.api.financial"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["vnstock.api.financial"].Finance = Finance
    monkeypatch.setattr(
        cafef, "_latest_completed_quarter", lambda today: (date(2026, 9, 30), "2026-Q3")
    )

    def derive(symbol, cur, **kwargs):
        assert kwargs.get("normalize_statements") is True
        calls.append("DERIVE")
        if should_fail:
            raise ValueError("rebuild failed")

    monkeypatch.setattr(cafef, "_store_derived_ratios", derive)
    result = cafef._fetch_vci_missing_quarter(Cursor(), normalize_statements=True)
    assert calls.index("INSERT") < calls.index("DERIVE") < calls.index("RELEASE")
    assert result["rows"] == (0 if should_fail else 1)
    assert result["errors"] == (1 if should_fail else 0)
    assert ("ROLLBACK" in calls) == should_fail


def test_ingestion_applies_existing_override_before_empty_row_filter(monkeypatch):
    pe = date(2026, 6, 30)
    monkeypatch.setitem(
        cafef._ISSUER_BS_OVERRIDES, ("TEST", pe), {"TỔNG CỘNG TÀI SẢN": 100}
    )

    class Cursor:
        def execute(self, query, values):
            self.values = values

    cur = Cursor()
    cafef._upsert(cur, "TEST", pe, "BS", "quarterly", {"TỔNG CỘNG TÀI SẢN": 0}, None)
    assert cur.values[4].adapted["TỔNG CỘNG TÀI SẢN"] == 100


@pytest.mark.parametrize("frequency,expected", [("quarterly", 100), ("yearly", 200)])
def test_alphastock_new_ingestion_uses_same_normalizer_as_backfill(
    monkeypatch, frequency, expected
):
    pe = date(2026, 6, 30)
    monkeypatch.setitem(
        alpha._ISSUER_BS_OVERRIDES, ("TEST", pe), {"TỔNG CỘNG TÀI SẢN": 100}
    )
    monkeypatch.setitem(
        alpha._ISSUER_BS_YEARLY_OVERRIDES, ("TEST", pe), {"TỔNG CỘNG TÀI SẢN": 200}
    )
    data = {"TỔNG CỘNG TÀI SẢN": 0}

    class Cursor:
        def execute(self, query, values):
            self.values = values

    cur = Cursor()
    alpha._upsert(cur, "TEST", pe, "BS", frequency, data, pe)
    assert cur.values[4].adapted == alpha.normalize_statement(
        "TEST", pe, "BS", frequency, data
    )
    assert cur.values[4].adapted["TỔNG CỘNG TÀI SẢN"] == expected
    assert cur.values[3] == frequency and cur.values[6] == pe
    assert data["TỔNG CỘNG TÀI SẢN"] == 0


def test_cafef_new_ingestion_and_stored_statement_repair_agree():
    pe = date(2026, 6, 30)
    data = {
        "XIII. Lợi nhuận sau thuế": 10,
        "lợi nhuận sau thuế": 8,
        "18_loi_nhuan_sau_thue": 8,
    }

    class Cursor:
        def execute(self, query, values):
            self.values = values

    cur = Cursor()
    cafef._upsert(cur, "TEST", pe, "IS", "quarterly", data, pe)
    assert cur.values[4].adapted == cafef.normalize_statement(
        "TEST", pe, "IS", "quarterly", data
    )
    assert cur.values[4].adapted["lợi nhuận sau thuế"] == 10
    assert cur.values[6] == pe


@pytest.mark.parametrize(
    "capex_label",
    [
        "1.Tiền chi để mua sắm, xây dựng TSCĐ và các TS dài hạn khác",
        "Tiền chi mua sắm, xây dựng TSCĐ và các tài sản dài hạn khác",
        "Tiền chi để mua sắm, xây dựng TSCĐ, BĐSĐT và các tài sản dài hạn khác",
        "1. Tiền chi để mua sắm, xây dựng tài sản cố định, bất động sản đầu tư và các tài sản khác",
    ],
)
def test_additional_capex_labels_preserve_baseline_null(capex_label):
    pe = date(2026, 6, 30)
    rows = [
        (
            pe,
            "quarterly",
            "BS",
            {"Tổng tài sản": 1228050000000, "Vốn chủ sở hữu": 848773438648},
            None,
        ),
        (
            pe,
            "quarterly",
            "IS",
            {"18. Lợi nhuận sau thuế thu nhập doanh nghiệp": 33533500991},
            None,
        ),
        (
            pe,
            "quarterly",
            "ratios",
            {"Chỉ số giá thị trường trên thu nhập (P/E)": 9.2},
            None,
        ),
        (
            pe,
            "quarterly",
            "CF",
            {
                "Lưu chuyển tiền thuần từ hoạt động kinh doanh": 91069623277,
                capex_label: -281127916,
                "3.Tiền chi cho vay, mua các công cụ nợ của đơn vị khác": -385100000000,
            },
            None,
        ),
    ]
    result = cafef.derive_financial_ratios("TEST", rows)[0]
    assert result[11] is None
    assert (
        command.fields.extract_financial_value(rows[-1][3], command.fields.CAPEX_KEYS)
        is None
    )
