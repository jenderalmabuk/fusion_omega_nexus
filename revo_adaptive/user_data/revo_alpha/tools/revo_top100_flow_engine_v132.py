#!/usr/bin/env python3
"""Control Tower v1.3.7 - Top100 Flow Engine + BTC Router + Rotating Scanner + Quadrant Refactor.

This keeps the old broad scanner feel, but makes universe selection and flow labels safer:
- BTC mode controls scanner rotation.
- CVD + OI 15m/1h are classified into continuation, unwind/cover, stale, and trap states.
- Flow only grants direction authority for strong/fresh continuation. It never opens trades.
"""
from __future__ import annotations

import argparse
import csv
import json
# CONTROL_TOWER_V13914F1D_PERSIST_SCHEMA_ALIAS_WRITER_START
# Purpose:
# - Persist schema alias compatibility at JSON writer level.
# - Keeps both old and new numeric field names available for gate/audit readers.
# - Entry logic unchanged.
# - Strategy unchanged.
import os as _ct_f1d_os
import json as _ct_f1d_json

_ct_f1d_original_json_dumps = _ct_f1d_json.dumps

_CT_F1D_ALIAS_MAP = {
    "price_delta_pct_15m": ["price_delta_15m_pct", "price_change_15m_pct", "p15"],
    "price_delta_pct_1h": ["price_delta_1h_pct", "price_change_1h_pct", "p1h"],
    "oi_delta_pct_15m": ["oi_delta_15m_pct", "open_interest_delta_15m_pct", "oi15"],
    "oi_delta_pct_1h": ["oi_delta_1h_pct", "open_interest_delta_1h_pct", "oi1h"],
    "cvd_zscore_15m": ["cvd_zscore", "cvd_z", "cvd_z_15m"],
    "cvd_delta_15m": ["cvd_delta"],
    "volume_zscore_15m": ["volume_zscore", "volume_z", "volume_z_15m"],
}

def _ct_f1d_alias_row(row):
    if not isinstance(row, dict):
        return
    for src, dsts in _CT_F1D_ALIAS_MAP.items():
        if src in row:
            val = row.get(src)
            for dst in dsts:
                if dst not in row:
                    row[dst] = val

def _ct_f1d_alias_payload(obj):
    if isinstance(obj, dict):
        _ct_f1d_alias_row(obj)

        pairs = obj.get("pairs")
        if isinstance(pairs, dict):
            for row in pairs.values():
                _ct_f1d_alias_payload(row)

        rows = obj.get("rows")
        if isinstance(rows, list):
            for row in rows:
                _ct_f1d_alias_payload(row)

        data = obj.get("data")
        if isinstance(data, list):
            for row in data:
                _ct_f1d_alias_payload(row)

        for v in list(obj.values()):
            if isinstance(v, (dict, list)):
                _ct_f1d_alias_payload(v)

    elif isinstance(obj, list):
        for row in obj:
            _ct_f1d_alias_payload(row)

    return obj

def _ct_f1d_json_dumps_with_schema_alias(obj, *args, **kwargs):
    enabled = str(_ct_f1d_os.environ.get("REVO_F1D_SCHEMA_ALIAS_COMPAT", "true")).lower() not in ("0", "false", "no", "off")
    if enabled:
        try:
            _ct_f1d_alias_payload(obj)
        except Exception:
            pass
    return _ct_f1d_original_json_dumps(obj, *args, **kwargs)

_ct_f1d_json.dumps = _ct_f1d_json_dumps_with_schema_alias
# CONTROL_TOWER_V13914F1D_PERSIST_SCHEMA_ALIAS_WRITER_END
import math
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

BYBIT_BASE_URL = "https://api.bybit.com"
# PATCHED: lower min quote volume from 4M to 1M — allow more pairs via price-change sort
DEFAULT_MIN_QUOTE_VOLUME = 1_000_000.0
DEFAULT_TOP_N = 200
DEFAULT_REFRESH_PERIOD = 300
STABLE_OR_NOISE_BASES = {"USDC", "USDE", "FDUSD", "TUSD", "USDD", "DAI", "BUSD", "EUR", "TRY"}
ACTIONABLE = {"LONG_ONLY", "SHORT_ONLY", "BOTH_ALLOWED"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "None"):
            return default
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default


