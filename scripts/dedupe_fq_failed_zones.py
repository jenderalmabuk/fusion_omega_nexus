from __future__ import annotations
import json,shutil
from pathlib import Path
from fusion_quantum.paper_runner import zone_key,lifecycle_key
ROOT=Path(__file__).resolve().parents[1]; STATE=ROOT/'runtime/fusion_quantum/state.json'; BACK=STATE.with_name('state.json.pre_failed_zone_dedupe.bak')
def canonical(event):
 e=dict(event); z=e.get('zone_key')
 if not z:return None
 e['zone_key']=zone_key({'t':z[1],'zlow':z[2],'zhigh':z[3]},z[0]); return lifecycle_key(e)
def main():
 d=json.loads(STATE.read_text()); s=d['setups']; groups={}
 for sid,r in s.items():
  if r.get('status')!='failed_confirmation':continue
  k=canonical(r.get('setup')or{})
  if k:groups.setdefault(k,[]).append(sid)
 shutil.copy2(STATE,BACK); removed=[]
 for ids in groups.values():
  if len(ids)<2:continue
  keep=next((x for x in ids if (s[x].get('lifecycle_reason')or'').endswith('shadow_migration')),ids[0])
  for sid in ids:
   if sid!=keep:removed.append(sid);del s[sid]
 # canonicalize retained key payloads
 for r in s.values():
  if r.get('status')=='failed_confirmation' and (r.get('setup')or{}).get('zone_key'):
   z=r['setup']['zone_key'];r['setup']['zone_key']=zone_key({'t':z[1],'zlow':z[2],'zhigh':z[3]},z[0])
 STATE.write_text(json.dumps(d,separators=(',',':')))
 print(json.dumps({'before_failed':sum(len(v) for v in groups.values()),'canonical_groups':len(groups),'removed':len(removed),'after_failed':sum(r.get('status')=='failed_confirmation' for r in s.values()),'rows':len(s),'backup':str(BACK)},indent=2))
if __name__=='__main__':main()
