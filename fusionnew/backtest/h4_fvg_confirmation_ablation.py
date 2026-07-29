"""Confirmation-only ablation on H4 FVG -> whole-zone M15 fractal baseline."""
from __future__ import annotations
import argparse, glob, json, os
import pandas as pd
from h4_m15_m5_fractal import bars,resample,ema,fractals
CACHE=os.path.dirname(__file__)+"/cache"
VARIANTS=('C0_base','C1_atr15','C2_atr20','C3_dist025','C4_dist050')

def engulf(side,p,q):
 if side=='long': return p.close<p.open and q.close>q.open and q.open<=p.close and q.close>=p.open
 return p.close>p.open and q.close<q.open and q.open>=p.close and q.close<=p.open

def trigger(side,seq,zlo,zhi,variant):
 ret=False; rows=list(seq.iterrows())
 for j,(ts,q) in enumerate(rows):
  touch=q.low<=zhi and q.high>=zlo
  if touch: ret=True
  if not ret or j<1: continue
  p=rows[j-1][1]
  if variant=='A0_break':
   ok=(side=='long' and q.close>p.high and q.close>q.open) or (side=='short' and q.close<p.low and q.close<q.open)
   if ok:return ts,q
  elif variant=='A1_engulf':
   if touch and engulf(side,p,q):return ts,q
  else:
   # Engulf candle must touch zone; next candle confirms in same direction.
   if j<2:continue
   e=rows[j-1][1]; before=rows[j-2][1]
   e_touch=e.low<=zhi and e.high>=zlo
   conf=(side=='long' and q.close>q.open and q.close>e.close) or (side=='short' and q.close<q.open and q.close<e.close)
   if e_touch and engulf(side,before,e) and conf:return ts,q,e
 return None

def simulate(sym,days,variant,side_filter='both'):
 f=glob.glob(f'{CACHE}/{sym}_5m_{days}d.csv');
 if not f:return [],{}
 m5=bars(f[0]);m15=resample(m5,'15min');h4=resample(m5,'4h');h4['e21']=ema(h4.close,21);h4['e50']=ema(h4.close,50)
 pc=m5.close.shift(1); trange=pd.concat([m5.high-m5.low,(m5.high-pc).abs(),(m5.low-pc).abs()],axis=1).max(axis=1);m5['atr14']=trange.rolling(14).mean()
 flo,fhi=fractals(h4);m15lo,m15hi=fractals(m15);seen=set();tr=[]
 fun={k:0 for k in ('h4','fvg','origin','zone','retest_window','trigger','closed')}
 for i in range(4,len(h4)):
  fun['h4']+=1;t=h4.index[i];a,b,c=h4.iloc[i-2],h4.iloc[i-1],h4.iloc[i]
  side=None
  if c.e21>c.e50 and b.close>b.open and a.high<c.low:side='long'
  elif c.e21<c.e50 and b.close<b.open and a.low>c.high:side='short'
  if not side or (side_filter!='both' and side!=side_filter):continue
  fun['fvg']+=1;piv=flo if side=='long' else fhi
  ids=[j for j in range(4,i-2) if bool(piv.iloc[j])]
  if not ids:continue
  pj=ids[-1];fun['origin']+=1;pt=h4.index[pj];sub=m15.loc[(m15.index>=pt)&(m15.index<t+pd.Timedelta(hours=4))]
  mids=[k for k in sub.index if k+pd.Timedelta(minutes=45)<=t+pd.Timedelta(hours=4) and bool((m15lo if side=='long' else m15hi).loc[k])]
  if not mids:continue
  origin=h4.iloc[pj]
  if side=='long': zlo=min(origin.low,min(m15.loc[k].low for k in mids));zhi=origin.high;zk=min(mids,key=lambda k:m15.loc[k].low)
  else:zlo=origin.low;zhi=max(origin.high,max(m15.loc[k].high for k in mids));zk=max(mids,key=lambda k:m15.loc[k].high)
  key=(side,zk)
  if key in seen:continue
  seen.add(key);fun['zone']+=1
  start=t+pd.Timedelta(hours=4);end=start+pd.Timedelta(hours=12);base=m15 if variant.startswith('A6') else m5
  seq=base.loc[(base.index>=start)&(base.index<end)]
  if not any((q.low<=zhi and q.high>=zlo) for _,q in seq.iterrows()):continue
  fun['retest_window']+=1;hit=trigger(side,seq,zlo,zhi,variant)
  if not hit:continue
  ts,q,e=hit; atr=float(m5.loc[ts,'atr14'])
  if not atr or pd.isna(atr):continue
  erange=float(e.high-e.low)
  if variant=='C1_atr15' and erange>1.5*atr:continue
  if variant=='C2_atr20' and erange>2.0*atr:continue
  entry=float(q.close); dist=max(zlo-entry,entry-zhi,0.0)
  if variant=='C3_dist025' and dist>0.25*atr:continue
  if variant=='C4_dist050' and dist>0.50*atr:continue
  fun['trigger']+=1;sl=zlo-.001*entry if side=='long' else zhi+.001*entry
  if (side=='long' and sl>=entry) or (side=='short' and sl<=entry):continue
  risk=abs(entry-sl)
  if risk/entry>0.05:continue
  tp=entry+(2*risk if side=='long' else -2*risk);out=None
  # Bar timestamps are OPEN times. Position exists only after trigger bar closes.
  ready=ts+(pd.Timedelta(minutes=15) if variant.startswith('A6') else pd.Timedelta(minutes=5))
  for _,x in m5.loc[m5.index>=ready].head(288).iterrows():
   hs=x.low<=sl if side=='long' else x.high>=sl;ht=x.high>=tp if side=='long' else x.low<=tp
   if hs:out=-1.;break
   if ht:out=2.;break
  if out is not None:
   net=out-.0009*entry/risk;tr.append(dict(symbol=sym,time=str(ts),side=side,r=net,gross_r=out,entry=entry,sl=sl,tp=tp));fun['closed']+=1
 return tr,fun

def metrics(tr):
 p=[x['r'] for x in tr];gp=sum(x for x in p if x>0);gl=-sum(x for x in p if x<0)
 return dict(n=len(p),wins=sum(x>0 for x in p),wr=sum(x>0 for x in p)/len(p) if p else None,pf=gp/gl if gl else None,net_r=sum(p))
def main():
 a=argparse.ArgumentParser();a.add_argument('--days',type=int,default=60);a.add_argument('--symbols',default='');z=a.parse_args()
 syms=z.symbols.split() or sorted(os.path.basename(x).split('_5m_')[0] for x in glob.glob(f'{CACHE}/*_5m_{z.days}d.csv'))
 out={}
 for v in VARIANTS:
  tr=[];fu={}
  for s in syms:
   x,f=simulate(s,z.days,v);tr+=x;fu[s]=f
  out[v]={'metrics':metrics(tr),'funnel':fu,'trades':tr}
 print(json.dumps({'days':z.days,'symbols':syms,'variants':out},indent=2,default=lambda o:o.item() if hasattr(o,'item') else str(o)))
if __name__=='__main__':main()