def http_get_json(path: str, params: Optional[Dict[str, Any]] = None, timeout: int = 20) -> Dict[str, Any]:
    query = urllib.parse.urlencode(params or {})
    url = f"{BYBIT_BASE_URL}{path}" + (f"?{query}" if query else "")
    req = urllib.request.Request(url, headers={"User-Agent": "FusionOmega-ControlTower-v1.3.7"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - public endpoint
        payload = resp.read().decode("utf-8")
    data = json.loads(payload)
    if int(data.get("retCode", -1)) != 0:
        raise RuntimeError(f"Bybit API error retCode={data.get('retCode')} retMsg={data.get('retMsg')} path={path}")
    return data


def bybit_symbol_to_pair(symbol: str) -> Optional[str]:
    text = str(symbol or "").upper().strip()
    if not text.endswith("USDT"):
        return None
    base = text[:-4]
    if not base or base in STABLE_OR_NOISE_BASES:
        return None
    return f"{base}/USDT:USDT"


def pair_to_symbol(pair: str) -> str:
    return str(pair).split(":")[0].replace("/", "")


def normalize_change_pct(raw: Any) -> float:
    value = safe_float(raw, 0.0)
    return value * 100.0 if abs(value) <= 1.0 else value


@dataclass
class TopRow:
    symbol: str
    pair: str
    last_price: float = 0.0
    quote_volume_24h: float = 0.0
    price_change_24h_pct: float = 0.0
    abs_price_change_24h_pct: float = 0.0
    funding_rate: float = 0.0
    rank_score: float = 0.0
    scanner_mode: str = "CORE_TOP_VOLUME"
    source: str = "BYBIT_TICKER"


@dataclass
class FlowRow:
    pair: str
    symbol: str
    ts: str
    last_price: float = 0.0
    price_delta_pct_15m: float = 0.0
    price_delta_pct_1h: float = 0.0
    oi_delta_pct_15m: float = 0.0
    oi_delta_pct_1h: float = 0.0
    cvd_delta_15m: float = 0.0
    cvd_zscore_15m: float = 0.0
    cvd_source: str = "NONE"
    funding_rate: float = 0.0
    funding_zscore: float = 0.0
    volume_zscore_15m: float = 0.0
    quote_volume_24h: float = 0.0
    price_change_24h_pct: float = 0.0
    scanner_mode: str = "CORE_TOP_VOLUME"
    btc_mode: str = "BTC_UNKNOWN"
    btc_weight: float = 0.0
    btc_weight_label: str = "UNKNOWN"
    coupling_status: str = "UNKNOWN"
    btc_alignment: str = "UNKNOWN"
    flow_quadrant: str = "NO_FLOW"
    flow_direction: str = "NO_TRADE"
    flow_strength: str = "NO_FLOW"
    flow_authority: str = "NO_TRADE"
    flow_risk: str = "NORMAL"
    oi_structure: str = "UNKNOWN"
    cvd_structure: str = "UNKNOWN"
    funding_context: str = "UNKNOWN"
    flow_ready: bool = False
    data_ready: bool = False
    data_quality: str = "INIT"
    missing_fields: str = ""


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def write_csv(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fields = list(rows[0].keys()) if rows else []
    with tmp.open("w", encoding="utf-8", newline="") as f:
        if fields:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    tmp.replace(path)


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        pass
    return default


def fetch_tickers() -> List[TopRow]:
    data = http_get_json("/v5/market/tickers", params={"category": "linear"}, timeout=30)
    rows: List[TopRow] = []
    for item in (data.get("result", {}) or {}).get("list", []) or []:
        symbol = str(item.get("symbol", "")).upper().strip()
        pair = bybit_symbol_to_pair(symbol)
        if not pair:
            continue
        quote_volume = safe_float(item.get("turnover24h"), 0.0)
        change_pct = normalize_change_pct(item.get("price24hPcnt"))
        last_price = safe_float(item.get("lastPrice"), 0.0)
        funding_rate = safe_float(item.get("fundingRate"), 0.0)
        rows.append(TopRow(
            symbol=symbol,
            pair=pair,
            last_price=last_price,
            quote_volume_24h=quote_volume,
            price_change_24h_pct=change_pct,
            abs_price_change_24h_pct=abs(change_pct),
            funding_rate=funding_rate,
        ))
    return rows


def resolve_scanner_mode(runtime: Path, requested: str, btc_ctx: Dict[str, Any]) -> str:
    requested = str(requested or 'AUTO').upper()
    if requested not in {'AUTO', 'BALANCED_ROTATION'}:
        return requested
    btc_mode = str(btc_ctx.get('btc_mode', 'BTC_NEUTRAL_VWAP')).upper()
    if requested == 'AUTO':
        if 'BULLISH_BREAKOUT' in btc_mode:
            return 'TRENDING_RUNNER'
        if 'BEARISH_BREAKDOWN' in btc_mode:
            return 'LOSER_DUMPER'
        if 'CHOP' in btc_mode:
            # CONTROL_TOWER_F2B_F1I_TOP100_CHOP_AS_NEUTRAL_START
            # F1I persistence: BTC_CHOP is treated as neutral scanner context.
            # Do not route active scanner to DEFENSIVE_CHOP. Entry quality remains controlled by gate/VIP.
            return 'BALANCED_ROTATION'
            # CONTROL_TOWER_F2B_F1I_TOP100_CHOP_AS_NEUTRAL_END
    state_path = runtime / 'scanner_rotation_state.json'
    state = load_json(state_path, {'cycle': 0})
    cycle = int(safe_float(state.get('cycle'), 0)) + 1
    modes = ['CORE_TOP_VOLUME', 'TRENDING_RUNNER', 'LOSER_DUMPER', 'RANGE_EXPANSION']
    mode = modes[(cycle - 1) % len(modes)]
    state.update({'cycle': cycle, 'scanner_mode': mode, 'updated_at': utc_now_iso(), 'btc_mode': btc_mode})
    write_json(state_path, state)
    return mode


def rank_for_mode(row: TopRow, mode: str) -> float:
    vol = math.log10(max(row.quote_volume_24h, 1.0)) * 10.0
    chg = row.price_change_24h_pct
    abs_chg = abs(chg)
    if mode == 'TRENDING_RUNNER':
        # Prefer positive movers but keep high-volume fallback.
        return vol + max(chg, 0.0) * 3.0 + min(abs_chg, 30.0) * 0.5
    if mode == 'LOSER_DUMPER':
        # Prefer negative movers for bearish scan; flow still decides short authority.
        return vol + max(-chg, 0.0) * 3.0 + min(abs_chg, 30.0) * 0.5
    if mode == 'RANGE_EXPANSION':
        return vol + min(abs_chg, 40.0) * 2.5
    if mode == 'DEFENSIVE_CHOP':
        return vol + min(abs_chg, 8.0) * 0.5
    return vol + min(abs_chg, 40.0) * 1.0


def select_top_rows(runtime: Path, top_n: int, min_quote_volume: float, scanner_mode: str, btc_ctx: Dict[str, Any]) -> List[TopRow]:
    try:
        rows = fetch_tickers()
    except Exception:
        stage = runtime / "pair_universe_stage15.json"
        if not stage.exists():
            raise
        data = json.loads(stage.read_text(encoding="utf-8"))
        rows = []
        for r in data.get("rows", []) or []:
            pair = r.get("pair")
            symbol = r.get("symbol") or (pair_to_symbol(pair) if pair else "")
            if not pair or not symbol:
                continue
            rows.append(TopRow(
                symbol=symbol,
                pair=pair,
                last_price=safe_float(r.get("last_price"), 0.0),
                quote_volume_24h=safe_float(r.get("quote_volume_24h"), 0.0),
                price_change_24h_pct=safe_float(r.get("price_change_24h_pct"), 0.0),
                abs_price_change_24h_pct=safe_float(r.get("abs_price_change_24h_pct"), 0.0),
                source="PRIOR_STAGE15_FALLBACK",
            ))
    mode = resolve_scanner_mode(runtime, scanner_mode, btc_ctx)
    rows = [r for r in rows if r.quote_volume_24h >= float(min_quote_volume)]
    for r in rows:
        r.scanner_mode = mode
        r.rank_score = rank_for_mode(r, mode)
    # PATCHED: Sort by abs 24h price change (pure volatility, not volume)
    rows.sort(key=lambda r: r.abs_price_change_24h_pct, reverse=True)
    return rows[: int(top_n)]


def fetch_kline(symbol: str, interval: str, limit: int) -> List[List[Any]]:
    data = http_get_json("/v5/market/kline", params={"category": "linear", "symbol": symbol, "interval": interval, "limit": int(limit)}, timeout=20)
    rows = (data.get("result", {}) or {}).get("list", []) or []
    return sorted(rows, key=lambda row: int(row[0]) if row and str(row[0]).isdigit() else 0)


def pct_change(new: float, old: float) -> float:
    return 0.0 if old == 0 else (new - old) / abs(old) * 100.0


def price_delta_from_klines(klines: List[List[Any]]) -> Tuple[float, float, float]:
    if len(klines) < 2:
        return 0.0, 0.0, 0.0
    first_open = safe_float(klines[0][1], 0.0)
    last_close = safe_float(klines[-1][4], 0.0)
    last_volume = safe_float(klines[-1][5] if len(klines[-1]) > 5 else 0.0, 0.0)
    return (pct_change(last_close, first_open) if first_open > 0 else 0.0), last_close, last_volume


def volume_zscore_from_15m(klines: List[List[Any]]) -> float:
    vols = [safe_float(k[5] if len(k) > 5 else 0.0, 0.0) for k in klines]
    vols = [v for v in vols if v >= 0]
    if len(vols) < 8:
        return 0.0
    cur = vols[-1]
    hist = vols[:-1]
    mean = sum(hist) / len(hist)
    var = sum((x - mean) ** 2 for x in hist) / max(len(hist), 1)
    sd = math.sqrt(max(var, 1e-12))
    return (cur - mean) / sd


def fetch_open_interest_delta(symbol: str, interval_time: str, limit: int = 4) -> Tuple[Optional[float], Optional[float]]:
    data = http_get_json("/v5/market/open-interest", params={"category": "linear", "symbol": symbol, "intervalTime": interval_time, "limit": int(limit)}, timeout=20)
    rows = (data.get("result", {}) or {}).get("list", []) or []
    rows = sorted(rows, key=lambda row: int(row.get("timestamp", 0) or 0))
    values = [safe_float(r.get("openInterest"), 0.0) for r in rows if safe_float(r.get("openInterest"), 0.0) > 0]
    if len(values) < 2:
        return None, None
    return pct_change(values[-1], values[0]), values[-1]


def fetch_recent_trade_cvd(symbol: str, last_price: float) -> Tuple[float, float, str]:
    data = http_get_json("/v5/market/recent-trade", params={"category": "linear", "symbol": symbol, "limit": 1000}, timeout=20)
    rows = (data.get("result", {}) or {}).get("list", []) or []
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - 15 * 60 * 1000
    buy = sell = 0.0
    used = 0
    def add_row(r: Dict[str, Any]):
        nonlocal buy, sell, used
        size = safe_float(r.get('size'), 0.0)
        px = safe_float(r.get('price'), last_price or 1.0)
        notional = size * max(px, 1e-12)
        side = str(r.get('side', '')).upper()
        if side == 'BUY':
            buy += notional
        elif side == 'SELL':
            sell += notional
        used += 1
    for r in rows:
        ts = int(safe_float(r.get('time'), 0.0))
        if ts and ts < cutoff_ms:
            continue
        add_row(r)
    if used <= 0:
        for r in rows:
            add_row(r)
    delta = buy - sell
    denom = max(math.sqrt(max(buy + sell, 1.0)), 1.0)
    z = max(min(delta / denom, 5.0), -5.0)
    return delta, z, 'RECENT_TRADES_PUBLIC_PROXY'


def funding_zscore(funding_rate: float) -> float:
    return max(min(funding_rate / 0.0003, 5.0), -5.0)


def classify_coupling(row: FlowRow) -> None:
    mode = row.btc_mode.upper()
    if 'BULLISH' in mode:
        if row.price_delta_pct_15m > 0:
            row.coupling_status = 'COUPLED'
            row.btc_alignment = 'ALIGNED'
        elif abs(row.price_delta_pct_15m) >= 0.5 and abs(row.cvd_zscore_15m) >= 1.0:
            row.coupling_status = 'DECOUPLED_STRONG'
            row.btc_alignment = 'CONFLICT_STRONG_PAIR_FLOW'
        else:
            row.coupling_status = 'PARTIAL_DECOUPLE'
            row.btc_alignment = 'CONFLICT_WEAK'
    elif 'BEARISH' in mode:
        if row.price_delta_pct_15m < 0:
            row.coupling_status = 'COUPLED'
            row.btc_alignment = 'ALIGNED'
        elif abs(row.price_delta_pct_15m) >= 0.5 and abs(row.cvd_zscore_15m) >= 1.0:
            row.coupling_status = 'DECOUPLED_STRONG'
            row.btc_alignment = 'CONFLICT_STRONG_PAIR_FLOW'
        else:
            row.coupling_status = 'PARTIAL_DECOUPLE'
            row.btc_alignment = 'CONFLICT_WEAK'
    elif 'CHOP' in mode:
        row.coupling_status = 'PARTIAL_DECOUPLE'
        row.btc_alignment = 'BTC_CHOP_AS_NEUTRAL_F1I'
    else:
        row.coupling_status = 'PARTIAL_DECOUPLE'
        row.btc_alignment = 'BTC_NEUTRAL'


def classify_flow(row: FlowRow) -> FlowRow:
    p15, p1h, oi15, oi1h, cvd_z, fr = row.price_delta_pct_15m, row.price_delta_pct_1h, row.oi_delta_pct_15m, row.oi_delta_pct_1h, row.cvd_zscore_15m, row.funding_rate
    price_up, price_down = p15 > 0.05, p15 < -0.05
    price_1h_up_or_ok, price_1h_down_or_ok = p1h > -0.15, p1h < 0.15
    oi15_up, oi15_up_strong, oi15_down = oi15 > 0.10, oi15 > 0.25, oi15 < -0.10
    oi1h_up, oi1h_down = oi1h > 0.25, oi1h < -0.25
    cvd_up, cvd_down = cvd_z > 0.25, cvd_z < -0.25
    funding_pos_extreme, funding_neg_extreme = fr >= 0.0003, fr <= -0.0003
    row.flow_quadrant = 'NO_FLOW'
    row.flow_direction = 'NO_TRADE'
    row.flow_strength = 'NO_FLOW'
    row.flow_authority = 'NO_TRADE'
    row.flow_risk = 'NORMAL'
    row.oi_structure = 'OI_15M_UP' if oi15_up else 'OI_15M_DOWN' if oi15_down else 'OI_15M_FLAT'
    if oi1h_up:
        row.oi_structure += '_OI_1H_UP'
    elif oi1h_down:
        row.oi_structure += '_OI_1H_DOWN'
    else:
        row.oi_structure += '_OI_1H_FLAT'
    row.cvd_structure = 'AGGRESSIVE_BUY_CONFIRM' if cvd_up else 'AGGRESSIVE_SELL_CONFIRM' if cvd_down else 'CVD_FLAT'
    row.funding_context = 'CROWDED_LONG' if funding_pos_extreme else 'CROWDED_SHORT' if funding_neg_extreme else 'FUNDING_SAFE'

    # Unwind / covering are watch-only. Never continuation authority.
    if price_down and oi15_down and cvd_down:
        row.flow_quadrant = 'LONG_UNWIND_FLUSH' if abs(p15) > 0.75 or abs(oi15) > 1.0 else 'LONG_UNWIND_NORMAL'
        row.flow_strength = 'UNWIND_OR_COVER'
        row.flow_authority = 'WATCH_ONLY'
        row.flow_risk = 'NO_SHORT_CHASE'
        if funding_neg_extreme:
            row.flow_quadrant = 'BEAR_TRAP_CROWDED_FUNDING'
            row.flow_risk = 'TRAP_RISK'
        classify_coupling(row)
        return row
    if price_up and oi15_down and cvd_up:
        row.flow_quadrant = 'SHORT_COVERING_SQUEEZE' if abs(p15) > 0.75 or abs(oi15) > 1.0 else 'SHORT_COVERING_NORMAL'
        row.flow_strength = 'UNWIND_OR_COVER'
        row.flow_authority = 'WATCH_ONLY'
        row.flow_risk = 'NO_LONG_CHASE'
        if funding_pos_extreme:
            row.flow_quadrant = 'BULL_TRAP_CROWDED_FUNDING'
            row.flow_risk = 'TRAP_RISK'
        classify_coupling(row)
        return row

    # Trap / divergence risk.
    if price_up and oi15_up and not cvd_up:
        row.flow_quadrant = 'BULL_TRAP_CVD_DIVERGENCE'
        row.flow_strength = 'CROWDING_TRAP_RISK'
        row.flow_authority = 'NO_TRADE'
        row.flow_risk = 'TRAP_RISK'
        classify_coupling(row)
        return row
    if price_down and oi15_up and not cvd_down:
        row.flow_quadrant = 'BEAR_TRAP_CVD_DIVERGENCE'
        row.flow_strength = 'CROWDING_TRAP_RISK'
        row.flow_authority = 'NO_TRADE'
        row.flow_risk = 'TRAP_RISK'
        classify_coupling(row)
        return row
    if price_up and oi15_up and cvd_up and funding_pos_extreme:
        row.flow_quadrant = 'BULLISH_CONTINUATION_OVEREXTENDED'
        row.flow_strength = 'STRONG_FLOW_CROWDED_LONG'
        row.flow_authority = 'WATCH_ONLY'
        row.flow_risk = 'OVEREXTENDED_NO_LONG_CHASE'
        classify_coupling(row)
        return row
    if price_down and oi15_up and cvd_down and funding_neg_extreme:
        row.flow_quadrant = 'BEARISH_CONTINUATION_OVEREXTENDED'
        row.flow_strength = 'STRONG_FLOW_CROWDED_SHORT'
        row.flow_authority = 'WATCH_ONLY'
        row.flow_risk = 'OVEREXTENDED_NO_SHORT_CHASE'
        classify_coupling(row)
        return row

    # Continuation authority only for strong/fresh, not weak/stale.
    if price_up and price_1h_up_or_ok and oi15_up and cvd_up:
        if oi1h_up:
            row.flow_quadrant = 'BULLISH_CONTINUATION_STRONG'
            row.flow_strength = 'STRONG_FLOW'
            row.flow_direction = 'LONG_ONLY'
            row.flow_authority = 'ENTRY_ELIGIBLE'
        elif oi15_up_strong and not oi1h_down:
            row.flow_quadrant = 'BULLISH_CONTINUATION_FRESH'
            row.flow_strength = 'FRESH_FLOW'
            row.flow_direction = 'LONG_ONLY'
            row.flow_authority = 'ENTRY_ELIGIBLE'
        else:
            row.flow_quadrant = 'BULLISH_CONTINUATION_WEAK'
            row.flow_strength = 'WEAK_FLOW'
            row.flow_authority = 'WATCH_ONLY'
        classify_coupling(row)
        return row
    if price_down and price_1h_down_or_ok and oi15_up and cvd_down:
        if oi1h_up:
            row.flow_quadrant = 'BEARISH_CONTINUATION_STRONG'
            row.flow_strength = 'STRONG_FLOW'
            row.flow_direction = 'SHORT_ONLY'
            row.flow_authority = 'ENTRY_ELIGIBLE'
        elif oi15_up_strong and not oi1h_down:
            row.flow_quadrant = 'BEARISH_CONTINUATION_FRESH'
            row.flow_strength = 'FRESH_FLOW'
            row.flow_direction = 'SHORT_ONLY'
            row.flow_authority = 'ENTRY_ELIGIBLE'
        else:
            row.flow_quadrant = 'BEARISH_CONTINUATION_WEAK'
            row.flow_strength = 'WEAK_FLOW'
            row.flow_authority = 'WATCH_ONLY'
        classify_coupling(row)
        return row

    if oi1h_up and not oi15_up:
        row.flow_quadrant = 'STALE_FLOW'
        row.flow_strength = 'STALE_FLOW'
        row.flow_authority = 'WATCH_ONLY'
    classify_coupling(row)
    return row


def build_flow_for_pair(top: TopRow, btc_ctx: Dict[str, Any], sleep_sec: float = 0.02) -> FlowRow:
    row = FlowRow(
        pair=top.pair,
        symbol=top.symbol,
        ts=utc_now_iso(),
        last_price=top.last_price,
        funding_rate=top.funding_rate,
        funding_zscore=funding_zscore(top.funding_rate),
        quote_volume_24h=top.quote_volume_24h,
        price_change_24h_pct=top.price_change_24h_pct,
        scanner_mode=top.scanner_mode,
        btc_mode=str(btc_ctx.get('btc_mode', 'BTC_UNKNOWN')),
        btc_weight=safe_float(btc_ctx.get('btc_weight'), 0.0),
        btc_weight_label=str(btc_ctx.get('btc_weight_label', 'UNKNOWN')),
    )
    missing: List[str] = []
    try:
        k15 = fetch_kline(top.symbol, '15', 8)
        p15, last, _ = price_delta_from_klines(k15[-2:] if len(k15) >= 2 else k15)
        row.price_delta_pct_15m = p15
        row.last_price = last or row.last_price
        row.volume_zscore_15m = volume_zscore_from_15m(k15)
    except Exception:
        missing.append('PRICE_15M')
        k15 = []
    time.sleep(sleep_sec)
    try:
        k1h = fetch_kline(top.symbol, '60', 2)
        p1h, last, _ = price_delta_from_klines(k1h)
        row.price_delta_pct_1h = p1h
        row.last_price = last or row.last_price
    except Exception:
        missing.append('PRICE_1H')
    time.sleep(sleep_sec)
    try:
        oi15, _ = fetch_open_interest_delta(top.symbol, '15min', limit=4)
        if oi15 is None:
            missing.append('OI_15M')
        else:
            row.oi_delta_pct_15m = oi15
    except Exception:
        missing.append('OI_15M')
    time.sleep(sleep_sec)
    try:
        oi1h, _ = fetch_open_interest_delta(top.symbol, '1h', limit=4)
        if oi1h is None:
            missing.append('OI_1H')
        else:
            row.oi_delta_pct_1h = oi1h
    except Exception:
        missing.append('OI_1H')
    time.sleep(sleep_sec)
    try:
        delta, z, source = fetch_recent_trade_cvd(top.symbol, row.last_price)
        row.cvd_delta_15m = delta
        row.cvd_zscore_15m = z
        row.cvd_source = source
    except Exception:
        try:
            last_vol = safe_float(k15[-1][5] if k15 and len(k15[-1]) > 5 else 0.0, 0.0)
            row.cvd_delta_15m = (1.0 if row.price_delta_pct_15m > 0 else -1.0 if row.price_delta_pct_15m < 0 else 0.0) * last_vol * max(row.last_price, 1.0)
            row.cvd_zscore_15m = max(min(row.cvd_delta_15m / max(math.sqrt(abs(row.cvd_delta_15m)), 1.0), 5.0), -5.0)
            row.cvd_source = 'KLINE_DIRECTION_VOLUME_PROXY'
        except Exception:
            missing.append('CVD_15M')
            row.cvd_source = 'NONE'
    required_missing = [m for m in missing if m in {'PRICE_15M', 'PRICE_1H', 'OI_15M', 'OI_1H', 'CVD_15M'}]
    row.missing_fields = ','.join(missing)
    if required_missing:
        row.flow_ready = False
        row.data_ready = False
        row.data_quality = 'MISSING_' + '_'.join(required_missing)
        classify_coupling(row)
        return row
    row.flow_ready = True
    row.data_ready = True
    row.data_quality = 'OK'
    return classify_flow(row)



# CONTROL_TOWER_V13914F1_BINANCE_CANONICAL_SOURCE_ALIGNMENT_START
# Purpose:
# - Make v1.3.7 Top100 scanner canonical to Binance USD-M Futures when REVO_MARKET_SOURCE=BINANCE.
# - Preserve original Bybit functions behind REVO_MARKET_SOURCE=BYBIT for rollback.
# - Entry logic is not changed here.
import os as _ct_f1_os
import urllib.parse as _ct_f1_urlparse
import urllib.request as _ct_f1_urlrequest

_ct_f1_bybit_fetch_tickers = fetch_tickers
_ct_f1_bybit_fetch_kline = fetch_kline
_ct_f1_bybit_fetch_open_interest_delta = fetch_open_interest_delta
_ct_f1_bybit_fetch_recent_trade_cvd = fetch_recent_trade_cvd
_CT_F1_BINANCE_FAPI = "https://fapi.binance.com"
_CT_F1_BINANCE_FDATA = "https://fapi.binance.com/futures/data"

def _ct_f1_market_source() -> str:
    return str(_ct_f1_os.environ.get("REVO_MARKET_SOURCE", "BINANCE")).upper().strip()

def _ct_f1_binance_json(path: str, params: Optional[Dict[str, Any]] = None, base: str = _CT_F1_BINANCE_FAPI, timeout: int = 25) -> Any:
    query = _ct_f1_urlparse.urlencode(params or {})
    url = f"{base}{path}" + (f"?{query}" if query else "")
    req = _ct_f1_urlrequest.Request(url, headers={"User-Agent": "FusionOmega-ControlTower-v13914F1-BinanceCanonical"})
    with _ct_f1_urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

def _ct_f1_binance_symbol_to_pair(symbol: str) -> Optional[str]:
    text = str(symbol or "").upper().strip()
    if not text.endswith("USDT"):
        return None
    base = text[:-4]
    if not base or base in STABLE_OR_NOISE_BASES:
        return None
    return f"{base}/USDT:USDT"

def _ct_f1_interval(interval: str) -> str:
    text = str(interval or "").lower().strip()
    return {"15": "15m", "15m": "15m", "15min": "15m", "60": "1h", "1h": "1h", "60m": "1h", "5": "5m", "5m": "5m"}.get(text, text)

def fetch_tickers() -> List[TopRow]:
    if _ct_f1_market_source() != "BINANCE":
        return _ct_f1_bybit_fetch_tickers()
    tickers = _ct_f1_binance_json("/fapi/v1/ticker/24hr", {}, _CT_F1_BINANCE_FAPI, timeout=30)
    funding_map: Dict[str, float] = {}
    try:
        premiums = _ct_f1_binance_json("/fapi/v1/premiumIndex", {}, _CT_F1_BINANCE_FAPI, timeout=30)
        if isinstance(premiums, list):
            for item in premiums:
                funding_map[str(item.get("symbol", "")).upper()] = safe_float(item.get("lastFundingRate"), 0.0)
    except Exception:
        funding_map = {}
    rows: List[TopRow] = []
    for item in tickers if isinstance(tickers, list) else []:
        symbol = str(item.get("symbol", "")).upper().strip()
        pair = _ct_f1_binance_symbol_to_pair(symbol)
        if not pair:
            continue
        quote_volume = safe_float(item.get("quoteVolume"), 0.0)
        change_pct = safe_float(item.get("priceChangePercent"), 0.0)
        last_price = safe_float(item.get("lastPrice"), 0.0)
        rows.append(TopRow(
            symbol=symbol,
            pair=pair,
            last_price=last_price,
            quote_volume_24h=quote_volume,
            price_change_24h_pct=change_pct,
            abs_price_change_24h_pct=abs(change_pct),
            funding_rate=funding_map.get(symbol, 0.0),
            source="BINANCE_FUTURES_TICKER",
        ))
    return rows

def fetch_kline(symbol: str, interval: str, limit: int) -> List[List[Any]]:
    if _ct_f1_market_source() != "BINANCE":
        return _ct_f1_bybit_fetch_kline(symbol, interval, limit)
    data = _ct_f1_binance_json("/fapi/v1/klines", {"symbol": str(symbol).upper(), "interval": _ct_f1_interval(interval), "limit": int(limit)}, _CT_F1_BINANCE_FAPI, timeout=20)
    rows = data if isinstance(data, list) else []
    return sorted(rows, key=lambda row: int(row[0]) if row and str(row[0]).isdigit() else 0)

def fetch_open_interest_delta(symbol: str, interval_time: str, limit: int = 4) -> Tuple[Optional[float], Optional[float]]:
    if _ct_f1_market_source() != "BINANCE":
        return _ct_f1_bybit_fetch_open_interest_delta(symbol, interval_time, limit)
    period = _ct_f1_interval(interval_time)
    data = _ct_f1_binance_json("/openInterestHist", {"symbol": str(symbol).upper(), "period": period, "limit": int(limit)}, _CT_F1_BINANCE_FDATA, timeout=20)
    values = [safe_float(r.get("sumOpenInterest"), 0.0) for r in data if isinstance(r, dict) and safe_float(r.get("sumOpenInterest"), 0.0) > 0]
    if len(values) < 2:
        return None, None
    return pct_change(values[-1], values[0]), values[-1]

def fetch_recent_trade_cvd(symbol: str, last_price: float) -> Tuple[float, float, str]:
    if _ct_f1_market_source() != "BINANCE":
        return _ct_f1_bybit_fetch_recent_trade_cvd(symbol, last_price)
    data = _ct_f1_binance_json("/fapi/v1/aggTrades", {"symbol": str(symbol).upper(), "limit": 1000}, _CT_F1_BINANCE_FAPI, timeout=20)
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - 15 * 60 * 1000
    buy = sell = 0.0
    used = 0
    rows = data if isinstance(data, list) else []
    for r in rows:
        try:
            ts = int(safe_float(r.get("T"), 0.0))
            if ts and ts < cutoff_ms:
                continue
            qty = safe_float(r.get("q"), 0.0)
            px = safe_float(r.get("p"), last_price or 1.0)
            notional = qty * max(px, 1e-12)
            if bool(r.get("m")):
                sell += notional
            else:
                buy += notional
            used += 1
        except Exception:
            continue
    if used <= 0:
        for r in rows[-100:]:
            qty = safe_float(r.get("q"), 0.0)
            px = safe_float(r.get("p"), last_price or 1.0)
            notional = qty * max(px, 1e-12)
            if bool(r.get("m")):
                sell += notional
            else:
                buy += notional
            used += 1
    delta = buy - sell
    denom = max(math.sqrt(max(buy + sell, 1.0)), 1.0)
    z = max(min(delta / denom, 5.0), -5.0)
    return delta, z, "BINANCE_AGGTRADES_PUBLIC_PROXY"
# CONTROL_TOWER_V13914F1_BINANCE_CANONICAL_SOURCE_ALIGNMENT_END


def write_compact_report(runtime: Path, top_rows: List[TopRow], flow_rows: List[FlowRow], btc_ctx: Dict[str, Any], scanner_mode: str) -> None:
    from collections import Counter
    ready = [r for r in flow_rows if r.flow_ready]
    publishable = [r for r in ready if r.flow_direction in ACTIONABLE]
    quadrant = Counter(r.flow_quadrant for r in flow_rows)
    direction = Counter(r.flow_direction for r in flow_rows)
    authority = Counter(r.flow_authority for r in flow_rows)
    coupling = Counter(r.coupling_status for r in flow_rows)
    quality = Counter(r.data_quality for r in flow_rows)
    lines = [
        'CONTROL TOWER v1.3.7 - TOP100 FLOW ENGINE / ROTATING SCANNER / QUADRANT REFACTOR',
        f'generated_at={utc_now_iso()}',
        f'btc_mode={btc_ctx.get("btc_mode")}',
        f'scanner_mode={scanner_mode}',
        f'top_rows={len(top_rows)}',
        f'flow_rows={len(flow_rows)}',
        f'flow_ready_count={len(ready)}',
        f'direction_ready_count={len(publishable)}',
        '', 'quadrant_counts:',
    ]
    for k, v in quadrant.most_common(): lines.append(f'- {k}: {v}')
    lines += ['', 'direction_counts:']
    for k, v in direction.most_common(): lines.append(f'- {k}: {v}')
    lines += ['', 'authority_counts:']
    for k, v in authority.most_common(): lines.append(f'- {k}: {v}')
    lines += ['', 'coupling_counts:']
    for k, v in coupling.most_common(): lines.append(f'- {k}: {v}')
    lines += ['', 'data_quality_counts:']
    for k, v in quality.most_common(): lines.append(f'- {k}: {v}')
    lines += ['', 'top_flow_ready_pairs:']
    for r in ready[:30]:
        lines.append(f'- {r.pair} quadrant={r.flow_quadrant} direction={r.flow_direction} authority={r.flow_authority} strength={r.flow_strength} scanner={r.scanner_mode} btc={r.btc_mode} coupling={r.coupling_status} p15={r.price_delta_pct_15m:.3f}% oi15={r.oi_delta_pct_15m:.3f}% oi1h={r.oi_delta_pct_1h:.3f}% cvd_z={r.cvd_zscore_15m:.2f} funding={r.funding_rate:.6f} quality={r.data_quality}')
    (runtime / 'TOP100_FLOW_ENGINE_COMPACT.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Control Tower v1.3.7 Top100 Flow Engine')
    p.add_argument('--runtime-dir', default='user_data/revo_alpha/runtime')
    p.add_argument('--top-n', type=int, default=DEFAULT_TOP_N)
    p.add_argument('--min-quote-volume', type=float, default=DEFAULT_MIN_QUOTE_VOLUME)
    p.add_argument('--sleep-sec', type=float, default=0.02)
    p.add_argument('--refresh-period', type=int, default=DEFAULT_REFRESH_PERIOD)
    p.add_argument('--scanner-mode', default='AUTO')
    p.add_argument('--btc-context-json', default='')
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    # PATCH: Force high pair count from stage15 (min $600K vol, top 400)
    args.top_n = 400
    args.min_quote_volume = 600000
    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)
    event_cycle_id = utc_now_iso()
    btc_path = Path(args.btc_context_json) if args.btc_context_json else runtime / 'btc_context_v135.json'
    btc_ctx = load_json(btc_path, {'btc_mode': 'BTC_CONTEXT_MISSING', 'scanner_mode': 'CORE_TOP_VOLUME', 'btc_weight': 0.15, 'btc_weight_label': 'W015'})
    scanner_mode = resolve_scanner_mode(runtime, str(args.scanner_mode), btc_ctx)
    top_rows = select_top_rows(runtime, int(args.top_n), float(args.min_quote_volume), scanner_mode, btc_ctx)
    for r in top_rows:
        r.scanner_mode = scanner_mode
    write_json(runtime / 'pair_universe_top100.json', {
        'generated_at': utc_now_iso(), 'top_n': int(args.top_n), 'min_quote_volume': float(args.min_quote_volume),
        'scanner_mode': scanner_mode, 'btc_context': btc_ctx, 'pairs': [r.pair for r in top_rows], 'rows': [asdict(r) for r in top_rows],
        'source': 'CONTROL_TOWER_V137_ROTATING_SCANNER',
    })
    write_csv(runtime / 'pair_universe_top100.csv', [asdict(r) for r in top_rows])
    flows: List[FlowRow] = []
    for i, r in enumerate(top_rows, start=1):
        try:
            flow = build_flow_for_pair(r, btc_ctx, sleep_sec=float(args.sleep_sec))
        except Exception as exc:
            flow = FlowRow(pair=r.pair, symbol=r.symbol, ts=utc_now_iso(), scanner_mode=scanner_mode, btc_mode=str(btc_ctx.get('btc_mode', 'BTC_UNKNOWN')), data_quality=f'FLOW_ERROR_{type(exc).__name__}')
        flows.append(flow)
        if i < len(top_rows) and float(args.sleep_sec) > 0:
            time.sleep(float(args.sleep_sec))
    flow_payload: Dict[str, Any] = {r.pair: asdict(r) for r in flows}
    write_json(runtime / 'revo_flow_context.json', flow_payload)
    write_csv(runtime / 'revo_flow_context_top100.csv', [asdict(r) for r in flows])
    try:
        import sys as _pct_sys
        _pct_user_data = Path(__file__).resolve().parents[2]
        if str(_pct_user_data) not in _pct_sys.path:
            _pct_sys.path.insert(0, str(_pct_user_data))
        from revo_alpha.pair_context.scanner_bridge import emit_top100_flow_scan

        emit_top100_flow_scan(
            runtime,
            top_rows=top_rows,
            flow_rows=flows,
            cycle_id=event_cycle_id,
        )
    except Exception as exc:
        try:
            (runtime / 'PAIR_CONTEXT_SCANNER_EVENT_TELEMETRY_COMPACT.txt').write_text(
                'PAIR_CONTEXT_SCANNER_EVENT_TELEMETRY\n'
                'producer=revo_top100_flow_engine_v132\n'
                'enabled=ERROR\n'
                f'cycle_id={event_cycle_id}\n'
                f'error={type(exc).__name__}:{exc}\n',
                encoding='utf-8',
            )
        except Exception:
            pass
    write_compact_report(runtime, top_rows, flows, btc_ctx, scanner_mode)
    print(f'TOP100_FLOW_ENGINE_V137_PASS scanner={scanner_mode} top={len(top_rows)} flow_ready={sum(1 for r in flows if r.flow_ready)} runtime={runtime}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
