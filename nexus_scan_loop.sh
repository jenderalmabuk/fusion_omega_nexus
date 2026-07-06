#!/usr/bin/env bash
# nexus_scan_loop.sh — Continuous universal scanner loop
# Fetches all Bybit pairs (OHLCV + OI) at configurable interval
# Binance used as collaborative fallback when available
# Pushes results to data/ cache for bot adapters to consume

set -euo pipefail

cd "${NEXUS_DIR:-/home/fusion_omega/fusion_omega_nexus}"
source venv/bin/activate

INTERVAL="${SCAN_INTERVAL:-60}"   # scan interval in seconds
TFS="${SCAN_TFS:-30m,5m,1m,1h}"   # timeframes to fetch
PAIRS_LIMIT="${PAIRS_LIMIT:-530}"  # max pairs to scan (0 = all)
VOL_MIN="${VOL_MIN:-0}"            # minimum 24h volume (0 = all)

echo "[nexus_loop] Starting: interval=${INTERVAL}s tfs=${TFS} pairs_limit=${PAIRS_LIMIT}"

python3 -c "
import asyncio, sys, time, os, signal

sys.path.insert(0, '.')
from scanner.universe_scanner import UniverseScanner

async def run_loop():
    scanner = UniverseScanner()
    interval = int(os.environ.get('SCAN_INTERVAL', 60))
    tfs = os.environ.get('SCAN_TFS', '30m,5m,1m,1h').split(',')
    pairs_limit = int(os.environ.get('PAIRS_LIMIT', 530))
    vol_min = float(os.environ.get('VOL_MIN', 0))
    
    print(f'[nexus_loop] Discovering pairs (min_vol={vol_min:,.0f})...')
    await scanner.discover_pairs(min_volume_24h=vol_min)
    
    if pairs_limit > 0 and len(scanner.pairs) > pairs_limit:
        scanner.pairs = scanner.pairs[:pairs_limit]
    
    print(f'[nexus_loop] Ready: {len(scanner.pairs)} pairs, '
          f'{len(tfs)} timeframes: {tfs}')
    
    cycle = 0
    while True:
        cycle += 1
        t_start = time.time()
        print(f'\\n[nexus_loop] Cycle {cycle} — '
              f'{time.strftime(\"%Y-%m-%dT%H:%M:%SZ\", time.gmtime())}')
        
        try:
            result = await scanner.full_scan(pairs=scanner.pairs, tfs=tfs, with_oi=True)
            scanner.save_cache(result, prefix='latest')
            
            elapsed = time.time() - t_start
            pairs_ok = len(result.get('oi', {}))
            tfs_ok = {tf: len(frames) for tf, frames in result.get('timeframes', {}).items()}
            
            print(f'[nexus_loop] Cycle {cycle} done: {elapsed:.1f}s, '
                  f'OI {pairs_ok}/ {len(scanner.pairs)}, '
                  f'klines {tfs_ok}')
        except Exception as e:
            print(f'[nexus_loop] ERROR cycle {cycle}: {e}')
        
        sleep_time = max(interval - (time.time() - t_start), 5)
        await asyncio.sleep(sleep_time)

async def main():
    try:
        await run_loop()
    except KeyboardInterrupt:
        print('[nexus_loop] Shutdown')

asyncio.run(main())
"