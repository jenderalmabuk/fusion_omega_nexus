#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple, Optional


BASE_URL = "https://api.bybit.com"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def norm(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def as_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def bybit_pair(symbol: str) -> str:
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}/USDT:USDT"
    return symbol


def bybit_symbol(pair: str) -> str:
    if "/USDT" in pair:
        return pair.split("/")[0] + "USDT"
    return pair.replace(":USDT", "").replace("/", "")


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def bybit_get(path: str, params: Dict[str, Any], timeout: int = 20, retries: int = 2) -> Dict[str, Any]:
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = BASE_URL + path + "?" + qs
    last_err = None

    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "F3A-market-wide-flow-cache-audit"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data
        except Exception as e:
            last_err = e
            time.sleep(0.4 + attempt * 0.6)

    raise RuntimeError(f"BYBIT_GET_FAIL path={path} params={params} err={last_err!r}")


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()

    cur.execute("""
    create table if not exists meta (
      key text primary key,
      value text,
      updated_at text
    )
    """)

    cur.execute("""
    create table if not exists eligible_pairs (
      cycle_id text,
      ts text,
      symbol text,
      pair text,
      base text,
      quote text,
      last_price real,
      turnover24h real,
      volume24h real,
      price24h_pct real,
      funding_rate real,
      next_funding_time text,
      selected_rank integer,
      fast_pool integer,
      source text,
      primary key (cycle_id, symbol)
    )
    """)

    cur.execute("""
    create table if not exists flow_snapshots (
      cycle_id text,
      ts text,
      symbol text,
      pair text,
      interval_name text,
      oi_now real,
      oi_prev real,
      oi_delta_abs real,
      oi_delta_pct real,
      kline_close_now real,
      kline_close_prev real,
      price_delta_pct real,
      volume_sum real,
      turnover_sum real,
      funding_rate real,
      status text,
      error text,
      source text,
      primary key (cycle_id, symbol, interval_name)
    )
    """)

    cur.execute("""
    create table if not exists latest_flow (
      symbol text primary key,
      pair text,
      cycle_id text,
      ts text,
      turnover24h real,
      volume24h real,
      last_price real,
      funding_rate real,

      oi_1h_now real,
      oi_1h_delta_pct real,
      price_1h_delta_pct real,
      volume_1h_sum real,

      oi_15m_now real,
      oi_15m_delta_pct real,
      price_15m_delta_pct real,
      volume_15m_sum real,

      oi_5m_now real,
      oi_5m_delta_pct real,
      price_5m_delta_pct real,
      volume_5m_sum real,

      oi_1m_now real,
      oi_1m_delta_pct real,
      price_1m_delta_pct real,
      volume_1m_sum real,

      data_status text,
      missing_intervals text,
      fast_pool integer,
      source text
    )
    """)

    cur.execute("create index if not exists idx_flow_snapshots_pair on flow_snapshots(pair)")
    cur.execute("create index if not exists idx_flow_snapshots_ts on flow_snapshots(ts)")
    cur.execute("create index if not exists idx_latest_flow_pair on latest_flow(pair)")

    con.commit()
    return con


def fetch_tickers() -> List[Dict[str, Any]]:
    data = bybit_get("/v5/market/tickers", {"category": "linear"})
    if str(data.get("retCode")) != "0":
        raise RuntimeError(f"TICKERS_RET_FAIL retCode={data.get('retCode')} retMsg={data.get('retMsg')}")
    return data.get("result", {}).get("list", []) or []


