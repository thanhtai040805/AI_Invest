"""Replay ML decisions and fills on historical DNSE depth; never use live quotes."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.domain.services.ml.standalone_ml_channel import StandaloneMLChannel
from app.domain.services.ml.hybrid_stacking_ranker import HybridStackingRanker
from app.domain.rules.stop_loss import StopLossEngine
from app.domain.services.ml.dual_tier_sniper_engine import dual_tier_engine
import pandas as pd
from app.domain.rules.execution.shadow_fill import shadow_fill
from app.domain.repositories.portfolio_repository import PortfolioRepository, calculate_is_t25_locked
from app.infrastructure.database.pg_pool import get_conn
from experiments.replay_agent_pipeline import market_dates, previous_market_date, bars_for_day, quote_history, replay_book, price_to_vnd

VN = ZoneInfo("Asia/Ho_Chi_Minh")
logger = logging.getLogger("ml_replay")


def pending_orders(account_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, symbol, side, quantity, price, created_at FROM orders WHERE user_id=%s AND status='PENDING_REPLAY' ORDER BY created_at", (account_id,))
        return [{'id':str(r[0]), 'ticker':r[1], 'side':r[2], 'shares':int(r[3]), 'price':float(r[4]), 'created_at':r[5].replace(tzinfo=VN)} for r in cur.fetchall()]


async def run(args):
    model = HybridStackingRanker()
    if not model.load_model(args.model):
        raise ValueError("Replay model cannot be loaded")
    end = date.fromisoformat(args.end)
    days = market_dates(end, args.days)
    if len(days) != args.days or not 1 <= args.days <= 52:
        raise ValueError("Replay needs 1..52 available trading sessions")
    if not model.trained_through or date.fromisoformat(model.trained_through) >= days[0]:
        raise ValueError("Model data cutoff must precede the first replay session")
    if days[-1] >= datetime.now(VN).date():
        raise ValueError("Replay sessions must be strictly historical")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM orders WHERE user_id=%s", (args.account_id,))
        orders_count = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM standalone_ml_predictions WHERE account_id=%s", (args.account_id,))
        predictions_count = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM positions WHERE user_id=%s AND quantity>0", (args.account_id,))
        if orders_count or predictions_count or cur.fetchone()[0]:
            raise ValueError("Replay requires an empty account history; this command never resets an account")
    repository = PortfolioRepository()
    channel = StandaloneMLChannel(account_id=args.account_id, model=model)
    risk = StopLossEngine()
    peaks = {}
    report = {'account_id':args.account_id, 'model_version':model.model_version, 'trained_through':model.trained_through, 'sessions':[], 'status':'RUNNING'}
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        repository.record_replay_mark(user_id=args.account_id, mark_as_of=previous_market_date(days[0]))
        for day in days:
            mark = previous_market_date(day)
            signal_time = datetime.combine(day, time(9,45), VN)
            bars = bars_for_day(day)
            state = repository.get_account_state(user_id=args.account_id, as_of=mark)
            predictions = channel.predict_universe(target_date=day)
            if predictions.empty:
                raise ValueError(f"No predictions for {day}")
            with get_conn() as conn:
                vnindex = pd.read_sql("SELECT date, close FROM market_data_daily_calculation WHERE ticker='VNINDEX' AND date<%s ORDER BY date",conn,params=(day,)).set_index('date')
            vnindex.index=pd.to_datetime(vnindex.index)
            regime=dual_tier_engine.evaluate_macro_regime(vnindex,pd.Timestamp(mark))
            positions = repository.get_open_positions(user_id=args.account_id, as_of=mark, as_of_time=signal_time)
            tracked = {str(p['ticker']).upper() for p in positions}
            tracked.update(str(t).upper() for t in predictions.sort_values('pred_score',ascending=False).head(args.max_candidates)['ticker'])
            # Fetch historical depth before persisting the day's decisions.
            quotes = {ticker:quote_history(day,ticker) for ticker in sorted(tracked)}
            if any(not rows for rows in quotes.values()):
                raise ValueError(f"Historical depth missing for {day}: {[t for t,r in quotes.items() if not r]}")
            original_predict = channel.predict_universe
            channel.predict_universe = lambda **kw: predictions
            try:
                result = await channel.run_autonomous_cycle(day, execution_mode='REPLAY', nav=float(state['total_nav']), max_candidates=args.max_candidates, buy_block_reason="BEAR_DEFENSE" if regime=="BEAR_DEFENSE" else None,
                    replay_prices={ticker:float(b['price']) for ticker,b in bars.items()})
            finally:
                channel.predict_universe = original_predict
            waiting = pending_orders(args.account_id)
            events = sorted(((snapshot['time'], ticker, snapshot) for ticker, rows in quotes.items() for snapshot in rows),key=lambda event:(event[0],event[1]))
            fills = 0
            for stamp,ticker,snapshot in events:
                book = replay_book({**snapshot,'symbol':ticker})
                # Existing positions can be protected before 09:45; new buys wait for a later snapshot.
                bids = book.get('bids') or []
                current_price = max((price_to_vnd(float(level['price'])) for level in bids if level.get('volume',0)>0), default=0)
                position = next((p for p in positions if str(p['ticker']).upper()==ticker),None)
                if position and current_price>0 and not any(o['ticker']==ticker and o['side']=='SELL' for o in waiting):
                    entry = float(position['average_price'])
                    peaks[ticker] = max(peaks.get(ticker,entry),current_price)
                    opened = position.get('opened_at')
                    available = 0 if calculate_is_t25_locked(opened,stamp) else int(position['shares'])
                    stop = risk.check_position(ticker,int(position['shares']),entry,current_price,float(state['total_nav']),
                        market_data={'peak_price':peaks[ticker], 'days_held':(day-opened.date()).days if opened else 0}, available_shares=available)
                    if stop and stop.quantity>0:
                        order_id=str(uuid.uuid4())
                        with get_conn() as conn,conn.cursor() as cur:
                            cur.execute("INSERT INTO orders (id,user_id,symbol,side,order_type,price,quantity,status,created_at) VALUES (%s,%s,%s,'SELL','REPLAY_ML_STOP',%s,%s,'PENDING_REPLAY',%s)",
                                (order_id,args.account_id,ticker,current_price*.985,stop.quantity,stamp.replace(tzinfo=None)))
                        waiting.append({'id':order_id,'ticker':ticker,'side':'SELL','shares':stop.quantity,'price':current_price*.985,'created_at':stamp})
                for order in list(waiting):
                    if order['ticker']!=ticker or stamp<=order['created_at']:
                        continue
                    try:
                        fill_price=shadow_fill(book,order['side'],order['shares'],order['price'],now=stamp)
                    except ValueError:
                        continue
                    repository.record_replay_execution(ticker,order['side'],order['shares'],fill_price,
                        user_id=args.account_id,executed_at=stamp,mark_as_of=mark,pending_order_id=order['id'])
                    waiting.remove(order)
                    fills+=1
                    positions=repository.get_open_positions(user_id=args.account_id,as_of=mark,as_of_time=stamp)
                if not waiting and not positions:
                    break
            with get_conn() as conn,conn.cursor() as cur:
                cur.execute("UPDATE orders SET status='EXPIRED_REPLAY' WHERE user_id=%s AND status='PENDING_REPLAY'",(args.account_id,))
            closing=repository.record_replay_mark(user_id=args.account_id,mark_as_of=day)
            summary={'date':day.isoformat(),'regime':regime,'predictions':len(predictions),'queued':len(result['orders']),'fills':fills,'nav':float(closing['total_nav']),'cash':float(closing['cash_balance'])}
            report['sessions'].append(summary)
            report_path.write_text(json.dumps(report,indent=2),encoding='utf-8')
            print(json.dumps(summary),flush=True)
        report['accuracy']=channel.evaluate_forward_accuracy(lookback_days=60)
        report['status']='COMPLETED'
    except Exception as exc:
        report['status']='FAILED'
        report['error']=str(exc)
        raise
    finally:
        report_path.write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account-id',required=True)
    parser.add_argument('--model',required=True)
    parser.add_argument('--end',required=True)
    parser.add_argument('--days',type=int,default=30)
    parser.add_argument('--max-candidates',type=int,default=5)
    parser.add_argument('--report',required=True)
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(args))


if __name__=='__main__':
    main()
