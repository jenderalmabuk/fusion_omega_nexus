"""Independent backtest-loop parity against lifecycle WebSocket shadow."""
import json,sys
from pathlib import Path
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'fusionnew'))
from bots.nexus_data import fetch_recent
from backtest.faithful_imbalance import _valid_obs,_imbalances
from fusion_quantum.backtest_quantum import CONFIRM_WINDOW,mss_confirm,ZoneLifecycle
from fusion_quantum.paper_runner import lifecycle_key,zone_key

SH=Path('runtime/fusion_quantum/ws_lifecycle_shadow_v2.jsonl')

def independent(symbol,h,l):
 lows,highs=l.low.to_numpy(),l.high.to_numpy(); touched=set(); out=[]
 for side in ('BULL','BEAR'):
  obs=_valid_obs(h,side)
  for im in _imbalances(l,side):
   ce=int(im['ce']); prior=[ob for ob in obs if ob['t']<im['t'] and im['leg_low']<=ob['zhigh'] and im['leg_high']>=ob['zlow']]
   if not prior:continue
   z=prior[-1]; entry0=im['leg_low']+.618*(im['leg_high']-im['leg_low']) if side=='BULL' else im['leg_high']-.618*(im['leg_high']-im['leg_low'])
   start=next((i for i in range(ce+1,min(ce+CONFIRM_WINDOW+1,len(l))) if (lows[i]<=entry0 if side=='BULL' else highs[i]>=entry0)),None)
   key=tuple(zone_key(z,side))
   if start is None or key in touched:continue
   touched.add(key)
   conf=mss_confirm(l,z,side,start,min(start+CONFIRM_WINDOW,len(l)))
   ev={'symbol':symbol,'side':'LONG' if side=='BULL' else 'SHORT','zone_key':zone_key(z,side),'first_touch_at':str(l.open_time.iloc[start]),'outcome':'confirmed' if conf else 'failed_confirmation'}
   out.append(ev)
 return out

def main():
 rows=[json.loads(x) for x in SH.read_text().splitlines() if x.strip()]
 scans=[x for x in rows if x.get('event')=='shadow_scan']; expected_ms=scans[0]['candle_start']; cutoff=pd.Timestamp(expected_ms,unit='ms',tz='UTC')
 mism=[]; total=0
 for n,x in enumerate(scans,1):
  h=fetch_recent(x['symbol'],'4h',1000);l=fetch_recent(x['symbol'],'15m',1000)
  h=h[pd.to_datetime(h.open_time,utc=True)<=cutoff];l=l[pd.to_datetime(l.open_time,utc=True)<=cutoff]
  exp=independent(x['symbol'],h,l); got=x['consumed']
  ek={(lifecycle_key(e),e['outcome'],e['first_touch_at']) for e in exp}; gk={(lifecycle_key(e),e['outcome'],e['first_touch_at']) for e in got}
  total+=len(ek)
  if ek!=gk:mism.append({'symbol':x['symbol'],'missing':list(ek-gk)[:5],'extra':list(gk-ek)[:5]})
 print(json.dumps({'candle':str(cutoff),'symbols':len(scans),'expected_events':total,'shadow_events':sum(len(x['consumed']) for x in scans),'mismatch_symbols':len(mism),'mismatches':mism[:10]},indent=2,default=str))
if __name__=='__main__':main()
