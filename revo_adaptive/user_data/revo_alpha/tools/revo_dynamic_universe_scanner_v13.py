#!/usr/bin/env python3
"""
Control Tower v1.3 - Multi-Symbol Dynamic Universe Scanner

Purpose:
- Rebuild the old Fusion/Revo market-discovery feel without making scoring the final entry authority.
- Select active Bybit USDT linear futures symbols by cheap ticker data first.
- Optionally run a 15m clean stage before publishing the Freqtrade pairlist.
- Write a RemotePairList-compatible JSON file for Freqtrade.

No private API keys are required. This script uses Bybit public endpoints.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

BYBIT_BASE_URL = "https://api.bybit.com"
# PATCHED: Lower volume filter from $4M to $600K to feed flow engine 200+ pairs
DEFAULT_MIN_QUOTE_VOLUME = 600_000.0
DEFAULT_REFRESH_PERIOD = 300
DEFAULT_KLINE_LIMIT_15M = 32


STABLE_OR_NOISE_BASES = {
    # Keep this conservative. Do not block ordinary volatile coins.
    "USDC", "USDE", "FDUSD", "TUSD", "USDD", "DAI", "BUSD", "EUR", "TRY",
}


@dataclass
class UniverseRow:
    symbol: str
    pair: str
    last_price: float
    quote_volume_24h: float
    price_change_24h_pct: float
    abs_price_change_24h_pct: float
    turnover_rank_score: float
    source: str = "BYBIT_TICKER"
    clean15_ok: bool = False
    clean15_reason: str = "NOT_RUN"
    clean15_change_pct: float = 0.0
    clean15_range_pct: float = 0.0
    clean15_turnover: float = 0.0
    clean15_body_to_range: float = 0.0
    stage: str = "TICKER"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "None"):
            return default
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default


def _http_get_json(path: str, params: Optional[Dict[str, Any]] = None, timeout: int = 20) -> Dict[str, Any]:
    query = urllib.parse.urlencode(params or {})
    url = f"{BYBIT_BASE_URL}{path}"
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "FusionOmega-ControlTower-v1.3"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - public market endpoint
        payload = resp.read().decode("utf-8")
    data = json.loads(payload)
    if int(data.get("retCode", -1)) != 0:
        raise RuntimeError(f"Bybit API error retCode={data.get('retCode')} retMsg={data.get('retMsg')}")
    return data


def _bybit_symbol_to_pair(symbol: str) -> Optional[str]:
    text = str(symbol or "").upper().strip()
    if not text.endswith("USDT"):
        return None
    base = text[:-4]
    if not base or base in STABLE_OR_NOISE_BASES:
        return None
    return f"{base}/USDT:USDT"


def _fetch_trading_linear_usdt_symbols() -> set[str]:
    symbols: set[str] = set()
    cursor = ""
    while True:
        params = {"category": "linear", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        data = _http_get_json("/v5/market/instruments-info", params=params)
        result = data.get("result", {}) or {}
        for item in result.get("list", []) or []:
            symbol = str(item.get("symbol", "")).upper()
            if not symbol.endswith("USDT"):
                continue
            if str(item.get("status", "")).upper() != "TRADING":
                continue
            if str(item.get("quoteCoin", "")).upper() != "USDT":
                continue
            if str(item.get("settleCoin", "")).upper() != "USDT":
                continue
            if _bybit_symbol_to_pair(symbol):
                symbols.add(symbol)
        cursor = str(result.get("nextPageCursor") or "")
        if not cursor:
            break
    return symbols


def _normalize_change_pct(raw: Any) -> float:
    value = _safe_float(raw, 0.0)
    # Bybit price24hPcnt is commonly a ratio (0.01 = 1%). Tolerate either representation.
    if abs(value) <= 1.0:
        return value * 100.0
    return value


def fetch_ticker_universe(
    *,
    min_quote_volume: float,
    min_abs_change_pct: float,
    require_instruments_info: bool,
) -> List[UniverseRow]:
    active_symbols = _fetch_trading_linear_usdt_symbols() if require_instruments_info else set()
    data = _http_get_json("/v5/market/tickers", params={"category": "linear"})
    rows: List[UniverseRow] = []
    for item in (data.get("result", {}) or {}).get("list", []) or []:
        symbol = str(item.get("symbol", "")).upper().strip()
        if require_instruments_info and symbol not in active_symbols:
            continue
        pair = _bybit_symbol_to_pair(symbol)
        if not pair:
            continue
        quote_volume = _safe_float(item.get("turnover24h"), 0.0)
        if quote_volume < float(min_quote_volume):
            continue
        change_pct = _normalize_change_pct(item.get("price24hPcnt"))
        if abs(change_pct) < float(min_abs_change_pct):
            continue
        last_price = _safe_float(item.get("lastPrice"), 0.0)
        # Ranking only. This is not entry permission.
        turnover_component = math.log10(max(quote_volume, 1.0)) * 10.0
        momentum_component = min(abs(change_pct), 30.0) * 2.0
        rows.append(
            UniverseRow(
                symbol=symbol,
                pair=pair,
                last_price=last_price,
                quote_volume_24h=quote_volume,
                price_change_24h_pct=change_pct,
                abs_price_change_24h_pct=abs(change_pct),
                turnover_rank_score=turnover_component + momentum_component,
            )
        )
    # PATCHED: Sort by abs 24h price change, not volume
    rows.sort(key=lambda r: r.abs_price_change_24h_pct, reverse=True)

    return rows


def _fetch_kline(symbol: str, interval: str = "15", limit: int = DEFAULT_KLINE_LIMIT_15M) -> List[List[Any]]:
    data = _http_get_json(
        "/v5/market/kline",
        params={"category": "linear", "symbol": symbol, "interval": interval, "limit": int(limit)},
        timeout=20,
    )
    rows = (data.get("result", {}) or {}).get("list", []) or []
    # Bybit returns newest first. Sort oldest first.
    return sorted(rows, key=lambda row: int(row[0]) if row and str(row[0]).isdigit() else 0)


def apply_stage15_clean(
    rows: List[UniverseRow],
    *,
    kline_limit: int,
    min_range_pct: float,
    min_turnover_15m: float,
    sleep_sec: float,
) -> List[UniverseRow]:
    out: List[UniverseRow] = []
    for i, row in enumerate(rows, start=1):
        updated = UniverseRow(**asdict(row))
        try:
            klines = _fetch_kline(row.symbol, interval="15", limit=kline_limit)
            if len(klines) < max(8, min(kline_limit, 12)):
                updated.clean15_ok = False
                updated.clean15_reason = "INSUFFICIENT_15M_ROWS"
            else:
                first_open = _safe_float(klines[0][1], 0.0)
                last_close = _safe_float(klines[-1][4], 0.0)
                highs = [_safe_float(k[2], 0.0) for k in klines]
                lows = [_safe_float(k[3], 0.0) for k in klines]
                opens = [_safe_float(k[1], 0.0) for k in klines]
                closes = [_safe_float(k[4], 0.0) for k in klines]
                turnovers = [_safe_float(k[6], 0.0) if len(k) > 6 else 0.0 for k in klines]
                high = max(highs) if highs else 0.0
                low = min([x for x in lows if x > 0.0], default=0.0)
                turnover_15m = turnovers[-1] if turnovers else 0.0
                range_pct = ((high - low) / max(last_close, 1e-9) * 100.0) if high > 0 and low > 0 else 0.0
                change_pct = ((last_close - first_open) / max(first_open, 1e-9) * 100.0) if first_open > 0 else 0.0
                body = abs(closes[-1] - opens[-1]) if closes and opens else 0.0
                candle_range = max(highs[-1] - lows[-1], 1e-9) if highs and lows else 1e-9
                body_to_range = body / candle_range
                updated.clean15_change_pct = change_pct
                updated.clean15_range_pct = range_pct
                updated.clean15_turnover = turnover_15m
                updated.clean15_body_to_range = body_to_range
                if range_pct < float(min_range_pct):
                    updated.clean15_ok = False
                    updated.clean15_reason = "LOW_15M_RANGE"
                elif turnover_15m < float(min_turnover_15m):
                    updated.clean15_ok = False
                    updated.clean15_reason = "LOW_15M_TURNOVER"
                else:
                    updated.clean15_ok = True
                    updated.clean15_reason = "PASS_15M_CLEAN"
                    updated.stage = "STAGE15_CLEAN"
        except Exception as exc:
            updated.clean15_ok = False
            updated.clean15_reason = f"KLINE_ERROR:{type(exc).__name__}"
        out.append(updated)
        if sleep_sec > 0 and i < len(rows):
            time.sleep(float(sleep_sec))
    # PATCHED: Sort by abs 24h price change, not volume
    rows.sort(key=lambda r: abs(r.price_change_24h), reverse=True)

    return out


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


def _write_csv(path: Path, rows: Iterable[UniverseRow]) -> None:
    rows_list = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fields = list(asdict(rows_list[0]).keys()) if rows_list else list(UniverseRow("", "", 0, 0, 0, 0, 0).__dict__.keys())
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows_list:
            writer.writerow(asdict(row))
    tmp.replace(path)


def _remote_pairlist_payload(pairs: List[str], refresh_period: int) -> Dict[str, Any]:
    return {"pairs": pairs, "refresh_period": int(refresh_period), "generated_at": _utc_now_iso()}


def _write_compact_report(
    path: Path,
    *,
    all_rows: List[UniverseRow],
    published_rows: List[UniverseRow],
    args: argparse.Namespace,
) -> None:
    reason_counts: Dict[str, int] = {}
    for row in all_rows:
        reason_counts[row.clean15_reason] = reason_counts.get(row.clean15_reason, 0) + 1
    top_rows = published_rows[:20]
    lines = []
    lines.append("CONTROL TOWER v1.3 - MULTI SYMBOL SCANNER REPORT")
    lines.append(f"generated_at={_utc_now_iso()}")
    lines.append(f"min_quote_volume={float(args.min_quote_volume):.2f}")
    lines.append(f"min_abs_change_pct={float(args.min_abs_change_pct):.4f}")
    lines.append(f"stage15_clean={bool(args.stage15_clean)}")
    lines.append(f"ticker_qualified_count={len(all_rows)}")
    lines.append(f"published_pair_count={len(published_rows)}")
    lines.append(f"remote_pairlist={path.parent / 'pair_universe_remote.json'}")
    lines.append("")
    lines.append("reason_counts:")
    for key, value in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("top_published_pairs:")
    for row in top_rows:
        lines.append(
            f"- {row.pair} vol24h={row.quote_volume_24h:.0f} change24h={row.price_change_24h_pct:.2f}% "
            f"range15={row.clean15_range_pct:.2f}% reason={row.clean15_reason}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Revo/Fusion Control Tower v1.3 dynamic universe scanner")
    parser.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime", help="Freqtrade user_data runtime directory")
    parser.add_argument("--min-quote-volume", type=float, default=DEFAULT_MIN_QUOTE_VOLUME, help="Minimum 24h USDT turnover")
    parser.add_argument("--min-abs-change-pct", type=float, default=0.0, help="Optional minimum absolute 24h price change percent")
    parser.add_argument("--refresh-period", type=int, default=DEFAULT_REFRESH_PERIOD, help="RemotePairList refresh_period")
    parser.add_argument("--stage15-clean", action="store_true", help="Run 15m clean stage before publishing pairlist")
    parser.add_argument("--kline-limit-15m", type=int, default=DEFAULT_KLINE_LIMIT_15M)
    parser.add_argument("--min-range-15m-pct", type=float, default=0.08)
    parser.add_argument("--min-turnover-15m", type=float, default=0.0)
    parser.add_argument("--sleep-between-kline", type=float, default=0.03)
    parser.add_argument("--no-instruments-info", action="store_true", help="Skip instruments-info active-market filter")
    parser.add_argument("--max-pairs", type=int, default=0, help="0 means unlimited. Use only for emergency throttling.")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)
    cycle_id = _utc_now_iso()

    rows = fetch_ticker_universe(
        min_quote_volume=float(args.min_quote_volume),
        min_abs_change_pct=float(args.min_abs_change_pct),
        require_instruments_info=not bool(args.no_instruments_info),
    )
    if int(args.max_pairs or 0) > 0:
        rows = rows[: int(args.max_pairs)]

    _write_csv(runtime / "pair_universe_all.csv", rows)
    _write_json(runtime / "pair_universe_all.json", {"generated_at": _utc_now_iso(), "pairs": [r.pair for r in rows], "rows": [asdict(r) for r in rows]})

    published_rows = rows
    if args.stage15_clean:
        stage15_rows = apply_stage15_clean(
            rows,
            kline_limit=int(args.kline_limit_15m),
            min_range_pct=float(args.min_range_15m_pct),
            min_turnover_15m=float(args.min_turnover_15m),
            sleep_sec=float(args.sleep_between_kline),
        )
        _write_csv(runtime / "pair_universe_stage15.csv", stage15_rows)
        _write_json(runtime / "pair_universe_stage15.json", {"generated_at": _utc_now_iso(), "pairs": [r.pair for r in stage15_rows if r.clean15_ok], "rows": [asdict(r) for r in stage15_rows]})
        clean_rows = [r for r in stage15_rows if r.clean15_ok]
        # Fail-open to ticker list if the clean stage failed completely. This prevents empty pairlist startup loops.
        published_rows = clean_rows if clean_rows else rows
    else:
        # Keep a stage15 file with NOT_RUN status so audit scripts have a stable file to read.
        _write_csv(runtime / "pair_universe_stage15.csv", rows)
        _write_json(runtime / "pair_universe_stage15.json", {"generated_at": _utc_now_iso(), "pairs": [r.pair for r in rows], "rows": [asdict(r) for r in rows]})

    published_pairs = [r.pair for r in published_rows]
    if not published_pairs:
        # Hard fallback to BTC to avoid invalid RemotePairList file.
        published_pairs = ["BTC/USDT:USDT"]
    _write_json(runtime / "pair_universe_remote.json", _remote_pairlist_payload(published_pairs, int(args.refresh_period)))

    # Future hook. This is intentionally not consumed by Freqtrade v1.3 yet.
    probe_candidates = [asdict(r) for r in published_rows[: min(50, len(published_rows))]]
    _write_json(runtime / "pair_universe_1m_probe_candidates.json", {"generated_at": _utc_now_iso(), "rows": probe_candidates})

    try:
        import sys as _pct_sys
        _pct_user_data = Path(__file__).resolve().parents[2]
        if str(_pct_user_data) not in _pct_sys.path:
            _pct_sys.path.insert(0, str(_pct_user_data))
        from revo_alpha.pair_context.scanner_bridge import emit_dynamic_universe_scan

        emit_dynamic_universe_scan(
            runtime,
            rows=rows,
            published_pairs=published_pairs,
            cycle_id=cycle_id,
        )
    except Exception as exc:
        try:
            (runtime / "PAIR_CONTEXT_SCANNER_EVENT_TELEMETRY_COMPACT.txt").write_text(
                "PAIR_CONTEXT_SCANNER_EVENT_TELEMETRY\n"
                "producer=revo_dynamic_universe_scanner_v13\n"
                "enabled=ERROR\n"
                f"cycle_id={cycle_id}\n"
                f"error={type(exc).__name__}:{exc}\n",
                encoding="utf-8",
            )
        except Exception:
            pass

    _write_compact_report(runtime / "UNIVERSE_SCANNER_COMPACT.txt", all_rows=rows, published_rows=published_rows, args=args)
    print(f"UNIVERSE_SCAN_PASS ticker={len(rows)} published={len(published_pairs)} runtime={runtime}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
