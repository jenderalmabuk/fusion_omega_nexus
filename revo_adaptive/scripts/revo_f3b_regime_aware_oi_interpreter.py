#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def as_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def is_num(v: Any) -> bool:
    return v is not None


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def connect_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"ERROR: DB missing: {db_path}")
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def get_meta(con: sqlite3.Connection) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        for r in con.execute("select key, value from meta"):
            out[str(r["key"])] = str(r["value"])
    except Exception:
        pass
    return out


def load_current_pairlist(runtime: Path) -> set[str]:
    p = runtime / "pair_universe_remote.json"
    data = load_json(p, {})
    pairs = data.get("pairs", []) if isinstance(data, dict) else []
    return set(str(x) for x in pairs)


def health_class(row: sqlite3.Row) -> str:
    core_ready = all(
        is_num(row[k])
        for k in ["oi_1h_delta_pct", "price_1h_delta_pct", "oi_15m_delta_pct", "price_15m_delta_pct"]
    )
    fast_pool = int(row["fast_pool"] or 0) == 1
    fast_ready = fast_pool and is_num(row["oi_5m_delta_pct"]) and is_num(row["price_5m_delta_pct"])

    if core_ready and fast_pool and fast_ready:
        return "FAST_OK"
    if core_ready and fast_pool and not fast_ready:
        return "FAST_PARTIAL"
    if core_ready and not fast_pool:
        return "CORE_OK"
    return "CORE_PARTIAL_OR_FAIL"


def sign_label(v: Optional[float], pos_th: float, neg_th: float) -> str:
    if v is None:
        return "MISSING"
    if v >= pos_th:
        return "UP"
    if v <= -abs(neg_th):
        return "DOWN"
    return "FLAT"


def base_oi_price_label(oi: Optional[float], price: Optional[float], oi_th: float, price_th: float) -> str:
    oi_s = sign_label(oi, oi_th, oi_th)
    px_s = sign_label(price, price_th, price_th)

    if oi_s == "UP" and px_s == "UP":
        return "OI_EXPANSION_PRICE_UP"
    if oi_s == "UP" and px_s == "DOWN":
        return "OI_EXPANSION_PRICE_DOWN"
    if oi_s == "DOWN" and px_s == "UP":
        return "OI_CONTRACTION_PRICE_UP"
    if oi_s == "DOWN" and px_s == "DOWN":
        return "OI_CONTRACTION_PRICE_DOWN"
    if oi_s == "UP" and px_s == "FLAT":
        return "OI_EXPANSION_PRICE_FLAT"
    if oi_s == "FLAT" and px_s == "UP":
        return "OI_FLAT_PRICE_UP"
    if oi_s == "FLAT" and px_s == "DOWN":
        return "OI_FLAT_PRICE_DOWN"
    if oi_s == "DOWN" and px_s == "FLAT":
        return "OI_CONTRACTION_PRICE_FLAT"
    return "OI_PRICE_NEUTRAL_OR_MISSING"


