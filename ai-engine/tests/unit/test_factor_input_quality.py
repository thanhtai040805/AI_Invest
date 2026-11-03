from datetime import date, timedelta
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.services.factor_service import FactorService


@pytest.fixture
def inputs(monkeypatch):
    cursor=MagicMock()
    cursor.fetchone.return_value=[10.,1.,None,.1,.03,0.,None,None,.2,0.,0.,date(2026,6,30),None]
    cursor.fetchall.return_value=[(20.,100.,0.,date(2026,8,20)-timedelta(days=i)) for i in range(60)]
    connection=MagicMock()
    connection.cursor.return_value.__enter__.return_value=cursor
    context=MagicMock()
    context.return_value.__enter__.return_value=connection
    monkeypatch.setattr('app.domain.services.factor_service.get_conn',context)
    return cursor


def test_real_zero_financial_values_are_preserved(inputs):
    inputs.fetchone.return_value=[10.,1.,None,0.,0.,0.,None,None,0.,0.,0.,date(2026,6,30),None]
    factors=FactorService.__new__(FactorService).compute_factors_for_ticker('TEST',date(2026,8,20))
    assert factors['raw_metrics']['roe']==0
    assert factors['raw_metrics']['de']==0
    assert factors['f2_quality']==52
    assert factors['f4_earnings']==50
    assert factors['raw_metrics']['missing_inputs']==[]
    assert factors['raw_metrics']['source']['as_of']=='2026-08-20'
    assert factors['raw_metrics']['source']['market_data_date']=='2026-08-20'
    assert factors['raw_metrics']['source']['ratio_date']=='2026-06-30'
    assert factors['raw_metrics']['source']['cache_complete'] is True


@pytest.mark.parametrize('index,name',[(0,'pe'),(1,'pb')])
def test_zero_valuation_multiple_is_unavailable_not_a_cheap_asset(inputs,index,name):
    inputs.fetchone.return_value[index]=0
    factors=FactorService.__new__(FactorService).compute_factors_for_ticker('TEST',date(2026,8,20))
    assert factors['raw_metrics'][name]==0
    assert factors['raw_metrics']['missing_inputs']==[name+'_zero']


@pytest.mark.parametrize('index,name',[(0,'pe'),(1,'pb'),(3,'roe'),(5,'debt_equity'),(8,'net_margin'),(9,'yoy_revenue_growth'),(10,'yoy_earnings_growth')])
def test_missing_used_financial_inputs_are_reported(inputs,index,name):
    inputs.fetchone.return_value[index]=None
    result=FactorService.__new__(FactorService).compute_factors_for_ticker('TEST',date(2026,8,20))
    assert result['raw_metrics']['missing_inputs']==[name]


def test_short_market_history_is_not_marked_complete(inputs):
    inputs.fetchall.return_value=inputs.fetchall.return_value[:59]
    result=FactorService.__new__(FactorService).compute_factors_for_ticker('TEST',date(2026,8,20))
    assert result['raw_metrics']['missing_inputs']==['market_window_60']


@pytest.mark.parametrize('index,name',[(1,'market_volume'),(2,'market_foreign_flow')])
def test_missing_market_inputs_are_not_treated_as_zero(inputs,index,name):
    row=list(inputs.fetchall.return_value[0]);row[index]=None
    inputs.fetchall.return_value[0]=tuple(row)
    result=FactorService.__new__(FactorService).compute_factors_for_ticker('TEST',date(2026,8,20))
    assert result['raw_metrics']['missing_inputs']==[name]


@pytest.mark.parametrize('missing,persisted,expected_count',[(['pe'],True,0),([],False,0),([],True,1)])
def test_daily_batch_counts_only_complete_successfully_persisted_scores(inputs,monkeypatch,missing,persisted,expected_count):
    inputs.fetchall.return_value=[('TEST',60)]
    repository=MagicMock()
    repository.save_factor_score.return_value=persisted
    monkeypatch.setattr('app.domain.repositories.intelligence_repository.IntelligenceRepository',lambda:repository)
    service=FactorService.__new__(FactorService)
    service.compute_factors_for_ticker=MagicMock(return_value={
        'f1_value':50,'f2_quality':50,'f3_momentum':50,'f4_earnings':50,'f5_flow':50,'f6_technical':50,
        'raw_metrics':{'missing_inputs':missing,'source':{'producer':'FACTOR_SERVICE','as_of':'2026-08-20','cache_complete':not missing}}})
    result=service.compute_daily_factors(date(2026,8,20))
    assert result['symbols_computed']==expected_count
    assert bool(result['errors'])==(expected_count==0)
    if missing:repository.save_factor_score.assert_not_called()
    if expected_count:
        assert repository.save_factor_score.call_args.kwargs['factor_details_extra']['source']['as_of']=='2026-08-20'


