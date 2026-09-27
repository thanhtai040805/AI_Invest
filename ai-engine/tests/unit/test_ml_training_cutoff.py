from datetime import date

import numpy as np
import pandas as pd

from scripts import train_hybrid_stacking as trainer


def test_training_queries_cut_off_both_universe_and_prices(monkeypatch):
    queries=[]
    class Connection:
        def __enter__(self): return self
        def __exit__(self,*args): pass
    monkeypatch.setattr(trainer,'get_conn',lambda:Connection())
    def read_sql(sql,conn,params=None):
        queries.append((sql,params))
        if 'SUM(close * volume_continuous)' in sql:
            return pd.DataFrame({'ticker':['HPG']})
        dates=pd.bdate_range(end='2026-07-10',periods=320)
        return pd.DataFrame({'ticker':['HPG']*320,'date':dates,'open':1,'high':1,'low':1,'close':1,'volume':1})
    monkeypatch.setattr(trainer.pd,'read_sql',read_sql)
    data,_=trainer.fetch_training_data('2026-07-10')
    assert len(queries)==2
    assert all('date <= %s::date' in sql and params==('2026-07-10',) for sql,params in queries)
    assert data['HPG'].index.max()==pd.Timestamp('2026-07-10')


def test_training_discards_incomplete_forward_labels(monkeypatch):
    dates=pd.bdate_range(end='2026-07-10',periods=40)
    frame=pd.DataFrame({'open':np.arange(40)+20,'high':np.arange(40)+21,'low':np.arange(40)+19,'close':np.arange(40)+20,'volume':1000},index=dates)
    monkeypatch.setattr(trainer.beneish_engine,'fetch_and_compute_scores',lambda tickers:pd.DataFrame())
    monkeypatch.setattr(trainer.feature_forge,'generate',lambda df,ticker:pd.DataFrame({'factor':1},index=df.index))
    monkeypatch.setattr(trainer.graph_engine,'extract_graph_contagion_signals',lambda data:{})
    master,features=trainer.build_master_dataset({'HPG':frame})
    assert not master.empty
    assert master.index.max()<=dates[-13]  # 7-session labels plus a 5-session ranking target.
    assert not any(name.startswith('fwd_') for name in features)
    assert master['fwd_ret_3d'].notna().all()
