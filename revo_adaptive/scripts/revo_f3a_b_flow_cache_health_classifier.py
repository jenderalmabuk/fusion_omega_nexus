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


def is_present(v: Any) -> bool:
    return v is not None


def as_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None:
            return default
        return float(v)
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


def classify_row(row: sqlite3.Row) -> Dict[str, Any]:
    pair = row["pair"]
    symbol = row["symbol"]
    fast_pool = int(row["fast_pool"] or 0)
    raw_status = row["data_status"]

    base_required = {
        "oi_1h_delta_pct": row["oi_1h_delta_pct"],
        "price_1h_delta_pct": row["price_1h_delta_pct"],
        "oi_15m_delta_pct": row["oi_15m_delta_pct"],
        "price_15m_delta_pct": row["price_15m_delta_pct"],
    }

    fast_required = {
        "oi_5m_delta_pct": row["oi_5m_delta_pct"],
        "price_5m_delta_pct": row["price_5m_delta_pct"],
    }

    optional_1m = {
        "oi_1m_delta_pct": row["oi_1m_delta_pct"],
        "price_1m_delta_pct": row["price_1m_delta_pct"],
    }

    base_missing = [k for k, v in base_required.items() if not is_present(v)]
    fast_missing = [k for k, v in fast_required.items() if fast_pool and not is_present(v)]
    optional_1m_missing = [k for k, v in optional_1m.items() if fast_pool and not is_present(v)]

    core_ready = len(base_missing) == 0
    fast_ready = fast_pool == 1 and core_ready and len(fast_missing) == 0

    if core_ready and fast_pool == 1 and fast_ready:
        health_class = "FAST_OK"
    elif core_ready and fast_pool == 1 and not fast_ready:
        health_class = "FAST_PARTIAL"
    elif core_ready and fast_pool == 0:
        health_class = "CORE_OK"
    elif len(base_missing) < len(base_required):
        health_class = "CORE_PARTIAL"
    else:
        health_class = "CORE_FAIL"

    if fast_pool == 1 and optional_1m_missing:
        optional_1m_status = "OPTIONAL_1M_MISSING"
    elif fast_pool == 1:
        optional_1m_status = "OPTIONAL_1M_PRESENT"
    else:
        optional_1m_status = "OPTIONAL_1M_SKIPPED_NON_FAST_POOL"

    f3b_ready = health_class in {"FAST_OK", "CORE_OK"}

    reasons = []
    if base_missing:
        reasons.append("BASE_MISSING:" + ",".join(base_missing))
    if fast_missing:
        reasons.append("FAST_5M_MISSING:" + ",".join(fast_missing))
    if optional_1m_missing:
        reasons.append("OPTIONAL_ONLY_1M_MISSING:" + ",".join(optional_1m_missing))
    if not reasons:
        reasons.append("OK")

    return {
        "symbol": symbol,
        "pair": pair,
        "cycle_id": row["cycle_id"],
        "raw_status": raw_status,
        "fast_pool": fast_pool,
        "health_class": health_class,
        "core_ready": int(core_ready),
        "fast_ready": int(fast_ready),
        "f3b_ready": int(f3b_ready),
        "optional_1m_status": optional_1m_status,
        "base_missing": base_missing,
        "fast_missing": fast_missing,
        "optional_1m_missing": optional_1m_missing,
        "reasons": reasons,
        "turnover24h": as_float(row["turnover24h"], 0.0),
        "volume24h": as_float(row["volume24h"], 0.0),
        "last_price": as_float(row["last_price"], 0.0),
        "funding_rate": as_float(row["funding_rate"], 0.0),
        "oi_1h_delta_pct": as_float(row["oi_1h_delta_pct"]),
        "oi_15m_delta_pct": as_float(row["oi_15m_delta_pct"]),
        "oi_5m_delta_pct": as_float(row["oi_5m_delta_pct"]),
        "oi_1m_delta_pct": as_float(row["oi_1m_delta_pct"]),
        "price_1h_delta_pct": as_float(row["price_1h_delta_pct"]),
        "price_15m_delta_pct": as_float(row["price_15m_delta_pct"]),
        "price_5m_delta_pct": as_float(row["price_5m_delta_pct"]),
        "price_1m_delta_pct": as_float(row["price_1m_delta_pct"]),
        "missing_intervals_raw": row["missing_intervals"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--db-path", default="")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    db_path = Path(args.db_path) if args.db_path else runtime / "f3a_market_wide_flow_cache.sqlite"

    con = connect_db(db_path)
    meta = get_meta(con)
    last_cycle = meta.get("last_cycle_id", "")

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

    classified = [classify_row(r) for r in rows]

    raw_status_counts = Counter(x["raw_status"] for x in classified)
    health_counts = Counter(x["health_class"] for x in classified)
    optional_counts = Counter(x["optional_1m_status"] for x in classified)
    reason_counts = Counter(reason for x in classified for reason in x["reasons"])

    total = len(classified)
    core_ready_count = sum(x["core_ready"] for x in classified)
    fast_pool_count = sum(1 for x in classified if x["fast_pool"] == 1)
    fast_ready_count = sum(x["fast_ready"] for x in classified)
    f3b_ready_count = sum(x["f3b_ready"] for x in classified)
    optional_1m_missing_count = sum(1 for x in classified if x["optional_1m_status"] == "OPTIONAL_1M_MISSING")

    core_ready_ratio = round(core_ready_count / total, 4) if total else 0.0
    f3b_ready_ratio = round(f3b_ready_count / total, 4) if total else 0.0
    fast_ready_ratio = round(fast_ready_count / fast_pool_count, 4) if fast_pool_count else 0.0

    if total == 0:
        decision = "F3A_CACHE_EMPTY"
    elif core_ready_ratio >= 0.90 and fast_ready_ratio >= 0.70:
        decision = "F3A_CACHE_HEALTH_READY_FOR_F3B_WITH_1M_OPTIONAL"
    elif core_ready_ratio >= 0.80:
        decision = "F3A_CACHE_CORE_READY_FAST_PARTIAL"
    else:
        decision = "F3A_CACHE_NOT_READY_FIX_COLLECTION"

    top_oi_15m = sorted(
        [x for x in classified if x["oi_15m_delta_pct"] is not None],
        key=lambda x: x["oi_15m_delta_pct"],
        reverse=True,
    )[:30]

    top_fast_oi_5m = sorted(
        [x for x in classified if x["fast_pool"] == 1 and x["oi_5m_delta_pct"] is not None],
        key=lambda x: x["oi_5m_delta_pct"],
        reverse=True,
    )[:30]

    bad_rows = [x for x in classified if x["health_class"] in {"CORE_PARTIAL", "CORE_FAIL", "FAST_PARTIAL"}]
    non_fatal_1m_only = [
        x for x in classified
        if x["health_class"] == "FAST_OK" and x["optional_1m_status"] == "OPTIONAL_1M_MISSING"
    ]

    payload = {
        "event": "F3A_B_FLOW_CACHE_HEALTH_CLASSIFIER",
        "generated_at": utc_now(),
        "runtime_dir": str(runtime),
        "db_path": str(db_path),
        "last_cycle_id": last_cycle,
        "storage": "SQLITE_PRIMARY_READ_ONLY_AUDIT",
        "total_pairs": total,
        "fast_pool_count": fast_pool_count,
        "core_ready_count": core_ready_count,
        "fast_ready_count": fast_ready_count,
        "f3b_ready_count": f3b_ready_count,
        "optional_1m_missing_count": optional_1m_missing_count,
        "core_ready_ratio": core_ready_ratio,
        "fast_ready_ratio": fast_ready_ratio,
        "f3b_ready_ratio": f3b_ready_ratio,
        "raw_status_counts": raw_status_counts.most_common(),
        "health_counts": health_counts.most_common(),
        "optional_1m_counts": optional_counts.most_common(),
        "reason_counts": reason_counts.most_common(),
        "decision": decision,
        "classified": classified,
        "top_oi_15m": top_oi_15m,
        "top_fast_oi_5m": top_fast_oi_5m,
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
        "db_write_change": "NONE",
    }

    out_state = runtime / "revo_f3a_b_flow_cache_health_classifier_state.json"
    out_compact_runtime = runtime / "F3A_B_FLOW_CACHE_HEALTH_CLASSIFIER_COMPACT.txt"
    out_compact_root = Path("F3A_B_FLOW_CACHE_HEALTH_CLASSIFIER_COMPACT.txt")

    out_state.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = []
    lines.append("F3A_B_FLOW_CACHE_HEALTH_CLASSIFIER_COMPACT")
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
    lines.append("COUNTS")
    lines.append(f"total_pairs={total}")
    lines.append(f"fast_pool_count={fast_pool_count}")
    lines.append(f"core_ready_count={core_ready_count}")
    lines.append(f"fast_ready_count={fast_ready_count}")
    lines.append(f"f3b_ready_count={f3b_ready_count}")
    lines.append(f"optional_1m_missing_count={optional_1m_missing_count}")
    lines.append(f"core_ready_ratio={core_ready_ratio}")
    lines.append(f"fast_ready_ratio={fast_ready_ratio}")
    lines.append(f"f3b_ready_ratio={f3b_ready_ratio}")
    lines.append("")
    lines.append("RAW_STATUS_COUNTS_FROM_F3A")
    for k, v in raw_status_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("HEALTH_CLASS_COUNTS")
    for k, v in health_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("OPTIONAL_1M_COUNTS")
    for k, v in optional_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("REASON_COUNTS")
    for k, v in reason_counts.most_common(40):
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("TOP_OI_15M_EXPANSION_HEALTH_CLASSIFIED")
    for x in top_oi_15m[:20]:
        lines.append(
            f"{x['pair']}|health={x['health_class']}|fast={x['fast_pool']}|"
            f"oi1h={x['oi_1h_delta_pct']}|oi15={x['oi_15m_delta_pct']}|"
            f"oi5={x['oi_5m_delta_pct']}|oi1m={x['oi_1m_delta_pct']}|"
            f"price15={x['price_15m_delta_pct']}|optional={x['optional_1m_status']}"
        )
    lines.append("")
    lines.append("TOP_FAST_OI_5M_EXPANSION_HEALTH_CLASSIFIED")
    for x in top_fast_oi_5m[:20]:
        lines.append(
            f"{x['pair']}|health={x['health_class']}|"
            f"oi1h={x['oi_1h_delta_pct']}|oi15={x['oi_15m_delta_pct']}|"
            f"oi5={x['oi_5m_delta_pct']}|oi1m={x['oi_1m_delta_pct']}|"
            f"price5={x['price_5m_delta_pct']}|optional={x['optional_1m_status']}"
        )
    lines.append("")
    lines.append("BAD_OR_PARTIAL_ROWS_SAMPLE")
    for x in bad_rows[:40]:
        lines.append(
            f"{x['pair']}|health={x['health_class']}|fast={x['fast_pool']}|"
            f"reasons={';'.join(x['reasons'])}|raw_missing={x['missing_intervals_raw']}"
        )
    lines.append("")
    lines.append("NON_FATAL_1M_ONLY_SAMPLE")
    for x in non_fatal_1m_only[:40]:
        lines.append(
            f"{x['pair']}|health={x['health_class']}|optional={x['optional_1m_status']}|"
            f"oi1h={x['oi_1h_delta_pct']}|oi15={x['oi_15m_delta_pct']}|oi5={x['oi_5m_delta_pct']}"
        )
    lines.append("")
    lines.append("DECISION")
    lines.append(decision)
    lines.append("ONE_MINUTE_OI_MISSING_IS_OPTIONAL_NOT_FATAL")
    lines.append("NEXT_F3B_CAN_USE_1H_15M_AS_CORE_AND_5M_AS_FAST_CONFIRMATION")
    lines.append("DO_NOT_CONNECT_TO_ENTRY_GATE_YET")
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
