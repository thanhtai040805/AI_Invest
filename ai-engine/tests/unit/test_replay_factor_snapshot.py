"""Replay source isolation without Agent registration, network or database writes."""
import ast
import asyncio
import hashlib
from contextlib import nullcontext
from copy import deepcopy
from datetime import date, datetime
import json
import logging
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from app.core.base_agent import BaseAgent
from app.domain.repositories.financial_repository import FinancialRepository
from app.domain.repositories.intelligence_repository import IntelligenceRepository
from app.domain.repositories.replay_factor_snapshot import (
    LEARNING_FACTOR_SQL, ReplayFactorSnapshot, current_factor_snapshot,
)
from app.domain.rules.scoring import CSSScoringEngine

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('failure',[None,'purpose','snapshot','database','table'])
def test_historical_reproduction_requires_exact_restored_local_inputs(tmp_path,monkeypatch,failure):
    snapshot=ReplayFactorSnapshot(data())
    fingerprint={'rows':1,'sha256':hashlib.sha256(b'row').hexdigest()}
    manifest={'purpose':'HISTORICAL_BASELINE_REPRODUCTION','target_database':'local_archive',
              'factor_snapshot_sha256':snapshot.sha256,
              'tables':{t:dict(fingerprint) for t in ('financial_statements','financial_ratios','factor_scores')}}
    connection=MagicMock()
    connection.get_dsn_parameters.return_value={'host':'localhost','dbname':'local_archive'}
    cursor=connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value=[('row',)]
    context=MagicMock();context.return_value.__enter__.return_value=connection
    monkeypatch.setattr('app.domain.repositories.replay_factor_snapshot.get_conn',context)
    if failure=='purpose':manifest['purpose']='CURRENT_CACHE_CERTIFICATION'
    if failure=='snapshot':manifest['factor_snapshot_sha256']='wrong'
    if failure=='database':connection.get_dsn_parameters.return_value={'host':'prod.example','dbname':'local_archive'}
    if failure=='table':manifest['tables']['factor_scores']['sha256']='changed'
    path=tmp_path/'archive.json';path.write_text(json.dumps(manifest),encoding='utf8')
    if failure:
        with pytest.raises(ValueError):snapshot.require_historical_inputs(path)
        assert not snapshot.historical_reproduction
    else:
        snapshot.require_historical_inputs(path)
        assert snapshot.historical_reproduction
        assert all('SELECT' in call.args[0] or 'READ ONLY' in call.args[0]
                   or call.args[0] == "SET LOCAL TIME ZONE 'Asia/Ho_Chi_Minh'"
                   for call in cursor.execute.call_args_list)


def load_class(filename, name, **extra):
    path = ROOT / 'app/domain/agents' / filename
    tree = ast.parse(path.read_text(encoding='utf-8'))
    body = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
    body += [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name]
    namespace = dict(globals(), logger=logging.getLogger(__name__), **extra)
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(path), 'exec'), namespace)
    return namespace[name]


def data(quality=69.87):
    return dict(version=1, as_of_dates=['2026-08-21','2026-08-23'], factor_rows=[
        ['CTG','2026-08-21',75.,quality,60.,94.,51.,54.,70.,90.,{'f2_quality':quality}],
    ], learning_rows={day:[['CTG',75.,quality,61.,94.,51.,70.]] for day in ['2026-08-21','2026-08-23']})


def dated_details(day='2026-08-21', **extra):
    return {'source':{'producer':'FACTOR_SERVICE','as_of':day,'cache_complete':True},
            'f1_value':75.,'f2_quality':69.87,'f3_momentum':60.,'f4_earnings':94.,'f5_flow':51.,'f6_technical':54.,**extra}


def dated_data(quality=69.87):
    result=data(quality)
    result['factor_rows'][0][10]=dated_details(f2_quality=quality)
    return result


