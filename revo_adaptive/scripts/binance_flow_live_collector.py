# CONTROL_TOWER_COLLECTOR_ISOLATION_PRESERVE_VIP
# generated_at=2026-05-06T17:56:59.117514+00:00
# purpose=collector writes supplemental files only; scanner owns revo_flow_context_collector.json
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
REVO Binance Flow Live Collector v1

Purpose:
- Collect Binance USD-M Futures public flow data for paper/live observation.
- Write latest JSON consumed by user_data/revo_alpha/flow_context.py:
    user_data/revo_alpha/runtime/revo_flow_context_collector.json
- Append historical CSV:
    user_data/revo_alpha/runtime/revo_flow_context_collector.csv

Collected per pair:
- price_delta_pct from Binance 5m futures klines
- oi_delta_pct from Binance openInterestHist
- taker buy/sell volume proxy:
    cvd_delta = buyVol - sellVol
    cvd_zscore = rolling z-score of cvd_delta
- funding_rate from Binance premiumIndex lastFundingRate
- funding_zscore = rolling z-score of latest funding history when available
- volume_zscore from 5m kline quote volume

Notes:
- CVD here is taker buy/sell volume proxy, not tick-by-tick orderflow CVD.
- This is audit/context only.
- It does not place orders.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


BASE_URL_FAPI = "https://fapi.binance.com"
BASE_URL_FDATA = "https://fapi.binance.com/futures/data"

DEFAULT_PAIRS = [
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
    "XRP/USDT:USDT",
    "DOGE/USDT:USDT",
    "ADA/USDT:USDT",
    "AVAX/USDT:USDT",
    "LINK/USDT:USDT",
    "LTC/USDT:USDT",
]

CSV_FIELDS = [
    "date",
    "pair",
    "symbol",
    "price_delta_pct",
    "oi_delta_pct",
    "cvd_delta",
    "cvd_zscore",
    "funding_rate",
    "funding_zscore",
    "volume_zscore",
    "flow_quadrant",
    "data_ready",
    "collector_version",
]


def utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def pair_to_symbol(pair: str) -> str:
    # BTC/USDT:USDT -> BTCUSDT
    return pair.split(":")[0].replace("/", "")


def symbol_to_pair(symbol: str) -> str:
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}/USDT:USDT"
    return symbol


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default


def zscore_latest(values: List[float]) -> float:
    vals = [safe_float(v) for v in values if v is not None]
    if len(vals) < 3:
        return 0.0
    mean = statistics.fmean(vals)
    stdev = statistics.pstdev(vals)
    if stdev <= 0:
        return 0.0
    return (vals[-1] - mean) / stdev


