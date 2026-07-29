from data import fetch_klines
import time
SYMS='XRPUSDT ADAUSDT LTCUSDT BCHUSDT DOTUSDT TRXUSDT SUIUSDT NEARUSDT APTUSDT 1000PEPEUSDT'.split()
for i,s in enumerate(SYMS,1):
 for attempt in range(6):
  try:
   d=fetch_klines(s,'5m',180)
   print(f'{i}/{len(SYMS)} {s} rows={len(d)} start={d.open_time.min()} end={d.open_time.max()}',flush=True)
   break
  except Exception as e:
   wait=45*(attempt+1)
   print(f'RETRY {s} attempt={attempt+1} wait={wait}s: {type(e).__name__}: {e}',flush=True)
   if attempt==5: print(f'ERROR {s}: exhausted',flush=True)
   else: time.sleep(wait)
 time.sleep(15)
