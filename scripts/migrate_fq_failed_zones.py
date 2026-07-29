from __future__ import annotations
import hashlib,json,shutil
from pathlib import Path
from fusion_quantum.paper_runner import lifecycle_key
ROOT=Path(__file__).resolve().parents[1]; STATE=ROOT/'runtime/fusion_quantum/state.json'; SH=ROOT/'runtime/fusion_quantum/ws_lifecycle_shadow_v2.jsonl'; BACK=STATE.with_name('state.json.pre_failed_zone_migration.bak')
def main():
 state=json.loads(STATE.read_text()); setups=state.setdefault('setups',{}); added=existing=0
 rows=[json.loads(x) for x in SH.read_text().splitlines() if x.strip()]
 events=[e for r in rows if r.get('event')=='shadow_scan' for e in r.get('consumed',[]) if e.get('outcome')=='failed_confirmation']
 shutil.copy2(STATE,BACK)
 for e in events:
  key=lifecycle_key(e)
  if not key:continue
  zid='zone_'+hashlib.sha256(key.encode()).hexdigest()[:19]
  if zid in setups:existing+=1;continue
  setups[zid]={'status':'failed_confirmation','updated_at':'2026-07-28T00:30:00+00:00','setup':e,'lifecycle_reason':'first_retest_failed_mss_shadow_migration'};added+=1
 STATE.write_text(json.dumps(state,separators=(',',':')))
 print(json.dumps({'events':len(events),'added':added,'existing':existing,'rows':len(setups),'backup':str(BACK)},indent=2))
if __name__=='__main__':main()