def test_partial_factor_batch_is_failed_without_switching_producers(monkeypatch):
    from app.domain.pipeline.daily_etl import DailyETLPipeline
    batch={'symbols_computed':1,'total_symbols':2,'errors':{'TEST':'Missing factor inputs: pe'}}
    monkeypatch.setattr('app.domain.services.factor_service.FactorService',lambda:SimpleNamespace(compute_daily_factors=lambda day:batch))
    fallback=MagicMock(side_effect=AssertionError('Must not replace incomplete inputs with a different producer'))
    monkeypatch.setattr('app.infrastructure.vendors.vn.factor_scores.refresh_all',fallback)
    result=asyncio.run(DailyETLPipeline.step_factor_scores(SimpleNamespace(trade_date=date(2026,8,20))))
    assert result['status']=='failed'
    fallback.assert_not_called()


def test_factor_exception_never_falls_back_to_vn(monkeypatch):
    from app.domain.pipeline.daily_etl import DailyETLPipeline
    compute=MagicMock(side_effect=RuntimeError('source unavailable'))
    monkeypatch.setattr('app.domain.services.factor_service.FactorService',lambda:SimpleNamespace(compute_daily_factors=compute))
    fallback=MagicMock(side_effect=AssertionError('Different factor definitions must not replace Generic'))
    monkeypatch.setattr('app.infrastructure.vendors.vn.factor_scores.refresh_all',fallback)
    result=asyncio.run(DailyETLPipeline.step_factor_scores(SimpleNamespace(trade_date=date(2026,8,20))))
    assert result=={'status':'failed','error':'source unavailable'}
    fallback.assert_not_called()


def test_daily_rebuild_invalidates_old_scores_even_when_inputs_are_missing(inputs,monkeypatch):
    inputs.rowcount=3
    service=FactorService.__new__(FactorService)
    def compute(symbol,day):
        assert inputs.execute.call_args.args[0].startswith('DELETE FROM factor_scores')
        return {'raw_metrics':{'missing_inputs':['pe']}}
    service.compute_factors_for_ticker=compute
    repository=MagicMock()
    monkeypatch.setattr('app.domain.repositories.intelligence_repository.IntelligenceRepository',lambda:repository)
    result=service.compute_daily_factors(date(2026,8,20),['test'])
    assert result['factor_scores_invalidated']==3
    assert result['symbols_computed']==0
    assert result['errors']=={'TEST':'Missing factor inputs: pe'}
    repository.save_factor_score.assert_not_called()


def test_vn_producer_cannot_write_generic_cache(monkeypatch):
    from app.infrastructure.vendors.vn import factor_scores
    compute=MagicMock(side_effect=AssertionError('VN cache must not be produced'))
    monkeypatch.setattr(factor_scores,'compute_factor_scores',compute)
    with pytest.raises(RuntimeError,match='cannot write factor_scores'):
        factor_scores.refresh_all(date(2026,8,20))
    compute.assert_not_called()


def test_daily_default_reports_short_history_without_hiding_financial_errors(inputs,monkeypatch):
    inputs.fetchall.return_value=[('NEW',59),('READY',60)]
    service=FactorService.__new__(FactorService)
    service.compute_factors_for_ticker=MagicMock(return_value={'raw_metrics':{'missing_inputs':['pe']}})
    monkeypatch.setattr('app.domain.repositories.intelligence_repository.IntelligenceRepository',MagicMock())
    result=service.compute_daily_factors(date(2026,8,20))
    assert result['ineligible_symbols']=={'NEW':{'reason':'FACTOR_HISTORY_INSUFFICIENT','observations':59,'required':60}}
    assert result['total_symbols']==1 and result['errors']=={'READY':'Missing factor inputs: pe'}
    service.compute_factors_for_ticker.assert_called_once_with('READY',date(2026,8,20))
    assert inputs.execute.call_args.args[1]==(date(2026,8,20),['NEW','READY'])


def test_daily_job_is_not_marked_completed_with_incomplete_factors(monkeypatch):
    from app.domain.pipeline import daily_etl
    pipeline=daily_etl.DailyETLPipeline()
    for name in ['step_ohlcv_backfill','step_technical_indicators','step_garch_volatility','step_beta_alpha',
                 'step_insider_trades','step_foreign_flow','step_financial_ratios','step_corporate_actions',
                 'step_corporate_documents','step_macro_indicators']:
        monkeypatch.setattr(pipeline,name,AsyncMock(return_value={'status':'success','target_rows':1}))
    monkeypatch.setattr(pipeline,'step_factor_scores',AsyncMock(return_value={'status':'failed','errors':{'TEST':'pe'}}))
    failed=MagicMock();completed=MagicMock()
    monkeypatch.setattr(daily_etl,'set_running',MagicMock())
    monkeypatch.setattr(daily_etl,'set_failed',failed)
    monkeypatch.setattr(daily_etl,'set_completed',completed)
    result=asyncio.run(pipeline.run(trade_date=date(2026,8,20)))
    assert result['status']=='FAILED'
    failed.assert_called_once()
    completed.assert_not_called()