def http_get_json(path: str, params: Dict[str, Any], base: str = BASE_URL_FAPI, timeout: int = 12) -> Any:
    query = urllib.parse.urlencode(params)
    url = f"{base}{path}?{query}" if query else f"{base}{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "revo-flow-live-collector-v1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_klines(symbol: str, interval: str = "5m", limit: int = 60) -> List[List[Any]]:
    return http_get_json("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit}, BASE_URL_FAPI)


def get_open_interest_hist(symbol: str, period: str = "5m", limit: int = 60) -> List[Dict[str, Any]]:
    return http_get_json("/openInterestHist", {"symbol": symbol, "period": period, "limit": limit}, BASE_URL_FDATA)


def get_taker_buy_sell(symbol: str, period: str = "5m", limit: int = 60) -> List[Dict[str, Any]]:
    return http_get_json("/takerlongshortRatio", {"symbol": symbol, "period": period, "limit": limit}, BASE_URL_FDATA)


def get_premium_index(symbol: str) -> Dict[str, Any]:
    return http_get_json("/fapi/v1/premiumIndex", {"symbol": symbol}, BASE_URL_FAPI)


def get_funding_history(symbol: str, limit: int = 60) -> List[Dict[str, Any]]:
    return http_get_json("/fapi/v1/fundingRate", {"symbol": symbol, "limit": limit}, BASE_URL_FAPI)


def derive_quadrant(price_delta: float, oi_delta: float, cvd_z: float, funding_z: float) -> str:
    price_up = price_delta > 0
    price_down = price_delta < 0
    oi_up = oi_delta > 0
    cvd_up = cvd_z > 0.25
    cvd_down = cvd_z < -0.25
    funding_neg = funding_z < -0.25
    funding_pos = funding_z > 0.25

    if price_up and oi_up and cvd_up and funding_neg:
        return "BULL_CONTINUATION_SHORTS_TRAPPED"
    if price_up and oi_up and cvd_up:
        return "BULL_CONTINUATION"
    if price_down and oi_up and cvd_down and funding_pos:
        return "BEAR_CONTINUATION_LONGS_TRAPPED"
    if price_down and oi_up and cvd_down:
        return "BEAR_CONTINUATION"
    if price_up and cvd_down:
        return "BULL_TRAP_RISK"
    if price_down and cvd_up:
        return "BEAR_TRAP_RISK"
    if price_up and (not oi_up) and cvd_up:
        return "SHORT_COVERING_RISK"
    if price_down and (not oi_up) and cvd_down:
        return "LONG_UNWIND_RISK"
    return "NEUTRAL"


def collect_symbol(pair: str) -> Dict[str, Any]:
    symbol = pair_to_symbol(pair)

    klines = get_klines(symbol, "5m", 60)
    if len(klines) < 2:
        raise RuntimeError(f"not enough klines for {symbol}")

    close_prev = safe_float(klines[-2][4])
    close_now = safe_float(klines[-1][4])
    price_delta_pct = ((close_now / close_prev) - 1.0) * 100.0 if close_prev else 0.0

    quote_volumes = [safe_float(k[7]) for k in klines]
    volume_zscore = zscore_latest(quote_volumes)

    oi_hist = get_open_interest_hist(symbol, "5m", 60)
    oi_delta_pct = 0.0
    if len(oi_hist) >= 2:
        oi_prev = safe_float(oi_hist[-2].get("sumOpenInterest"))
        oi_now = safe_float(oi_hist[-1].get("sumOpenInterest"))
        if oi_prev:
            oi_delta_pct = ((oi_now / oi_prev) - 1.0) * 100.0

    taker = get_taker_buy_sell(symbol, "5m", 60)
    cvd_values = []
    for row in taker:
        buy = safe_float(row.get("buyVol"))
        sell = safe_float(row.get("sellVol"))
        cvd_values.append(buy - sell)
    cvd_delta = cvd_values[-1] if cvd_values else 0.0
    cvd_zscore = zscore_latest(cvd_values)

    premium = get_premium_index(symbol)
    funding_rate = safe_float(premium.get("lastFundingRate"))

    funding_hist = get_funding_history(symbol, 60)
    funding_values = [safe_float(x.get("fundingRate")) for x in funding_hist]
    funding_zscore = zscore_latest(funding_values)
    if len(funding_values) < 3:
        funding_zscore = 0.0

    flow_quadrant = derive_quadrant(price_delta_pct, oi_delta_pct, cvd_zscore, funding_zscore)

    return {
        "date": utc_now_iso(),
        "pair": pair,
        "symbol": symbol,
        "price_delta_pct": round(price_delta_pct, 6),
        "oi_delta_pct": round(oi_delta_pct, 6),
        "cvd_delta": round(cvd_delta, 6),
        "cvd_zscore": round(cvd_zscore, 6),
        "funding_rate": round(funding_rate, 10),
        "funding_zscore": round(funding_zscore, 6),
        "volume_zscore": round(volume_zscore, 6),
        "flow_quadrant": flow_quadrant,
        "data_ready": True,
        "collector_version": "binance_flow_live_collector_v1",
    }


def write_latest_json(runtime_dir: Path, rows: List[Dict[str, Any]]) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    out = {row["pair"]: {k: v for k, v in row.items() if k not in {"pair", "symbol"}} for row in rows}
    path = runtime_dir / "revo_flow_context_collector.json"
    tmp = runtime_dir / "revo_flow_context_collector.json.tmp"
    tmp.write_text(json.dumps(out, indent=2), encoding="utf-8")
    tmp.replace(path)


def append_csv(runtime_dir: Path, rows: List[Dict[str, Any]]) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = runtime_dir / "revo_flow_context_collector.csv"
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})


def run_once(runtime_dir: Path, pairs: List[str]) -> int:
    rows = []
    errors = []
    for pair in pairs:
        try:
            row = collect_symbol(pair)
            rows.append(row)
            print(
                f"[OK] {pair} {row['flow_quadrant']} "
                f"price={row['price_delta_pct']} oi={row['oi_delta_pct']} "
                f"cvd_z={row['cvd_zscore']} funding={row['funding_rate']}",
                flush=True,
            )
        except Exception as e:
            errors.append((pair, str(e)))
            print(f"[WARN] {pair} collect failed: {e}", flush=True)

    if rows:
        write_latest_json(runtime_dir, rows)
        append_csv(runtime_dir, rows)
        print(f"[OK] wrote {runtime_dir / 'revo_flow_context_collector.json'}", flush=True)
        print(f"[OK] appended {runtime_dir / 'revo_flow_context_collector.csv'}", flush=True)

    if errors and not rows:
        print("[ERROR] no rows collected", flush=True)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime")
    parser.add_argument("--pairs", default=",".join(DEFAULT_PAIRS))
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()

    runtime_dir = Path(args.runtime_dir)
    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]

    if not args.loop:
        return run_once(runtime_dir, pairs)

    while True:
        code = run_once(runtime_dir, pairs)
        time.sleep(max(30, args.interval_seconds))
        if code != 0:
            # Keep looping for intermittent endpoint errors.
            continue


if __name__ == "__main__":
    raise SystemExit(main())
