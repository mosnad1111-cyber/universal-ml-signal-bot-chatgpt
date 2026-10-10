"""Research-only TVC:GOLD PDH/PDL backtest; does not alter production bot behavior.
Run from repository root: python research/pdh_pdl_breakout_backtest.py --bars 5000
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
from datetime import time
import numpy as np
import pandas as pd
from market import fetch

NY = "America/New_York"
OUT = Path("research/results")
TOL_ATR, SL_BUFFER_ATR, RR = 0.10, 0.10, 2.0
MAX_RETEST, MAX_FALSE_RETURN = 6, 3
SESSION_START, SESSION_END = time(9, 30), time(16, 0)

def normalize(df):
    x = df.copy(); x.index = pd.to_datetime(x.index, utc=True)
    x = x[~x.index.duplicated(keep="last")].sort_index()
    for c in ("Open", "High", "Low", "Close"): x[c] = pd.to_numeric(x[c], errors="coerce")
    x = x.dropna(subset=["Open", "High", "Low", "Close"])
    pc = x.Close.shift(1)
    tr = pd.concat([x.High-x.Low, (x.High-pc).abs(), (x.Low-pc).abs()], axis=1).max(axis=1)
    x["ATR14"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    x["NY_time"] = x.index.tz_convert(NY)
    # DST-aware New York trading-day rollover at 17:00 local time.
    x["trade_day"] = (x["NY_time"] - pd.Timedelta(hours=17)).map(lambda t: t.date())
    x["NY_clock"] = x["NY_time"].map(lambda t: t.time().replace(tzinfo=None))
    x["in_session"] = x.NY_clock.map(lambda t: SESSION_START <= t < SESSION_END)
    return x

def previous_day_levels(hourly):
    grouped = hourly.groupby("trade_day").agg(PDH=("High", "max"), PDL=("Low", "min"))
    keys = list(grouped.index); result = {}
    for i in range(1, len(keys)):
        result[keys[i]] = (float(grouped.loc[keys[i-1], "PDH"]), float(grouped.loc[keys[i-1], "PDL"]))
    return result

def bullish(prev, cur):
    rng=max(float(cur.High-cur.Low),1e-9); body=abs(float(cur.Close-cur.Open))
    wick=min(float(cur.Open),float(cur.Close))-float(cur.Low)
    engulf=cur.Close>cur.Open and prev.Close<prev.Open and cur.Open<=prev.Close and cur.Close>=prev.Open
    reject=cur.Close>cur.Open and wick>=max(body*1.2,rng*.25) and cur.Close>=cur.Low+.65*rng
    return bool(engulf or reject)

def bearish(prev, cur):
    rng=max(float(cur.High-cur.Low),1e-9); body=abs(float(cur.Close-cur.Open))
    wick=float(cur.High)-max(float(cur.Open),float(cur.Close))
    engulf=cur.Close<cur.Open and prev.Close>prev.Open and cur.Open>=prev.Close and cur.Close<=prev.Open
    reject=cur.Close<cur.Open and wick>=max(body*1.2,rng*.25) and cur.Close<=cur.Low+.35*rng
    return bool(engulf or reject)

def inside(close,pdh,pdl): return pdl <= close <= pdh

def exit_trade(df, ei, side, entry, stop, target, risk, cost):
    day=df.iloc[ei].trade_day; exit_i=None; outcome=None; price=None
    for j in range(ei+1,len(df)):
        r=df.iloc[j]
        if r.trade_day != day: break
        sl=(r.Low<=stop) if side=="LONG" else (r.High>=stop)
        tp=(r.High>=target) if side=="LONG" else (r.Low<=target)
        if sl: exit_i,price,outcome=j,stop,"SL (same candle ambiguity)" if tp else "SL"; break
        if tp: exit_i,price,outcome=j,target,"TP"; break
        if r.NY_clock >= time(15,55): exit_i,price,outcome=j,float(r.Close),"SESSION_CLOSE"; break
    if exit_i is None:
        pos=np.flatnonzero(df.trade_day.values==day)
        exit_i=int(pos[-1]) if len(pos) else ei
        price,outcome=float(df.iloc[exit_i].Close),"SESSION_CLOSE"
    gross=(price-entry)/risk if side=="LONG" else (entry-price)/risk
    return {"exit_time_utc":df.index[exit_i].isoformat(),"exit_price":round(float(price),3),"outcome":outcome,
            "gross_R":round(float(gross),4),"net_R":round(float(gross-cost/risk),4),"bars_held":int(exit_i-ei),"exit_i":exit_i}

def backtest(df, levels, mode, cost):
    trades=[]; i=1; n=len(df)
    while i<n-2:
        row,prev=df.iloc[i],df.iloc[i-1]; day=row.trade_day
        if not row.in_session or day not in levels or not np.isfinite(row.ATR14) or row.ATR14<=0: i+=1; continue
        pdh,pdl=levels[day]; tol=TOL_ATR*float(row.ATR14); setup=None
        if mode=="successful":
            if prev.Close<=pdh and row.Close>pdh:
                for r in range(i+1,min(i+MAX_RETEST+1,n-1)):
                    q=df.iloc[r]
                    if q.trade_day!=day or not q.in_session: break
                    if q.Low<=pdh+tol and q.Close>=pdh:
                        c=df.iloc[r+1]
                        if c.trade_day==day and c.in_session and c.Close>pdh and bullish(q,c): setup=(r+1,"LONG",pdh,"SUCCESSFUL_UP_BREAKOUT")
                        break
            elif prev.Close>=pdl and row.Close<pdl:
                for r in range(i+1,min(i+MAX_RETEST+1,n-1)):
                    q=df.iloc[r]
                    if q.trade_day!=day or not q.in_session: break
                    if q.High>=pdl-tol and q.Close<=pdl:
                        c=df.iloc[r+1]
                        if c.trade_day==day and c.in_session and c.Close<pdl and bearish(q,c): setup=(r+1,"SHORT",pdl,"SUCCESSFUL_DOWN_BREAKOUT")
                        break
        else:
            if prev.Close<=pdh and row.Close>pdh:
                back=None
                for k in range(i+1,min(i+MAX_FALSE_RETURN+1,n)):
                    q=df.iloc[k]
                    if q.trade_day!=day or not q.in_session: break
                    if inside(float(q.Close),pdh,pdl): back=k; break
                if back is not None:
                    for r in range(back+1,min(back+MAX_RETEST+1,n-1)):
                        q=df.iloc[r]
                        if q.trade_day!=day or not q.in_session: break
                        if q.High>=pdh-tol and pdl<=q.Close<pdh:
                            c=df.iloc[r+1]
                            if c.trade_day==day and c.in_session and pdl<=c.Close<pdh and bearish(q,c): setup=(r+1,"SHORT",pdh,"FALSE_UP_BREAKOUT")
                            break
            elif prev.Close>=pdl and row.Close<pdl:
                back=None
                for k in range(i+1,min(i+MAX_FALSE_RETURN+1,n)):
                    q=df.iloc[k]
                    if q.trade_day!=day or not q.in_session: break
                    if inside(float(q.Close),pdh,pdl): back=k; break
                if back is not None:
                    for r in range(back+1,min(back+MAX_RETEST+1,n-1)):
                        q=df.iloc[r]
                        if q.trade_day!=day or not q.in_session: break
                        if q.Low<=pdl+tol and pdl<q.Close<=pdh:
                            c=df.iloc[r+1]
                            if c.trade_day==day and c.in_session and pdl<c.Close<=pdh and bullish(q,c): setup=(r+1,"LONG",pdl,"FALSE_DOWN_BREAKOUT")
                            break
        if setup is None: i+=1; continue
        ei,side,level,name=setup; er=df.iloc[ei]; entry=float(er.Close); buf=SL_BUFFER_ATR*float(er.ATR14)
        stop=level-buf if side=="LONG" else level+buf; risk=abs(entry-stop)
        if not np.isfinite(risk) or risk<=0: i=ei+1; continue
        target=entry+RR*risk if side=="LONG" else entry-RR*risk
        ex=exit_trade(df,ei,side,entry,stop,target,risk,cost)
        trades.append({"strategy":mode,"setup":name,"entry_time_utc":df.index[ei].isoformat(),"entry_time_ny":er.NY_time.isoformat(),
          "side":side,"PDH":round(pdh,3),"PDL":round(pdl,3),"entry":round(entry,3),"SL":round(stop,3),"TP":round(target,3),
          "risk_points":round(risk,4),**{k:v for k,v in ex.items() if k!="exit_i"}})
        i=ex["exit_i"]+1
    return trades

def summarize(trades,cost):
    if not trades: return {"trades":0,"wins":0,"losses":0,"win_rate_pct":0.0,"gross_R":0.0,"net_R":0.0,"expectancy_R":0.0,"profit_factor":None,"cost_points_per_trade":cost}
    net=np.array([t["net_R"] for t in trades]); gross=np.array([t["gross_R"] for t in trades]); loss=abs(float(net[net<0].sum()))
    return {"trades":len(trades),"wins":int((net>0).sum()),"losses":int((net<0).sum()),"win_rate_pct":round(100*float((net>0).sum())/len(net),2),
      "gross_R":round(float(gross.sum()),3),"net_R":round(float(net.sum()),3),"expectancy_R":round(float(net.mean()),4),
      "profit_factor":round(float(net[net>0].sum())/loss,3) if loss else None,"cost_points_per_trade":cost}

def main():
    p=argparse.ArgumentParser(); p.add_argument("--bars",type=int,default=5000); p.add_argument("--cost-points",type=float,default=.5)
    p.add_argument("--start",default=None); p.add_argument("--end",default=None); a=p.parse_args(); OUT.mkdir(parents=True,exist_ok=True)
    print("Fetching TVC:GOLD 5m and 1h candles...")
    df=normalize(fetch("TVC:GOLD","5m",period=a.bars)); hourly=normalize(fetch("TVC:GOLD","1h",period=max(5000,min(a.bars,10000))))
    if a.start: df=df[df.index>=pd.Timestamp(a.start,tz="UTC")]
    if a.end: df=df[df.index<pd.Timestamp(a.end,tz="UTC")+pd.Timedelta(days=1)]
    levels=previous_day_levels(hourly)
    if not levels: raise RuntimeError("Not enough 1h data to build previous-trading-day levels")
    df=df[df.trade_day.isin(levels)].copy(); results={}
    for mode in ("successful","false"):
        ts=backtest(df,levels,mode,a.cost_points); results[mode]=summarize(ts,a.cost_points)
        pd.DataFrame(ts).to_csv(OUT/f"{mode}_breakouts.csv",index=False)
    report={"symbol":"TVC:GOLD","signal_timeframe":"5m","levels_source":"1h candles; prior completed New York trading day 17:00–17:00, DST-aware",
      "entry_session":"09:30–16:00 America/New_York","retest_tolerance":"0.10 x 5m ATR(14)","stop_buffer":"0.10 x 5m ATR(14) beyond PDH/PDL","target":"2R",
      "confirmation":"bullish/bearish engulfing or rejection candle","false_break_return_window_bars":MAX_FALSE_RETURN,"max_retest_window_bars":MAX_RETEST,
      "same_bar_sl_tp_policy":"count as SL","trade_management":"one open trade per scenario; force close at 15:55 NY if still open",
      "assumed_round_trip_cost_points":a.cost_points,"data":{"5m_rows":int(len(df)),"1h_rows":int(len(hourly)),
      "5m_first_utc":df.index.min().isoformat() if len(df) else None,"5m_last_utc":df.index.max().isoformat() if len(df) else None,
      "1h_first_utc":hourly.index.min().isoformat() if len(hourly) else None,"1h_last_utc":hourly.index.max().isoformat() if len(hourly) else None},
      "results":results,"note":"Research only. Cost is an assumption, not a measured broker quote."}
    (OUT/"summary.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2,ensure_ascii=False)); print(f"Saved summary and trade CSVs under {OUT}/")
if __name__=="__main__": main()
