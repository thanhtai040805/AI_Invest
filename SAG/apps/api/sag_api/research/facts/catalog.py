"""Canonical financial line-item catalog for Vietnamese listed issuers.

Each canonical item lists the exact Core PG `financial_statements.data` keys that carry it,
in priority order, per accounting template:

- `CORPORATE`  (TT200 non-financial issuers)
- `BANK`       (TT49 credit institutions)
- `SECURITIES` (TT210/TT334 securities companies)
- `INSURANCE`  (TT232 insurers)

Keys come from two vendor schemas observed in Core PG: English snake keys (`is_*`, `bs_*`,
`cf_*`, used by the latest fetches) and Vietnamese VAS-numbered snake keys (historical rows).
Matching is EXACT on keys (never substring), which removes the label-collision errors of the
previous substring matcher.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class Template(StrEnum):
    CORPORATE = "CORPORATE"
    BANK = "BANK"
    SECURITIES = "SECURITIES"
    INSURANCE = "INSURANCE"


class ItemKind(StrEnum):
    FLOW = "FLOW"  # period amounts (IS/CF): summable across quarters
    STOCK = "STOCK"  # point-in-time balances (BS)
    PER_SHARE = "PER_SHARE"  # VND per share, never summed or scaled


class SignPolicy(StrEnum):
    """Canonical sign conventions, derived from observed vendor conventions in Core PG.

    Vendor conventions (verified on live rows): English-schema IS expenses are negative while
    Vietnamese-schema IS expenses are positive; CF outflows and BS contra-asset provisions are
    negative in both schemas. Canonical output: expenses/outflows/provisions are positive
    magnitudes, while reversals and credits keep their opposite sign (never `abs()`).
    """

    AS_REPORTED = "AS_REPORTED"
    EXPENSE_IS = "EXPENSE_IS"  # IS expense line: negate English-schema keys only
    OUTFLOW = "OUTFLOW"  # CF payment or BS contra-asset provision: negate in both schemas


ENGLISH_KEY_PREFIXES: tuple[str, ...] = ("is_", "bs_", "cf_")


def canonical_sign(policy: SignPolicy, key: str) -> float:
    if policy is SignPolicy.OUTFLOW:
        return -1.0
    if policy is SignPolicy.EXPENSE_IS and key.startswith(ENGLISH_KEY_PREFIXES):
        return -1.0
    return 1.0


_INDUSTRY_TEMPLATE: dict[str, Template] = {
    "ngân hàng": Template.BANK,
    "chứng khoán": Template.SECURITIES,
    "bảo hiểm": Template.INSURANCE,
}


def resolve_template(industry: str | None) -> Template:
    """Map Core PG `stocks.industry` to an accounting template (default `CORPORATE`)."""
    return _INDUSTRY_TEMPLATE.get(str(industry or "").strip().lower(), Template.CORPORATE)


@dataclass(frozen=True, slots=True)
class CanonicalItem:
    item_id: str
    statement: str  # IS | BS | CF
    kind: ItemKind
    label_vi: str
    keys: Mapping[Template, tuple[str, ...]]
    sign: SignPolicy = SignPolicy.AS_REPORTED
    zero_is_missing: bool = False
    templates: frozenset[Template] = field(default_factory=frozenset)


_ALL = (Template.CORPORATE, Template.BANK, Template.SECURITIES, Template.INSURANCE)


def _item(
    item_id: str,
    statement: str,
    kind: ItemKind,
    label_vi: str,
    *,
    common: tuple[str, ...] = (),
    corporate: tuple[str, ...] | None = None,
    bank: tuple[str, ...] | None = None,
    securities: tuple[str, ...] | None = None,
    insurance: tuple[str, ...] | None = None,
    sign: SignPolicy = SignPolicy.AS_REPORTED,
    zero_is_missing: bool = False,
    only: tuple[Template, ...] | None = None,
) -> CanonicalItem:
    specific = {
        Template.CORPORATE: corporate,
        Template.BANK: bank,
        Template.SECURITIES: securities,
        Template.INSURANCE: insurance,
    }
    applicable = only if only is not None else _ALL
    keys: dict[Template, tuple[str, ...]] = {}
    for tmpl in applicable:
        tmpl_keys = specific[tmpl]
        if only is None and tmpl_keys is None and not common:
            continue
        keys[tmpl] = tuple(dict.fromkeys([*common, *(tmpl_keys or ())]))
    return CanonicalItem(
        item_id=item_id,
        statement=statement,
        kind=kind,
        label_vi=label_vi,
        keys=keys,
        sign=sign,
        zero_is_missing=zero_is_missing,
        templates=frozenset(keys),
    )


_F, _S, _P = ItemKind.FLOW, ItemKind.STOCK, ItemKind.PER_SHARE
_EXP = SignPolicy.EXPENSE_IS
_OUT = SignPolicy.OUTFLOW

CATALOG: tuple[CanonicalItem, ...] = (
    # ── Common: Income statement ──────────────────────────────────────────────
    _item(
        "pbt", "IS", _F, "Lợi nhuận trước thuế",
        common=("is_profit_before_tax",),
        corporate=("15_tổng_lợi_nhuận_kế_toán_trước_thuế",),
        bank=("xi_tổng_lợi_nhuận_trước_thuế_ix_x",),
        securities=("ix_tổng_lợi_nhuận_kế_toán_trước_thuế",),
        insurance=("26_tổng_lợi_nhuận_kế_toán_trước_thuế", "tổng_lợi_nhuận_trước_thuế_thu_nhập_doanh_nghiệp"),
    ),
    _item(
        "income_tax", "IS", _F, "Chi phí thuế TNDN",
        common=("is_corporate_income_tax_expenses",),
        bank=("xii_chi_phí_thuế_tndn",),
        securities=("x_chi_phí_thuế_thu_nhập_doanh_nghiệp",),
        corporate=(),
        insurance=(),
        sign=_EXP,
    ),
    _item(
        "npat", "IS", _F, "Lợi nhuận sau thuế",
        common=("is_net_profit_after_tax",),
        corporate=("18_lợi_nhuận_sau_thuế_thu_nhập_doanh_nghiệp",),
        bank=("xiii_lợi_nhuận_sau_thuế_xi_xii",),
        securities=("xi_lợi_nhuận_kế_toán_sau_thuế_tndn",),
        insurance=("29_lợi_nhuận_sau_thuế_thu_nhập_doanh_nghiệp",),
    ),
    _item(
        "npat_parent", "IS", _F, "LNST của cổ đông công ty mẹ",
        common=("is_profit_after_tax_for_shareholders_of_parent_company",),
        corporate=("lợi_nhuận_sau_thuế_của_cổ_đông_của_công_ty_mẹ",),
        bank=("xv_lợi_nhuận_sau_thuế_của_cổ_đông_của_ngân_hàng_mẹ_xiii_xiv",),
        securities=("11_1_lợi_nhuận_sau_thuế_phân_bổ_cho_chủ_sở_hữu",),
        insurance=("31_lợi_nhuận_sau_thuế_của_cổ_đông_của_công_ty_mẹ",),
    ),
    _item(
        "eps_basic", "IS", _P, "Lãi cơ bản trên cổ phiếu (VND)",
        common=("is_basic_earnings_per_share",),
        corporate=("19_lãi_cơ_bản_trên_cổ_phiếu_vnð",),
        bank=("lãi_cơ_bản_trên_cổ_phiếu_bctc_vnð",),
        securities=("13_1_lãi_cơ_bản_trên_cổ_phiếu_đồng_1_cổ_phiếu_vnð",),
        insurance=("32_lãi_cơ_bản_trên_cổ_phiếu_vnð",),
    ),
    # ── Common: Balance sheet ─────────────────────────────────────────────────
    _item(
        "total_assets", "BS", _S, "Tổng cộng tài sản",
        common=("bs_total_assets",),
        corporate=("tổng_cộng_tài_sản",),
        bank=("tổng_cộng_tài_sản",),
        securities=("tổng_cộng_tài_sản_270_100_200",),
        insurance=("tổng_cộng_tài_sản_270_100_200",),
        zero_is_missing=True,
    ),
    _item(
        "total_liabilities", "BS", _S, "Tổng nợ phải trả",
        common=("bs_total_liabilities",),
        corporate=("c_nợ_phải_trả",),
        bank=("tổng_nợ_phải_trả",),
        securities=("a_nợ_phải_trả_300_310_340",),
        insurance=("a_nợ_phải_trả_300_210_330",),
        zero_is_missing=True,
    ),
    _item(
        "equity", "BS", _S, "Vốn chủ sở hữu",
        common=("bs_equity",),
        corporate=("d_vốn_chủ_sở_hữu",),
        bank=("viii_vốn_và_các_quỹ",),
        securities=("b_vốn_chủ_sở_hữu_400_410_420",),
        insurance=("b_vốn_chủ_sở_hữu_400_410_430",),
        zero_is_missing=True,
    ),
    _item(
        "minority_interest_bs", "BS", _S, "Lợi ích cổ đông không kiểm soát",
        common=("bs_minority_interest",),
        corporate=("13_lợi_ích_cổ_đông_không_kiểm_soát",),
        bank=(),
        securities=(),
        insurance=(),
    ),
    _item(
        "charter_capital", "BS", _S, "Vốn điều lệ / vốn góp",
        common=("bs_charter_capital",),
        corporate=("1_vốn_góp_của_chủ_sở_hữu",),
        bank=("a_vốn_điều_lệ",),
        securities=("1_1_vốn_góp_của_chủ_sở_hữu",),
        insurance=("1_vốn_góp_của_chủ_sở_hữu",),
        zero_is_missing=True,
    ),
    # ── Common: Cash flow ─────────────────────────────────────────────────────
    _item(
        "cfo", "CF", _F, "Lưu chuyển tiền thuần từ HĐKD",
        common=("cf_net_cash_flows_from_operating_activities",),
        corporate=("lưu_chuyển_tiền_thuần_từ_hoạt_động_kinh_doanh",),
        bank=("i_lưu_chuyển_tiền_thuần_từ_hoạt_động_kinh_doanh",),
        securities=("lưu_chuyển_tiền_thuần_từ_hoạt_động_kinh_doanh_chứng_khoán",),
        insurance=("lưu_chuyển_tiền_thuần_từ_hoạt_động_kinh_doanh", "lưu_chuyển_tiền_thuần_từ_hđkd"),
    ),
    _item(
        "cfi", "CF", _F, "Lưu chuyển tiền thuần từ HĐ đầu tư",
        common=("cf_net_cash_flows_from_investing_activities", "lưu_chuyển_tiền_thuần_từ_hoạt_động_đầu_tư"),
        bank=("ii_lưu_chuyển_tiền_thuần_từ_hoạt_động_đầu_tư",),
    ),
    _item(
        "cff", "CF", _F, "Lưu chuyển tiền thuần từ HĐ tài chính",
        common=("cf_net_cash_flows_from_financing_activities", "lưu_chuyển_tiền_thuần_từ_hoạt_động_tài_chính"),
        bank=("iii_lưu_chuyển_tiền_thuần_từ_hoạt_động_tài_chính",),
    ),
    _item(
        "cash_end", "CF", _S, "Tiền và tương đương tiền cuối kỳ",
        common=("cf_cash_and_cash_equivalents_at_end_of_period", "tiền_và_tương_đương_tiền_cuối_kỳ"),
        bank=("vii_tiền_và_các_khoản_tương_đương_tiền_tại_thời_điểm_cuối_kỳ",),
    ),
    _item(
        "capex", "CF", _F, "Chi mua sắm TSCĐ",
        common=("cf_payments_for_fixed_assets",),
        corporate=("1_tiền_chi_để_mua_sắm_xây_dựng_tscđ_và_các_tài_sản_dài_hạn_khác",),
        bank=("1_mua_sắm_tài_sản_cố_định",),
        securities=("1_tiền_chi_để_mua_sắm_xây_dựng_tscđ_và_các_tài_sản_dài_hạn_khác",),
        insurance=("1_tiền_chi_để_mua_sắm_xây_dựng_tscđ_và_các_tài_sản_dài_hạn_khác", "5_tiền_mua_tài_sản_cố_định"),
        sign=_OUT,
    ),
    _item(
        "dividends_paid", "CF", _F, "Cổ tức đã trả",
        common=("cf_dividends_paid",),
        corporate=("6_cổ_tức_lợi_nhuận_đã_trả_cho_chủ_sở_hữu",),
        bank=("4_cổ_tức_trả_cho_cổ_đông_lợi_nhuận_đã_chia",),
        securities=(),
        insurance=("6_cổ_tức_lợi_nhuận_đã_trả_cho_chủ_sở_hữu",),
        sign=_OUT,
    ),
    _item(
        "depreciation", "CF", _F, "Khấu hao",
        common=("cf_depreciation_and_amortisation", "cf_depreciation_amortization"),
        corporate=("khấu_hao_tscđ_và_bđsđt",),
        securities=("khấu_hao_tài_sản_cố_định",),
        insurance=("khấu_hao_tài_sản_cố_định",),
        bank=(),
    ),
    # ── Corporate (TT200) ─────────────────────────────────────────────────────
    _item(
        "revenue_net", "IS", _F, "Doanh thu thuần",
        corporate=("is_net_revenue", "3_doanh_thu_thuần_về_bán_hàng_và_cung_cấp_dịch_vụ"),
        securities=("is_net_revenue", "cộng_doanh_thu_hoạt_động_01_11"),
        only=(Template.CORPORATE, Template.SECURITIES),
        zero_is_missing=True,
    ),
    _item(
        "cogs", "IS", _F, "Giá vốn hàng bán",
        corporate=("is_cost_of_goods_sold", "4_giá_vốn_hàng_bán"),
        only=(Template.CORPORATE,),
        sign=_EXP,
    ),
    _item(
        "gross_profit", "IS", _F, "Lợi nhuận gộp",
        corporate=("is_gross_profit", "5_lợi_nhuận_gộp_về_bán_hàng_và_cung_cấp_dịch_vụ"),
        only=(Template.CORPORATE,),
    ),
    _item(
        "selling_expense", "IS", _F, "Chi phí bán hàng",
        corporate=("is_selling_expenses", "9_chi_phí_bán_hàng"),
        only=(Template.CORPORATE,),
        sign=_EXP,
    ),
    _item(
        "ga_expense", "IS", _F, "Chi phí quản lý doanh nghiệp",
        corporate=("is_general_and_administrative_expenses", "10_chi_phí_quản_lý_doanh_nghiệp"),
        only=(Template.CORPORATE,),
        sign=_EXP,
    ),
    _item(
        "financial_income", "IS", _F, "Doanh thu hoạt động tài chính",
        corporate=("is_financial_income", "7_doanh_thu_hoạt_động_tài_chính", "6_doanh_thu_hoạt_động_tài_chính"),
        insurance=("is_financial_income", "18_doanh_thu_hoạt_động_tài_chính"),
        only=(Template.CORPORATE, Template.INSURANCE),
    ),
    _item(
        "financial_expense", "IS", _F, "Chi phí tài chính",
        corporate=("is_financial_expenses", "8_chi_phí_tài_chính", "7_chi_phí_tài_chính"),
        only=(Template.CORPORATE,),
        sign=_EXP,
    ),
    _item(
        "interest_expense", "IS", _F, "Chi phí lãi vay",
        corporate=("is_interest_expenses", "trong_đó_chi_phí_đi_vay", "trong_đó_chi_phí_lãi_vay"),
        bank=("is_interest_and_similar_expenses", "2_chi_phí_lãi_và_các_chi_phí_tương_tự"),
        securities=("is_interest_expenses", "4_2_chi_phí_lãi_vay"),
        only=(Template.CORPORATE, Template.BANK, Template.SECURITIES),
        sign=_EXP,
    ),
    _item(
        "operating_profit", "IS", _F, "Lợi nhuận thuần từ HĐKD",
        corporate=("is_operating_profit", "11_lợi_nhuận_thuần_từ_hoạt_động_kinh_doanh"),
        insurance=("22_lợi_nhuận_thuần_từ_hoạt_động_kinh_doanh",),
        only=(Template.CORPORATE, Template.INSURANCE),
    ),
    _item(
        "current_assets", "BS", _S, "Tài sản ngắn hạn",
        corporate=("bs_short_term_assets", "a_tài_sản_ngắn_hạn"),
        securities=("bs_short_term_assets", "a_tài_sản_ngắn_hạn"),
        insurance=("bs_short_term_assets", "a_tài_sản_ngắn_hạn_100_110_120_130_140_150_190"),
        only=(Template.CORPORATE, Template.SECURITIES, Template.INSURANCE),
        zero_is_missing=True,
    ),
    _item(
        "current_liabilities", "BS", _S, "Nợ ngắn hạn",
        corporate=("bs_short_term_liabilities", "i_nợ_ngắn_hạn"),
        securities=("bs_short_term_liabilities", "i_nợ_ngắn_hạn"),
        insurance=("bs_short_term_liabilities", "i_nợ_ngắn_hạn"),
        only=(Template.CORPORATE, Template.SECURITIES, Template.INSURANCE),
    ),
    _item(
        "inventory", "BS", _S, "Hàng tồn kho",
        corporate=("bs_inventories", "iv_hàng_tồn_kho"),
        only=(Template.CORPORATE,),
    ),
    _item(
        "st_receivables", "BS", _S, "Các khoản phải thu ngắn hạn",
        corporate=("bs_short_term_receivables", "iii_các_khoản_phải_thu_ngắn_hạn"),
        securities=("bs_short_term_receivables", "7_các_khoản_phải_thu_ngắn_hạn"),
        insurance=("bs_short_term_receivables", "iii_các_khoản_phải_thu_ngắn_hạn"),
        only=(Template.CORPORATE, Template.SECURITIES, Template.INSURANCE),
    ),
    _item(
        "trade_receivables", "BS", _S, "Phải thu ngắn hạn của khách hàng",
        corporate=("bs_trade_receivables", "1_phải_thu_ngắn_hạn_của_khách_hàng"),
        only=(Template.CORPORATE,),
    ),
    _item(
        "lt_receivables", "BS", _S, "Các khoản phải thu dài hạn",
        corporate=("bs_long_term_receivables", "i_các_khoản_phải_thu_dài_hạn"),
        only=(Template.CORPORATE,),
    ),
    _item(
        "st_investments", "BS", _S, "Đầu tư tài chính ngắn hạn",
        corporate=("bs_short_term_investments", "ii_đầu_tư_tài_chính_ngắn_hạn"),
        insurance=("bs_short_term_investments", "ii_đầu_tư_tài_chính_ngắn_hạn"),
        only=(Template.CORPORATE, Template.INSURANCE),
    ),
    _item(
        "st_borrowings", "BS", _S, "Vay và nợ thuê tài chính ngắn hạn",
        corporate=("bs_short_term_borrowings", "11_vay_và_nợ_thuê_tài_chính_ngắn_hạn"),
        securities=("bs_short_term_borrowings", "1_vay_và_nợ_thuê_tài_chính_ngắn_hạn"),
        insurance=("bs_short_term_borrowings", "1_vay_và_nợ_ngắn_hạn"),
        only=(Template.CORPORATE, Template.SECURITIES, Template.INSURANCE),
    ),
    _item(
        "lt_borrowings", "BS", _S, "Vay và nợ thuê tài chính dài hạn",
        corporate=("bs_long_term_borrowings", "9_vay_và_nợ_thuê_tài_chính_dài_hạn"),
        securities=("bs_long_term_borrowings",),
        insurance=("bs_long_term_borrowings",),
        only=(Template.CORPORATE, Template.SECURITIES, Template.INSURANCE),
    ),
    _item(
        "advances_from_customers", "BS", _S, "Người mua trả tiền trước ngắn hạn",
        corporate=("bs_advances_from_customers", "2_người_mua_trả_tiền_trước_ngắn_hạn"),
        only=(Template.CORPORATE,),
    ),
    _item(
        "construction_in_progress", "BS", _S, "Chi phí xây dựng cơ bản dở dang",
        corporate=("bs_construction_in_progress", "2_chi_phí_xây_dựng_cơ_bản_dở_dang"),
        only=(Template.CORPORATE,),
    ),
    # ── Bank (TT49) ───────────────────────────────────────────────────────────
    _item(
        "interest_income", "IS", _F, "Thu nhập lãi và tương tự",
        bank=("is_interest_income_and_similar_income", "1_thu_nhập_lãi_và_các_khoản_thu_nhập_tương_tự"),
        only=(Template.BANK,),
    ),
    _item(
        "nii", "IS", _F, "Thu nhập lãi thuần",
        bank=("is_net_interest_income", "i_thu_nhập_lãi_thuần"),
        only=(Template.BANK,),
    ),
    _item(
        "net_fee_income", "IS", _F, "Lãi thuần từ hoạt động dịch vụ",
        bank=("is_net_fee_and_commission_income", "ii_lãi_lỗ_thuần_từ_hoạt_động_dịch_vụ"),
        only=(Template.BANK,),
    ),
    _item(
        "fx_gold_income", "IS", _F, "Lãi thuần kinh doanh ngoại hối và vàng",
        bank=(
            "is_net_gain_loss_from_foreign_currencies_and_gold_trading",
            "iii_lãi_lỗ_thuần_từ_hoạt_động_kinh_doanh_ngoại_hối_và_vàng",
        ),
        only=(Template.BANK,),
    ),
    _item(
        "trading_securities_income", "IS", _F, "Lãi thuần mua bán chứng khoán kinh doanh",
        bank=(
            "is_net_gain_loss_from_trading_securities",
            "iv_lãi_lỗ_thuần_từ_mua_bán_chứng_khoán_kinh_doanh",
        ),
        only=(Template.BANK,),
    ),
    _item(
        "investment_securities_income", "IS", _F, "Lãi thuần mua bán chứng khoán đầu tư",
        bank=(
            "is_net_gain_loss_from_investment_securities",
            "v_lãi_lỗ_thuần_từ_mua_bán_chứng_khoán_đầu_tư",
        ),
        only=(Template.BANK,),
    ),
    _item(
        "other_income_net", "IS", _F, "Lãi thuần từ hoạt động khác",
        bank=("is_net_other_income", "vi_lãi_lỗ_thuần_từ_hoạt_động_khác"),
        only=(Template.BANK,),
    ),
    _item(
        "equity_investment_income", "IS", _F, "Thu nhập từ góp vốn, mua cổ phần",
        bank=(
            "is_income_from_capital_contribution_and_long_term_investments",
            "vii_thu_nhập_từ_góp_vốn_mua_cổ_phần",
        ),
        only=(Template.BANK,),
    ),
    _item(
        "toi", "IS", _F, "Tổng thu nhập hoạt động",
        bank=("is_total_operating_income",),
        only=(Template.BANK,),
        zero_is_missing=True,
    ),
    _item(
        "opex", "IS", _F, "Chi phí hoạt động",
        bank=("viii_chi_phí_hoạt_động",),
        only=(Template.BANK,),
        sign=_EXP,
    ),
    _item(
        "ppop", "IS", _F, "Lợi nhuận trước dự phòng rủi ro tín dụng",
        bank=(
            "is_operating_profit_before_provision_for_credit_losses",
            "ix_lợi_nhuận_thuần_từ_hoạt_động_kinh_doanh_trước_chi_phí_dự_phòng_rủi_ro_tín_dụng_i_ii_iii_iv_v_vi_vii_viii",
        ),
        only=(Template.BANK,),
    ),
    _item(
        "credit_provision_expense", "IS", _F, "Chi phí dự phòng rủi ro tín dụng",
        bank=("is_provision_for_credit_losses", "x_chi_phí_dự_phòng_rủi_ro_tín_dụng"),
        only=(Template.BANK,),
        sign=_EXP,
    ),
    _item(
        "customer_loans_gross", "BS", _S, "Cho vay khách hàng (gộp)",
        bank=("bs_loans_to_customers_gross", "1_cho_vay_và_cho_thuê_tài_chính_khách_hàng"),
        only=(Template.BANK,),
        zero_is_missing=True,
    ),
    _item(
        "loan_loss_reserve", "BS", _S, "Dự phòng rủi ro cho vay khách hàng",
        bank=(
            "bs_provision_loans_to_customers",
            "2_dự_phòng_rủi_ro_cho_vay_và_cho_thuê_tài_chính_khách_hàng",
        ),
        only=(Template.BANK,),
        sign=_OUT,
    ),
    _item(
        "customer_loans_net", "BS", _S, "Cho vay khách hàng (thuần)",
        bank=("bs_loans_to_customers", "vi_cho_vay_khách_hàng"),
        only=(Template.BANK,),
        zero_is_missing=True,
    ),
    _item(
        "customer_deposits", "BS", _S, "Tiền gửi của khách hàng",
        bank=("bs_customer_deposits", "iii_tiền_gửi_của_khách_hàng"),
        only=(Template.BANK,),
        zero_is_missing=True,
    ),
    _item(
        "valuable_papers_issued", "BS", _S, "Phát hành giấy tờ có giá",
        bank=("bs_valuable_papers_issued", "vi_phát_hành_giấy_tờ_có_giá"),
        only=(Template.BANK,),
    ),
    _item(
        "interbank_funding", "BS", _S, "Tiền gửi và vay các TCTD khác",
        bank=(
            "bs_placements_and_borrowings_from_credit_institutions",
            "ii_tiền_gửi_và_vay_các_tctd_khác",
        ),
        only=(Template.BANK,),
    ),
    _item(
        "due_to_government_sbv", "BS", _S, "Các khoản nợ Chính phủ và NHNN",
        bank=("bs_due_to_government_and_sbv", "i_các_khoản_nợ_chính_phủ_và_nhnn"),
        only=(Template.BANK,),
    ),
    _item(
        "investment_securities", "BS", _S, "Chứng khoán đầu tư",
        bank=("bs_investment_securities", "viii_chứng_khoán_đầu_tư"),
        only=(Template.BANK,),
    ),
    _item(
        "interest_fee_receivable", "BS", _S, "Các khoản lãi, phí phải thu",
        bank=("bs_interest_and_fee_receivables", "2_các_khoản_lãi_phí_phải_thu"),
        only=(Template.BANK,),
    ),
    # ── Securities (TT210/TT334) ──────────────────────────────────────────────
    _item(
        "brokerage_revenue", "IS", _F, "Doanh thu môi giới chứng khoán",
        securities=("is_revenue_from_brokerage_services", "1_6_doanh_thu_môi_giới_chứng_khoán"),
        only=(Template.SECURITIES,),
    ),
    _item(
        "margin_lending_income", "IS", _F, "Lãi từ các khoản cho vay và phải thu",
        securities=("is_interest_income_from_loans_and_receivables", "1_3_lãi_từ_các_khoản_cho_vay_và_phải_thu"),
        only=(Template.SECURITIES,),
    ),
    _item(
        "fvtpl_gains", "IS", _F, "Lãi từ tài sản tài chính FVTPL",
        securities=(
            "is_gains_from_financial_assets_at_fair_value_through_profit_or_loss_fvtpl",
            "1_1_lãi_từ_các_tài_sản_tài_chính_ghi_nhận_thông_qua_lãi_lỗ_fvtpl",
        ),
        only=(Template.SECURITIES,),
    ),
    _item(
        "fvtpl_losses", "IS", _F, "Lỗ từ tài sản tài chính FVTPL",
        securities=(
            "is_loss_from_fvtpl_financial_assets",
            "2_1_lỗ_các_tài_sản_tài_chính_ghi_nhận_thông_qua_lỗ_fvtpl",
        ),
        only=(Template.SECURITIES,),
        sign=_EXP,
    ),
    _item(
        "htm_income", "IS", _F, "Lãi từ khoản đầu tư HTM",
        securities=(
            "is_gains_from_held_to_maturity_investments",
            "1_2_lãi_từ_các_khoản_đầu_tư_nắm_giữ_đến_ngày_đáo_hạn_htm",
        ),
        only=(Template.SECURITIES,),
    ),
    _item(
        "afs_income", "IS", _F, "Lãi từ tài sản tài chính AFS",
        securities=(
            "is_gains_from_available_for_sale_financial_assets_afs",
            "1_4_lãi_từ_các_tài_sản_tài_chính_sẵn_sàng_để_bán_afs",
        ),
        only=(Template.SECURITIES,),
    ),
    _item(
        "realised_profit", "IS", _F, "Lợi nhuận đã thực hiện",
        securities=("is_realised_profit", "9_1_lợi_nhuận_đã_thực_hiện"),
        only=(Template.SECURITIES,),
    ),
    _item(
        "unrealised_profit", "IS", _F, "Lợi nhuận chưa thực hiện",
        securities=("is_unrealised_profit", "9_2_lợi_nhuận_chưa_thực_hiện"),
        only=(Template.SECURITIES,),
    ),
    _item(
        "margin_loans", "BS", _S, "Các khoản cho vay (margin & ứng trước)",
        securities=("bs_loans", "4_các_khoản_cho_vay"),
        only=(Template.SECURITIES,),
    ),
    _item(
        "fvtpl_assets", "BS", _S, "Tài sản tài chính FVTPL",
        securities=("bs_fvtpl_financial_assets", "2_các_tài_sản_tài_chính_ghi_nhận_thông_qua_lãi_lỗ_fvtpl"),
        only=(Template.SECURITIES,),
    ),
    _item(
        "htm_assets", "BS", _S, "Các khoản đầu tư nắm giữ đến ngày đáo hạn",
        securities=(
            "bs_held_to_maturity_securities",
            "bs_held_to_maturity_investments",
            "3_các_khoản_đầu_tư_nắm_giữ_đến_ngày_đáo_hạn_htm",
        ),
        only=(Template.SECURITIES,),
    ),
    _item(
        "afs_assets", "BS", _S, "Tài sản tài chính sẵn sàng để bán",
        securities=("bs_available_for_sale_financial_assets_afs", "5_các_tài_sản_tài_chính_sẵn_sàn_để_bán_afs"),
        only=(Template.SECURITIES,),
    ),
    _item(
        "bonds_issued_st", "BS", _S, "Trái phiếu phát hành ngắn hạn",
        securities=("bs_short_term_bonds",),
        only=(Template.SECURITIES,),
    ),
    _item(
        "bonds_issued_lt", "BS", _S, "Trái phiếu phát hành dài hạn",
        securities=("bs_long_term_bonds",),
        only=(Template.SECURITIES,),
    ),
    # ── Insurance (TT232) ─────────────────────────────────────────────────────
    _item(
        "gross_written_premium", "IS", _F, "Doanh thu phí bảo hiểm",
        insurance=("is_gross_written_premium", "1_doanh_thu_phí_bảo_hiểm_01_01_1_01_2_01_3"),
        only=(Template.INSURANCE,),
    ),
    _item(
        "net_premium", "IS", _F, "Doanh thu phí bảo hiểm thuần",
        insurance=("is_net_insurance_premium", "3_doanh_thu_phí_bảo_hiểm_thuần_03_01_02"),
        only=(Template.INSURANCE,),
    ),
    _item(
        "net_insurance_revenue", "IS", _F, "Doanh thu thuần hoạt động kinh doanh bảo hiểm",
        insurance=("is_total_net_revenue_from_insurance_business", "5_doanh_thu_thuần_hđkd_bh_10_03_04"),
        only=(Template.INSURANCE,),
    ),
    _item(
        "insurance_claims", "IS", _F, "Tổng chi bồi thường bảo hiểm",
        insurance=("is_total_insurance_claims_expenses", "10_tổng_chi_bồi_thường_bảo_hiểm_15_11_12_13_14"),
        only=(Template.INSURANCE,),
        sign=_EXP,
    ),
    _item(
        "insurance_gross_profit", "IS", _F, "Lợi nhuận gộp hoạt động kinh doanh bảo hiểm",
        insurance=("is_gross_insurance_operating_profit", "14_lợi_nhuận_gộp_hoạt_động_kinh_doanh_bảo_hiểm"),
        only=(Template.INSURANCE,),
    ),
    _item(
        "financial_activity_profit", "IS", _F, "Lợi nhuận gộp hoạt động tài chính",
        insurance=("is_profit_form_financial_activities", "20_lợi_nhuận_gộp_hoạt_động_tài_chính"),
        only=(Template.INSURANCE,),
    ),
    _item(
        "technical_reserves", "BS", _S, "Dự phòng nghiệp vụ",
        insurance=("bs_insurance_reserves", "13_dự_phòng_nghiệp_vụ"),
        only=(Template.INSURANCE,),
    ),
)

CATALOG_BY_ID: dict[str, CanonicalItem] = {it.item_id: it for it in CATALOG}


def items_for_template(template: Template) -> tuple[CanonicalItem, ...]:
    return tuple(it for it in CATALOG if template in it.templates)
