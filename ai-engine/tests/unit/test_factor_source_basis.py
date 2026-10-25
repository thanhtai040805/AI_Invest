"""Source-score contract without Agent startup, database or LLM calls."""
from datetime import date, timedelta
import json
from unittest.mock import MagicMock

import pytest
import pandas as pd

from app.infrastructure.vendors.vn import factor_scores as scores
from app.domain.services import financial_fields as fields


class SourceCursor:
    def __init__(self, bank_debt=15.):
        self.bank_debt = bank_debt
        self.queries = []

    def execute(self, query, params=None):
        self.query = query
        self.queries.append(query)

    def fetchone(self):
        return None

    def fetchall(self):
        q = self.query
        symbols = ['CTG', 'MSB', 'NAB', 'HPG']
        if 'FROM financial_ratios' in q:
            return [
                (sym, 8., 1., roe, .01, self.bank_debt, .2, .1, .03, 8., .1, growth, period, None)
                for sym, roe, growth, period in [
                    ('CTG', .05, .1, date(2026,6,30)), ('MSB', .10, .2, date(2026,6,30)),
                    ('NAB', .90, .9, date(2026,3,31)), ('HPG', .99, .99, date(2026,6,30))]
            ]
        if 'FROM ohlcv_unadjusted' in q:
            return [(sym, date(2026,8,21)-timedelta(days=i), 30.-i*.01, 1e6)
                    for sym in symbols for i in range(150)]
        if 'SELECT symbol, market_cap' in q:
            return [(sym, 1e12) for sym in symbols]
        if 'SELECT symbol, ceiling, floor' in q:
            return [(sym, None, None) for sym in symbols]
        if 'SELECT symbol, sector, industry' in q:
            return [(sym, 'BASIC_RESOURCES' if sym=='HPG' else 'BANKS', None) for sym in symbols]
        return []


def test_source_keeps_core_fraction_and_prod_global_export_with_sector_details(monkeypatch):
    seen = []
    original = scores.compute_quality_score

    def observe(frame, group):
        seen.append((frame.copy(), group))
        return original(frame, group)

    monkeypatch.setattr(scores, 'compute_quality_score', observe)
    cursor = SourceCursor()
    rows = {row[0]:row for row in scores.compute_factor_scores(['CTG','MSB','NAB','HPG'], date(2026,8,23),cursor)}
    ctg = rows['CTG']; details = json.loads(ctg[20])
    assert any(frame.loc['CTG','roe_norm']==.05 for frame, _ in seen if 'CTG' in frame.index)
    assert [rows[sym][3] for sym in ['CTG','MSB','NAB','HPG']] == [25.,50.,75.,81.25]
    assert details['source']['quality_peer_count'] == 4
    assert details['source']['quality_benchmark'] == 'PROD_GLOBAL_ELIGIBLE_UNIVERSE'
    assert details['source']['sector_group'] == 'FINANCIALS'
    assert details['source']['nim_status'] == 'UNAVAILABLE_NO_VERIFIED_SOURCE'
    assert ctg[2] == 50.
    assert details['quality_score'] == pytest.approx(100./3., abs=.05)
    assert ctg[3] != details['quality_score']
    assert details['financial_inputs']['roe'] == .05
    assert details['source']['roe_basis'] == 'REPORTED_PERIOD_NOT_ANNUALIZED_OR_TTM'
    high_debt = scores.compute_factor_scores(['CTG','MSB','NAB','HPG'],date(2026,8,23),SourceCursor(150.))
    assert next(row[3] for row in high_debt if row[0]=='CTG') == ctg[3]
    technical_query = next(q for q in cursor.queries if 'FROM technical_indicators' in q)
    assert 'calc_date <= %s' in technical_query
    assert not any('FROM factor_scores' in q for q in cursor.queries)