def select_eligible_pairs(
    tickers: List[Dict[str, Any]],
    min_turnover24h: float,
    max_pairs: int,
) -> List[Dict[str, Any]]:
    rows = []

    for t in tickers:
        symbol = norm(t.get("symbol"))
        if not symbol.endswith("USDT"):
            continue

        turnover = as_float(t.get("turnover24h"))
        if turnover < min_turnover24h:
            continue

        base = symbol[:-4]
        rows.append({
            "symbol": symbol,
            "pair": bybit_pair(symbol),
            "base": base,
            "quote": "USDT",
            "last_price": as_float(t.get("lastPrice")),
            "turnover24h": turnover,
            "volume24h": as_float(t.get("volume24h")),
            "price24h_pct": as_float(t.get("price24hPcnt")) * 100.0,
            "funding_rate": as_float(t.get("fundingRate")),
            "next_funding_time": norm(t.get("nextFundingTime")),
        })

    rows.sort(key=lambda x: x["turnover24h"], reverse=True)
    return rows[:max_pairs]


def load_fast_symbols(runtime: Path, eligible: List[Dict[str, Any]], fast_max_pairs: int) -> set[str]:
    fast = set()

    pairlist_path = runtime / "pair_universe_remote.json"
    pairlist = load_json(pairlist_path, {})
    for p in pairlist.get("pairs", []) if isinstance(pairlist, dict) else []:
        fast.add(bybit_symbol(str(p)))

    # Add top turnover pairs to fast pool as fallback.
    for row in eligible[:fast_max_pairs]:
        fast.add(row["symbol"])

    return set(list(fast)[: max(fast_max_pairs, len(fast))])


def fetch_open_interest(symbol: str, interval_name: str, limit: int = 3) -> Tuple[Optional[float], Optional[float], Optional[float], str, str]:
    interval_map = {
        "1h": "1h",
        "15m": "15min",
        "5m": "5min",
        "1m": "1min",
    }

    interval_time = interval_map[interval_name]

    try:
        data = bybit_get("/v5/market/open-interest", {
            "category": "linear",
            "symbol": symbol,
            "intervalTime": interval_time,
            "limit": limit,
        })
        if str(data.get("retCode")) != "0":
            return None, None, None, "MISSING", f"retCode={data.get('retCode')} retMsg={data.get('retMsg')}"

        rows = data.get("result", {}).get("list", []) or []
        if len(rows) < 1:
            return None, None, None, "MISSING", "NO_OI_ROWS"

        rows = sorted(rows, key=lambda r: int(r.get("timestamp", 0)))
        now = as_float(rows[-1].get("openInterest"))
        prev = as_float(rows[-2].get("openInterest")) if len(rows) >= 2 else now
        delta_abs = now - prev
        delta_pct = (delta_abs / prev * 100.0) if prev else 0.0
        return now, prev, delta_pct, "OK", ""
    except Exception as e:
        return None, None, None, "MISSING", repr(e)


def fetch_kline(symbol: str, interval_name: str, limit: int = 3) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float], str, str]:
    interval_map = {
        "1h": "60",
        "15m": "15",
        "5m": "5",
        "1m": "1",
    }

    interval = interval_map[interval_name]

    try:
        data = bybit_get("/v5/market/kline", {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        })
        if str(data.get("retCode")) != "0":
            return None, None, None, None, None, "MISSING", f"retCode={data.get('retCode')} retMsg={data.get('retMsg')}"

        rows = data.get("result", {}).get("list", []) or []
        if len(rows) < 1:
            return None, None, None, None, None, "MISSING", "NO_KLINE_ROWS"

        # Bybit returns newest first. Sort ascending by startTime.
        rows = sorted(rows, key=lambda r: int(r[0]))

        now_close = as_float(rows[-1][4])
        prev_close = as_float(rows[-2][4]) if len(rows) >= 2 else now_close
        price_delta_pct = ((now_close - prev_close) / prev_close * 100.0) if prev_close else 0.0

        volume_sum = sum(as_float(r[5]) for r in rows[-limit:])
        turnover_sum = sum(as_float(r[6]) for r in rows[-limit:])

        return now_close, prev_close, price_delta_pct, volume_sum, turnover_sum, "OK", ""
    except Exception as e:
        return None, None, None, None, None, "MISSING", repr(e)