def interpret_row(
    row: sqlite3.Row,
    current_pairlist: set[str],
    oi_core_th: float,
    oi_fast_th: float,
    price_core_th: float,
    price_fast_th: float,
) -> Dict[str, Any]:
    pair = row["pair"]
    symbol = row["symbol"]
    fast_pool = int(row["fast_pool"] or 0)

    oi1h = as_float(row["oi_1h_delta_pct"])
    oi15 = as_float(row["oi_15m_delta_pct"])
    oi5 = as_float(row["oi_5m_delta_pct"])
    oi1m = as_float(row["oi_1m_delta_pct"])

    px1h = as_float(row["price_1h_delta_pct"])
    px15 = as_float(row["price_15m_delta_pct"])
    px5 = as_float(row["price_5m_delta_pct"])
    px1m = as_float(row["price_1m_delta_pct"])

    funding = as_float(row["funding_rate"], 0.0) or 0.0

    h = health_class(row)
    base_1h = base_oi_price_label(oi1h, px1h, oi_core_th, price_core_th)
    base_15m = base_oi_price_label(oi15, px15, oi_core_th, price_core_th)
    fast_5m = base_oi_price_label(oi5, px5, oi_fast_th, price_fast_th) if fast_pool else "FAST_NOT_REQUESTED"
    fast_1m = "OPTIONAL_1M_MISSING" if fast_pool and oi1m is None else base_oi_price_label(oi1m, px1m, oi_fast_th, price_fast_th)

    long_score = 0.0
    short_score = 0.0
    trap_score = 0.0
    notes: List[str] = []

    # Structural 1H.
    if base_1h == "OI_EXPANSION_PRICE_UP":
        long_score += 2.0
        notes.append("STRUCTURAL_LONG_PARTICIPATION")
    elif base_1h == "OI_EXPANSION_PRICE_DOWN":
        short_score += 1.5
        trap_score += 1.0
        notes.append("STRUCTURAL_SHORT_BUILD_OR_LONG_ABSORPTION")
    elif base_1h == "OI_CONTRACTION_PRICE_UP":
        long_score += 0.5
        trap_score += 0.5
        notes.append("STRUCTURAL_SHORT_COVER_WEAK_LONG")
    elif base_1h == "OI_CONTRACTION_PRICE_DOWN":
        short_score += 0.5
        trap_score += 0.5
        notes.append("STRUCTURAL_LONG_UNWIND_WEAK_SHORT")

    # Tactical 15M.
    if base_15m == "OI_EXPANSION_PRICE_UP":
        long_score += 3.0
        notes.append("TACTICAL_LONG_EXPANSION")
    elif base_15m == "OI_EXPANSION_PRICE_DOWN":
        short_score += 3.0
        trap_score += 1.0
        notes.append("TACTICAL_SHORT_BUILD_OR_LONG_TRAP")
    elif base_15m == "OI_EXPANSION_PRICE_FLAT":
        trap_score += 1.0
        notes.append("TACTICAL_OI_BUILD_PRICE_STALL")
    elif base_15m == "OI_CONTRACTION_PRICE_UP":
        long_score += 1.0
        trap_score += 1.0
        notes.append("TACTICAL_SHORT_COVER_NOT_FRESH_LONG")
    elif base_15m == "OI_CONTRACTION_PRICE_DOWN":
        short_score += 1.0
        trap_score += 1.0
        notes.append("TACTICAL_LONG_UNWIND_NOT_FRESH_SHORT")

    # Fast 5M, only for fast pool.
    if fast_pool:
        if fast_5m == "OI_EXPANSION_PRICE_UP":
            long_score += 2.0
            notes.append("FAST_LONG_ACCELERATION")
        elif fast_5m == "OI_EXPANSION_PRICE_DOWN":
            short_score += 2.0
            trap_score += 1.0
            notes.append("FAST_SHORT_BUILD_OR_LONG_TRAP")
        elif fast_5m == "OI_EXPANSION_PRICE_FLAT":
            trap_score += 1.0
            notes.append("FAST_OI_SPIKE_PRICE_STALL")
        elif fast_5m == "OI_CONTRACTION_PRICE_UP":
            long_score += 0.5
            trap_score += 1.0
            notes.append("FAST_SHORT_COVER_WEAK_LONG")
        elif fast_5m == "OI_CONTRACTION_PRICE_DOWN":
            short_score += 0.5
            trap_score += 1.0
            notes.append("FAST_LONG_UNWIND_WEAK_SHORT")

    # Funding soft notes only. Do not block in audit.
    if funding > 0.0005:
        notes.append("FUNDING_POSITIVE_CROWD_LONG_RISK")
        if long_score >= short_score:
            trap_score += 0.25
    elif funding < -0.0005:
        notes.append("FUNDING_NEGATIVE_CROWD_SHORT_RISK")
        if short_score > long_score:
            trap_score += 0.25

    # Regime-aware interpretations.
    if long_score >= 5.0 and long_score - short_score >= 2.0:
        trending_interpretation = "TRENDING_LONG_FLOW_OK"
    elif short_score >= 5.0 and short_score - long_score >= 2.0:
        trending_interpretation = "TRENDING_SHORT_FLOW_OK"
    elif max(long_score, short_score) >= 4.0 and abs(long_score - short_score) < 2.0:
        trending_interpretation = "TRENDING_MIXED_OR_TRAP"
    elif fast_pool and trap_score >= 2.0:
        trending_interpretation = "TRENDING_FAST_CONFLICT_OR_TRAP"
    else:
        trending_interpretation = "TRENDING_NO_CLEAR_FLOW"

    if base_15m in {"OI_EXPANSION_PRICE_FLAT", "OI_EXPANSION_PRICE_DOWN", "OI_EXPANSION_PRICE_UP"} and trap_score >= 1.5:
        ranging_interpretation = "RANGING_TRAP_OR_ABSORPTION_WARNING"
    elif base_15m == "OI_CONTRACTION_PRICE_UP":
        ranging_interpretation = "RANGING_WEAK_BOUNCE_SHORT_COVER"
    elif base_15m == "OI_CONTRACTION_PRICE_DOWN":
        ranging_interpretation = "RANGING_WEAK_DUMP_LONG_UNWIND"
    elif abs((px15 or 0.0)) < price_core_th and (oi15 or 0.0) > oi_core_th:
        ranging_interpretation = "RANGING_OI_BUILD_NO_PRICE_REACTION"
    else:
        ranging_interpretation = "RANGING_NEUTRAL_WAIT_LOCATION_REACTION"

    if trap_score >= 2.0:
        chop_interpretation = "CHOP_TRAP_WARNING_NO_ENTRY"
    elif base_15m == "OI_PRICE_NEUTRAL_OR_MISSING":
        chop_interpretation = "CHOP_LOW_SIGNAL"
    else:
        chop_interpretation = "CHOP_OBSERVE_ONLY"

    if "TRAP" in " ".join(notes) or trap_score >= 2.0:
        trap_interpretation = "TRAP_RISK_ELEVATED"
    elif trap_score >= 1.0:
        trap_interpretation = "TRAP_RISK_WATCH"
    else:
        trap_interpretation = "TRAP_RISK_LOW"

    # Unified classification for ranking.
    if trending_interpretation == "TRENDING_LONG_FLOW_OK":
        primary_bias = "LONG_FLOW"
    elif trending_interpretation == "TRENDING_SHORT_FLOW_OK":
        primary_bias = "SHORT_FLOW"
    elif trap_interpretation == "TRAP_RISK_ELEVATED":
        primary_bias = "TRAP_WARNING"
    elif long_score > short_score:
        primary_bias = "WEAK_LONG_FLOW"
    elif short_score > long_score:
        primary_bias = "WEAK_SHORT_FLOW"
    else:
        primary_bias = "NEUTRAL_FLOW"

    in_pairlist = pair in current_pairlist

    return {
        "symbol": symbol,
        "pair": pair,
        "cycle_id": row["cycle_id"],
        "health_class": h,
        "fast_pool": fast_pool,
        "in_current_pairlist": int(in_pairlist),
        "turnover24h": as_float(row["turnover24h"], 0.0),
        "funding_rate": funding,
        "oi_1h_delta_pct": oi1h,
        "oi_15m_delta_pct": oi15,
        "oi_5m_delta_pct": oi5,
        "oi_1m_delta_pct": oi1m,
        "price_1h_delta_pct": px1h,
        "price_15m_delta_pct": px15,
        "price_5m_delta_pct": px5,
        "price_1m_delta_pct": px1m,
        "base_1h": base_1h,
        "base_15m": base_15m,
        "fast_5m": fast_5m,
        "fast_1m": fast_1m,
        "long_score": round(long_score, 4),
        "short_score": round(short_score, 4),
        "trap_score": round(trap_score, 4),
        "primary_bias": primary_bias,
        "trending_interpretation": trending_interpretation,
        "ranging_interpretation": ranging_interpretation,
        "chop_interpretation": chop_interpretation,
        "trap_interpretation": trap_interpretation,
        "notes": notes,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--db-path", default="")
    ap.add_argument("--oi-core-th", type=float, default=0.10)
    ap.add_argument("--oi-fast-th", type=float, default=0.05)
    ap.add_argument("--price-core-th", type=float, default=0.05)
    ap.add_argument("--price-fast-th", type=float, default=0.03)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    db_path = Path(args.db_path) if args.db_path else runtime / "f3a_market_wide_flow_cache.sqlite"

    con = connect_db(db_path)
    meta = get_meta(con)
    last_cycle = meta.get("last_cycle_id", "")

    current_pairlist = load_current_pairlist(runtime)

    rows = list(con.execute("""
        select
          symbol, pair, cycle_id, ts, turnover24h, volume24h, last_price, funding_rate,
          oi_1h_delta_pct, price_1h_delta_pct, volume_1h_sum,
          oi_15m_delta_pct, price_15m_delta_pct, volume_15m_sum,
          oi_5m_delta_pct, price_5m_delta_pct, volume_5m_sum,
          oi_1m_delta_pct, price_1m_delta_pct, volume_1m_sum,
          data_status, missing_intervals, fast_pool
        from latest_flow
        order by turnover24h desc
    """))

    interpreted = [
        interpret_row(
            r,
            current_pairlist=current_pairlist,
            oi_core_th=args.oi_core_th,
            oi_fast_th=args.oi_fast_th,
            price_core_th=args.price_core_th,
            price_fast_th=args.price_fast_th,
        )
        for r in rows
    ]

    primary_counts = Counter(x["primary_bias"] for x in interpreted)
    trend_counts = Counter(x["trending_interpretation"] for x in interpreted)
    ranging_counts = Counter(x["ranging_interpretation"] for x in interpreted)
    chop_counts = Counter(x["chop_interpretation"] for x in interpreted)
    trap_counts = Counter(x["trap_interpretation"] for x in interpreted)
    health_counts = Counter(x["health_class"] for x in interpreted)

    long_candidates = sorted(
        [x for x in interpreted if x["primary_bias"] in {"LONG_FLOW", "WEAK_LONG_FLOW"}],
        key=lambda x: (x["long_score"] - x["trap_score"], x["oi_15m_delta_pct"] or -999),
        reverse=True,
    )[:30]

    short_candidates = sorted(
        [x for x in interpreted if x["primary_bias"] in {"SHORT_FLOW", "WEAK_SHORT_FLOW"}],
        key=lambda x: (x["short_score"] - x["trap_score"], x["oi_15m_delta_pct"] or -999),
        reverse=True,
    )[:30]

    trap_candidates = sorted(
        [x for x in interpreted if x["trap_interpretation"] in {"TRAP_RISK_ELEVATED", "TRAP_RISK_WATCH"}],
        key=lambda x: x["trap_score"],
        reverse=True,
    )[:40]

    fast_long = sorted(
        [x for x in interpreted if x["fast_pool"] and x["fast_5m"] == "OI_EXPANSION_PRICE_UP"],
        key=lambda x: x["oi_5m_delta_pct"] or -999,
        reverse=True,
    )[:30]

    fast_short_or_trap = sorted(
        [x for x in interpreted if x["fast_pool"] and x["fast_5m"] == "OI_EXPANSION_PRICE_DOWN"],
        key=lambda x: x["oi_5m_delta_pct"] or -999,
        reverse=True,
    )[:30]

    pairlist_overlay = [x for x in interpreted if x["in_current_pairlist"] == 1]

    if len(interpreted) == 0:
        decision = "F3B_NO_DATA"
    elif sum(1 for x in interpreted if x["health_class"] in {"CORE_OK", "FAST_OK"}) / len(interpreted) >= 0.90:
        decision = "F3B_REGIME_AWARE_OI_INTERPRETER_READY_FOR_F3C_EVENT_SNAPSHOT_AUDIT"
    else:
        decision = "F3B_DATA_HEALTH_INSUFFICIENT"

    payload = {
        "event": "F3B_REGIME_AWARE_OI_INTERPRETER",
        "generated_at": utc_now(),
        "runtime_dir": str(runtime),
        "db_path": str(db_path),
        "last_cycle_id": last_cycle,
        "thresholds": {
            "oi_core_th": args.oi_core_th,
            "oi_fast_th": args.oi_fast_th,
            "price_core_th": args.price_core_th,
            "price_fast_th": args.price_fast_th,
        },
        "total_pairs": len(interpreted),
        "current_pairlist_count": len(current_pairlist),
        "health_counts": health_counts.most_common(),
        "primary_counts": primary_counts.most_common(),
        "trend_counts": trend_counts.most_common(),
        "ranging_counts": ranging_counts.most_common(),
        "chop_counts": chop_counts.most_common(),
        "trap_counts": trap_counts.most_common(),
        "long_candidates": long_candidates,
        "short_candidates": short_candidates,
        "trap_candidates": trap_candidates,
        "fast_long": fast_long,
        "fast_short_or_trap": fast_short_or_trap,
        "pairlist_overlay": pairlist_overlay,
        "interpreted": interpreted,
        "decision": decision,
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
        "db_write_change": "NONE",
    }

    out_state = runtime / "revo_f3b_regime_aware_oi_interpreter_state.json"
    out_compact_runtime = runtime / "F3B_REGIME_AWARE_OI_INTERPRETER_COMPACT.txt"
    out_compact_root = Path("F3B_REGIME_AWARE_OI_INTERPRETER_COMPACT.txt")

    out_state.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = []
    lines.append("F3B_REGIME_AWARE_OI_INTERPRETER_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append(f"runtime_dir={runtime}")
    lines.append(f"db_path={db_path}")
    lines.append(f"last_cycle_id={last_cycle}")
    lines.append("storage=SQLITE_PRIMARY_READ_ONLY_AUDIT")
    lines.append("behavior_change=NONE")
    lines.append("entry_gate_change=NONE")
    lines.append("risk_change=NONE")
    lines.append("db_write_change=NONE")
    lines.append("")
    lines.append("CONFIG")
    lines.append(f"oi_core_th={args.oi_core_th}")
    lines.append(f"oi_fast_th={args.oi_fast_th}")
    lines.append(f"price_core_th={args.price_core_th}")
    lines.append(f"price_fast_th={args.price_fast_th}")
    lines.append("1m_oi=OPTIONAL_IGNORED_IF_MISSING")
    lines.append("")
    lines.append("COUNTS")
    lines.append(f"total_pairs={len(interpreted)}")
    lines.append(f"current_pairlist_count={len(current_pairlist)}")
    lines.append("")
    lines.append("HEALTH_COUNTS")
    for k, v in health_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("PRIMARY_BIAS_COUNTS")
    for k, v in primary_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("TRENDING_INTERPRETATION_COUNTS")
    for k, v in trend_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("RANGING_INTERPRETATION_COUNTS")
    for k, v in ranging_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("CHOP_INTERPRETATION_COUNTS")
    for k, v in chop_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("TRAP_INTERPRETATION_COUNTS")
    for k, v in trap_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("TOP_TRENDING_LONG_CANDIDATES_AUDIT")
    for x in long_candidates[:20]:
        lines.append(
            f"{x['pair']}|bias={x['primary_bias']}|trend={x['trending_interpretation']}|"
            f"long={x['long_score']}|short={x['short_score']}|trap={x['trap_score']}|"
            f"oi1h={x['oi_1h_delta_pct']}|oi15={x['oi_15m_delta_pct']}|oi5={x['oi_5m_delta_pct']}|"
            f"px15={x['price_15m_delta_pct']}|px5={x['price_5m_delta_pct']}|"
            f"health={x['health_class']}|pairlist={x['in_current_pairlist']}|notes={','.join(x['notes'])}"
        )
    lines.append("")
    lines.append("TOP_TRENDING_SHORT_CANDIDATES_AUDIT")
    for x in short_candidates[:20]:
        lines.append(
            f"{x['pair']}|bias={x['primary_bias']}|trend={x['trending_interpretation']}|"
            f"long={x['long_score']}|short={x['short_score']}|trap={x['trap_score']}|"
            f"oi1h={x['oi_1h_delta_pct']}|oi15={x['oi_15m_delta_pct']}|oi5={x['oi_5m_delta_pct']}|"
            f"px15={x['price_15m_delta_pct']}|px5={x['price_5m_delta_pct']}|"
            f"health={x['health_class']}|pairlist={x['in_current_pairlist']}|notes={','.join(x['notes'])}"
        )
    lines.append("")
    lines.append("TOP_TRAP_WARNING_CANDIDATES_AUDIT")
    for x in trap_candidates[:25]:
        lines.append(
            f"{x['pair']}|trap={x['trap_score']}|bias={x['primary_bias']}|"
            f"trend={x['trending_interpretation']}|ranging={x['ranging_interpretation']}|"
            f"oi15={x['oi_15m_delta_pct']}|px15={x['price_15m_delta_pct']}|"
            f"oi5={x['oi_5m_delta_pct']}|px5={x['price_5m_delta_pct']}|notes={','.join(x['notes'])}"
        )
    lines.append("")
    lines.append("FAST_LONG_ACCELERATION_AUDIT")
    for x in fast_long[:20]:
        lines.append(
            f"{x['pair']}|oi5={x['oi_5m_delta_pct']}|px5={x['price_5m_delta_pct']}|"
            f"oi15={x['oi_15m_delta_pct']}|px15={x['price_15m_delta_pct']}|"
            f"bias={x['primary_bias']}|trend={x['trending_interpretation']}|notes={','.join(x['notes'])}"
        )
    lines.append("")
    lines.append("FAST_SHORT_OR_LONG_TRAP_AUDIT")
    for x in fast_short_or_trap[:20]:
        lines.append(
            f"{x['pair']}|oi5={x['oi_5m_delta_pct']}|px5={x['price_5m_delta_pct']}|"
            f"oi15={x['oi_15m_delta_pct']}|px15={x['price_15m_delta_pct']}|"
            f"bias={x['primary_bias']}|trend={x['trending_interpretation']}|trap={x['trap_score']}|notes={','.join(x['notes'])}"
        )
    lines.append("")
    lines.append("CURRENT_PAIRLIST_OVERLAY_AUDIT")
    for x in pairlist_overlay[:40]:
        lines.append(
            f"{x['pair']}|bias={x['primary_bias']}|trend={x['trending_interpretation']}|"
            f"ranging={x['ranging_interpretation']}|trap={x['trap_interpretation']}|"
            f"oi1h={x['oi_1h_delta_pct']}|oi15={x['oi_15m_delta_pct']}|oi5={x['oi_5m_delta_pct']}|"
            f"px15={x['price_15m_delta_pct']}|px5={x['price_5m_delta_pct']}|health={x['health_class']}"
        )
    lines.append("")
    lines.append("REGIME_POLICY_AUDIT_ONLY")
    lines.append("TRENDING: use 1H/15M as core; require 5M confirmation only for fast_pool; 1M optional.")
    lines.append("RANGING: use 1H/15M as core; 5M optional confirmation; avoid 1M dependency.")
    lines.append("CHOP: use OI expansion with price stall/mismatch as trap warning, not entry trigger.")
    lines.append("TRAP_RISK: fast OI + price mismatch is veto candidate, not promotion.")
    lines.append("")
    lines.append("DECISION")
    lines.append(decision)
    lines.append("DO_NOT_CONNECT_TO_ENTRY_GATE_YET")
    lines.append("NEXT_F3C_SHOULD_CREATE_EVENT_ALIGNED_FLOW_SNAPSHOT_FROM_SQLITE_CACHE")
    lines.append("")
    lines.append("OUTPUT_FILES")
    lines.append(f"state={out_state}")
    lines.append(f"compact_runtime={out_compact_runtime}")
    lines.append(f"compact_root={out_compact_root}")

    text = "\n".join(lines) + "\n"
    out_compact_runtime.write_text(text, encoding="utf-8")
    out_compact_root.write_text(text, encoding="utf-8")
    print(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