def test_source_exact_mapping_does_not_confuse_adjacent_profit_or_equity_lines():
    inc = {'20_lợi_nhuận_sau_thuế_chưa_phân_phối':999.,
           '19_lợi_nhuận_sau_thuế_của_cổ_đông_công_ty_mẹ':12.,
           '18_lợi_nhuận_sau_thuế_thu_nhập_doanh_nghiệp':20.}
    bs = {'Tổng nguồn vốn':10000., 'Nợ phải trả':9000., 'D_VỐN CHỦ SỞ HỮU':1000.}
    assert scores._get_val(inc, fields.NI_KEYS) == 20.
    assert scores._get_val(dict(reversed(list(inc.items()))),fields.NI_KEYS) == 20.
    assert scores._get_val(bs,fields.EQUITY_KEYS) == 1000.
    assert scores._get_val({'lợi nhuận sau thuế của cổ đông công ty mẹ':12.},fields.NI_KEYS) is None


def test_latest_period_query_limits_each_statement_after_availability_filter():
    cur=MagicMock()
    cur.fetchall.return_value=[('CTG','IS',date(2026,6,30),'{"net_income":20}'),
                              ('CTG','IS',date(2026,3,31),{'net_income':10}),
                              ('CTG','BS',date(2026,6,30),{'equity':100})]
    result=scores._extract_multi_stmts(cur,['CTG'],date(2026,9,17),n_quarters=2)
    assert [item['period_end'] for item in result['CTG']['IS']]==[date(2026,6,30),date(2026,3,31)]
    assert result['CTG']['IS'][0]['data']['net_income']==20
    assert result['CTG']['BS'][0]['data']['equity']==100
    sql,params=cur.execute.call_args.args
    assert params==(['CTG'],date(2026,9,17),date(2026,9,17),2)
    assert sql.index('fs.period_end <= %s') < sql.index('LIMIT %s')
    assert sql.index(') <= %s') < sql.index('LIMIT %s')
    assert 'fs.symbol = u.symbol AND fs.statement_type = t.statement_type' in sql


def test_prod_detail_missing_and_single_observation_behavior_is_preserved():
    frame=pd.DataFrame({'roe_norm':[.05,None], 'yoy_earnings_growth':[.1,.2]},index=['CTG','MSB'])
    result=scores.compute_quality_score(frame,scores.FINANCIALS)
    assert result.tolist()==[75.,50.]
    assert scores.compute_quality_score(frame[['roe_norm']],scores.FINANCIALS).tolist()==[100.,0.]
    assert scores.compute_quality_score(frame.iloc[:1],scores.FINANCIALS).tolist()==[100.]


@pytest.mark.parametrize('group', [scores.FINANCIALS, scores.REAL_ESTATE, scores.OTHERS])
def test_exported_factors_keep_prod_component_sets_and_equal_weights(monkeypatch,group):
    monkeypatch.setattr(scores,'classify',lambda industry,symbol:group)
    rows=scores.compute_factor_scores(['CTG','MSB','NAB','HPG'],date(2026,8,23),SourceCursor())
    for row,quality_rank in zip(rows,[25.,50.,75.,100.]):
        if group==scores.FINANCIALS:
            expected_value=(62.5+quality_rank+62.5)/3.
            expected_quality=quality_rank
        elif group==scores.REAL_ESTATE:
            expected_value=62.5
            expected_quality=(2*quality_rank+2*62.5)/4.
        else:
            expected_value=62.5
            expected_quality=(2*quality_rank+2*62.5)/4.
        assert row[2]==pytest.approx(expected_value)
        assert row[3]==pytest.approx(expected_quality)


def test_sector_code_does_not_override_prod_industry_classifier(monkeypatch):
    classify=MagicMock(return_value=scores.OTHERS)
    monkeypatch.setattr(scores,'classify',classify)
    rows=scores.compute_factor_scores(['CTG','MSB','NAB','HPG'],date(2026,8,23),SourceCursor())
    assert classify.call_count==4
    assert all(json.loads(row[20])['source']['sector_group']==scores.OTHERS for row in rows)