@pytest.mark.parametrize('failure', [None, 'missing_symbol', 'missing_pe', 'invalid_score'])
def test_extend_archive_with_complete_vn_scores_only(monkeypatch, failure):
    import sys
    import types
    source = ReplayFactorSnapshot(data())
    source.historical_reproduction = True
    day = date(2026, 8, 24)
    details = {'source': {'producer': 'VN_FACTOR_SCORES', 'as_of': str(day), 'cache_complete': True},
               'financial_inputs': {'pe': None if failure == 'missing_pe' else 10, 'pb': 1, 'roe': .1}}
    row = ['CTG', day, *([60.] * 18), json.dumps(details)]
    if failure == 'invalid_score':
        row[5] = None
    module = types.ModuleType('app.infrastructure.vendors.vn.factor_scores')
    module.select_scoring_symbols = lambda cur, day: ['CTG']
    module.compute_factor_scores = lambda *args: [] if failure == 'missing_symbol' else [row]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    connection = MagicMock()
    monkeypatch.setattr('app.domain.repositories.replay_factor_snapshot.get_conn', lambda: connection)
    if failure:
        with pytest.raises(ValueError, match='coverage|Incomplete'):
            source.extend_with_vn_producer([day])
        return
    combined = source.extend_with_vn_producer([date(2026,8,21), day])
    assert source.sha256 == ReplayFactorSnapshot(data()).sha256
    assert combined.factor_rows('CTG', date(2026,8,21)) == source.factor_rows('CTG', date(2026,8,21))
    assert combined.allows_historical_defaults(date(2026,8,21))
    assert not combined.allows_historical_defaults(day)
    assert combined.factor_rows('CTG', day)[0][2:8] == [60.] * 6
    sql = ' '.join(str(call) for call in connection.mock_calls)
    assert 'READ ONLY' in sql
    assert 'INSERT' not in sql and 'UPDATE' not in sql and 'DELETE' not in sql


def test_saved_snapshot_is_defensive_and_reusable_and_rejects_tampering(tmp_path):
    source = data(); snapshot = ReplayFactorSnapshot(source)
    source['factor_rows'][0][3] = 15
    first = snapshot.factor_rows('CTG', date(2026,8,21))
    first[0][10]['f2_quality'] = 15
    assert snapshot.factor_rows('CTG',date(2026,8,21))[0][10]['f2_quality'] == 69.87
    assert snapshot.factor_rows('CTG',date(2026,8,23)) == []
    with pytest.raises(ValueError, match='outside'):
        snapshot.learning_rows(date(2026,8,24))
    path = tmp_path/'snapshot.json'; snapshot.save(path)
    assert ReplayFactorSnapshot.load(path).sha256 == snapshot.sha256
    with pytest.raises(FileExistsError):snapshot.save(path)
    payload = json.loads(path.read_text()); payload['data']['factor_rows'][0][3] = 15
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='hash mismatch'):ReplayFactorSnapshot.load(path)


def test_repository_reads_frozen_missing_and_present_values_and_blocks_source_writes():
    storage = MagicMock(); storage.fetch_all.return_value = [('CTG',date(2026,8,21),75.,15.,60.,94.,51.,54.,70.,90.,dated_details(f2_quality=15.))]
    repo = IntelligenceRepository(storage)
    with ReplayFactorSnapshot(data()).activate():
        assert repo.get_factor_score('ctg',date(2026,8,21))['f2_quality'] == 69.87
        assert repo.get_factor_score('CTG',date(2026,8,23)) is None
        storage.fetch_all.assert_not_called()
        with pytest.raises(ValueError, match='outside'):repo.get_factor_score('CTG',date(2026,8,24))
        with pytest.raises(RuntimeError, match='cannot write'):
            repo.save_factor_score('CTG',75.,15.,60.,94.,51.,54.,70.)
        storage.execute.assert_not_called()
    assert repo.get_factor_score('CTG',date(2026,8,21))['f2_quality'] == 15
    assert repo.save_factor_score('CTG',75.,15.,60.,94.,51.,54.,70.,percentile=90.,score_date=date(2026,8,21),factor_details_extra=dated_details())
    storage.execute.assert_called_once()


def test_contexts_do_not_leak_between_concurrent_replays_or_after_failure():
    async def run_one(quality):
        with ReplayFactorSnapshot(data(quality)).activate():
            await asyncio.sleep(0)
            assert current_factor_snapshot().learning_rows('2026-08-21')[0][2] == quality
            raise ValueError('test cleanup')
    async def run():
        results = await asyncio.gather(run_one(15.),run_one(69.87),return_exceptions=True)
        assert all(isinstance(value,ValueError) for value in results)
        assert current_factor_snapshot() is None
    asyncio.run(run())
    assert current_factor_snapshot() is None