def upsert_eligible(con: sqlite3.Connection, cycle_id: str, ts: str, rows: List[Dict[str, Any]], fast_symbols: set[str]) -> None:
    cur = con.cursor()
    for idx, r in enumerate(rows, start=1):
        cur.execute("""
        insert or replace into eligible_pairs (
          cycle_id, ts, symbol, pair, base, quote, last_price,
          turnover24h, volume24h, price24h_pct, funding_rate,
          next_funding_time, selected_rank, fast_pool, source
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cycle_id, ts, r["symbol"], r["pair"], r["base"], r["quote"],
            r["last_price"], r["turnover24h"], r["volume24h"],
            r["price24h_pct"], r["funding_rate"], r["next_funding_time"],
            idx, 1 if r["symbol"] in fast_symbols else 0, "BYBIT_TICKERS"
        ))
    con.commit()


def upsert_snapshot(
    con: sqlite3.Connection,
    cycle_id: str,
    ts: str,
    row: Dict[str, Any],
    interval_name: str,
    oi_now: Optional[float],
    oi_prev: Optional[float],
    oi_delta_pct: Optional[float],
    close_now: Optional[float],
    close_prev: Optional[float],
    price_delta_pct: Optional[float],
    volume_sum: Optional[float],
    turnover_sum: Optional[float],
    status: str,
    error: str,
) -> None:
    oi_delta_abs = None
    if oi_now is not None and oi_prev is not None:
        oi_delta_abs = oi_now - oi_prev

    cur = con.cursor()
    cur.execute("""
    insert or replace into flow_snapshots (
      cycle_id, ts, symbol, pair, interval_name,
      oi_now, oi_prev, oi_delta_abs, oi_delta_pct,
      kline_close_now, kline_close_prev, price_delta_pct,
      volume_sum, turnover_sum, funding_rate, status, error, source
    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        cycle_id, ts, row["symbol"], row["pair"], interval_name,
        oi_now, oi_prev, oi_delta_abs, oi_delta_pct,
        close_now, close_prev, price_delta_pct,
        volume_sum, turnover_sum, row["funding_rate"], status, error,
        "BYBIT_PUBLIC_MARKET"
    ))
    con.commit()


