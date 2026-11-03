"""Source-label regressions; no Agent startup, provider requests or DB writes."""
from datetime import date

import pytest

from app.domain.services import financial_fields as fields
from app.infrastructure.data_pipelines import financial_etl_cafef as etl


@pytest.mark.parametrize("reverse", [False, True])
def test_metadata_and_funding_cannot_be_ratios_or_equity(reverse):
    ratios = {"year_period": 2025, "Chỉ số giá thị trường trên thu nhập (P/E)": 8.15}
    bs = {"TỔNG NỢ PHẢI TRẢ VÀ VỐN CHỦ SỞ HỮU": 440998586000000, "VIII. Vốn và các quỹ": 45246509000000}
    if reverse:
        ratios, bs = dict(reversed(list(ratios.items()))), dict(reversed(list(bs.items())))
    assert etl._extract_value([ratios], etl._PE_KEYS) == 8.15
    assert etl._extract_value([bs], etl._EQUITY_KEYS) == 45246509000000
    assert fields.extract_financial_value({"year_period": 2025}, fields.PE_KEYS) is None


@pytest.mark.parametrize("key", ["P/E", "P_E", "p e", "Chỉ số giá thị trường trên thu nhập (P/E)"])
def test_ratio_notation_and_unicode_aliases(key):
    assert fields.extract_financial_value({key: "8.15"}, fields.PE_KEYS) == 8.15
    assert fields.extract_financial_value({"D/E": 0}, fields.DE_KEYS) == 0


@pytest.mark.parametrize("bad", [True, False, "NaN", float("inf"), "bad", None])
def test_nonfinancial_values_are_never_numeric_evidence(bad):
    assert fields.extract_financial_value({"P/E": bad}, fields.PE_KEYS) is None


def test_total_net_income_and_net_sales_outrank_other_concepts():
    inc = {"Doanh thu bán hàng và cung cấp dịch vụ": 110, "3_doanh_thu_thuần_về_bán_hàng_và_cung_cấp_dịch_vụ": 100,
           "lợi nhuận sau thuế": 8, "18. Lợi nhuận sau thuế thu nhập doanh nghiệp": 10,
           "Lợi nhuận sau thuế của cổ đông của Công ty mẹ": 8}
    assert fields.extract_financial_value(inc, fields.REVENUE_KEYS) == 100
    assert fields.extract_financial_value(inc, fields.NI_KEYS) == 10
    assert fields.extract_financial_value({"Lợi nhuận sau thuế phân bổ cho chủ sở hữu": 8}, fields.NI_KEYS) is None
    assert fields.extract_financial_value({"XI. LỢI NHUẬN KẾ TOÁN SAU THUẾ TNDN": 10}, fields.NI_KEYS) == 10


@pytest.mark.parametrize("reverse", [False, True])
def test_final_cfo_outranks_polluted_alias_and_before_working_capital(reverse):
    data = {"Lưu chuyển tiền thuần từ hoạt động kinh doanh": 9,
            "I. Lưu chuyền tiền thuần từ hoạt động kinh doanh": -42,
            "Lưu chuyển tiền thuần từ hoạt động kinh doanh trước những thay đổi về tài sản và vốn lưu động": 9}
    if reverse:
        data = dict(reversed(list(data.items())))
    assert fields.extract_financial_value(data, fields.CFO_KEYS) == -42
    stage = "Lưu chuyển tiền thuần từ hoạt động kinh doanh trước những thay đổi về tài sản và vốn lưu động"
    assert fields.extract_financial_value({stage: 9}, fields.CFO_KEYS) is None


def test_conflicting_authoritative_aliases_remain_unavailable(caplog):
    data = {"18_lợi_nhuận_sau_thuế_thu_nhập_doanh_nghiệp": 10,
            "18. Lợi nhuận sau thuế thu nhập doanh nghiệp": 20}
    for values in [data, dict(reversed(list(data.items())))]:
        assert fields.extract_financial_value(values, fields.NI_KEYS) is None
    assert "FINANCIAL_MAPPING_CONFLICT" in caplog.text


