"""Read-only replay parity for Fusion Quantum live setup state vs current Nexus history."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
from bots.nexus_data import fetch_recent
from fusion_quantum.paper_runner import confirmed_setups, setup_id

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'runtime/fusion_quantum/state.json'


def main():
    rows=json.loads(STATE.read_text())['setups']
    targets={sid:r['setup'] for sid,r in rows.items() if r.get('setup') and r.get('status')=='paper_opened'}
    symbols=sorted({s['symbol'] for s in targets.values()})
    market={}; errors={}
    for n,symbol in enumerate(symbols,1):
        try:
            market[symbol]=(fetch_recent(symbol,'4h',1000),fetch_recent(symbol,'15m',1000))
        except Exception as exc: errors[symbol]=repr(exc)
        if n%20==0: print(n,'/',len(symbols),flush=True)
    exact=0; level=0; missing=[]
    for sid,s in targets.items():
        pair=market.get(s['symbol'])
        if not pair:
            missing.append({'setup_id':sid,'symbol':s['symbol'],'reason':'fetch_error','stored':s})
            continue
        h,l=pair
        # Reproduce detector view at first possible discovery cycle. conf bar plus
        # expiry bars may exist; no later history may influence valid-ob search.
        conf=pd.Timestamp(s['confirmed_at']); conf=conf.tz_localize('UTC') if conf.tzinfo is None else conf.tz_convert('UTC')
        cutoff=conf+pd.Timedelta(minutes=15*6)
        lt=l[pd.to_datetime(l.open_time,utc=True)<=cutoff].copy()
        ht=h[pd.to_datetime(h.open_time,utc=True)<=cutoff].copy()
        candidates=confirmed_setups(s['symbol'],ht,lt)
        ids={setup_id(x):x for x in candidates}
        if sid in ids: exact+=1; continue
        same=[x for x in candidates if x['side']==s['side'] and str(x['confirmed_at'])==str(s['confirmed_at'])]
        if same:
            x=same[0]; de=abs(x['entry_price']/s['entry_price']-1)*10000; ds=abs(x['sl_price']/s['sl_price']-1)*10000
            if de<.001 and ds<.001: level+=1
            else: missing.append({'setup_id':sid,'symbol':s['symbol'],'reason':'level_changed','entry_delta_bp':de,'sl_delta_bp':ds,'stored':s,'replay':x})
        else: missing.append({'setup_id':sid,'symbol':s['symbol'],'reason':'not_emitted','stored':s,'replay_candidates':candidates})
    out={'targets':len(targets),'symbols':len(symbols),'exact_id':exact,'same_level':level,'missing':len(missing),'errors':errors,'missing_rows':missing}
    dest=ROOT/'fusion_quantum/results/live_setup_replay_parity.json'; dest.write_text(json.dumps(out,indent=2,default=str))
    print(json.dumps({k:v for k,v in out.items() if k!='missing_rows'},indent=2));
    for x in missing[:20]: print(x['symbol'],x['reason'],len(x.get('replay_candidates',[])))
    print('written',dest)
if __name__=='__main__': main()