def upsert_latest(con: sqlite3.Connection, cycle_id: str, ts: str, row: Dict[str, Any], fast_pool: int) -> Dict[str, Any]:
    cur = con.cursor()
    snap_rows = cur.execute("""
    select interval_name, oi_now, oi_delta_pct, price_delta_pct, volume_sum, status, error
    from flow_snapshots
    where cycle_id = ? and symbol = ?
    """, (cycle_id, row["symbol"])).fetchall()

    by_interval = {r[0]: r for r in snap_rows}
    missing = []

    def vals(interval: str) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        r = by_interval.get(interval)
        if not r:
            missing.append(interval)
            return None, None, None, None
        if r[5] != "OK":
            missing.append(interval)
        return r[1], r[2], r[3], r[4]

    oi_1h, oi_1h_delta, price_1h, vol_1h = vals("1h")
    oi_15m, oi_15m_delta, price_15m, vol_15m = vals("15m")
    oi_5m, oi_5m_delta, price_5m, vol_5m = vals("5m") if fast_pool else (None, None, None, None)
    oi_1m, oi_1m_delta, price_1m, vol_1m = vals("1m") if fast_pool else (None, None, None, None)

    if fast_pool == 0:
        missing.extend(["5m_SKIPPED_NON_FAST_POOL", "1m_SKIPPED_NON_FAST_POOL"])

    data_status = "OK" if not missing else "PARTIAL"

    cur.execute("""
    insert or replace into latest_flow (
      symbol, pair, cycle_id, ts, turnover24h, volume24h, last_price, funding_rate,
      oi_1h_now, oi_1h_delta_pct, price_1h_delta_pct, volume_1h_sum,
      oi_15m_now, oi_15m_delta_pct, price_15m_delta_pct, volume_15m_sum,
      oi_5m_now, oi_5m_delta_pct, price_5m_delta_pct, volume_5m_sum,
      oi_1m_now, oi_1m_delta_pct, price_1m_delta_pct, volume_1m_sum,
      data_status, missing_intervals, fast_pool, source
    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        row["symbol"], row["pair"], cycle_id, ts, row["turnover24h"],
        row["volume24h"], row["last_price"], row["funding_rate"],
        oi_1h, oi_1h_delta, price_1h, vol_1h,
        oi_15m, oi_15m_delta, price_15m, vol_15m,
        oi_5m, oi_5m_delta, price_5m, vol_5m,
        oi_1m, oi_1m_delta, price_1m, vol_1m,
        data_status, ",".join(missing), fast_pool, "F3A_SQLITE_CACHE"
    ))
    con.commit()

    return {
        "symbol": row["symbol"],
        "pair": row["pair"],
        "fast_pool": fast_pool,
        "data_status": data_status,
        "missing": missing,
        "oi_1h_delta_pct": oi_1h_delta,
        "oi_15m_delta_pct": oi_15m_delta,
        "oi_5m_delta_pct": oi_5m_delta,
        "oi_1m_delta_pct": oi_1m_delta,
        "price_1h_delta_pct": price_1h,
        "price_15m_delta_pct": price_15m,
        "price_5m_delta_pct": price_5m,
        "price_1m_delta_pct": price_1m,
    }


def write_compact(
    runtime: Path,
    db_path: Path,
    cycle_id: str,
    selected: List[Dict[str, Any]],
    latest_rows: List[Dict[str, Any]],
    errors: List[str],
    min_turnover24h: float,
    max_pairs: int,
    fast_max_pairs: int,
) -> str:
    full_count = len(selected)
    fast_count = sum(1 for r in latest_rows if r.get("fast_pool") == 1)
    ok_count = sum(1 for r in latest_rows if r.get("data_status") == "OK")
    partial_count = sum(1 for r in latest_rows if r.get("data_status") != "OK")

    top_oi_15m = sorted(
        [r for r in latest_rows if r.get("oi_15m_delta_pct") is not None],
        key=lambda x: x.get("oi_15m_delta_pct") or 0.0,
        reverse=True,
    )[:20]

    top_oi_5m = sorted(
        [r for r in latest_rows if r.get("oi_5m_delta_pct") is not None],
        key=lambda x: x.get("oi_5m_delta_pct") or 0.0,
        reverse=True,
    )[:20]

    lines = []
    lines.append("F3A_MARKET_WIDE_BYBIT_FLOW_CACHE_COMPACT")
    lines.append(f"generated_at={utc_now()}")
    lines.append(f"cycle_id={cycle_id}")
    lines.append(f"runtime_dir={runtime}")
    lines.append(f"db_path={db_path}")
    lines.append("storage=SQLITE_PRIMARY")
    lines.append("redis=NOT_USED_OPTIONAL_LATER")
    lines.append("behavior_change=NONE")
    lines.append("entry_gate_change=NONE")
    lines.append("risk_change=NONE")
    lines.append("")
    lines.append("CONFIG")
    lines.append(f"min_turnover24h={min_turnover24h}")
    lines.append(f"max_pairs={max_pairs}")
    lines.append(f"fast_max_pairs={fast_max_pairs}")
    lines.append("base_intervals=1h,15m")
    lines.append("fast_intervals=5m,1m")
    lines.append("")
    lines.append("COUNTS")
    lines.append(f"selected_pairs={full_count}")
    lines.append(f"fast_pool_pairs={fast_count}")
    lines.append(f"latest_ok={ok_count}")
    lines.append(f"latest_partial={partial_count}")
    lines.append(f"errors={len(errors)}")
    lines.append("")
    lines.append("TOP_OI_15M_EXPANSION")
    for r in top_oi_15m:
        lines.append(f"{r['pair']} oi15={r.get('oi_15m_delta_pct')} price15={r.get('price_15m_delta_pct')} fast={r.get('fast_pool')} status={r.get('data_status')}")
    lines.append("")
    lines.append("TOP_OI_5M_EXPANSION_FAST_POOL")
    for r in top_oi_5m:
        lines.append(f"{r['pair']} oi5={r.get('oi_5m_delta_pct')} price5={r.get('price_5m_delta_pct')} oi1m={r.get('oi_1m_delta_pct')} fast={r.get('fast_pool')} status={r.get('data_status')}")
    lines.append("")
    lines.append("SAMPLE_LATEST_ROWS")
    for r in latest_rows[:30]:
        lines.append(
            f"{r['pair']}|fast={r.get('fast_pool')}|status={r.get('data_status')}|"
            f"oi1h={r.get('oi_1h_delta_pct')}|oi15={r.get('oi_15m_delta_pct')}|"
            f"oi5={r.get('oi_5m_delta_pct')}|oi1m={r.get('oi_1m_delta_pct')}|"
            f"missing={','.join(r.get('missing') or [])}"
        )
    lines.append("")
    lines.append("ERROR_SAMPLE")
    for e in errors[:40]:
        lines.append(e)
    lines.append("")
    lines.append("DECISION")
    lines.append("F3A_IS_AUDIT_ONLY_MARKET_WIDE_CACHE")
    lines.append("NEXT_F3B_SHOULD_INTERPRET_OI_BY_REGIME")
    lines.append("DO_NOT_CONNECT_TO_ENTRY_GATE_YET")

    text = "\n".join(lines) + "\n"

    (runtime / "F3A_MARKET_WIDE_BYBIT_FLOW_CACHE_COMPACT.txt").write_text(text, encoding="utf-8")
    Path("F3A_MARKET_WIDE_BYBIT_FLOW_CACHE_COMPACT.txt").write_text(text, encoding="utf-8")

    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default=os.environ.get("REVO_RUNTIME_DIR", "user_data/revo_alpha/runtime/bybit"))
    ap.add_argument("--db-path", default=os.environ.get("F3A_DB_PATH", ""))
    ap.add_argument("--min-turnover24h", type=float, default=float(os.environ.get("F3A_MIN_TURNOVER24H", "1000000")))
    ap.add_argument("--max-pairs", type=int, default=int(os.environ.get("F3A_MAX_PAIRS", "150")))
    ap.add_argument("--fast-max-pairs", type=int, default=int(os.environ.get("F3A_FAST_MAX_PAIRS", "30")))
    ap.add_argument("--sleep-sec", type=float, default=float(os.environ.get("F3A_SLEEP_SEC", "0.18")))
    ap.add_argument("--include-1m", action="store_true", default=os.environ.get("F3A_INCLUDE_1M", "1") == "1")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    db_path = Path(args.db_path) if args.db_path else runtime / "f3a_market_wide_flow_cache.sqlite"
    con = init_db(db_path)

    cycle_id = compact_ts()
    ts = utc_now()
    errors: List[str] = []

    tickers = fetch_tickers()
    selected = select_eligible_pairs(tickers, args.min_turnover24h, args.max_pairs)

    # CONTROL_TOWER_F3A_DIRECTIONAL_PRIORITY
    # Ensure flow-directional pairs (LONG_ONLY/SHORT_ONLY/BOTH) always get fresh OI this cycle,
    # even if their turnover is below the top-N cut. The flow engine (step 3) runs BEFORE f3a
    # (step 4), so revo_flow_context.json already carries direction. Without this, low-turnover
    # actionable pairs keep stale OI and get dropped by the publisher completeness check.
    try:
        _flow = load_json(runtime / "revo_flow_context.json", {})
        _sel_syms = {r["symbol"] for r in selected}
        _tick_by_sym = {norm(t.get("symbol")): t for t in tickers}
        _added = 0
        if isinstance(_flow, dict):
            for _pair, _fr in _flow.items():
                if not isinstance(_fr, dict):
                    continue
                if str(_fr.get("flow_direction", "NO_TRADE")).upper() not in ("LONG_ONLY", "SHORT_ONLY", "BOTH_ALLOWED"):
                    continue
                _sym = bybit_symbol(str(_pair))
                if _sym in _sel_syms:
                    continue
                _t = _tick_by_sym.get(_sym)
                if not _t:
                    continue
                _base = _sym[:-4] if _sym.endswith("USDT") else _sym
                selected.append({
                    "symbol": _sym, "pair": bybit_pair(_sym), "base": _base, "quote": "USDT",
                    "last_price": as_float(_t.get("lastPrice")), "turnover24h": as_float(_t.get("turnover24h")),
                    "volume24h": as_float(_t.get("volume24h")), "price24h_pct": as_float(_t.get("price24hPcnt")) * 100.0,
                    "funding_rate": as_float(_t.get("fundingRate")), "next_funding_time": norm(_t.get("nextFundingTime")),
                })
                _sel_syms.add(_sym)
                _added += 1
        print(f"{utc_now()} F3A_DIRECTIONAL_PRIORITY added={_added} total_selected={len(selected)}", flush=True)
    except Exception as _e:
        print(f"{utc_now()} F3A_DIRECTIONAL_PRIORITY_ERROR {_e!r}", flush=True)

    fast_symbols = load_fast_symbols(runtime, selected, args.fast_max_pairs)

    upsert_eligible(con, cycle_id, ts, selected, fast_symbols)

    latest_rows = []

    for idx, row in enumerate(selected, start=1):
        symbol = row["symbol"]
        fast_pool = 1 if symbol in fast_symbols else 0

        intervals = ["1h", "15m"]
        if fast_pool:
            intervals.append("5m")
            if args.include_1m:
                intervals.append("1m")

        print(f"{utc_now()} F3A_COLLECT {idx}/{len(selected)} {symbol} fast={fast_pool} intervals={','.join(intervals)}", flush=True)

        for interval_name in intervals:
            oi_now, oi_prev, oi_delta_pct, oi_status, oi_error = fetch_open_interest(symbol, interval_name)
            time.sleep(args.sleep_sec)

            close_now, close_prev, price_delta_pct, volume_sum, turnover_sum, kl_status, kl_error = fetch_kline(symbol, interval_name)
            time.sleep(args.sleep_sec)

            status = "OK" if oi_status == "OK" and kl_status == "OK" else "MISSING"
            error = ";".join([x for x in [oi_error, kl_error] if x])

            if status != "OK":
                errors.append(f"{symbol}|{interval_name}|{error}")

            upsert_snapshot(
                con, cycle_id, ts, row, interval_name,
                oi_now, oi_prev, oi_delta_pct,
                close_now, close_prev, price_delta_pct,
                volume_sum, turnover_sum,
                status, error,
            )

        latest = upsert_latest(con, cycle_id, ts, row, fast_pool)
        latest_rows.append(latest)

    cur = con.cursor()
    cur.execute("insert or replace into meta(key, value, updated_at) values (?, ?, ?)", ("last_cycle_id", cycle_id, utc_now()))
    cur.execute("insert or replace into meta(key, value, updated_at) values (?, ?, ?)", ("storage", "SQLITE_PRIMARY", utc_now()))
    cur.execute("insert or replace into meta(key, value, updated_at) values (?, ?, ?)", ("behavior_change", "NONE", utc_now()))
    con.commit()

    text = write_compact(
        runtime=runtime,
        db_path=db_path,
        cycle_id=cycle_id,
        selected=selected,
        latest_rows=latest_rows,
        errors=errors,
        min_turnover24h=args.min_turnover24h,
        max_pairs=args.max_pairs,
        fast_max_pairs=args.fast_max_pairs,
    )

    print(text)
    print("F3A_MARKET_WIDE_BYBIT_FLOW_CACHE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
