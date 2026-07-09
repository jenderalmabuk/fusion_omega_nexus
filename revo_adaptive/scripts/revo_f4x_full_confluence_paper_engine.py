#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


BYBIT_BASE = "https://api.bybit.com"


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def utc_now() -> str:
    return utc_now_dt().isoformat()


def norm(v: Any) -> str:
    if v is None:
        return "UNKNOWN"
    s = str(v).strip()
    return s if s else "UNKNOWN"


def as_float(v: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def as_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(float(v))
    except Exception:
        return default


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        return {"_load_error": repr(e), "_path": str(path)}


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    text = str(v).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        text = text.replace(" UTC", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def age_min(ts: Any, now: datetime) -> Optional[float]:
    dt = parse_dt(ts)
    if not dt:
        return None
    return round((now - dt).total_seconds() / 60.0, 3)


def pair_to_symbol(pair: str) -> str:
    pair = pair.replace(":USDT", "")
    if "/" in pair:
        return pair.split("/")[0].replace("1000", "1000") + "USDT"
    return pair


def symbol_to_pair(symbol: str) -> str:
    if symbol.endswith("USDT"):
        base = symbol[:-4]
        return f"{base}/USDT:USDT"
    return symbol


def http_json(url: str, timeout: int = 12) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "revo-f4x-paper/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def bybit_get(path: str, params: Dict[str, Any], timeout: int = 12) -> Dict[str, Any]:
    qs = urllib.parse.urlencode(params)
    return http_json(f"{BYBIT_BASE}{path}?{qs}", timeout=timeout)


def run_cmd(cmd: List[str], label: str, allow_fail: bool = False) -> Dict[str, Any]:
    started = time.time()
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=420)
        ok = p.returncode == 0
        if not ok and not allow_fail:
            return {
                "label": label,
                "ok": False,
                "returncode": p.returncode,
                "elapsed_sec": round(time.time() - started, 3),
                "tail": "\n".join((p.stdout or "").splitlines()[-80:]),
            }
        return {
            "label": label,
            "ok": ok,
            "returncode": p.returncode,
            "elapsed_sec": round(time.time() - started, 3),
            "tail": "\n".join((p.stdout or "").splitlines()[-40:]),
        }
    except Exception as e:
        return {
            "label": label,
            "ok": False,
            "returncode": -1,
            "elapsed_sec": round(time.time() - started, 3),
            "tail": repr(e),
        }


def maybe_run_f3(runtime: Path, args: argparse.Namespace) -> List[Dict[str, Any]]:
    if args.skip_f3:
        return [{"label": "F3_SEQUENCE", "ok": True, "tail": "skip_f3=1"}]

    steps = [
        ("F3A_MARKET_WIDE_CACHE", ["python3", "scripts/revo_f3a_market_wide_bybit_flow_cache.py",
            "--runtime-dir", str(runtime),
            "--min-turnover24h", str(args.min_turnover24h),
            "--max-pairs", str(args.max_pairs),
            "--fast-max-pairs", str(args.fast_max_pairs),
            "--sleep-sec", str(args.collector_sleep_sec)]),
        ("F3A_B_HEALTH", ["python3", "scripts/revo_f3a_b_flow_cache_health_classifier.py",
            "--runtime-dir", str(runtime)]),
        ("F3B_OI_INTERPRETER", ["python3", "scripts/revo_f3b_regime_aware_oi_interpreter.py",
            "--runtime-dir", str(runtime)]),
        ("F3C_SNAPSHOT", ["python3", "scripts/revo_f3c_event_aligned_flow_snapshot_from_sqlite_cache.py",
            "--runtime-dir", str(runtime), "--max-align-sec", "900"]),
        ("F3D_SCORER", ["python3", "scripts/revo_f3d_current_flow_snapshot_scorer.py",
            "--runtime-dir", str(runtime)]),
        ("F3E_GATE_COMPARE", ["python3", "scripts/revo_f3e_compare_f3d_ready_with_real_gate_telemetry.py",
            "--runtime-dir", str(runtime), "--jsonl-tail-lines", "30000", "--per-pair-limit", "80"]),
        ("F3F_LATEST_GATE", ["python3", "scripts/revo_f3f_latest_gate_state_classifier.py",
            "--runtime-dir", str(runtime), "--jsonl-tail-lines", "30000", "--recent-n", "12"]),
        ("F3G_LIFECYCLE", ["python3", "scripts/revo_f3g_watch_expiry_and_recheck.py",
            "--runtime-dir", str(runtime), "--max-location-age-min", "180", "--max-trigger-age-min", "60",
            "--max-entry-ready-age-min", "30", "--missing-expire-min", "30"]),
        ("F3G_B_FRESHNESS", ["python3", "scripts/revo_f3g_b_watch_lifecycle_freshness_guard.py",
            "--runtime-dir", str(runtime), "--max-location-gate-age-min", "30", "--max-trigger-gate-age-min", "15",
            "--max-entry-ready-gate-age-min", "10", "--max-recheck-gate-age-min", "15",
            "--max-generic-gate-age-min", "30", "--stale-expire-gate-age-min", "1440"]),
        ("F3Z_FINAL_AUDIT", ["python3", "scripts/revo_f3z_control_tower_final_audit_runner.py",
            "--runtime-dir", str(runtime), "--write-shadow-proposal"]),
    ]

    out = []
    for label, cmd in steps:
        if not Path(cmd[1]).exists():
            out.append({"label": label, "ok": False, "returncode": -2, "tail": f"missing_script={cmd[1]}"})
            if not args.allow_missing_f3:
                break
            continue
        out.append(run_cmd(cmd, label, allow_fail=args.allow_f3_fail))
    return out


def open_latest_flow(runtime: Path) -> Dict[str, Dict[str, Any]]:
    db = runtime / "f3a_market_wide_flow_cache.sqlite"
    out: Dict[str, Dict[str, Any]] = {}
    if not db.exists():
        return out
    try:
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
        rows = con.execute("select * from latest_flow").fetchall()
        for r in rows:
            out[str(r["pair"])] = {k: r[k] for k in r.keys()}
        con.close()
    except Exception:
        return out
    return out


def load_states(runtime: Path) -> Dict[str, Any]:
    return {
        "f3a_b": load_json(runtime / "revo_f3a_b_flow_cache_health_classifier_state.json", {}),
        "f3b": load_json(runtime / "revo_f3b_regime_aware_oi_interpreter_state.json", {}),
        "f3c": load_json(runtime / "revo_f3c_event_aligned_flow_snapshot_state.json", {}),
        "f3d": load_json(runtime / "revo_f3d_current_flow_snapshot_scorer_state.json", {}),
        "f3e": load_json(runtime / "revo_f3e_compare_f3d_ready_gate_telemetry_state.json", {}),
        "f3f": load_json(runtime / "revo_f3f_latest_gate_state_classifier_state.json", {}),
        "f3g": load_json(runtime / "revo_f3g_watch_expiry_state.json", {}),
        "f3g_b": load_json(runtime / "revo_f3g_b_watch_lifecycle_freshness_guard_state.json", {}),
        "f3z": load_json(runtime / "F3Z_CONTROL_TOWER_FINAL_AUDIT_FULL.json", {}),
    }


def safe_rows(state: Dict[str, Any], key: str) -> List[Dict[str, Any]]:
    rows = state.get(key, []) if isinstance(state, dict) else []
    return rows if isinstance(rows, list) else []


def key_pair_side(pair: str, side: str) -> str:
    return f"{pair}|{side.upper()}"


def index_by_pair_side(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for r in rows:
        pair = norm(r.get("pair"))
        side = norm(r.get("side")).upper()
        if pair != "UNKNOWN" and side in {"LONG", "SHORT"}:
            out[key_pair_side(pair, side)] = r
    return out


def build_candidate_keys(runtime: Path, states: Dict[str, Any], latest_flow: Dict[str, Dict[str, Any]], max_candidates: int) -> List[str]:
    """
    F4X-F:
    Active observation pairlist must be the primary candidate seed.

    Old behavior was too dependent on F3/F4 cache rows, so many active observation
    pairs had gate telemetry but never reached F4X signal/lane scoring. This function
    prioritizes:
      1. F4X-D2 promoted pairs
      2. current active pairlist
      3. F4X-C prior watch/execution rows
      4. F3Z/F3D/F3F/F3G-B rows
      5. top latest_flow fallback
    """
    ordered: List[str] = []
    seen = set()

    def add(pair: str, side: str, source: str = "") -> None:
        pair = norm(pair)
        side = norm(side).upper()
        if pair == "UNKNOWN" or side not in {"LONG", "SHORT"}:
            return
        k = key_pair_side(pair, side)
        if k in seen:
            return
        seen.add(k)
        ordered.append(k)

    def side_from_latest_flow(pair: str) -> str:
        row = latest_flow.get(pair, {})
        px15 = as_float(row.get("price_15m_delta_pct"), 0.0) or 0.0
        oi15 = as_float(row.get("oi_15m_delta_pct"), 0.0) or 0.0
        px5 = as_float(row.get("price_5m_delta_pct"), 0.0)
        if oi15 > 0.05:
            return "LONG" if px15 >= 0 else "SHORT"
        if px15 > 0.10:
            return "LONG"
        if px15 < -0.10:
            return "SHORT"
        if px5 is not None and px5 > 0:
            return "LONG"
        if px5 is not None and px5 < 0:
            return "SHORT"
        return "LONG"

    def side_from_f3b(pair: str) -> str:
        for r in safe_rows(states.get("f3b", {}), "interpreted"):
            if norm(r.get("pair")) != pair:
                continue
            bias = norm(r.get("primary_bias")).upper()
            direction = norm(r.get("direction")).upper()
            flow_direction = norm(r.get("flow_direction")).upper()
            text = " ".join([bias, direction, flow_direction])
            if "LONG" in text:
                return "LONG"
            if "SHORT" in text:
                return "SHORT"
        return ""

    def best_side_from_rows(pair: str) -> str:
        candidate_rows = []

        # F4X-C previous lane state is useful because it already knows side from prior scoring.
        f4xc = load_json(runtime / "F4X_C_LANE_SEPARATION_FULL.json", {})
        for r in safe_rows(f4xc, "entry_ready") + safe_rows(f4xc, "execution_watch") + safe_rows(f4xc, "discovery_watch") + safe_rows(f4xc, "lanes"):
            if norm(r.get("pair")) == pair:
                lane = norm(r.get("lane")).upper()
                score = as_int(r.get("score"))
                side = norm(r.get("side")).upper()
                rank = {"ENTRY_READY": 1000, "EXECUTION_WATCH": 900, "DISCOVERY_WATCH": 800, "RECHECK_DATA": 300}.get(lane, 100)
                if side in {"LONG", "SHORT"}:
                    candidate_rows.append((rank + score, side))

        # F3 rows.
        for state_name, keys, base in [
            ("f3z", ["candidates"], 700),
            ("f3d", ["flow_ready", "watch_confirm", "current_pairlist_scored", "weak"], 650),
            ("f3f", ["reports"], 600),
            ("f3g_b", ["guarded_records"], 550),
        ]:
            state = states.get(state_name, {})
            for list_key in keys:
                for r in safe_rows(state, list_key):
                    if norm(r.get("pair")) == pair:
                        side = norm(r.get("side")).upper()
                        score = as_int(r.get("score") or r.get("shadow_score") or r.get("ratio"))
                        if side in {"LONG", "SHORT"}:
                            candidate_rows.append((base + score, side))

        if candidate_rows:
            candidate_rows.sort(reverse=True)
            return candidate_rows[0][1]

        s = side_from_f3b(pair)
        if s:
            return s

        return side_from_latest_flow(pair)

    # 1. Promoted pairs from F4X-D2 must be first.
    d2 = load_json(runtime / "F4X_D2_SAFE_MERGE_ACTIVE_PAIRLIST_PROPOSAL.json", {})
    promoted = d2.get("promoted_in_final") or d2.get("promoted_pairs") or []
    if isinstance(promoted, list):
        for pair in promoted:
            pair = norm(pair)
            add(pair, best_side_from_rows(pair), "F4X_D2_PROMOTED")

    # 2. Active observation pairlist is primary seed.
    pairlist = load_json(runtime / "pair_universe_remote.json", {})
    active_pairs = pairlist.get("pairs", []) if isinstance(pairlist, dict) else []
    if isinstance(active_pairs, list):
        for pair in active_pairs:
            pair = norm(pair)
            add(pair, best_side_from_rows(pair), "ACTIVE_PAIRLIST")

    # 3. Prior F4X-C lanes.
    f4xc = load_json(runtime / "F4X_C_LANE_SEPARATION_FULL.json", {})
    for lane_key in ["entry_ready", "execution_watch", "discovery_watch", "recheck_data", "lanes"]:
        for r in safe_rows(f4xc, lane_key):
            pair = norm(r.get("pair"))
            side = norm(r.get("side")).upper()
            add(pair, side, f"F4X_C_{lane_key}")

    # 4. F3Z/F3D/F3F/F3G-B rows.
    for r in safe_rows(states["f3z"], "candidates"):
        add(norm(r.get("pair")), norm(r.get("side")).upper(), "F3Z")

    for section in ["flow_ready", "watch_confirm", "current_pairlist_scored", "weak"]:
        for r in safe_rows(states["f3d"], section):
            add(norm(r.get("pair")), norm(r.get("side")).upper(), f"F3D_{section}")

    for r in safe_rows(states["f3f"], "reports"):
        add(norm(r.get("pair")), norm(r.get("side")).upper(), "F3F")

    for r in safe_rows(states["f3g_b"], "guarded_records"):
        add(norm(r.get("pair")), norm(r.get("side")).upper(), "F3G_B")

    # 5. Latest-flow fallback if candidate limit still not full.
    if len(ordered) < max_candidates:
        sorted_pairs = sorted(
            latest_flow.items(),
            key=lambda kv: as_float(kv[1].get("turnover24h"), 0.0) or 0.0,
            reverse=True,
        )
        for pair, row in sorted_pairs:
            side = side_from_latest_flow(pair)
            add(pair, side, "LATEST_FLOW")
            if len(ordered) >= max_candidates:
                break

    return ordered[:max_candidates]


def fetch_true_cvd_for_pair(pair: str, limit: int, sleep_sec: float) -> Dict[str, Any]:
    symbol = pair_to_symbol(pair)
    try:
        data = bybit_get("/v5/market/recent-trade", {"category": "linear", "symbol": symbol, "limit": limit})
        rows = (((data or {}).get("result") or {}).get("list") or [])
        buy_qty = 0.0
        sell_qty = 0.0
        buy_count = 0
        sell_count = 0
        notion_buy = 0.0
        notion_sell = 0.0
        latest_ts = None

        for r in rows:
            side = norm(r.get("side")).lower()
            qty = as_float(r.get("size"), 0.0) or 0.0
            px = as_float(r.get("price"), 0.0) or 0.0
            ts = r.get("time")
            latest_ts = max(str(latest_ts or ""), str(ts or ""))
            if side == "buy":
                buy_qty += qty
                notion_buy += qty * px
                buy_count += 1
            elif side == "sell":
                sell_qty += qty
                notion_sell += qty * px
                sell_count += 1

        total_qty = buy_qty + sell_qty
        total_notional = notion_buy + notion_sell
        cvd_qty = buy_qty - sell_qty
        cvd_notional = notion_buy - notion_sell
        cvd_ratio = cvd_qty / total_qty if total_qty > 0 else 0.0
        cvd_notional_ratio = cvd_notional / total_notional if total_notional > 0 else 0.0

        if total_qty <= 0 or len(rows) == 0:
            label = "CVD_NO_TRADES"
        elif cvd_ratio >= 0.15:
            label = "CVD_BUY_AGGRESSION"
        elif cvd_ratio <= -0.15:
            label = "CVD_SELL_AGGRESSION"
        elif cvd_ratio >= 0.05:
            label = "CVD_BUY_LEAN"
        elif cvd_ratio <= -0.05:
            label = "CVD_SELL_LEAN"
        else:
            label = "CVD_NEUTRAL"

        time.sleep(max(0.0, sleep_sec))
        return {
            "pair": pair,
            "symbol": symbol,
            "cvd_source": "BYBIT_PUBLIC_RECENT_TRADE_TRUE_AGGRESSOR",
            "cvd_status": "OK",
            "trade_count": len(rows),
            "buy_count": buy_count,
            "sell_count": sell_count,
            "buy_qty": buy_qty,
            "sell_qty": sell_qty,
            "cvd_qty": cvd_qty,
            "cvd_ratio": round(cvd_ratio, 6),
            "cvd_notional": round(cvd_notional, 4),
            "cvd_notional_ratio": round(cvd_notional_ratio, 6),
            "cvd_label": label,
            "error": "NONE",
        }
    except Exception as e:
        time.sleep(max(0.0, sleep_sec))
        return {
            "pair": pair,
            "symbol": symbol,
            "cvd_source": "BYBIT_PUBLIC_RECENT_TRADE_TRUE_AGGRESSOR",
            "cvd_status": "ERROR",
            "trade_count": 0,
            "cvd_ratio": 0.0,
            "cvd_notional_ratio": 0.0,
            "cvd_label": "CVD_MISSING",
            "error": repr(e),
        }


def fetch_klines(pair: str, interval: str, limit: int, sleep_sec: float) -> Dict[str, Any]:
    symbol = pair_to_symbol(pair)
    try:
        data = bybit_get("/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": interval, "limit": limit})
        rows = (((data or {}).get("result") or {}).get("list") or [])
        candles = []
        for r in rows:
            try:
                candles.append({
                    "ts": int(r[0]),
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                    "volume": float(r[5]),
                    "turnover": float(r[6]) if len(r) > 6 else 0.0,
                })
            except Exception:
                continue
        candles.sort(key=lambda x: x["ts"])
        time.sleep(max(0.0, sleep_sec))
        return {"pair": pair, "symbol": symbol, "status": "OK", "candles": candles, "error": "NONE"}
    except Exception as e:
        time.sleep(max(0.0, sleep_sec))
        return {"pair": pair, "symbol": symbol, "status": "ERROR", "candles": [], "error": repr(e)}


def calc_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 2:
        return None
    gains = []
    losses = []
    for i in range(1, period + 1):
        diff = closes[-period - 1 + i] - closes[-period - 2 + i]
        gains.append(max(diff, 0.0))
        losses.append(abs(min(diff, 0.0)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 4)


def calc_stoch(candles: List[Dict[str, Any]], period: int = 14) -> Tuple[Optional[float], Optional[float]]:
    if len(candles) < period + 3:
        return None, None
    recent = candles[-period:]
    hh = max(x["high"] for x in recent)
    ll = min(x["low"] for x in recent)
    close = candles[-1]["close"]
    if hh == ll:
        k = 50.0
    else:
        k = 100.0 * (close - ll) / (hh - ll)
    ks = []
    for j in range(3):
        sub = candles[-period - j: -j if j > 0 else None]
        if len(sub) < period:
            continue
        h = max(x["high"] for x in sub)
        l = min(x["low"] for x in sub)
        c = sub[-1]["close"]
        ks.append(50.0 if h == l else 100.0 * (c - l) / (h - l))
    d = sum(ks) / len(ks) if ks else None
    return round(k, 4), round(d, 4) if d is not None else None


def trigger_for_side(pair: str, side: str, kline: Dict[str, Any]) -> Dict[str, Any]:
    candles = kline.get("candles", [])
    closes = [x["close"] for x in candles]
    rsi = calc_rsi(closes)
    stoch_k, stoch_d = calc_stoch(candles)

    if kline.get("status") != "OK" or len(candles) < 20:
        return {"pair": pair, "side": side, "trigger_status": "DATA_MISSING", "rsi": rsi, "stoch_k": stoch_k, "stoch_d": stoch_d}

    side = side.upper()
    last = candles[-1]
    prev = candles[-2]
    body_up = last["close"] > last["open"]
    body_down = last["close"] < last["open"]
    close_up = last["close"] > prev["close"]
    close_down = last["close"] < prev["close"]

    if side == "LONG":
        if rsi is not None and rsi > 78:
            status = "TRIGGER_OVEREXTENDED"
        elif stoch_k is not None and stoch_d is not None and stoch_k > stoch_d and body_up and close_up:
            status = "TRIGGER_CONFIRMED"
        elif stoch_k is not None and stoch_d is not None and stoch_k >= stoch_d:
            status = "TRIGGER_WEAK"
        else:
            status = "TRIGGER_NOT_READY"
    else:
        if rsi is not None and rsi < 22:
            status = "TRIGGER_OVEREXTENDED"
        elif stoch_k is not None and stoch_d is not None and stoch_k < stoch_d and body_down and close_down:
            status = "TRIGGER_CONFIRMED"
        elif stoch_k is not None and stoch_d is not None and stoch_k <= stoch_d:
            status = "TRIGGER_WEAK"
        else:
            status = "TRIGGER_NOT_READY"

    return {
        "pair": pair,
        "side": side,
        "trigger_status": status,
        "rsi": rsi,
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,
        "last_close": last["close"],
        "prev_close": prev["close"],
    }


def cvdoi_label(side: str, flow: Dict[str, Any], cvd: Dict[str, Any]) -> Dict[str, Any]:
    px15 = as_float(flow.get("price_15m_delta_pct"), 0.0) or 0.0
    oi15 = as_float(flow.get("oi_15m_delta_pct"), 0.0) or 0.0
    px5 = as_float(flow.get("price_5m_delta_pct"), 0.0)
    oi5 = as_float(flow.get("oi_5m_delta_pct"), 0.0)
    funding = as_float(flow.get("funding_rate"), 0.0) or 0.0

    cvd_ratio = as_float(cvd.get("cvd_ratio"), 0.0) or 0.0
    cvd_status = norm(cvd.get("cvd_status"))
    cvd_lab = norm(cvd.get("cvd_label"))

    price_up = px15 > 0.05
    price_down = px15 < -0.05
    oi_up = oi15 > 0.10
    oi_down = oi15 < -0.10
    cvd_up = cvd_ratio > 0.05
    cvd_down = cvd_ratio < -0.05

    if cvd_status != "OK":
        label = "CVD_MISSING_RECHECK"
        direction = "RECHECK"
        cvd_support = "CVD_MISSING"
    elif price_up and oi_up and cvd_up:
        label = "BULLISH_CONTINUATION_STRONG"
        direction = "LONG_ONLY"
        cvd_support = "CVD_SUPPORTS_LONG"
    elif price_down and oi_up and cvd_down:
        label = "BEARISH_CONTINUATION_STRONG"
        direction = "SHORT_ONLY"
        cvd_support = "CVD_SUPPORTS_SHORT"
    elif price_up and oi_up and cvd_down:
        label = "BULL_TRAP_RISK"
        direction = "DENY_LONG_WATCH_SHORT"
        cvd_support = "CVD_CONTRA_LONG"
    elif price_down and oi_up and cvd_up:
        label = "BEAR_TRAP_RISK"
        direction = "DENY_SHORT_WATCH_LONG"
        cvd_support = "CVD_CONTRA_SHORT"
    elif abs(px15) <= 0.05 and oi_up and cvd_down:
        label = "BEARISH_ABSORPTION_OR_BUY_PRESSURE_ABSORBED"
        direction = "WATCH_SHORT"
        cvd_support = "CVD_SUPPORTS_SHORT_WEAK"
    elif abs(px15) <= 0.05 and oi_up and cvd_up:
        label = "BULLISH_ABSORPTION_OR_SELL_PRESSURE_ABSORBED"
        direction = "WATCH_LONG"
        cvd_support = "CVD_SUPPORTS_LONG_WEAK"
    elif price_up and oi_down and cvd_up:
        label = "SHORT_SQUEEZE"
        direction = "WATCH_LONG_CAUTION"
        cvd_support = "CVD_SUPPORTS_LONG_WEAK"
    elif price_down and oi_down and cvd_down:
        label = "LONG_UNWIND"
        direction = "WATCH_SHORT_CAUTION"
        cvd_support = "CVD_SUPPORTS_SHORT_WEAK"
    else:
        label = "MIXED_FLOW"
        direction = "WATCH_ONLY"
        cvd_support = "CVD_NEUTRAL_OR_MIXED"

    if side == "LONG":
        side_alignment = direction in {"LONG_ONLY", "WATCH_LONG", "WATCH_LONG_CAUTION"} or cvd_support in {"CVD_SUPPORTS_LONG", "CVD_SUPPORTS_LONG_WEAK"}
        side_contra = "CONTRA_LONG" in cvd_support or label == "BULL_TRAP_RISK"
    elif side == "SHORT":
        side_alignment = direction in {"SHORT_ONLY", "WATCH_SHORT", "WATCH_SHORT_CAUTION"} or cvd_support in {"CVD_SUPPORTS_SHORT", "CVD_SUPPORTS_SHORT_WEAK"}
        side_contra = "CONTRA_SHORT" in cvd_support or label == "BEAR_TRAP_RISK"
    else:
        side_alignment = False
        side_contra = False

    return {
        "cvdoi_label": label,
        "cvdoi_direction": direction,
        "cvd_support": cvd_support,
        "side_alignment": int(side_alignment),
        "side_contra": int(side_contra),
        "price_15m_delta_pct": px15,
        "oi_15m_delta_pct": oi15,
        "price_5m_delta_pct": px5,
        "oi_5m_delta_pct": oi5,
        "funding_rate": funding,
        "cvd_ratio": cvd_ratio,
        "cvd_label": cvd_lab,
    }


def btc_guard(side: str, latest_flow: Dict[str, Dict[str, Any]], runtime: Path) -> Dict[str, Any]:
    btc = latest_flow.get("BTC/USDT:USDT", {})
    btc_mode_file = runtime / "BTC_MODE_ROUTER_COMPACT.txt"
    btc_mode_text = btc_mode_file.read_text(encoding="utf-8", errors="replace") if btc_mode_file.exists() else ""
    btc_mode = "UNKNOWN"
    for line in btc_mode_text.splitlines():
        if line.startswith("btc_mode="):
            btc_mode = line.split("=", 1)[1].strip()

    px15 = as_float(btc.get("price_15m_delta_pct"), 0.0) or 0.0
    oi15 = as_float(btc.get("oi_15m_delta_pct"), 0.0) or 0.0

    side = side.upper()
    if side == "LONG":
        if px15 < -0.35 and oi15 > 0.05:
            status = "BTC_HARD_CONTRA_BLOCK"
        elif px15 < -0.15:
            status = "BTC_CONTRA_WARNING"
        elif px15 >= 0:
            status = "BTC_SUPPORTS_LONG"
        else:
            status = "BTC_NEUTRAL_ALLOW"
    else:
        if px15 > 0.35 and oi15 > 0.05:
            status = "BTC_HARD_CONTRA_BLOCK"
        elif px15 > 0.15:
            status = "BTC_CONTRA_WARNING"
        elif px15 <= 0:
            status = "BTC_SUPPORTS_SHORT"
        else:
            status = "BTC_NEUTRAL_ALLOW"

    return {
        "btc_guard": status,
        "btc_mode": btc_mode,
        "btc_price_15m_delta_pct": px15,
        "btc_oi_15m_delta_pct": oi15,
    }


def smc_score(side: str, f3f: Dict[str, Any], f3g_b: Dict[str, Any]) -> Dict[str, Any]:
    side = side.upper()
    zone = norm(f3f.get("pd_zone") or f3g_b.get("pd_zone")).upper()
    location = norm(f3f.get("location_state") or f3g_b.get("location_state"))
    reason = norm(f3f.get("latest_reason") or f3g_b.get("latest_reason")).upper()

    if side == "LONG":
        if zone == "DISCOUNT":
            grade = "SMC_A"
            status = "SMC_LOCATION_GOOD"
        elif zone == "MID":
            grade = "SMC_B"
            status = "SMC_WAIT_TRIGGER_OR_LOCATION"
        elif zone == "PREMIUM" or "LONG_IN_PREMIUM" in reason:
            grade = "SMC_REJECT"
            status = "SMC_WAIT_LOCATION_PREMIUM_FOR_LONG"
        else:
            grade = "SMC_C"
            status = "SMC_UNKNOWN"
    else:
        if zone == "PREMIUM":
            grade = "SMC_A"
            status = "SMC_LOCATION_GOOD"
        elif zone == "MID":
            grade = "SMC_B"
            status = "SMC_WAIT_TRIGGER_OR_LOCATION"
        elif zone == "DISCOUNT" or "SHORT_IN_DISCOUNT" in reason:
            grade = "SMC_REJECT"
            status = "SMC_WAIT_LOCATION_DISCOUNT_FOR_SHORT"
        else:
            grade = "SMC_C"
            status = "SMC_UNKNOWN"

    if "GEOMETRY" in reason or "TPSL" in reason:
        grade = "SMC_REJECT"
        status = "SMC_GEOMETRY_REJECT"

    return {
        "smc_grade": grade,
        "smc_status": status,
        "pd_zone": zone,
        "location_state": location,
        "location_reason": reason,
    }


def grade_rank(g: str) -> int:
    return {"A+": 5, "A": 4, "B+": 3, "B": 3, "C": 2, "D": 1, "UNKNOWN": 0}.get(g, 0)


def legacy_shadow_grade(side: str, f3d: Dict[str, Any], f3f: Dict[str, Any], f3z: Dict[str, Any]) -> Dict[str, Any]:
    side = side.upper()

    f3d_ratio = as_float(f3d.get("ratio"), None)
    f3d_score = as_float(f3d.get("score"), None)
    f3d_decision = norm(f3d.get("decision"))
    has_f3d = (
        f3d_ratio is not None
        or f3d_score is not None
        or f3d_decision not in {"UNKNOWN", "NONE", ""}
    )

    raw_shadow_grade = norm(f3f.get("shadow_grade"))
    shadow_score = as_float(f3f.get("shadow_score"), None)
    shadow_hard_veto = norm(
        f3f.get("shadow_hard_veto_reason")
        or f3f.get("shadow_hard_veto")
        or f3z.get("shadow_hard_veto_reason")
    )

    has_shadow = (
        raw_shadow_grade not in {"UNKNOWN", "NONE", ""}
        or shadow_score is not None
    )

    def grade_from_ratio(ratio: Optional[float]) -> str:
        if ratio is None:
            return "PENDING"
        if ratio >= 0.90:
            return "A+"
        if ratio >= 0.80:
            return "A"
        if ratio >= 0.60:
            return "B"
        if ratio >= 0.40:
            return "C"
        return "D"

    def grade_from_score(score: Optional[float]) -> str:
        if score is None:
            return "PENDING"
        if score >= 95:
            return "A+"
        if score >= 85:
            return "A"
        if score >= 70:
            return "B"
        if score >= 55:
            return "C"
        return "D"

    legacy_grade = grade_from_ratio(f3d_ratio if has_f3d else None)

    if raw_shadow_grade in {"A+", "A", "B+", "B", "C", "D"}:
        shadow_grade = raw_shadow_grade
    elif has_shadow:
        shadow_grade = grade_from_score(shadow_score)
    else:
        shadow_grade = "PENDING"

    available = [g for g in [legacy_grade, shadow_grade] if g not in {"PENDING", "UNKNOWN", "NONE", ""}]
    grade_pending = len(available) < 2

    if not available:
        combined = "PENDING"
    else:
        combined_rank = min(grade_rank(g) for g in available)
        if combined_rank >= 5:
            combined = "A+"
        elif combined_rank >= 4:
            combined = "A"
        elif combined_rank >= 3:
            combined = "B"
        elif combined_rank >= 2:
            combined = "C"
        else:
            combined = "D"

    explicit_shadow_hard_veto = shadow_hard_veto not in {"UNKNOWN", "NONE", "", "OK"}
    confirmed_double_d = (
        has_f3d
        and has_shadow
        and legacy_grade == "D"
        and shadow_grade == "D"
    )

    grade_hard_reject = bool(explicit_shadow_hard_veto or confirmed_double_d)

    if not has_f3d and not has_shadow:
        grade_status = "GRADE_PENDING_NO_LEGACY_OR_SHADOW"
    elif grade_hard_reject:
        grade_status = "GRADE_CONFIRMED_HARD_REJECT"
    elif grade_pending:
        grade_status = "GRADE_PARTIAL_PENDING_NOT_HARD_BLOCK"
    else:
        grade_status = "GRADE_CONFIRMED"

    return {
        "legacy_grade": legacy_grade,
        "legacy_source": "F3D_RATIO_PROXY" if has_f3d else "PENDING",
        "shadow_grade": shadow_grade,
        "shadow_score": shadow_score if shadow_score is not None else 0.0,
        "shadow_source": "F3F_SHADOW" if has_shadow else "PENDING",
        "combined_grade_pre_f4": combined,
        "grade_pending": int(grade_pending),
        "grade_confirmed": int(not grade_pending and bool(available)),
        "grade_hard_reject": int(grade_hard_reject),
        "grade_status": grade_status,
        "shadow_hard_veto_reason": shadow_hard_veto,
    }


def final_decision_for_candidate(
    key: str,
    flow: Dict[str, Any],
    cvd: Dict[str, Any],
    cvdoi: Dict[str, Any],
    btc: Dict[str, Any],
    smc: Dict[str, Any],
    trigger: Dict[str, Any],
    grades: Dict[str, Any],
    f3z: Dict[str, Any],
    f3f: Dict[str, Any],
    f3g_b: Dict[str, Any],
    now: datetime,
    ttl_sec: int,
) -> Dict[str, Any]:
    pair, side = key.split("|", 1)
    side = side.upper()

    blockers: List[str] = []
    supports: List[str] = []
    entry_blockers: List[str] = []
    score = 0

    f3z_class = norm(f3z.get("final_class"))
    latest_state = norm(f3f.get("latest_state"))
    guarded = norm(f3g_b.get("guarded_watch_status"))
    freshness = norm(f3g_b.get("freshness_state"))
    final_allow = as_int(f3f.get("final_allow") or f3z.get("final_allow"))
    gate_allow = as_int(f3f.get("gate_allow") or f3z.get("gate_allow"))
    score_allow = as_int(f3f.get("score_allow") or f3z.get("score_allow"))
    dir_opposite = as_int(f3f.get("direction_opposite") or f3z.get("direction_opposite"))

    # 1. True CVD.
    if cvd.get("cvd_status") == "OK":
        supports.append("TRUE_CVD_OK")
        score += 10
    else:
        blockers.append("TRUE_CVD_MISSING")

    # 2. CVDOI side alignment.
    if cvdoi["side_alignment"]:
        supports.append("CVDOI_SUPPORTS_SIDE")
        score += 20
    if cvdoi["side_contra"]:
        blockers.append("CVDOI_CONTRA_SIDE")
    if "TRAP" in cvdoi["cvdoi_label"]:
        blockers.append(cvdoi["cvdoi_label"])

    # 3. BTC explicit guard.
    if btc["btc_guard"] in {"BTC_SUPPORTS_LONG", "BTC_SUPPORTS_SHORT", "BTC_NEUTRAL_ALLOW"}:
        supports.append(btc["btc_guard"])
        score += 10
    elif btc["btc_guard"] == "BTC_CONTRA_WARNING":
        supports.append("BTC_CONTRA_WARNING_DOWNGRADE")
        entry_blockers.append("BTC_CONTRA_WARNING_ENTRY_DOWNGRADE")
        score += 2
    elif btc["btc_guard"] == "BTC_HARD_CONTRA_BLOCK":
        blockers.append("BTC_HARD_CONTRA_BLOCK")

    # 4. SMC/location. Location wait is not hard reject for watch.
    smc_grade = norm(smc.get("smc_grade"))
    smc_status = norm(smc.get("smc_status"))

    if smc_grade in {"SMC_A_PLUS", "SMC_A"}:
        supports.append(smc_grade)
        score += 15
    elif smc_grade == "SMC_B":
        supports.append("SMC_B_WATCHABLE")
        score += 8
    elif smc_grade == "SMC_REJECT":
        if "WAIT_LOCATION" in smc_status:
            entry_blockers.append(smc_status)
            supports.append("SMC_WAIT_LOCATION_NOT_HARD_BLOCK")
        elif "GEOMETRY" in smc_status or "TPSL" in smc_status:
            blockers.append(smc_status)
        else:
            entry_blockers.append(smc_status)
    else:
        supports.append("SMC_C_OR_UNKNOWN_OBSERVE")

    # 5. Trigger. Overextended blocks entry, not necessarily watch/recheck.
    trig = norm(trigger.get("trigger_status"))
    if trig == "TRIGGER_CONFIRMED":
        supports.append("TRIGGER_CONFIRMED")
        score += 15
    elif trig == "TRIGGER_WEAK":
        supports.append("TRIGGER_WEAK")
        score += 6
    elif trig == "TRIGGER_OVEREXTENDED":
        entry_blockers.append("TRIGGER_OVEREXTENDED_ENTRY_BLOCK")
    elif trig == "TRIGGER_REJECTED":
        blockers.append("TRIGGER_REJECTED")
    elif trig == "DATA_MISSING":
        blockers.append("TRIGGER_DATA_MISSING")
    else:
        supports.append("TRIGGER_NOT_READY_OBSERVE")

    # 6. Legacy/shadow grade merger. Pending grade is downgrade, not universal death.
    g = norm(grades.get("combined_grade_pre_f4"))
    grade_pending = bool(as_int(grades.get("grade_pending")))
    grade_hard_reject = bool(as_int(grades.get("grade_hard_reject")))
    grade_status = norm(grades.get("grade_status"))

    if grade_hard_reject:
        blockers.append("GRADE_CONFIRMED_HARD_REJECT")
    elif g in {"A+", "A"}:
        supports.append(f"COMBINED_GRADE_{g}")
        score += 15
    elif g == "B":
        supports.append("COMBINED_GRADE_B_WATCH")
        score += 7
    elif g == "C":
        supports.append("COMBINED_GRADE_C_OBSERVE")
        score += 2
        entry_blockers.append("COMBINED_GRADE_C_ENTRY_DOWNGRADE")
    elif grade_pending or g in {"PENDING", "UNKNOWN", "NONE", ""}:
        supports.append("GRADE_PENDING_NOT_HARD_BLOCK")
        entry_blockers.append("GRADE_PENDING_ENTRY_DOWNGRADE")
    elif g == "D":
        supports.append("COMBINED_GRADE_D_DOWNGRADE_ONLY")
        entry_blockers.append("COMBINED_GRADE_D_ENTRY_DOWNGRADE")
    else:
        entry_blockers.append(f"COMBINED_GRADE_{g}_ENTRY_DOWNGRADE")

    # 7. F3 lifecycle / latest gate state.
    if latest_state == "ENTRY_READY_SHADOW" or final_allow or gate_allow:
        supports.append("LATEST_GATE_ENTRY_READY_OR_ALLOW")
        score += 15
    elif latest_state in {"WAIT_LOCATION", "WAIT_TRIGGER"}:
        supports.append(f"F3_LIFECYCLE_{latest_state}")
        score += 5
        entry_blockers.append(f"F3_LIFECYCLE_{latest_state}_ENTRY_WAIT")
    elif latest_state in {"INVALIDATED_DIRECTION", "AVOID_TRAP", "CONTEXT_BLOCK", "GEOMETRY_BLOCK"}:
        blockers.append(f"F3_LATEST_{latest_state}")
    elif latest_state in {"UNKNOWN", "NONE", ""}:
        supports.append("F3_LATEST_UNKNOWN_OBSERVE")

    # 8. Freshness guard.
    if guarded == "STALE_GATE_TELEMETRY_RECHECK" or freshness == "STALE_GATE_TELEMETRY_RECHECK":
        blockers.append("FRESHNESS_STALE_RECHECK")
    elif guarded == "EXPIRED":
        blockers.append("F3G_B_EXPIRED")
    elif freshness == "FRESH_GATE_TELEMETRY":
        supports.append("FRESH_GATE_TELEMETRY")
        score += 10
    elif freshness in {"UNKNOWN", "NONE", ""}:
        supports.append("FRESHNESS_UNKNOWN_OBSERVE")
        entry_blockers.append("FRESHNESS_UNKNOWN_ENTRY_DOWNGRADE")

    if dir_opposite:
        blockers.append("LATEST_DIRECTION_OPPOSITE")

    hard_terms = [
        "CVDOI_CONTRA_SIDE",
        "BULL_TRAP_RISK",
        "BEAR_TRAP_RISK",
        "BTC_HARD_CONTRA_BLOCK",
        "TRUE_CVD_MISSING",
        "TRIGGER_DATA_MISSING",
        "TRIGGER_REJECTED",
        "F3_LATEST_INVALIDATED_DIRECTION",
        "F3_LATEST_AVOID_TRAP",
        "CONTEXT_BLOCK",
        "GEOMETRY_BLOCK",
        "F3G_B_EXPIRED",
        "FRESHNESS_STALE_RECHECK",
        "LATEST_DIRECTION_OPPOSITE",
        "GRADE_CONFIRMED_HARD_REJECT",
    ]

    hard_blockers = [b for b in blockers if any(t in b for t in hard_terms)]

    entry_ready = bool(latest_state == "ENTRY_READY_SHADOW" or final_allow or gate_allow)
    cvd_ok = cvd.get("cvd_status") == "OK"
    side_aligned = bool(cvdoi["side_alignment"])
    trigger_ok_for_watch = trig in {"TRIGGER_CONFIRMED", "TRIGGER_WEAK", "TRIGGER_NOT_READY"}
    smc_not_fatal = not any("GEOMETRY" in b or "TPSL" in b for b in blockers)

    if hard_blockers:
        paper_action = "DENY"
        final_grade = "D"
        reason = "HARD_BLOCKER_PRESENT"
    elif score >= 85 and entry_ready and not entry_blockers:
        paper_action = "ALLOW_PAPER_ENTRY"
        final_grade = "A+"
        reason = "FULL_CONFLUENCE_ENTRY_READY"
    elif score >= 75 and entry_ready and not entry_blockers:
        paper_action = "ALLOW_PAPER_ENTRY"
        final_grade = "A"
        reason = "STRONG_CONFLUENCE_ENTRY_READY"
    elif score >= 65:
        paper_action = "WATCH_ONLY"
        final_grade = "B"
        reason = "HIGH_CONFLUENCE_WATCH_ONLY"
    elif score >= 55 and cvd_ok and side_aligned and trigger_ok_for_watch and smc_not_fatal:
        paper_action = "WATCH_ONLY"
        final_grade = "B"
        reason = "CALIBRATED_FLOW_WATCH_ONLY"
    elif score >= 45:
        paper_action = "RECHECK"
        final_grade = "C"
        reason = "PARTIAL_CONFLUENCE_RECHECK"
    else:
        paper_action = "DENY"
        final_grade = "D"
        reason = "LOW_CONFLUENCE"

    if paper_action == "ALLOW_PAPER_ENTRY":
        risk_mode = "PAPER_MIN_RISK"
    elif paper_action == "WATCH_ONLY":
        risk_mode = "WATCH_ONLY"
    else:
        risk_mode = "NONE"

    return {
        "signal_id": f"F4X-{pair_to_symbol(pair)}-{side}-{now.strftime('%Y%m%dT%H%M%SZ')}",
        "pair": pair,
        "side": side,
        "paper_action": paper_action,
        "final_grade": final_grade,
        "score": score,
        "max_score": 100,
        "reason": reason,
        "risk_mode": risk_mode,
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl_sec)).isoformat(),
        "ttl_sec": ttl_sec,
        "live_allowed": False,
        "cvdoi": cvdoi,
        "cvd": cvd,
        "btc": btc,
        "smc": smc,
        "trigger": trigger,
        "grades": grades,
        "f3": {
            "f3z_class": f3z_class,
            "latest_state": latest_state,
            "guarded_watch_status": guarded,
            "freshness_state": freshness,
            "final_allow": final_allow,
            "gate_allow": gate_allow,
            "score_allow": score_allow,
            "direction_opposite": dir_opposite,
        },
        "supports": supports,
        "blockers": blockers,
        "entry_blockers": entry_blockers,
        "hard_blockers": hard_blockers,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--skip-f3", action="store_true")
    ap.add_argument("--allow-f3-fail", action="store_true")
    ap.add_argument("--allow-missing-f3", action="store_true")
    ap.add_argument("--max-pairs", type=int, default=150)
    ap.add_argument("--fast-max-pairs", type=int, default=36)
    ap.add_argument("--min-turnover24h", type=float, default=1000000.0)
    ap.add_argument("--collector-sleep-sec", type=float, default=0.18)
    ap.add_argument("--candidate-limit", type=int, default=36)
    ap.add_argument("--cvd-trade-limit", type=int, default=200)
    ap.add_argument("--kline-limit", type=int, default=80)
    ap.add_argument("--http-sleep-sec", type=float, default=0.16)
    ap.add_argument("--signal-ttl-sec", type=int, default=300)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)
    now = utc_now_dt()

    f3_runs = maybe_run_f3(runtime, args)
    states = load_states(runtime)
    latest_flow = open_latest_flow(runtime)

    keys = build_candidate_keys(runtime, states, latest_flow, args.candidate_limit)

    f3d_idx = index_by_pair_side(
        safe_rows(states["f3d"], "flow_ready")
        + safe_rows(states["f3d"], "watch_confirm")
        + safe_rows(states["f3d"], "current_pairlist_scored")
        + safe_rows(states["f3d"], "weak")
    )
    f3f_idx = index_by_pair_side(safe_rows(states["f3f"], "reports"))
    f3gb_idx = index_by_pair_side(safe_rows(states["f3g_b"], "guarded_records"))
    f3z_idx = index_by_pair_side(safe_rows(states["f3z"], "candidates"))

    unique_pairs = sorted({k.split("|", 1)[0] for k in keys})

    cvd_by_pair = {}
    for pair in unique_pairs:
        cvd_by_pair[pair] = fetch_true_cvd_for_pair(pair, args.cvd_trade_limit, args.http_sleep_sec)

    kline_by_pair = {}
    for pair in unique_pairs:
        kline_by_pair[pair] = fetch_klines(pair, "5", args.kline_limit, args.http_sleep_sec)

    signals = []
    data_quality_rows = []

    for k in keys:
        pair, side = k.split("|", 1)
        flow = latest_flow.get(pair, {})
        cvd = cvd_by_pair.get(pair, {"cvd_status": "MISSING", "cvd_label": "CVD_MISSING"})
        cdoi = cvdoi_label(side, flow, cvd)
        btc = btc_guard(side, latest_flow, runtime)
        f3f = f3f_idx.get(k, {})
        f3gb = f3gb_idx.get(k, {})
        smc = smc_score(side, f3f, f3gb)
        trigger = trigger_for_side(pair, side, kline_by_pair.get(pair, {"status": "ERROR", "candles": []}))
        grades = legacy_shadow_grade(side, f3d_idx.get(k, {}), f3f, f3z_idx.get(k, {}))

        sig = final_decision_for_candidate(
            key=k,
            flow=flow,
            cvd=cvd,
            cvdoi=cdoi,
            btc=btc,
            smc=smc,
            trigger=trigger,
            grades=grades,
            f3z=f3z_idx.get(k, {}),
            f3f=f3f,
            f3g_b=f3gb,
            now=now,
            ttl_sec=args.signal_ttl_sec,
        )
        signals.append(sig)

        data_quality_rows.append({
            "pair": pair,
            "side": side,
            "oi_present": int(bool(flow)),
            "oi_15m": flow.get("oi_15m_delta_pct"),
            "oi_5m": flow.get("oi_5m_delta_pct"),
            "funding_rate": flow.get("funding_rate"),
            "cvd_status": cvd.get("cvd_status"),
            "cvd_source": cvd.get("cvd_source"),
            "cvd_trade_count": cvd.get("trade_count"),
            "kline_status": kline_by_pair.get(pair, {}).get("status"),
            "kline_count": len(kline_by_pair.get(pair, {}).get("candles", [])),
            "gate_freshness": f3gb.get("freshness_state"),
            "latest_gate_age_min": f3gb.get("latest_gate_age_min"),
        })

    action_counts = Counter(s["paper_action"] for s in signals)
    grade_counts = Counter(s["final_grade"] for s in signals)
    cvdoi_counts = Counter(s["cvdoi"]["cvdoi_label"] for s in signals)
    cvd_counts = Counter(s["cvd"].get("cvd_status") for s in signals)
    trigger_counts = Counter(s["trigger"].get("trigger_status") for s in signals)
    smc_counts = Counter(s["smc"].get("smc_grade") for s in signals)
    blocker_counts = Counter(b for s in signals for b in s["blockers"])
    support_counts = Counter(sup for s in signals for sup in s["supports"])

    allow_entries = [s for s in signals if s["paper_action"] == "ALLOW_PAPER_ENTRY"]
    watch_only = [s for s in signals if s["paper_action"] == "WATCH_ONLY"]
    recheck = [s for s in signals if s["paper_action"] == "RECHECK"]
    deny = [s for s in signals if s["paper_action"] == "DENY"]

    if allow_entries:
        final_decision = "F4X_ALLOW_PAPER_ENTRY_EXISTS"
    elif watch_only:
        final_decision = "F4X_WATCH_ONLY_EXISTS_NO_ENTRY"
    elif recheck:
        final_decision = "F4X_RECHECK_REQUIRED_NO_ENTRY"
    else:
        final_decision = "F4X_NO_PAPER_ENTRY"

    payload = {
        "event": "F4X_FULL_CONFLUENCE_PAPER_ENGINE",
        "generated_at": now.isoformat(),
        "runtime_dir": str(runtime),
        "final_decision": final_decision,
        "paper_mode_only": True,
        "live_allowed": False,
        "candidate_count": len(keys),
        "allow_entry_count": len(allow_entries),
        "watch_only_count": len(watch_only),
        "recheck_count": len(recheck),
        "deny_count": len(deny),
        "action_counts": action_counts.most_common(),
        "grade_counts": grade_counts.most_common(),
        "cvdoi_counts": cvdoi_counts.most_common(),
        "cvd_counts": cvd_counts.most_common(),
        "trigger_counts": trigger_counts.most_common(),
        "smc_counts": smc_counts.most_common(),
        "support_counts": support_counts.most_common(),
        "blocker_counts": blocker_counts.most_common(),
        "f3_runs": f3_runs,
        "allow_entries": allow_entries,
        "watch_only": watch_only,
        "recheck": recheck,
        "deny": deny,
        "signals": signals,
        "data_quality": data_quality_rows,
        "policy": {
            "paper_entry_allowed_only_when": [
                "F4X confluence has no hard blocker",
                "True CVD is present and not contra",
                "CVDOI aligns with side",
                "BTC guard is not hard contra",
                "SMC is not reject",
                "Trigger confirmed or strong enough",
                "Fresh gate or entry-ready state exists",
                "Live remains disabled",
            ],
            "live_allowed": False,
        },
    }

    out_full = runtime / "F4X_FULL_CONFLUENCE_FINAL_FULL.json"
    out_signals = runtime / "F4X_PAPER_DECISION_SIGNALS.json"
    out_compact = runtime / "F4X_EXTENDED_CONFLUENCE_FINAL_COMPACT.txt"
    out_quality = runtime / "F4X_DATA_QUALITY_COMPACT.txt"
    out_root_compact = Path("F4X_EXTENDED_CONFLUENCE_FINAL_COMPACT.txt")

    write_json(out_full, payload)
    write_json(out_signals, {
        "event": "F4X_PAPER_DECISION_SIGNALS",
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=args.signal_ttl_sec)).isoformat(),
        "ttl_sec": args.signal_ttl_sec,
        "paper_mode_only": True,
        "live_allowed": False,
        "final_decision": final_decision,
        "signals": signals,
        "allow_entries": allow_entries,
        "watch_only": watch_only,
        "recheck": recheck,
        "deny": deny,
    })

    lines = []
    lines.append("F4X_EXTENDED_CONFLUENCE_FINAL_COMPACT")
    lines.append(f"generated_at={now.isoformat()}")
    lines.append(f"runtime_dir={runtime}")
    lines.append("mode=FULL_CONFLUENCE_PAPER_ENGINE")
    lines.append("paper_mode_only=True")
    lines.append("live_allowed=False")
    lines.append("risk_change=NONE")
    lines.append("gate_loosen=NONE")
    lines.append("")
    lines.append("FINAL_DECISION")
    lines.append(f"final_decision={final_decision}")
    lines.append("")
    lines.append("COUNTS")
    lines.append(f"candidate_count={len(keys)}")
    lines.append(f"allow_entry_count={len(allow_entries)}")
    lines.append(f"watch_only_count={len(watch_only)}")
    lines.append(f"recheck_count={len(recheck)}")
    lines.append(f"deny_count={len(deny)}")
    lines.append("")
    lines.append("ACTION_COUNTS")
    for k, v in action_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("GRADE_COUNTS")
    for k, v in grade_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("CVDOI_COUNTS")
    for k, v in cvdoi_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("CVD_COUNTS")
    for k, v in cvd_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("TRIGGER_COUNTS")
    for k, v in trigger_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("SMC_COUNTS")
    for k, v in smc_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("TOP_SUPPORTS")
    for k, v in support_counts.most_common(30):
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("TOP_BLOCKERS")
    for k, v in blocker_counts.most_common(40):
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("ALLOW_PAPER_ENTRY")
    for s in allow_entries:
        lines.append(
            f"{s['pair']}|side={s['side']}|grade={s['final_grade']}|score={s['score']}/{s['max_score']}|"
            f"reason={s['reason']}|risk={s['risk_mode']}|cvdoi={s['cvdoi']['cvdoi_label']}|"
            f"cvd={s['cvd'].get('cvd_label')}|trigger={s['trigger'].get('trigger_status')}|"
            f"smc={s['smc'].get('smc_grade')}|btc={s['btc'].get('btc_guard')}"
        )
    lines.append("")
    lines.append("WATCH_ONLY")
    for s in watch_only[:40]:
        lines.append(
            f"{s['pair']}|side={s['side']}|grade={s['final_grade']}|score={s['score']}|"
            f"reason={s['reason']}|cvdoi={s['cvdoi']['cvdoi_label']}|trigger={s['trigger'].get('trigger_status')}|"
            f"smc={s['smc'].get('smc_grade')}|blockers={s['blockers']}"
        )
    lines.append("")
    lines.append("RECHECK")
    for s in recheck[:40]:
        lines.append(
            f"{s['pair']}|side={s['side']}|grade={s['final_grade']}|score={s['score']}|"
            f"reason={s['reason']}|blockers={s['blockers']}"
        )
    lines.append("")
    lines.append("DENY")
    for s in deny[:60]:
        lines.append(
            f"{s['pair']}|side={s['side']}|grade={s['final_grade']}|score={s['score']}|"
            f"reason={s['reason']}|cvdoi={s['cvdoi']['cvdoi_label']}|"
            f"cvd={s['cvd'].get('cvd_label')}|trigger={s['trigger'].get('trigger_status')}|"
            f"smc={s['smc'].get('smc_grade')}|blockers={s['blockers']}"
        )
    lines.append("")
    lines.append("PAPER_POLICY")
    lines.append("ALLOW_PAPER_ENTRY may be consumed only by paper bot bridge.")
    lines.append("WATCH_ONLY is not entry.")
    lines.append("RECHECK is not entry.")
    lines.append("DENY is not entry.")
    lines.append("LIVE remains disabled.")
    lines.append("")
    lines.append("OUTPUT_FILES")
    lines.append(f"full_json={out_full}")
    lines.append(f"signals={out_signals}")
    lines.append(f"compact={out_compact}")
    lines.append(f"data_quality={out_quality}")

    compact_text = "\n".join(lines) + "\n"
    out_compact.write_text(compact_text, encoding="utf-8")
    out_root_compact.write_text(compact_text, encoding="utf-8")

    q = []
    q.append("F4X_DATA_QUALITY_COMPACT")
    q.append(f"generated_at={now.isoformat()}")
    q.append(f"candidate_count={len(keys)}")
    q.append("")
    q.append("DATA_QUALITY_COUNTS")
    q.append(f"oi_present={sum(x['oi_present'] for x in data_quality_rows)}")
    q.append(f"cvd_ok={sum(1 for x in data_quality_rows if x['cvd_status'] == 'OK')}")
    q.append(f"cvd_error={sum(1 for x in data_quality_rows if x['cvd_status'] != 'OK')}")
    q.append(f"kline_ok={sum(1 for x in data_quality_rows if x['kline_status'] == 'OK')}")
    q.append(f"kline_error={sum(1 for x in data_quality_rows if x['kline_status'] != 'OK')}")
    q.append("")
    q.append("DETAIL")
    for x in data_quality_rows:
        q.append(
            f"{x['pair']}|side={x['side']}|oi={x['oi_present']}|oi15={x['oi_15m']}|oi5={x['oi_5m']}|"
            f"funding={x['funding_rate']}|cvd={x['cvd_status']}|cvd_count={x['cvd_trade_count']}|"
            f"kline={x['kline_status']}|kline_count={x['kline_count']}|gate_fresh={x['gate_freshness']}|gate_age={x['latest_gate_age_min']}"
        )
    out_quality.write_text("\n".join(q) + "\n", encoding="utf-8")

    print(compact_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
