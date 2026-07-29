"""Bounded no-lookahead replay of the LIVE FusionNew detector.

Mirrors live: trailing zone=300, LTF=1000, scan ~hourly, nearest-unmitigated,
CVD/BTC-strong/EMA/current-liquidity/stoch, finite max age, per-tier SL floor.
A setup is decided at `end`; fill search starts strictly at end+1.
"""
from __future__ import annotations
import sys, json, statistics
from collections import defaultdict
sys.path.insert(0, "/app")
sys.path.insert(0, "/app/fusionnew")

import numpy as np
from backtest.data import fetch_klines
from backtest.faithful_imbalance import (
    TIERS, FIB_EXPIRY, MAX_HOLD, RR, _trend, _trend_ok_strong,
    _filter_flow, _filter_ema_dist, _filter_liquidity, _filter_stochastic,
    nearest_unmitigated_setups, _manage_exit, _atr,
)

DAYS = int(sys.argv[1]) if len(sys.argv)>1 else 60
TIER = sys.argv[2] if len(sys.argv)>2 else "H1"
MODE = sys.argv[3] if len(sys.argv)>3 else "full"  # raw|structural|full
# XRP cache was empty in the deployed M30 container; exclude it from BOTH tiers/arms
# so coverage remains identical and valid instead of silently accepting partial results.
PAIRS = ["BTCUSDT","ETHUSDT","SOLUSDT","LINKUSDT","AVAXUSDT","DOGEUSDT",
         "ADAUSDT","BNBUSDT","LTCUSDT","NEARUSDT","APTUSDT"]
ZONE_WIN, LTF_WIN = 300, 1000
SETTINGS = {
    "H1": dict(step=12, floor=1.0, age=288),
    "M30": dict(step=20, floor=2.5, age=480),
}


def metric(ts):
    if not ts: return dict(n=0,wr=0,pf=0,netR=0,expR=0,ddR=0)
    rs=[t["netR"] for t in sorted(ts,key=lambda x:x["time"])]
    gp=sum(x for x in rs if x>0); gl=abs(sum(x for x in rs if x<=0))
    eq=peak=dd=0
    for x in rs:
        eq+=x; peak=max(peak,eq); dd=max(dd,peak-eq)
    return dict(n=len(rs),wr=sum(x>0 for x in rs)/len(rs)*100,
                pf=gp/gl if gl else 999,netR=sum(rs),expR=sum(rs)/len(rs),ddR=dd)


def fold_report(trades):
    trades=sorted(trades,key=lambda x:x["time"])
    if not trades: return []
    times=[t["time"] for t in trades]
    lo,hi=times[0],times[-1]
    span=hi-lo
    cuts=[lo+span*i/4 for i in range(5)]
    # Last interval is closed on the right; avoid integer arithmetic on pandas Timestamp.
    return [metric([t for t in trades if cuts[i] <= t["time"] and
                    (t["time"] < cuts[i+1] if i < 3 else t["time"] <= hi)])
            for i in range(4)]