def test_equity_total_and_debt_maturities_are_not_arbitrary_components():
    bs = {"D. VỐN CHỦ SỞ HỮU": 100, "I. Vốn chủ sở hữu": 95,
          "1. Vay và nợ thuê tài chính ngắn hạn": 10, "2. Vay và nợ thuê tài chính dài hạn": 20}
    assert fields.extract_financial_value(bs, fields.EQUITY_KEYS) == 100
    assert fields.interest_bearing_debt(bs) == 30
    assert fields.interest_bearing_debt({"Vay và nợ thuê tài chính ngắn hạn": 10}) is None
    assert fields.extract_financial_value({"1. Tiền": 5}, fields.CASH_KEYS) is None
    assert fields.extract_financial_value({"EBIT": 10, "Lợi nhuận thuần từ hoạt động kinh doanh": 8}, fields.EBITDA_KEYS) is None


def test_source_parsers_preserve_final_cfo_and_consolidated_income():
    statement = {"templace": [{"code": "18", "name": "18. Lợi nhuận sau thuế thu nhập doanh nghiệp"},
                              {"code": "19", "name": "Lợi nhuận sau thuế của cổ đông của Công ty mẹ"}],
                 "data": [{"time": "Q2-2026", "year": 2026, "data": [{"code": "18", "value": 100}, {"code": "19", "value": 80}]}]}
    inc = etl._parse_kqkd(statement, False)["Q2-2026"]
    assert inc["lợi nhuận sau thuế"] == 100
    assert inc["Lợi nhuận sau thuế của cổ đông của Công ty mẹ"] == 80
    statement = {"templace": [{"data": [{"code": "1", "name": "Lưu chuyển tiền thuần từ hoạt động kinh doanh trước những thay đổi về tài sản và vốn lưu động"},
                                        {"code": "2", "name": "I. Lưu chuyền tiền thuần từ hoạt động kinh doanh"}]}],
                 "data": [{"data": [{"time": "Q2-2026", "data": [{"code": "1", "value": 9}, {"code": "2", "value": -42}]}]}]}
    cf = etl._parse_lctt(statement, True)["Q2-2026"]
    assert cf["Lưu chuyển tiền thuần từ hoạt động kinh doanh"] == -42
    assert cf["I. Lưu chuyền tiền thuần từ hoạt động kinh doanh"] == -42


class NoWriteCursor:
    rowcount = 0
    def __init__(self, rows):
        self.rows, self.inserts = rows, []

    def execute(self, sql, params):
        if sql.lstrip().startswith("INSERT"):
            self.inserts.append(params)
        else:
            assert sql.lstrip().startswith("SELECT")

    def fetchall(self):
        return self.rows


def test_derive_preserves_frequency_units_and_correct_bank_formula():
    pe = date(2026, 6, 30)
    bs = {"TỔNG CỘNG TÀI SẢN": 440998586000000, "TỔNG NỢ PHẢI TRẢ": 395752077000000,
          "TỔNG NỢ PHẢI TRẢ VÀ VỐN CHỦ SỞ HỮU": 440998586000000, "VIII. Vốn và các quỹ": 45246509000000}
    inc = {"XIII. Lợi nhuận sau thuế (XI-XII)": 1250603000000}
    cursor = NoWriteCursor([(pe, freq, st, d, None) for freq in ("quarterly", "yearly") for st,d in [("BS",bs),("IS",inc)]])
    etl._store_derived_ratios("MSB", cursor)
    assert len(cursor.inserts) == 2
    for row in cursor.inserts:
        assert row[5] == pytest.approx(1250603000000 / 45246509000000)
        assert row[6] == pytest.approx(1250603000000 / 440998586000000)
        assert row[7] == pytest.approx(395752077000000 / 45246509000000)


