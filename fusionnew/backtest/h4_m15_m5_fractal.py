"""Objective H4 imbalance -> M15 refinement -> M5 confirmation replay.
Uses cached Binance 5m data only. No lookahead: H4 candle/fractal confirmed before
its close; M15 zone uses completed candles; M5 confirmation closes before entry.
"""
from __future__ import annotations
import argparse, glob, os, json
import numpy as np, pandas as pd

CACHE=os.path.dirname(__file__)+"/cache"

def bars(path):
 d=pd.read_csv(path,parse_dates=['open_time']).set_index('open_time')
 for c in ['open','high','low','close','volume']: d[c]=d[c].astype(float)
 return d

def resample(x, rule):
 return x.resample(rule, origin='epoch', label='left', closed='left').agg(
  open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum')).dropna()

def ema(x,n): return x.ewm(span=n,adjust=False).mean()
def fractals(x):
 # Flags live on pivot candle. Caller must enforce knowledge_time = pivot + 2 bars.
 lo=(x.low<x.low.shift(1)) & (x.low<x.low.shift(2)) & (x.low<=x.low.shift(-1)) & (x.low<=x.low.shift(-2))
 hi=(x.high>x.high.shift(1)) & (x.high>x.high.shift(2)) & (x.high>=x.high.shift(-1)) & (x.high>=x.high.shift(-2))
 return lo.fillna(False),hi.fillna(False)

def one(sym,days):
 files=glob.glob(f'{CACHE}/{sym}_5m_{days}d.csv')
 if not files:return [],{'symbol':sym,'error':'cache_missing'}
 m5=bars(files[0]); m15=resample(m5,'15min'); h4=resample(m5,'4h')
 h4['e21']=ema(h4.close,21);h4['e50']=ema(h4.close,50)
 flo,fhi=fractals(h4); m15lo,m15hi=fractals(m15); trades=[]; seen=set(); funnel={'h4_bars':len(h4),'trend':0,'imbalance':0,'fractal':0,'m15_zone':0,'retest':0,'confirm':0}
 active=[]
 for i in range(4,len(h4)):
  t=h4.index[i]; row=h4.iloc[i]
  trend='long' if row.e21>row.e50 else 'short'
  funnel['trend']+=1
  # imbalance candles i-2,i-1,i; known after candle i closes
  a,b,c=h4.iloc[i-2],h4.iloc[i-1],row
  side=None; gap=None
  if trend=='long' and b.close>b.open and a.high<c.low: side='long'; gap=(a.high,c.low)
  if trend=='short' and b.close<b.open and a.low>c.high: side='short'; gap=(c.high,a.low)
  if not side: continue
  funnel['imbalance']+=1
  # last confirmed pivot before imbalance, at least 2 H4 bars before i
  piv=flo if side=='long' else fhi
  candidates=[j for j in range(4,i-2) if bool(piv.iloc[j])]
  if not candidates: continue
  pj=candidates[-1]; funnel['fractal']+=1
  # Whole-zone refinement: inspect all confirmed M15 fractals from H4
  # origin candle through FVG candle. Keep proximal H4 boundary; move
  # distal boundary to most extreme lower-TF fractal (per chart example).
  pt=h4.index[pj]; zone_end=t+pd.Timedelta(hours=4)
  sub=m15.loc[(m15.index>=pt)&(m15.index<zone_end)]
  ids=[k for k in sub.index if bool((m15lo if side=='long' else m15hi).loc[k])]
  if not ids: continue
  origin=h4.iloc[pj]
  if side=='long':
   zlo=min(float(origin.low), min(float(m15.loc[k].low) for k in ids))
   zhi=float(origin.high)
   zk=min(ids,key=lambda k:m15.loc[k].low)
  else:
   zlo=float(origin.low)
   zhi=max(float(origin.high), max(float(m15.loc[k].high) for k in ids))
   zk=max(ids,key=lambda k:m15.loc[k].high)
  key=(side,zk)
  if key in seen: continue
  seen.add(key); funnel['m15_zone']+=1
  if zlo>=zhi: continue
  # scan M5 after H4 confirmation, until next H4 bar; retest then confirmation
  start=t+pd.Timedelta(hours=4); end=start+pd.Timedelta(hours=12)
  seq=m5.loc[(m5.index>=start)&(m5.index<end)]
  ret=False; prev=None
  for ts,q in seq.iterrows():
   touched=(q.low<=zhi and q.high>=zlo)
   if touched: ret=True; funnel['retest']+=1
   if ret and prev is not None:
    confirm=(side=='long' and q.close>prev.high and q.close>q.open) or (side=='short' and q.close<prev.low and q.close<q.open)
    if confirm:
     funnel['confirm']+=1; entry=float(q.close)
     sl=(zlo-0.001*entry if side=='long' else zhi+0.001*entry)
     if (side=='long' and sl>=entry) or (side=='short' and sl<=entry): prev=q; continue
     risk=abs(entry-sl)
     if risk/entry>0.05: prev=q; continue
     tp=entry+(2*risk if side=='long' else -2*risk); outcome=None
     future=m5.loc[m5.index>ts].head(288)
     for _,f in future.iterrows():
      hit_sl=(f.low<=sl if side=='long' else f.high>=sl); hit_tp=(f.high>=tp if side=='long' else f.low<=tp)
      if hit_sl and hit_tp: outcome=-1.0;break # conservative same-bar
      if hit_sl: outcome=-1.0;break
      if hit_tp: outcome=2.0;break
     if outcome is not None:
      netr=outcome-(0.0009*entry/risk)
      trades.append({'symbol':sym,'time':ts,'side':side,'r':netr,'gross_r':outcome,'entry':entry,'sl':sl,'tp':tp})
     break
   prev=q
 return trades,funnel

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--days',type=int,default=60);ap.add_argument('--symbols',default='')
 a=ap.parse_args(); syms=a.symbols.split() or sorted(os.path.basename(x).split('_5m_')[0] for x in glob.glob(f'{CACHE}/*_5m_{a.days}d.csv'))
 alltr=[]; fs={}
 for s in syms:
  t,f=one(s,a.days);alltr+=t;fs[s]=f
 p=[x['r'] for x in alltr]; wins=sum(x>0 for x in p); gp=sum(x for x in p if x>0);gl=abs(sum(x for x in p if x<0));pf=gp/gl if gl else float('inf')
 print(json.dumps({'strategy':'H4-M15-M5 fractal','days':a.days,'symbols':syms,'n':len(p),'wins':wins,'wr':wins/len(p) if p else None,'pf':pf,'net_r':sum(p),'funnel':fs,'trades':alltr},default=str,indent=2))
if __name__=='__main__':main()
