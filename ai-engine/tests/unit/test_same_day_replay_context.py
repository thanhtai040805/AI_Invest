"""An explicit paper Replay stays historical even when its last session is today."""
import ast
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


@pytest.mark.parametrize('target,portfolio,account,expected', [
    (date(2026,11,2), {}, 'paper-account', '2026-11-02T09:45:00+07:00'),
    (date(2026,11,2), {}, None, '2026-11-02T09:45:00+07:00'),
    (date(2026,11,2), None, 'paper-account', '2026-11-02T09:45:00+07:00'),
    (date(2026,11,2), None, None, None),
    (date(2026,10,30), None, None, '2026-10-30T09:45:00+07:00'),
])
def test_explicit_replay_uses_session_timestamp_instead_of_live_context(target, portfolio, account, expected):
    path=Path(__file__).resolve().parents[2]/'app/domain/pipeline/daily_pipeline_orchestrator.py'
    tree=ast.parse(path.read_text(encoding='utf8'))
    assignment=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign)
                    and any(isinstance(t,ast.Name) and t.id=='replay_at' for t in n.targets))
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return cls(2026,11,2,18,0,tzinfo=tz)
    namespace=dict(datetime=Clock,time=time,ZoneInfo=ZoneInfo,target_date_obj=target,
                   replay_portfolio=portfolio,replay_account_id=account)
    actual=eval(compile(ast.Expression(assignment.value),str(path),'eval'),namespace)
    assert (actual.isoformat() if actual else None)==expected