def run_symbol(sym):
    cfg=TIERS[TIER]; st=SETTINGS[TIER]
    zone=fetch_klines(sym,cfg["zone"],DAYS)
    ltf=fetch_klines(sym,cfg["ltf"],DAYS)
    btc_zone=fetch_klines("BTCUSDT",cfg["zone"],DAYS)
    if min(len(zone),len(ltf),len(btc_zone))<300: return [],{"short":1}
    ll,lh,lc=ltf.low.to_numpy(),ltf.high.to_numpy(),ltf.close.to_numpy()
    latr=_atr(ltf); trades=[]; seen=set(); funnel=defaultdict(int)
    start=max(LTF_WIN-1,260)
    for end in range(start,len(ltf)-FIB_EXPIRY-MAX_HOLD-1,st["step"]):
        now=ltf.open_time.iloc[end]
        l0=max(0,end-LTF_WIN+1)
        lcut=ltf.iloc[l0:end+1].reset_index(drop=True)
        zhist=zone[zone.open_time<=now]
        zcut=zhist.iloc[-ZONE_WIN:].reset_index(drop=True)
        bhist=btc_zone[btc_zone.open_time<=now]
        bcut=bhist.iloc[-ZONE_WIN:].reset_index(drop=True)
        if min(len(zcut),len(bcut))<260: continue
        trend=_trend(zcut); btc_trend=_trend(bcut)
        candidates=[]
        for side in ("BULL","BEAR"):
            ss=nearest_unmitigated_setups(zcut,lcut,trend,side,RR,max_age=st["age"],
                                           sl_floor_pct=st["floor"])
            funnel["raw"]+=len(ss)
            if MODE in ("structural","full"):
                ss=_filter_ema_dist(ss,zcut,1.0); funnel["ema"]+=len(ss)
                ss=_filter_liquidity(ss,lcut,1_000_000,at_idx=len(lcut)-1); funnel["liq"]+=len(ss)
            if MODE=="full":
                ss=_filter_flow(sym,DAYS,side,ss,lcut,True,False); funnel["cvd"]+=len(ss)
                ss=[s for s in ss if _trend_ok_strong(btc_trend,s["t_complete"],side,0.75)]
                funnel["btc"]+=len(ss)
                ss=_filter_stochastic(ss,lcut,side,70); funnel["stoch"]+=len(ss)
            candidates += ss[:1]
        candidates.sort(key=lambda s:(s.get("dist_pct",999),s.get("age_bars",999)))
        for s in candidates[:1]:
            side=s["side"]
            key=(sym,side,str(s["t_complete"]))
            if key in seen: continue
            seen.add(key); funnel["orders"]+=1
            entry,sl,tp=s["entry"],s["sl"],s["tp"]
            fill=None
            for f in range(end+1,min(end+1+FIB_EXPIRY,len(ltf))):
                if (side=="BULL" and ll[f]<=entry) or (side=="BEAR" and lh[f]>=entry):
                    fill=f; break
            if fill is None: funnel["expired"]+=1; continue
            funnel["fills"]+=1
            pu,reason=_manage_exit(side,entry,sl,tp,ll,lh,lc,latr,fill,"fixed")
            risk=abs(entry-sl)
            trades.append(dict(symbol=sym,side=side,time=ltf.open_time.iloc[fill],
                               netR=pu/risk if risk else 0,reason=reason,
                               slpct=risk/entry*100,age=s.get("age_bars",-1)))
    return trades,dict(funnel)


def main():
    alltr=[]; agg=defaultdict(int); errors=[]
    print(f"RUN tier={TIER} mode={MODE} days={DAYS} pairs={len(PAIRS)} settings={SETTINGS[TIER]}",flush=True)
    for sym in PAIRS:
        try: tr,fu=run_symbol(sym)
        except Exception as e: errors.append(f"{sym}:{type(e).__name__}:{e}"); print("ERR",errors[-1],flush=True); continue
        alltr+=tr
        for k,v in fu.items(): agg[k]+=v
        print(f"  {sym:10} trades={len(tr):3d} raw={fu.get('raw',0):4d} fills={fu.get('fills',0):3d}",flush=True)
    m=metric(alltr); folds=fold_report(alltr)
    print("FUNNEL",dict(agg)); print("ALL",m)
    for i,x in enumerate(folds,1): print(f"FOLD{i}",x)
    sides=defaultdict(list)
    for t in alltr:sides[t["side"]].append(t)
    for k,v in sides.items():print("SIDE",k,metric(v))
    valid=len(errors)==0 and agg["raw"]>0
    print("VALID",valid,"ERRORS",errors)
    out=dict(tier=TIER,mode=MODE,days=DAYS,metric=m,folds=folds,funnel=dict(agg),errors=errors,trades=alltr)
    json.dump(out,open(f"/tmp/wf_{TIER}_{MODE}.json","w"),indent=2,default=str)
    if not valid: raise SystemExit(2)

if __name__=="__main__": main()
