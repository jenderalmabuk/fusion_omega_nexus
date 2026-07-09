#!/usr/bin/env python3
from __future__ import annotations
import csv, math, statistics
from pathlib import Path
from datetime import datetime

CACHE = Path('/tmp/fusion_extract/fusion/backtest/cache')
FEE = 2 * 0.00055


def ema(vals, span):
    a = 2 / (span + 1)
    out = []
    prev = None
    for v in vals:
        prev = v if prev is None else a * v + (1 - a) * prev
        out.append(prev)
    return out


def rsi(close, period=14):
    out = [50.0]
    ag = al = None
    for i in range(1, len(close)):
        d = close[i] - close[i-1]
        g, l = max(d, 0), max(-d, 0)
        if ag is None:
            ag, al = g, l
        else:
            ag = (ag * (period - 1) + g) / period
            al = (al * (period - 1) + l) / period
        rs = ag / (al or 1e-12)
        out.append(100 - 100 / (1 + rs))
    return out


def read_csv(path: Path):
    rows = []
    with path.open() as f:
        for r in csv.DictReader(f):
            try:
                rows.append({
                    't': r['open_time'],
                    'open': float(r['open']),
                    'high': float(r['high']),
                    'low': float(r['low']),
                    'close': float(r['close']),
                    'volume': float(r['volume']),
                    'taker_buy_base': float(r.get('taker_buy_base') or 0),
                })
            except Exception:
                pass
    return rows


def max_prev(vals, i, n):
    if i <= 0:
        return None
    s = vals[max(0, i-n):i]
    return max(s) if s else None


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs):
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x-m)**2 for x in xs) / (len(xs)-1))


def pick_trending(files, lookback_bars=30*24*4, topn=12):
    scored = []
    for p in files:
        rows = read_csv(p)
        if len(rows) <= lookback_bars + 300:
            continue
        c = [r['close'] for r in rows]
        ret = c[-1] / c[-lookback_bars] - 1
        scored.append((ret, p.stem.split('_')[0], p, rows))
    scored.sort(reverse=True)
    return scored[:topn]


def backtest(rows, symbol, lookback=20, room_min=0.0, vol_mult=1.2, cvd_min=0.08, tp=0.04, sl=0.02, timeout=32):
    close=[r['close'] for r in rows]; high=[r['high'] for r in rows]; low=[r['low'] for r in rows]; vol=[r['volume'] for r in rows]
    e50=ema(close,50); e200=ema(close,200); rrsi=rsi(close)
    trades=[]; i=240
    while i < len(rows)-2:
        prior_high=max_prev(high,i,lookback)
        resistance=max_prev(high,i,180)
        if prior_high is None or resistance is None or close[i] <= 0:
            i+=1; continue
        vol_ma=mean(vol[max(0,i-48):i]) or 1e-12
        taker=rows[i]['taker_buy_base']
        cvd_ratio=((2*taker)-vol[i])/(vol[i] or 1e-12)
        room=(resistance/close[i]-1) if resistance>close[i] else 0.0
        trend = close[i] > e200[i] and e50[i] > e200[i] and close[i] > close[max(0,i-96)]
        brk = close[i] > prior_high
        ok = trend and brk and (vol[i]/vol_ma >= vol_mult) and (cvd_ratio >= cvd_min) and (room >= room_min)
        # RSI deliberately NOT blocking; this is breakout mode.
        if not ok:
            i+=1; continue
        entry=rows[i+1]['open']
        stop=entry*(1-sl); target=entry*(1+tp)
        exitp=None; reason='timeout'; exit_i=min(i+1+timeout, len(rows)-1)
        for j in range(i+1, min(i+1+timeout, len(rows))):
            hit_sl=low[j] <= stop
            hit_tp=high[j] >= target
            if hit_sl and hit_tp:
                # conservative: assume stop first when both same candle
                exitp=stop; reason='sl'; exit_i=j; break
            if hit_sl:
                exitp=stop; reason='sl'; exit_i=j; break
            if hit_tp:
                exitp=target; reason='tp'; exit_i=j; break
        if exitp is None:
            exitp=rows[exit_i]['close']
        ret=exitp/entry-1-FEE
        trades.append({'symbol':symbol,'i':i,'entry_t':rows[i+1]['t'],'ret':ret,'reason':reason,'rsi':rrsi[i],'room':room,'cvd':cvd_ratio,'vol_mult':vol[i]/vol_ma})
        i=exit_i+1
    return trades


def metrics(trades):
    if not trades:
        return {'n':0,'wr':0,'pf':0,'ret':0,'avg':0,'maxdd':0}
    rets=[t['ret'] for t in trades]
    wins=[r for r in rets if r>0]; losses=[r for r in rets if r<0]
    pf=sum(wins)/abs(sum(losses)) if losses else 99
    eq=1.0; peak=1.0; maxdd=0.0
    for r in rets:
        eq*=1+r; peak=max(peak,eq); maxdd=max(maxdd,1-eq/peak)
    return {'n':len(rets),'wr':len(wins)/len(rets)*100,'pf':pf,'ret':(eq-1)*100,'avg':mean(rets)*100,'maxdd':maxdd*100}


def main():
    files=sorted(CACHE.glob('*_15m_180d.csv'))
    trending=pick_trending(files, topn=12)
    print('TRENDING_UNIVERSE top by 30d return')
    for ret,sym,p,rows in trending:
        print(f'{sym:12s} ret30d={ret*100:7.2f}% bars={len(rows)}')
    variants=[
        ('breakout_basic',0.00,1.2,0.05),
        ('breakout_room5',0.05,1.2,0.05),
        ('breakout_room10',0.10,1.2,0.05),
        ('strong_cvd_room5',0.05,1.5,0.15),
        ('strict_sniper_room10',0.10,1.8,0.20),
    ]
    print('\nRESULTS realistic market-next-open, fee=11bps, TP4/SL2/timeout32 bars')
    all_results=[]
    for name,room,vm,cvd in variants:
        trades=[]
        per={}
        for ret,sym,p,rows in trending:
            ts=backtest(rows,sym,room_min=room,vol_mult=vm,cvd_min=cvd)
            trades.extend(ts); per[sym]=metrics(ts)
        m=metrics(trades); all_results.append((name,m,trades,per))
        print(f'{name:22s} trades={m["n"]:4d} WR={m["wr"]:5.1f}% PF={m["pf"]:5.2f} ret={m["ret"]:7.2f}% avg={m["avg"]:6.3f}% maxDD={m["maxdd"]:6.2f}%')
    print('\nBEST DETAIL')
    best=max(all_results, key=lambda x: (x[1]['pf'] if x[1]['n']>=20 else -1, x[1]['ret']))
    print('best',best[0],best[1])
    print('per_symbol')
    for sym,m in sorted(best[3].items(), key=lambda kv: kv[1]['ret'], reverse=True):
        print(f'{sym:12s} n={m["n"]:3d} WR={m["wr"]:5.1f}% PF={m["pf"]:5.2f} ret={m["ret"]:7.2f}% DD={m["maxdd"]:5.2f}%')
    print('sample_trades')
    for t in best[2][:15]:
        print(t)

if __name__=='__main__':
    main()