def test_capture_uses_one_read_only_repeatable_read_transaction(monkeypatch):
    connection = MagicMock(); cursor = connection.cursor.return_value.__enter__.return_value
    cached_learning = [('MSB',40.,35.,60.,70.,80.,65.), ('CTG',75.,69.87,61.,94.,51.,70.),
                       ('MSB',30.,25.,50.,60.,70.,55.)]
    cached_factors = [('CTG', date(2026,8,21), 75.,69.87,60.,94.,51.,54.,70.,90.,dated_details(f2_quality=69.87))]
    cursor.fetchall.side_effect = [cached_factors, cached_learning]
    get_conn = MagicMock(); get_conn.return_value.__enter__.return_value = connection
    monkeypatch.setattr('app.domain.repositories.replay_factor_snapshot.get_conn',get_conn)
    compute = MagicMock(side_effect=AssertionError('Cache capture must not rebuild producer factors'))
    monkeypatch.setattr('app.infrastructure.vendors.vn.factor_scores.compute_factor_scores',compute)
    snapshot = ReplayFactorSnapshot.capture([date(2026,8,21)])
    assert snapshot.version == 1
    snapshot.require_current_source()
    assert snapshot.factor_rows('CTG',date(2026,8,21))[0][3] == 69.87
    calls = cursor.execute.call_args_list
    assert calls[0].args == ('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY',)
    compute.assert_not_called()
    assert snapshot.factor_rows('MSB',date(2026,8,21)) == []
    assert snapshot.learning_rows(date(2026,8,21)) == [list(row) for row in cached_learning]
    assert snapshot._data['source_policy'] == 'PROD_EXACT_DATE_CACHE_WITH_GENERIC_FALLBACK'
    cache_reads = [call for call in calls if 'FROM factor_scores' in call.args[0]]
    assert len(cache_reads) == 2
    assert 'score_date = ANY(%s)' in cache_reads[0].args[0]
    assert cache_reads[0].args[1] == ([date(2026,8,21)],)
    assert cache_reads[1].args == (LEARNING_FACTOR_SQL,(date(2026,8,21),))
    assert all(call.args[0].lstrip().startswith(('SET TRANSACTION','SELECT','WITH')) for call in calls)


@pytest.mark.parametrize('replay',[False,True])
@pytest.mark.parametrize('source',['cache','missing','neutral','historical_missing'])
def test_research_reads_cache_or_generic_and_only_writes_audit(monkeypatch,replay,source):
    cls = load_class('equity_research.py','EquityResearchAgent')
    agent = cls.__new__(cls); agent.scoring_engine=CSSScoringEngine(); agent.factor_service=MagicMock()
    factors={
        'f1_value':75.,'f2_quality':69.87,'f3_momentum':60.,'f4_earnings':94.,'f5_flow':51.,'f6_technical':54.,'factor_details':{}}
    cached=deepcopy(factors)
    if source=='neutral': cached.update(f3_momentum=50.,f4_earnings=50.,f5_flow=50.)
    agent.intel_repo=MagicMock()
    agent.intel_repo.get_factor_score.return_value=None if source in ('missing','historical_missing') else cached
    agent.factor_service.compute_factors_for_ticker.return_value={**factors,'raw_metrics':{'missing_inputs':['pe']} if source=='historical_missing' else {}}
    monkeypatch.setattr(FinancialRepository,'__init__',lambda self:None)
    monkeypatch.setattr(FinancialRepository,'get_latest_ratios',lambda *args,**kwargs:{})
    event=dict(ticker='CTG',sector='BANKS',current_price=32100,target_date='2026-08-24',
               market_data_date='2026-08-21',policy_weights={'f1_value':1.},is_replay=replay)
    snapshot=ReplayFactorSnapshot(data())
    snapshot.historical_reproduction=True
    with snapshot.activate() if source=='historical_missing' else nullcontext():
        result=asyncio.run(agent.process(event))
    assert {key:result['data'][key] for key in factors if key.startswith('f') and key!='factor_details'} == {
        key:value for key,value in factors.items() if key!='factor_details'}
    assert result['data']['css']==75.
    agent.intel_repo.get_factor_score.assert_called_once_with('CTG',score_date=date(2026,8,21))
    if source=='cache': agent.factor_service.compute_factors_for_ticker.assert_not_called()
    else: agent.factor_service.compute_factors_for_ticker.assert_called_once_with('CTG',date(2026,8,21))
    agent.intel_repo.save_factor_score.assert_not_called()
    agent.intel_repo.log_equity_research.assert_called_once()
    metadata=agent.intel_repo.log_equity_research.call_args.kwargs['factor_raw_metrics']['factor_input']
    assert metadata['persistence']=='AUDIT_ONLY'