@pytest.mark.parametrize("label", ["1. Tiền chi để mua sắm, xây dựng TSCĐ và các tài sản dài hạn khác", "Mua sắm tài sản cố định"])
def test_neighbor_aliases(label):
    assert fields.extract_financial_value({label: -100}, fields.CAPEX_KEYS) == -100
    assert fields.extract_financial_value({"A. NỢ PHẢI TRẢ (300=310+340)": 100}, fields.LIAB_KEYS) == 100
    assert fields.extract_financial_value({"CURRENT ASSETS": 100}, fields.CA_KEYS) == 100


def test_bank_source_line_outranks_legacy_numbered_convenience_alias():
    inc = {"XIII. Lợi nhuận sau thuế": 100, "18_loi_nhuan_sau_thue": 80, "lợi nhuận sau thuế": 80,
           "XV. Lợi nhuận sau thuế của cổ đông của ngân hàng mẹ": 80}
    assert fields.extract_financial_value(inc, fields.NI_KEYS) == 100


def test_verified_override_updates_total_aliases_without_overwriting_equity_subheading():
    bs = {"D VỐN CHỦ SỞ HỮU": 40, "I. Vốn chủ sở hữu": 40, "C NỢ PHẢI TRẢ": 0, "TỔNG TÀI SẢN": 100}
    fixed = fields.apply_financial_overrides(bs, {"D. VỐN CHỦ SỞ HỮU": 60, "C. NỢ PHẢI TRẢ": 50, "tổng cộng tài sản": 110})
    assert fixed["I. Vốn chủ sở hữu"] == 40
    assert fields.extract_financial_value(fixed, fields.EQUITY_KEYS) == 60
    assert fields.extract_financial_value(fixed, fields.LIAB_KEYS) == 50
    assert fields.extract_financial_value(fixed, fields.ASSET_KEYS) == 110


def test_beneish_uses_the_shared_total_concepts():
    from app.domain.rules import beneish
    bs = {"A. NỢ PHẢI TRẢ (300=310+340)": 100}
    inc = {"Doanh thu bán hàng và cung cấp dịch vụ": 110, "3. Doanh thu thuần về bán hàng và cung cấp dịch vụ": 100}
    assert beneish._extract_val(bs, beneish.DEBT_KEYS) == 100
    assert beneish._extract_val(inc, beneish.REV_KEYS) == 100


def test_securities_total_accounting_income_without_tax_abbreviation():
    inc = {"LỢI NHUẬN KẾ TOÁN SAU THUẾ": 502617939631,
           "Lợi nhuận sau thuế phân bổ cho chủ sở hữu": 502617939631}
    assert fields.extract_financial_value(inc, fields.NI_KEYS) == 502617939631


@pytest.mark.parametrize('label',['net_profit_loss_after_tax','Net profit/(loss) after tax','NET PROFIT/(LOSS) AFTER TAX'])
@pytest.mark.parametrize('income',[0.,-100.,228457671479.])
def test_vci_total_net_income_alias_preserves_zero_and_losses(label,income):
    data={label:income,'Net profit attributable to owners of parent':999.}
    assert fields.extract_financial_value(data,fields.NI_KEYS)==income


def test_vci_net_income_alias_rebuilds_roe_margin_and_growth():
    from app.infrastructure.data_pipelines.financial_etl_cafef import derive_financial_ratios
    rows=[]
    for year,ni in [(2025,10.),(2026,20.)]:
        period=date(year,6,30)
        rows.extend([(period,'quarterly','BS',{'total_assets':200.,'total_liabilities':100.,'equity':100.},None,'vnstock_vci'),
                     (period,'quarterly','IS',{'net_revenue':100.,'net_profit_loss_after_tax':ni},None,'vnstock_vci')])
    derived={row[1]:row for row in derive_financial_ratios('TEST',rows)}[date(2026,6,30)]
    assert derived[5]==.2 and derived[10]==.2 and derived[14]==1.
    assert derived[3] is None and derived[4] is None  # No invented valuation multiples.


