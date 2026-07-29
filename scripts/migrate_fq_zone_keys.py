from __future__ import annotations
import json, shutil
from pathlib import Path
import pandas as pd
from bots.nexus_data import fetch_recent
from fusion_quantum.paper_runner import confirmed_setups

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'runtime/fusion_quantum/state.json'
BACK=STATE.with_name('state.json.pre_zone_migration.bak')

def main():
    state=json.loads(STATE.read_text())
    rows=state.get('setups',{})
    symbols=sorted({(r.get('setup') or {}).get('symbol') for r in rows.values() if (r.get('setup') or {}).get('symbol')})
    market={}; errors={}
    for n,sym in enumerate(symbols,1):
        try:
            market[sym]=(fetch_recent(sym,'4h',1000),fetch_recent(sym,'15m',1000))
        except Exception as e: errors[sym]=repr(e)
        if n%20==0: print(n,'/',len(symbols),flush=True)
    matched=unmatched=0
    for sid,row in rows.items():
        s=row.get('setup') or {}
        if s.get('zone_key'): matched+=1; continue
        pair=market.get(s.get('symbol'))
        if not pair:
            unmatched+=1; continue
        h,l=pair
        conf=pd.Timestamp(s['confirmed_at']); conf=conf.tz_localize('UTC') if conf.tzinfo is None else conf.tz_convert('UTC')
        cutoff=conf+pd.Timedelta(minutes=90)
        ht=h[pd.to_datetime(h.open_time,utc=True)<=cutoff].copy()
        lt=l[pd.to_datetime(l.open_time,utc=True)<=cutoff].copy()
        cand=confirmed_setups(s['symbol'],ht,lt)
        same=[]
        for x in cand:
            if x.get('side')!=s.get('side') or str(x.get('confirmed_at'))!=str(s.get('confirmed_at')): continue
            if abs(float(x['entry_price'])/float(s['entry_price'])-1)<1e-8 and abs(float(x['sl_price'])/float(s['sl_price'])-1)<1e-8:
                same.append(x)
        if same:
            s['zone_key']=same[0]['zone_key']; row['setup']=s; matched+=1
        else: unmatched+=1
    shutil.copy2(STATE,BACK)
    STATE.write_text(json.dumps(state,separators=(',',':')))
    print(json.dumps({'rows':len(rows),'matched':matched,'unmatched':unmatched,'errors':errors,'backup':str(BACK)},indent=2))
if __name__=='__main__': main()

