"""Exercise Universe's real process without Agent registration or database writes."""
import ast
import asyncio
from datetime import date, datetime
import logging
from pathlib import Path
from unittest.mock import MagicMock

from app.core.base_agent import BaseAgent
from app.domain.rules.universe_manager import UniverseGroup, TradingStatus
from app.domain.services.factor_service import MIN_MARKET_OBSERVATIONS


def test_universe_requires_full_generic_history_even_for_vn30():
    path=Path(__file__).resolve().parents[2]/'app/domain/agents/universe_discovery.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    nodes=[n for n in tree.body if isinstance(n,(ast.ClassDef,ast.FunctionDef))
           and n.name in ('UniverseDiscoveryAgent','_coerce_date')]
    ns=dict(globals(),logger=logging.getLogger(__name__))
    cursor=MagicMock()
    cursor.fetchall.side_effect=[
        [(s,'NORMAL','UNQUALIFIED',20e12,'Technology','Technology') for s in ['NEW','READY']],
        [(s,60e9,1e6,20) for s in ['NEW','READY']],
        [('NEW',date(2026,1,1),59),('READY',date(2026,1,1),60)],
    ]
    get_conn=MagicMock()
    get_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value=cursor
    ns['get_conn']=get_conn
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),*nodes],type_ignores=[])
    exec(compile(ast.fix_missing_locations(module),str(path),'exec'),ns)
    agent=ns['UniverseDiscoveryAgent'].__new__(ns['UniverseDiscoveryAgent'])
    agent.session_manager=MagicMock();agent.universe_manager=MagicMock();agent.beneish_engine=MagicMock()
    agent.universe_manager._get_vn30_list.return_value=['NEW','READY']
    result=asyncio.run(agent.process({'tickers':['NEW','READY'],'target_date':'2026-08-12',
        'market_data_date':'2026-08-11','strategy_mode':'Fundamental',
        'beneish_overrides':{'NEW':-2.,'READY':-2.}}))['data']
    assert [r['ticker'] for r in result['discovery_list']]==['READY']
    assert result['exclusion_log'][0]['ticker']=='NEW'
    assert result['exclusion_log'][0]['reason']=='FACTOR_HISTORY_INSUFFICIENT'