def test_agent_callers_never_write_source_factors():
    for path in (ROOT/'app/domain/agents').glob('*.py'):
        tree=ast.parse(path.read_text(encoding='utf-8'))
        assert not any(isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
                       and node.func.attr in ('save_factor_score','compute_daily_factors')
                       for node in ast.walk(tree)), path


def test_daily_producer_still_persists_factors(monkeypatch):
    from app.domain.services.factor_service import FactorService
    connection=MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchall.return_value=[('CTG',)]
    get_conn=MagicMock(); get_conn.return_value.__enter__.return_value=connection
    monkeypatch.setattr('app.domain.services.factor_service.get_conn',get_conn)
    repository=MagicMock()
    monkeypatch.setattr('app.domain.repositories.intelligence_repository.IntelligenceRepository',lambda:repository)
    producer=FactorService.__new__(FactorService)
    producer.compute_factors_for_ticker=MagicMock(return_value=dict(
        f1_value=75.,f2_quality=69.87,f3_momentum=60.,f4_earnings=94.,f5_flow=51.,f6_technical=54.,raw_metrics=dated_details()))
    result=producer.compute_daily_factors(date(2026,8,21))
    assert result['symbols_computed']==1
    repository.save_factor_score.assert_called_once()
    assert repository.save_factor_score.call_args.kwargs['score_date']==date(2026,8,21)


def test_research_defers_missing_inputs_instead_of_using_default_scores():
    cls=load_class('equity_research.py','EquityResearchAgent')
    agent=cls.__new__(cls)
    agent.intel_repo=MagicMock()
    agent.intel_repo.get_factor_score.return_value=None
    agent.factor_service=MagicMock()
    agent.factor_service.compute_factors_for_ticker.return_value={'raw_metrics':{'missing_inputs':['pe']}}
    result=asyncio.run(agent.process(dict(ticker='CTG',target_date='2026-08-24',market_data_date='2026-08-21')))
    assert result['data']['status']=='DEFERRED'
    assert result['data']['reason']=='FACTOR_INPUTS_MISSING'
    agent.intel_repo.save_factor_score.assert_not_called()
    agent.intel_repo.log_equity_research.assert_not_called()


def test_rl_uses_snapshot_while_retaining_current_calibration_and_governance(monkeypatch):
    cls=load_class('reinforcement_learning.py','ReinforcementLearningAgent',np=__import__('numpy'),
                   _change_gate_approved=lambda *args:False)
    agent=cls.__new__(cls);agent.storage=MagicMock();agent.publish_event=AsyncMock()
    agent.mral_engine=MagicMock();agent.factor_engine=MagicMock();agent.prob_engine=MagicMock()
    agent.decay_engine=MagicMock();agent.oos_gatekeeper=MagicMock()
    agent.decay_engine.diagnose.return_value=dict(decay_pct=0.,diagnosis='TEST',detail='TEST',cdc_triggered=False)
    agent.oos_gatekeeper.validate_proposal.return_value={'approved':False}
    from app.core.registry import AgentRegistry
    monkeypatch.setattr(AgentRegistry,'dispatch',AsyncMock(return_value={'status':'SUCCESS','result':{'approved':False}}))
    with ReplayFactorSnapshot(data()).activate() as snapshot:
        result=asyncio.run(agent.process(dict(target_date='2026-08-24',market_data_date='2026-08-23',
            is_replay=True,regime='RANGE_BOUND',realized_trades=[{'ticker':'CTG','pnl':1.,'conviction':'B'}],
            current_state={'f1_value':.2})))
    assert result['trace']['factor_snapshot_sha256']==snapshot.sha256
    agent.storage.fetch_all.assert_not_called()
    assert result['data']['governance_approved'] is False
    agent.storage.execute.assert_not_called()


