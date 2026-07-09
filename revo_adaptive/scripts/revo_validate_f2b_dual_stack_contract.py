#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAIL = []
WARN = []

def rel(path: str) -> Path:
    return ROOT / path

def require(cond: bool, msg: str) -> None:
    if not cond:
        FAIL.append(msg)

def warn(cond: bool, msg: str) -> None:
    if not cond:
        WARN.append(msg)

def read(path: str) -> str:
    p = rel(path)
    return p.read_text(encoding='utf-8') if p.exists() else ''

def load_json(path: str):
    p = rel(path)
    if not p.exists():
        FAIL.append(f'missing json: {path}')
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception as exc:
        FAIL.append(f'broken json: {path}: {type(exc).__name__}: {exc}')
        return {}

runtime_root = rel('user_data/revo_alpha/runtime')
runtime_binance = rel('user_data/revo_alpha/runtime/binance')
runtime_bybit = rel('user_data/revo_alpha/runtime/bybit')

require(runtime_root.exists(), 'runtime root missing')
require(runtime_binance.exists(), 'runtime/binance missing')
require(runtime_bybit.exists(), 'runtime/bybit missing')

flow_context = read('user_data/revo_alpha/flow_context.py')
require('CONTROL_TOWER_F2B_RUNTIME_DIR_RESOLVER_START' in flow_context, 'flow_context missing F2B runtime resolver marker')
require('REVO_RUNTIME_DIR' in flow_context, 'flow_context missing REVO_RUNTIME_DIR support')
require('REVO_RUNTIME_PROFILE' in flow_context, 'flow_context missing REVO_RUNTIME_PROFILE support')

router = read('user_data/revo_alpha/tools/revo_btc_mode_router_v135.py')
top100 = read('user_data/revo_alpha/tools/revo_top100_flow_engine_v132.py')
require('CONTROL_TOWER_F2B_F1I_BTC_CHOP_AS_NEUTRAL_START' in router, 'BTC router missing F1I source persistence marker')
require('F1I_BTC_CHOP_AS_NEUTRAL' in router, 'BTC router missing F1I override value')
require('CONTROL_TOWER_F2B_F1I_TOP100_CHOP_AS_NEUTRAL_START' in top100, 'Top100 missing F1I CHOP-as-neutral marker')
require("return 'BALANCED_ROTATION'" in top100 and "return 'DEFENSIVE_CHOP'" not in top100.split('def resolve_scanner_mode', 1)[-1].split('def rank_for_mode', 1)[0], 'Top100 AUTO resolver can still return DEFENSIVE_CHOP for CHOP')

collector = read('scripts/binance_flow_live_collector.py')
require('revo_flow_context_collector.json' in collector, 'collector missing isolated collector json output')
warn('revo_flow_context.json' not in collector, 'collector still mentions revo_flow_context.json; inspect manually before running collector')

bybit_cfg = load_json('user_data/config.bybit.dynamic-universe.paper.json')
require((bybit_cfg.get('exchange') or {}).get('name') == 'bybit', 'Bybit dedicated config exchange.name is not bybit')
pairlists = bybit_cfg.get('pairlists') or []
remote_urls = [p.get('pairlist_url', '') for p in pairlists if isinstance(p, dict) and p.get('method') == 'RemotePairList']
require(any('runtime/bybit/pair_universe_remote.json' in u for u in remote_urls), 'Bybit RemotePairList does not point to runtime/bybit')
require(not any('/runtime/pair_universe_remote.json' in u and '/runtime/bybit/' not in u for u in remote_urls), 'Bybit RemotePairList still points to shared root runtime')

binance_cfg_path = rel('user_data/config.binance.dynamic-universe.paper.json')
if binance_cfg_path.exists():
    binance_cfg = load_json('user_data/config.binance.dynamic-universe.paper.json')
    require((binance_cfg.get('exchange') or {}).get('name') == 'binance', 'Binance dynamic config exchange.name changed')

compose = read('docker-compose.bybit-paper.yml')
require('revo_freqtrade_f2_bybit_dynamic_paper' in compose, 'Bybit compose missing dedicated container name')
require('tradesv3_revo_v13914f2_bybit_dynamic_watch_promote.dryrun.sqlite' in compose, 'Bybit compose missing dedicated DB')
require('freqtrade-revo-v13914f2-bybit-dynamic-watch-promote.log' in compose, 'Bybit compose missing dedicated logfile')
require('REVO_RUNTIME_DIR' in compose and '/runtime/bybit' in compose, 'Bybit compose missing runtime/bybit env')
require('REVO_MARKET_SOURCE: BYBIT' in compose, 'Bybit compose missing REVO_MARKET_SOURCE=BYBIT')
require('config.bybit.dynamic-universe.paper.json' in compose, 'Bybit compose missing dedicated config')

print('F2B_DUAL_STACK_CONTRACT_AUDIT')
print(f'runtime_root={runtime_root}')
print(f'runtime_binance_exists={runtime_binance.exists()}')
print(f'runtime_bybit_exists={runtime_bybit.exists()}')
print(f'bybit_remote_urls={remote_urls}')
print(f'warnings={len(WARN)}')
for item in WARN:
    print(f'WARN: {item}')

if FAIL:
    print('F2B_DUAL_STACK_CONTRACT_FAIL')
    for item in FAIL:
        print(f'FAIL: {item}')
    sys.exit(1)

print('F2B_DUAL_STACK_CONTRACT_PASS')
