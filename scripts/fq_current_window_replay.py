"""Current-window Fusion Quantum replay using fresh Nexus H4+M15 data. Read-only."""
from __future__ import annotations
import json,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import pandas as pd
from bots.nexus_data import fetch_recent
import fusion_quantum.backtest_quantum as bt

ROOT=Path(__file__).resolve().parents[1]
START=pd.Timestamp('2026-07-27T01:48:35Z')
END=pd.Timestamp('2026-07-27T14:54:00Z')


def load_symbol(sym):
    h=fetch_recent(sym,'4h',1000); l=fetch_recent(sym,'15m',1000)
    for df in (h,l):
        if 'open_time' in df.columns:
            ts=pd.to_datetime(df['open_time'],utc=True)
            df['open_time']=ts
            df['open_time_ms']=(ts.astype('int64')//1_000_000).astype('int64')
    return sym,h,l


def main():
    syms=[x.strip() for x in open(ROOT/'runtime/revo/canonical_universe.txt') if x.strip()]
    data={}; fetch_errors=[]; t0=time.time()
    with ThreadPoolExecutor(max_workers=12) as ex:
        fs={ex.submit(load_symbol,s):s for s in syms}
        for n,f in enumerate(as_completed(fs),1):
            try:
                s,h,l=f.result(); data[(s,'4h')]=h; data[(s,'15m')]=l
            except Exception as e: fetch_errors.append({'symbol':fs[f],'error':repr(e)})
            if n%100==0: print('fetch',n,'/',len(syms),flush=True)
    bt.DATA=data
    rows=[]; sim_errors=[]
    with ThreadPoolExecutor(max_workers=10) as ex:
        fs={ex.submit(bt.simulate,s,'H4_M15',180,'baseline_2r'):s for s in syms if (s,'4h') in data}
        for n,f in enumerate(as_completed(fs),1):
            try: rows.append(f.result())
            except Exception as e: sim_errors.append({'symbol':fs[f],'error':repr(e)})
            if n%100==0: print('simulate',n,'/',len(fs),flush=True)
    alltr=[t for r in rows for t in r.get('trades',[])]
    tr=[t for t in alltr if START<=pd.Timestamp(t['entry_time'])<=END]
    tr.sort(key=lambda x:x['entry_time'])
    cutoff=START+(END-START)*.6
    def m(xs): return bt.metrics(xs)
    journal=json.loads((ROOT/'runtime/fusion_quantum/journal/trade_history.json').read_text())
    live=[x for x in journal if pd.Timestamp(x['timestamp_open'])>=START]
    state=json.loads((ROOT/'runtime/fusion_quantum/state.json').read_text())['setups']
    pending_events=[r for r in state.values() if r.get('setup') and pd.Timestamp(r['setup']['confirmed_at'],tz='UTC')>=START]
    funnel={k:sum(r.get('funnel',{}).get(k,0) for r in rows) for k in bt.ZoneLifecycle().funnel}
    out={
      'generated_at':str(END),'range':{'start':str(START),'cutoff':str(cutoff),'end':str(END)},
      'funnel':funnel,
      'symbols':len(syms),'fetch_errors':fetch_errors,'sim_errors':sim_errors,
      'theoretical':{'all':m(tr),'is':m([x for x in tr if pd.Timestamp(x['entry_time'])<cutoff]),'oos':m([x for x in tr if pd.Timestamp(x['entry_time'])>=cutoff]),'long':m([x for x in tr if x['side']=='BULL']),'short':m([x for x in tr if x['side']=='BEAR'])},
      'live_journal':{'n':len(live),'wins':sum(x['pnl_usd']>0 for x in live),'net_usd':sum(x['pnl_usd'] for x in live)},
      'state_setups_in_window':len(pending_events),'trades':tr,
      'elapsed_sec':round(time.time()-t0,1)
    }
    dest=ROOT/'fusion_quantum/results/current_window_replay.json'; dest.write_text(json.dumps(out,indent=2,default=str))
    print(json.dumps({k:v for k,v in out.items() if k!='trades'},indent=2,default=str)); print('written',dest)
if __name__=='__main__': main()