def test_runner_loads_snapshot_before_sessions_and_validates_before_reset(tmp_path):
    path=ROOT/'experiments/replay_agent_pipeline.py';tree=ast.parse(path.read_text(encoding='utf-8'))
    node=next(node for node in tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='run')
    snapshot_path=tmp_path/'input.json';snapshot=ReplayFactorSnapshot(dated_data());snapshot.save(snapshot_path)
    sessions=AsyncMock()
    namespace=dict(globals(),timedelta=__import__('datetime').timedelta,ZoneInfo=__import__('zoneinfo').ZoneInfo,
                   argparse=__import__('argparse'),market_dates=lambda *args:[date(2026,8,24)],_run_sessions=sessions)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),node],type_ignores=[])),str(path),'exec'),namespace)
    args=SimpleNamespace(end='2026-08-24',days=1,factor_snapshot=snapshot_path,reset=True)
    from unittest.mock import patch
    async def inspect_context(*args):assert current_factor_snapshot().sha256==snapshot.sha256
    sessions.side_effect=inspect_context
    with patch.object(ReplayFactorSnapshot,'capture',return_value=ReplayFactorSnapshot(dated_data())):
        asyncio.run(namespace['run'](args))
    sessions.assert_awaited_once()
    namespace['market_dates']=lambda *args:[date(2026,8,25)]
    with pytest.raises(ValueError,match='outside'):asyncio.run(namespace['run'](args))
    assert sessions.await_count==1


def test_rebuilt_snapshot_freezes_financial_handoff_and_never_falls_back(monkeypatch):
    source = data(50.)
    source['version'] = 2
    row = source['factor_rows'][0]
    row[4:8] = [50.,50.,50.,50.]
    row[10] = {'source': {'version':'vn-core-reported-period-v1', 'ratio_date':'2026-06-30',
                          'ratio_frequency':'quarterly'},
               'financial_inputs': {'pe':8.,'pb':1.,'roe':.05,'gross_margin':None,'yoy_earnings_growth':.1}}
    storage = MagicMock()
    cls = load_class('equity_research.py','EquityResearchAgent')
    agent = cls.__new__(cls); agent.scoring_engine=CSSScoringEngine();agent.factor_service=MagicMock()
    agent.intel_repo=IntelligenceRepository(storage)
    agent.intel_repo.log_equity_research=MagicMock(return_value=True)
    monkeypatch.setattr(FinancialRepository,'get_latest_ratios',MagicMock(side_effect=AssertionError('unfrozen financial read')))
    event=dict(ticker='CTG',sector='BANKS',current_price=32100,target_date='2026-08-24',
               market_data_date='2026-08-21',policy_weights={'f1_value':1.},is_replay=True,
               audit_opinion='UNQUALIFIED')
    with ReplayFactorSnapshot(source).activate():
        result=asyncio.run(agent.process(event))
        assert result['data']['financial_metrics']['roe']==5.
        assert result['data']['financial_metrics']['source']['roe_basis']=='REPORTED_PERIOD_NOT_ANNUALIZED_OR_TTM'
        assert result['trace']['factor_source']=='REBUILT_FACTOR_SNAPSHOT'
        assert result['data']['f2_quality']==50.
        with pytest.raises(ValueError,match='no inputs'):
            asyncio.run(agent.process({**event,'market_data_date':'2026-08-23'}))
    agent.factor_service.compute_factors_for_ticker.assert_not_called()
    storage.fetch_all.assert_not_called()
    storage.execute.assert_not_called()


def test_rebuilt_snapshot_cannot_be_replayed_with_another_source_definition():
    source=data();source['version']=2;source['source_version']='old-source'
    source['factor_rows'][0][10]={'source':{'version':'old-source'}}
    with pytest.raises(ValueError,match='source version differs'):
        ReplayFactorSnapshot(source).require_current_source()
    from app.infrastructure.vendors.vn.factor_scores import FACTOR_SOURCE_VERSION
    source['source_version']=FACTOR_SOURCE_VERSION
    source['source_code_sha256']={'different-file':'different-hash'}
    with pytest.raises(ValueError,match='source code hash differs'):
        ReplayFactorSnapshot(source).require_current_source()
    ReplayFactorSnapshot(data()).require_current_source()  # Explicit historical v1 inputs remain inspectable.


@pytest.mark.parametrize('details',[{},dated_details('2026-08-20'),{'source':{'producer':'FACTOR_SERVICE','as_of':'2026-08-21','cache_complete':False}}])
def test_live_reader_does_not_use_legacy_wrong_day_or_incomplete_cache(details):
    storage=MagicMock()
    storage.fetch_all.return_value=[('CTG',date(2026,8,21),75.,15.,60.,94.,51.,54.,70.,90.,details)]
    assert IntelligenceRepository(storage).get_factor_score('CTG',date(2026,8,21)) is None