@pytest.mark.parametrize("is_bank", [False, True])
def test_insurance_crawl_preserves_baseline_total_revenue_and_reserve_codes(is_bank):
    # BVH Q2/2016: captured CafeF codes, independently retained in the LOCAL baseline.
    raw = {"templace": [
        {"code": "8", "name": "3. Doanh thu phí bảo hiểm thuần"},
        {"code": "15", "name": "5. Doanh thu thuần từ HĐKD BH"},
        {"code": "22", "name": "7.1 Tăng dự phòng nghiệp vụ bảo hiểm gốc"},
        {"code": "60", "name": "28. Lợi nhuận sau thuế thu nhập doanh nghiệp"},
        {"code": "61", "name": "29. Lợi nhuận sau thuế của cổ đông Công ty mẹ"},
    ], "data": [{"time": "Q2-2016", "year": 2016, "data": [
        {"code": "8", "value": 0}, {"code": "15", "value": 4679369810000},
        {"code": "21", "value": 560200000}, {"code": "22", "value": 1573662890000},
        {"code": "60", "value": 397429420000}, {"code": "61", "value": 14085830000},
    ]}]}
    data = etl._parse_kqkd(raw, is_bank)["Q2-2016"]
    assert fields.extract_financial_value(data, fields.REVENUE_KEYS) == 4679369810000
    assert data["doanh thu thuần"] == 4679369810000
    assert data["7.1 Tăng dự phòng nghiệp vụ bảo hiểm gốc"] == 1573662890000
    assert data["lợi nhuận sau thuế"] == 397429420000
    rows = [(date(2016, 6, 30), "quarterly", kind, value, None, "cafef") for kind, value in (
        ("BS", {"total_assets": 10000000000000, "equity": 5000000000000}), ("IS", data),
    )]
    assert etl.derive_financial_ratios("BVH", rows)[0][10] == pytest.approx(397429420000 / 4679369810000)


@pytest.mark.parametrize("year,expected", [(2025, 100), (2026, 200)])
def test_general_template_retains_year_specific_financial_income_shift(year, expected):
    raw = {"templace": [{"code": "22", "name": "6. Doanh thu hoạt động tài chính"}],
           "data": [{"time": f"Q2-{year}", "year": year, "data": [
               {"code": "21", "value": 100}, {"code": "22", "value": 200},
           ]}]}
    assert etl._parse_kqkd(raw, False)[f"Q2-{year}"]["6. Doanh thu hoạt động tài chính"] == expected


@pytest.mark.parametrize("total_label", [
    "5. Doanh thu thuần từ HĐKD BH", "Doanh thu thuần hoạt động kinh doanh bảo hiểm",
    "7_total_net_revenue_from_insurance_business",
])
def test_insurance_total_revenue_precedes_net_premium_component(total_label):
    data = {"3. Doanh thu phí bảo hiểm thuần": 0, total_label: 4679369810000}
    assert fields.extract_financial_value(data, fields.REVENUE_KEYS) == 4679369810000


def test_retained_statement_normalization_detects_a_concurrent_change():
    class Cursor:
        rowcount = 0
        queries = []

        def execute(self, query, params):
            self.queries.append(query)

        def fetchall(self):
            return [(date(2000, 3, 31), "quarterly", "IS", {
                "18. Lợi nhuận sau thuế thu nhập doanh nghiệp": 10, "lợi nhuận sau thuế": 8,
            }, None, "cafef")]

    cur = Cursor()
    with pytest.raises(RuntimeError, match="changed while normalizing"):
        etl._store_derived_ratios("TEST", cur, normalize_statements=True)
    assert not any(query.lstrip().startswith("INSERT") for query in cur.queries)
