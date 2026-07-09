#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from pathlib import Path


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def age_sec(path: Path) -> float:
    return time.time() - path.stat().st_mtime


def rows_from_flow(data):
    if isinstance(data, dict):
        return [v for v in data.values() if isinstance(v, dict)]
    if isinstance(data, list):
        return [v for v in data if isinstance(v, dict)]
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", required=True)
    ap.add_argument("--max-age-sec", type=float, default=420.0)
    ap.add_argument("--expected-top-n", type=int, default=None)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    expected_top_n = args.expected_top_n
    if expected_top_n is None:
        expected_top_n = int(os.environ.get("REVO_TOP_UNIVERSE_LIMIT", "100") or "100")

    min_rows = expected_top_n if expected_top_n <= 100 else 100
    max_rows = expected_top_n

    required_files = [
        "revo_flow_context.json",
        "revo_execution_context.json",
        "pair_universe_remote.json",
    ]
    optional_files = [
        "BTC_MODE_ROUTER_COMPACT.txt",
        "WATCHLIST_HEARTBEAT_COMPACT.txt",
        "revo_flow_context_canonical.json",
    ]

    failures = []
    warnings = []

    print("F2C_BYBIT_SCANNER_FRESHNESS_AUDIT")
    print(f"runtime={runtime}")
    print(f"max_age_sec={float(args.max_age_sec)}")
    print(f"expected_top_n={expected_top_n}")
    print(f"expected_flow_rows_min={min_rows}")
    print(f"expected_flow_rows_max={max_rows}")

    for name in required_files + optional_files:
        p = runtime / name
        if not p.exists():
            print(f"{name}: exists=False age_sec=NA size=0")
            if name in required_files:
                failures.append(f"MISSING:{name}")
            continue
        a = age_sec(p)
        print(f"{name}: exists=True age_sec={a:.1f} size={p.stat().st_size}")
        if name in required_files and a > float(args.max_age_sec):
            failures.append(f"STALE:{name}:{a:.1f}")

    flow_rows = 0
    flow_ready = 0
    entry_eligible = 0
    data_quality_counts = Counter()

    flow_p = runtime / "revo_flow_context.json"
    if flow_p.exists():
        try:
            flow = load_json(flow_p)
            rows = rows_from_flow(flow)
            flow_rows = len(rows)
            flow_ready = sum(1 for r in rows if r.get("flow_ready") is True)
            entry_eligible = sum(1 for r in rows if str(r.get("flow_authority")) == "ENTRY_ELIGIBLE")
            data_quality_counts = Counter(str(r.get("data_quality", "UNKNOWN")) for r in rows)
        except Exception as e:
            failures.append(f"FLOW_READ_ERROR:{e}")

    contract_status = None
    remote_pair_count = None
    execution_pair_count = None
    exec_p = runtime / "revo_execution_context.json"
    if exec_p.exists():
        try:
            ex = load_json(exec_p)
            if isinstance(ex, dict):
                contract_status = ex.get("contract_status")
                remote_pair_count = ex.get("remote_pair_count")
                execution_pair_count = ex.get("execution_pair_count")
        except Exception as e:
            failures.append(f"EXEC_READ_ERROR:{e}")

    published_pairs = 0
    current_actionable_count = None
    sticky_retained_count = None
    pair_p = runtime / "pair_universe_remote.json"
    if pair_p.exists():
        try:
            pr = load_json(pair_p)
            if isinstance(pr, dict):
                published_pairs = len(pr.get("pairs", []) or [])
                current_actionable_count = pr.get("current_actionable_count")
                sticky_retained_count = pr.get("sticky_retained_count")
        except Exception as e:
            failures.append(f"PAIRLIST_READ_ERROR:{e}")

    btc_mode = "UNKNOWN"
    scanner_mode = "UNKNOWN"
    btc_p = runtime / "btc_context_v135.json"
    if btc_p.exists():
        try:
            btc = load_json(btc_p)
            if isinstance(btc, dict):
                btc_mode = str(btc.get("btc_mode", "UNKNOWN"))
                scanner_mode = str(btc.get("scanner_mode", "UNKNOWN"))
                active_btc_mode = btc_mode.upper()
                active_scanner = scanner_mode.upper()
                if active_scanner == "DEFENSIVE_CHOP":
                    failures.append("F1I_FAIL_ACTIVE_SCANNER_DEFENSIVE_CHOP")
                if active_btc_mode == "BTC_CHOP" and active_scanner == "DEFENSIVE_CHOP":
                    failures.append("F1I_FAIL_ACTIVE_BTC_CHOP_DEFENSIVE_SCANNER")
        except Exception as e:
            failures.append(f"BTC_CONTEXT_READ_ERROR:{e}")

    compact_p = runtime / "BTC_MODE_ROUTER_COMPACT.txt"
    if compact_p.exists():
        txt = compact_p.read_text(encoding="utf-8", errors="replace")
        if "scanner=DEFENSIVE_CHOP" in txt or "scanner_mode=DEFENSIVE_CHOP" in txt:
            failures.append("F1I_FAIL_ACTIVE_DEFENSIVE_CHOP_IN_COMPACT")

    print(f"flow_rows={flow_rows}")
    print(f"flow_ready={flow_ready}")
    print(f"entry_eligible={entry_eligible}")
    print(f"data_quality_counts={dict(data_quality_counts)}")
    print(f"contract_status={contract_status}")
    print(f"remote_pair_count={remote_pair_count}")
    print(f"execution_pair_count={execution_pair_count}")
    print(f"published_pairs={published_pairs}")
    print(f"current_actionable_count={current_actionable_count}")
    print(f"sticky_retained_count={sticky_retained_count}")
    print(f"btc_mode={btc_mode}")
    print(f"scanner_mode={scanner_mode}")

    if flow_rows < min_rows or flow_rows > max_rows:
        failures.append(
            f"FLOW_ROWS_OUT_OF_EXPECTED_RANGE:{flow_rows}:expected={expected_top_n}:min={min_rows}:max={max_rows}"
        )

    if flow_rows > 0:
        ready_ratio = flow_ready / flow_rows
        ok_count = data_quality_counts.get("OK", 0)
        ok_ratio = ok_count / flow_rows
        print(f"flow_ready_ratio={ready_ratio:.4f}")
        print(f"data_quality_ok_ratio={ok_ratio:.4f}")
        if ready_ratio < 0.95:
            failures.append(f"FLOW_READY_RATIO_LOW:{ready_ratio:.4f}")
        if ok_ratio < 0.90:
            failures.append(f"DATA_QUALITY_OK_RATIO_LOW:{ok_count}/{flow_rows}")

    if contract_status != "OK":
        failures.append(f"CONTRACT_STATUS_NOT_OK:{contract_status}")

    if published_pairs != current_actionable_count:
        warnings.append(f"PUBLISHED_NE_CURRENT_ACTIONABLE:{published_pairs}!={current_actionable_count}")

    print(f"warnings={len(warnings)}")
    for w in warnings:
        print(f"WARN:{w}")

    print(f"failures={len(failures)}")
    for f in failures:
        print(f"FAIL:{f}")

    if failures:
        print("F2C_BYBIT_SCANNER_FRESHNESS_FAIL")
        return 1

    print("F2C_BYBIT_SCANNER_FRESHNESS_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