def test_writer_rejects_moving_old_inputs_to_a_new_score_date():
    storage=MagicMock();repo=IntelligenceRepository(storage)
    with pytest.raises(ValueError,match='same score date'):
        repo.save_factor_score('CTG',75.,15.,60.,94.,51.,54.,70.,score_date=date(2026,8,24),factor_details_extra=dated_details())
    storage.execute.assert_not_called()


@pytest.mark.parametrize('bad',[None,float('nan'),101.,-1.])
def test_writer_rejects_invalid_numeric_cache_even_when_metadata_says_complete(bad):
    storage=MagicMock();repo=IntelligenceRepository(storage)
    with pytest.raises(ValueError,match='invalid factor cache'):
        repo.save_factor_score('CTG',75.,bad,60.,94.,51.,54.,70.,score_date=date(2026,8,21),
                               percentile=90.,factor_details_extra=dated_details())
    storage.execute.assert_not_called()


def test_capture_excludes_untraceable_cache_rows(monkeypatch):
    cursor=MagicMock()
    good=('CTG',date(2026,8,21),75.,69.87,60.,94.,51.,54.,70.,90.,dated_details())
    old=('OLD',date(2026,8,21),75.,69.87,60.,94.,51.,54.,70.,90.,{})
    cursor.fetchall.side_effect=[[good,old],[]]
    context=MagicMock();context.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value=cursor
    monkeypatch.setattr('app.domain.repositories.replay_factor_snapshot.get_conn',context)
    snapshot=ReplayFactorSnapshot.capture([date(2026,8,21)])
    assert snapshot.factor_rows('CTG',date(2026,8,21))
    assert snapshot.factor_rows('OLD',date(2026,8,21))==[]
    assert snapshot._data['rejected_factor_rows']==1


def test_metadata_cannot_overwrite_canonical_factor_values():
    storage=MagicMock();repo=IntelligenceRepository(storage)
    repo.save_factor_score('CTG',75.,15.,60.,94.,51.,54.,70.,score_date=date(2026,8,21),percentile=90.,
                           factor_details_extra=dated_details(f2_quality=95.,css=99.))
    details=json.loads(storage.execute.call_args.args[1][-1])
    assert details['f2_quality']==15.
    assert details['css']==70.


def test_snapshot_rejects_old_cache_without_touching_the_database(monkeypatch):
    capture=MagicMock(side_effect=AssertionError('Should reject before querying'))
    monkeypatch.setattr(ReplayFactorSnapshot,'capture',capture)
    with pytest.raises(ValueError,match='producer/as-of'):
        ReplayFactorSnapshot(data()).require_current_cache()
    capture.assert_not_called()


def test_snapshot_file_must_match_database_cache_after_backfill(monkeypatch):
    current=ReplayFactorSnapshot(dated_data())
    monkeypatch.setattr(ReplayFactorSnapshot,'capture',classmethod(lambda cls,dates:current))
    current.require_current_cache()
    with pytest.raises(ValueError,match='differs from the current database cache'):
        ReplayFactorSnapshot(dated_data(15.)).require_current_cache()


def test_replay_rejects_stale_snapshot_before_any_session_or_reset(tmp_path,monkeypatch):
    path=ROOT/'experiments/replay_agent_pipeline.py'
    tree=ast.parse(path.read_text(encoding='utf8'))
    run=next(node for node in tree.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='run')
    file=tmp_path/'old.json';ReplayFactorSnapshot(dated_data()).save(file)
    monkeypatch.setattr(ReplayFactorSnapshot,'capture',classmethod(lambda cls,dates:ReplayFactorSnapshot(dated_data(15.))))
    sessions=AsyncMock()
    namespace=dict(globals(),timedelta=__import__('datetime').timedelta,ZoneInfo=__import__('zoneinfo').ZoneInfo,
                   argparse=__import__('argparse'),market_dates=lambda *args:[date(2026,8,24)],_run_sessions=sessions)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),run],type_ignores=[])),str(path),'exec'),namespace)
    with pytest.raises(ValueError,match='differs from the current database cache'):
        asyncio.run(namespace['run'](SimpleNamespace(end='2026-08-24',days=1,factor_snapshot=file,reset=True)))
    sessions.assert_not_awaited()
