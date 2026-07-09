#!/usr/bin/env python3
"""Control Tower v1.3.5 - BTC Native Mode Router.

Uses the user's BTC 15m VWAP / EMA20 / structure-break idea as a market-mode router.
This is not an entry trigger. It only controls scanner rotation and audit context.
"""
from __future__ import annotations

import argparse
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BYBIT_BASE_URL = "https://api.bybit.com"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v in (None, "", "None"):
            return default
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def http_get_json(path: str, params: Optional[Dict[str, Any]] = None, timeout: int = 20) -> Dict[str, Any]:
    q = urllib.parse.urlencode(params or {})
    url = f"{BYBIT_BASE_URL}{path}" + (f"?{q}" if q else "")
    req = urllib.request.Request(url, headers={"User-Agent": "FusionOmega-ControlTower-v1.3.5"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - public market data
        data = json.loads(resp.read().decode("utf-8"))
    if int(data.get("retCode", -1)) != 0:
        raise RuntimeError(f"Bybit API error retCode={data.get('retCode')} retMsg={data.get('retMsg')}")
    return data


def fetch_btc_15m(limit: int = 80) -> List[Dict[str, float]]:
    data = http_get_json('/v5/market/kline', {'category': 'linear', 'symbol': 'BTCUSDT', 'interval': '15', 'limit': int(limit)}, timeout=25)
    rows = (data.get('result', {}) or {}).get('list', []) or []
    rows = sorted(rows, key=lambda r: int(r[0]) if r and str(r[0]).isdigit() else 0)
    out = []
    for r in rows:
        if len(r) < 6:
            continue
        out.append({
            'ts': safe_float(r[0]),
            'open': safe_float(r[1]),
            'high': safe_float(r[2]),
            'low': safe_float(r[3]),
            'close': safe_float(r[4]),
            'volume': safe_float(r[5]),
        })
    return out


def ema(values: List[float], period: int) -> List[float]:
    if not values:
        return []
    k = 2.0 / (float(period) + 1.0)
    e = values[0]
    out = []
    for v in values:
        e = v * k + e * (1.0 - k)
        out.append(e)
    return out


def rolling_vwap(rows: List[Dict[str, float]], window: int = 25) -> float:
    sample = rows[-window:] if len(rows) >= window else rows
    num = 0.0
    den = 0.0
    for r in sample:
        typical = (r['high'] + r['low'] + r['close']) / 3.0
        vol = max(r['volume'], 0.0)
        num += typical * vol
        den += vol
    return num / den if den > 0 else (sample[-1]['close'] if sample else 0.0)


def classify_btc(rows: List[Dict[str, float]], lookback: int = 25, break_pct: float = 0.0015) -> Dict[str, Any]:
    if len(rows) < max(lookback + 3, 30):
        return {
            'btc_mode': 'BTC_CONTEXT_NOT_READY',
            'btc_weight': 0.15,
            'btc_weight_label': 'W015',
            'btc_vwap_15m': 0.0,
            'btc_ema20_15m': 0.0,
            'btc_follower_bias': 'UNKNOWN',
            'scanner_mode': 'CORE_TOP_VOLUME',
            'reason': 'NOT_ENOUGH_15M_CANDLES',
        }
    close = [r['close'] for r in rows]
    highs = [r['high'] for r in rows]
    lows = [r['low'] for r in rows]
    ema20 = ema(close, 20)
    vwap = rolling_vwap(rows, lookback)
    c = close[-1]
    e = ema20[-1]
    e2 = ema20[-3]
    prev_high = max(highs[-lookback-1:-1])
    prev_low = min(lows[-lookback-1:-1])
    bull_bias = c > vwap and e > e2
    bear_bias = c < vwap and e < e2
    long_signal = bull_bias and c > e and c > prev_high * (1.0 + break_pct)
    short_signal = bear_bias and c < e and c < prev_low * (1.0 - break_pct)
    dist_vwap_pct = ((c / vwap) - 1.0) * 100.0 if vwap else 0.0
    ema_slope_pct = ((e / e2) - 1.0) * 100.0 if e2 else 0.0
    range_pct = ((prev_high - prev_low) / c) * 100.0 if c else 0.0

    if long_signal:
        mode = 'BTC_BULLISH_BREAKOUT'
        scanner = 'TRENDING_RUNNER'
        weight = 0.40
        label = 'W040'
        follower = 'COUPLED_BULLISH'
        reason = 'VWAP_EMA20_PREVHIGH_BREAKOUT'
    elif short_signal:
        mode = 'BTC_BEARISH_BREAKDOWN'
        scanner = 'LOSER_DUMPER'
        weight = 0.40
        label = 'W040'
        follower = 'COUPLED_BEARISH'
        reason = 'VWAP_EMA20_PREVLOW_BREAKDOWN'
    elif abs(dist_vwap_pct) < 0.20 and range_pct < 1.2:
        mode = 'BTC_CHOP'
        scanner = 'DEFENSIVE_CHOP'
        weight = 0.40
        label = 'W040'
        follower = 'COUPLED_CHOP'
        reason = 'NEAR_VWAP_RANGE_COMPRESSED'
    else:
        mode = 'BTC_NEUTRAL_VWAP'
        scanner = 'BALANCED_ROTATION'
        weight = 0.15
        label = 'W015'
        follower = 'PARTIAL_DECOUPLE'
        reason = 'NO_STRUCTURE_BREAK'

    return {
        'btc_mode': mode,
        'btc_weight': weight,
        'btc_weight_label': label,
        'btc_follower_bias': follower,
        'scanner_mode': scanner,
        'reason': reason,
        'btc_close': c,
        'btc_vwap_15m': vwap,
        'btc_ema20_15m': e,
        'btc_ema20_slope_pct': ema_slope_pct,
        'btc_prev_high_25': prev_high,
        'btc_prev_low_25': prev_low,
        'btc_dist_vwap_pct': dist_vwap_pct,
        'btc_range_25_pct': range_pct,
        'long_signal': bool(long_signal),
        'short_signal': bool(short_signal),
        'lookback': lookback,
        'break_pct': break_pct,
    }



# CONTROL_TOWER_V13914F1_BINANCE_CANONICAL_SOURCE_ALIGNMENT_START
# Purpose:
# - Make BTC native mode router use Binance BTCUSDT futures when REVO_MARKET_SOURCE=BINANCE.
# - Preserve original Bybit fetch behind REVO_MARKET_SOURCE=BYBIT.
import os as _ct_f1_os
import urllib.parse as _ct_f1_urlparse
import urllib.request as _ct_f1_urlrequest

_ct_f1_bybit_fetch_btc_15m = fetch_btc_15m
_CT_F1_BINANCE_FAPI = "https://fapi.binance.com"

def _ct_f1_market_source() -> str:
    return str(_ct_f1_os.environ.get("REVO_MARKET_SOURCE", "BINANCE")).upper().strip()

def _ct_f1_binance_json(path: str, params: Optional[Dict[str, Any]] = None, timeout: int = 25) -> Any:
    q = _ct_f1_urlparse.urlencode(params or {})
    url = f"{_CT_F1_BINANCE_FAPI}{path}" + (f"?{q}" if q else "")
    req = _ct_f1_urlrequest.Request(url, headers={"User-Agent": "FusionOmega-ControlTower-v13914F1-BinanceBTCRouter"})
    with _ct_f1_urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

def fetch_btc_15m(limit: int = 80) -> List[Dict[str, float]]:
    if _ct_f1_market_source() != "BINANCE":
        return _ct_f1_bybit_fetch_btc_15m(limit)
    rows = _ct_f1_binance_json('/fapi/v1/klines', {'symbol': 'BTCUSDT', 'interval': '15m', 'limit': int(limit)}, timeout=25)
    out: List[Dict[str, float]] = []
    for r in rows if isinstance(rows, list) else []:
        if len(r) < 6:
            continue
        out.append({
            'ts': safe_float(r[0]),
            'open': safe_float(r[1]),
            'high': safe_float(r[2]),
            'low': safe_float(r[3]),
            'close': safe_float(r[4]),
            'volume': safe_float(r[5]),
        })
    return out
# CONTROL_TOWER_V13914F1_BINANCE_CANONICAL_SOURCE_ALIGNMENT_END


# CONTROL_TOWER_F2B_F1I_BTC_CHOP_AS_NEUTRAL_START
# Purpose: persist approved F1I policy in source.
# BTC_CHOP must not activate DEFENSIVE_CHOP scanner mode.
# This is scanner contract protection only; entry/gate/VIP/ROI/SL/TP/leverage/sizing unchanged.
def _ct_f2b_apply_f1i_chop_as_neutral(ctx: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(ctx or {})
    btc_mode = str(out.get('btc_mode', '')).upper()
    scanner_mode = str(out.get('scanner_mode', '')).upper()
    if btc_mode == 'BTC_CHOP' or scanner_mode == 'DEFENSIVE_CHOP':
        out['original_btc_mode_before_f1i'] = out.get('btc_mode', 'BTC_UNKNOWN')
        out['original_scanner_mode_before_f1i'] = out.get('scanner_mode', 'UNKNOWN')
        out['btc_mode'] = 'BTC_NEUTRAL_VWAP'
        out['scanner_mode'] = 'BALANCED_ROTATION'
        out['btc_weight'] = 0.15
        out['btc_weight_label'] = 'W015'
        out['btc_follower_bias'] = 'PARTIAL_DECOUPLE'
        out['scanner_policy_override'] = 'F1I_BTC_CHOP_AS_NEUTRAL'
        out['f1i_policy'] = 'BTC_CHOP_AS_NEUTRAL_SCANNER_NO_DEFENSIVE_CHOP'
        reason = str(out.get('reason', ''))
        out['reason'] = (reason + '|F1I_BTC_CHOP_AS_NEUTRAL').strip('|')
    return out
# CONTROL_TOWER_F2B_F1I_BTC_CHOP_AS_NEUTRAL_END


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description='BTC Native Mode Router v1.3.5')
    p.add_argument('--runtime-dir', default='user_data/revo_alpha/runtime')
    p.add_argument('--lookback', type=int, default=25)
    p.add_argument('--break-pct', type=float, default=0.0015)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    rt = Path(args.runtime_dir)
    rt.mkdir(parents=True, exist_ok=True)
    rows = []
    error = None
    try:
        rows = fetch_btc_15m(80)
        ctx = classify_btc(rows, int(args.lookback), float(args.break_pct))
        ctx = _ct_f2b_apply_f1i_chop_as_neutral(ctx)
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
        prior = rt / 'btc_context_v135.json'
        if prior.exists():
            try:
                ctx = json.loads(prior.read_text(encoding='utf-8'))
                ctx['fallback_from_prior'] = True
                ctx['last_error'] = error
            except Exception:
                ctx = {'btc_mode': 'BTC_CONTEXT_ERROR', 'scanner_mode': 'CORE_TOP_VOLUME', 'btc_weight': 0.15, 'btc_weight_label': 'W015', 'last_error': error}
        else:
            ctx = {'btc_mode': 'BTC_CONTEXT_ERROR', 'scanner_mode': 'CORE_TOP_VOLUME', 'btc_weight': 0.15, 'btc_weight_label': 'W015', 'last_error': error}
    ctx = _ct_f2b_apply_f1i_chop_as_neutral(ctx)
    ctx['generated_at'] = utc_now_iso()
    ctx['source'] = 'CONTROL_TOWER_V13914F1_BINANCE_CANONICAL_BTC_MODE_ROUTER' if _ct_f1_market_source() == 'BINANCE' else 'CONTROL_TOWER_V135_BTC_NATIVE_MODE_ROUTER'
    ctx['data_quality'] = 'OK' if error is None and rows else 'FALLBACK_OR_ERROR'
    write_json(rt / 'btc_context_v135.json', ctx)
    lines = [
        'CONTROL TOWER v1.3.5 - BTC NATIVE MODE ROUTER',
        f"generated_at={ctx.get('generated_at')}",
        f"btc_mode={ctx.get('btc_mode')}",
        f"scanner_mode={ctx.get('scanner_mode')}",
        f"btc_weight={ctx.get('btc_weight')}",
        f"btc_weight_label={ctx.get('btc_weight_label')}",
        f"btc_follower_bias={ctx.get('btc_follower_bias')}",
        f"btc_close={ctx.get('btc_close')}",
        f"btc_vwap_15m={ctx.get('btc_vwap_15m')}",
        f"btc_ema20_15m={ctx.get('btc_ema20_15m')}",
        f"reason={ctx.get('reason')}",
        f"data_quality={ctx.get('data_quality')}",
    ]
    (rt / 'BTC_MODE_ROUTER_COMPACT.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f"BTC_MODE_ROUTER_PASS mode={ctx.get('btc_mode')} scanner={ctx.get('scanner_mode')} runtime={rt}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
