"""Financial ratios must respect period basis and currency scale."""
from datetime import date

import pytest

from app.infrastructure.data_pipelines.financial_etl_cafef import derive_financial_ratios


def statements(period=date(2025,12,31),frequency='yearly',scale=1):
    def amounts(**values):return {k:v*scale for k,v in values.items()}
    return [
        (period,frequency,'BS',amounts(total_assets=1e12,total_liabilities=5e11,equity=5e11,
                                      interest_bearing_debt=2e11,cash_and_cash_equivalents=1e11,short_term_investments=0),None,'cafef'),
        (period,frequency,'IS',amounts(net_income=1e11,net_revenue=5e11,EBITDA=1e12),None,'cafef'),
        (period,frequency,'CF',amounts(net_cash_inflows_outflows_from_operating_activities=2e11,capital_expenditures=-5e10),None,'cafef'),
        (period,frequency,'ratios',{'P/E':10},None,'cafef'),
    ]


def test_annual_yields_use_annual_flows_and_are_invariant_to_currency_scale():
    first=derive_financial_ratios('TEST',statements())[0]
    scaled=derive_financial_ratios('TEST',statements(scale=10))[0]
    assert first[11]==pytest.approx(.15)
    assert scaled[11]==pytest.approx(.15)
    assert first[12]==pytest.approx(1.1)
    assert scaled[12]==pytest.approx(1.1)


def test_quarterly_flow_does_not_guess_ttm_from_amount_magnitude():
    row=derive_financial_ratios('TEST',statements(frequency='quarterly'))[0]
    assert row[11] is None
    assert row[12] is None


def test_yoy_requires_the_same_period_in_the_previous_year():
    rows=statements(period=date(2026,6,30),frequency='quarterly')
    rows+=statements(period=date(2024,6,30),frequency='quarterly',scale=.5)
    row=next(r for r in derive_financial_ratios('TEST',rows) if r[1]==date(2026,6,30))
    assert row[13] is None
    assert row[14] is None


@pytest.mark.parametrize('missing', ['interest_bearing_debt','cash_and_cash_equivalents','short_term_investments'])
def test_ev_does_not_assume_missing_balance_sheet_components_are_zero(missing):
    rows=statements()
    del rows[0][3][missing]
    assert derive_financial_ratios('TEST',rows)[0][12] is None


def test_reported_ev_multiple_is_preserved_without_guessing_quarterly_ttm():
    rows=statements(frequency='quarterly')
    rows[-1][3]['EV/EBITDA']=6.25
    assert derive_financial_ratios('TEST',rows)[0][12]==6.25


@pytest.mark.parametrize('codes_only', [False, True])
def test_insurance_ratios_use_business_revenue_and_gross_profit_not_premiums(codes_only):
    current=date(2026,6,30)
    previous=date(2025,6,30)
    revenue_key='is_total_net_revenue_from_insurance_business' if codes_only else '‣ Doanh thu thuần từ hoạt động kinh doanh bảo hiểm'
    gross_key='is_gross_insurance_operating_profit' if codes_only else 'Lợi nhuận gộp hoạt động kinh doanh bảo hiểm'
    rows=[
        (current,'quarterly','BS',{'total_assets':2000,'equity':1000},None,'alphastock'),
        (current,'quarterly','IS',{revenue_key:1000,gross_key:250,'net_income':100,
                                  'Doanh thu phí bảo hiểm thuần':700,'net_revenue':1400,'gross_profit':60},None,'alphastock'),
        (previous,'quarterly','IS',{revenue_key:800,'Doanh thu phí bảo hiểm thuần':900},None,'alphastock'),
        (current,'quarterly','ratios',{'rt_prt_gross_margin':.9},None,'alphastock'),
    ]
    result=derive_financial_ratios('TEST',rows)[0]
    assert result[9]==pytest.approx(.25)
    assert result[10]==pytest.approx(.10)
    assert result[13]==pytest.approx(.25)


def test_insurance_premium_alone_is_not_business_revenue():
    rows=[(date(2026,6,30),'quarterly',kind,payload,None,'alphastock') for kind,payload in [
        ('BS',{'total_assets':2000,'equity':1000}),
        ('IS',{'Doanh thu phí bảo hiểm thuần':700,'net_income':100}),
    ]]
    assert derive_financial_ratios('TEST',rows)[0][10] is None


@pytest.mark.parametrize('published,expected',[(None,None),('2025-07-25',date(2025,7,25))])
def test_publication_date_comes_from_source_not_crawl_clock(monkeypatch,published,expected):
    from app.infrastructure.data_pipelines import financial_etl_alphastock as alpha
    monkeypatch.setattr(alpha,'_get_workspace',lambda symbol:{'statements':{'income_statement':{
        'quarter':{'data':[{'period_end_date':'2025-06-30','published_at':published,'metrics_json':{'net_income':100}}]}}}})
    seen=[]
    def store(cur,symbol,period,kind,frequency,data,published_date,**options):seen.append(published_date)
    monkeypatch.setattr(alpha,'_upsert',store)
    alpha.fetch_and_store_financials('TEST',object(),backfill=True,refresh_ratios=False)
    assert seen==[expected]
