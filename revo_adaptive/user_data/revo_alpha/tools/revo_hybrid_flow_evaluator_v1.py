#!/usr/bin/env python3
"""
Revo Hybrid Flow Evaluator v1

Evaluates flow gate and proxy fallback for every pair in execution context,
after sticky publisher and real flow context are both available.

Input:
 - pair_universe_top100.json (298 pairs)
 - revo_flow_context.json (real CVD/OI/funding/volume zscore)
 - pair_universe_sticky_state.json (sticky retention)

Output:
 - pair_universe_hybrid.json (final remote pairlist for Freqtrade)

Env vars:
 REVO_HYBRID_TOP_N : max pairs to publish (default: 400)
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- Config ----------
CVD_FLOW_THRESHOLD = 0.4
OI_FLOW_THRESHOLD = 0.05
VOL_Z_MIN = 0.1
FUNDING_Z_SOFT_CAP = 2.0
MIN_VOL_Z_FALLBACK = 0.3
MIN_DISCOUNT_FALLBACK = -0.5
MIN_TURNOVER_FALLBACK = 1_000_000


def asfloat(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def evaluate_flow_real(flow_row: Dict[str, Any]) -> bool:
    """Real CVD/OI/vol_z/funding all pass using actual flow-context field names."""
    cvd = asfloat(flow_row.get("cvd_zscore_15m")) >= CVD_FLOW_THRESHOLD
    oi = asfloat(flow_row.get("oi_delta_pct_15m")) >= OI_FLOW_THRESHOLD
    vol_z = asfloat(flow_row.get("volume_zscore_15m")) >= VOL_Z_MIN
    funding = abs(asfloat(flow_row.get("funding_zscore"))) <= FUNDING_Z_SOFT_CAP
    return cvd and oi and vol_z and funding


def proxy_usable(static_row: Dict[str, Any], flow_row: Dict[str, Any]) -> bool:
    """Price/volume proxy when real flow absent.
    Uses volume_zscore from flow_context (not in static rows).
    Falls back to price-change proxy if no volume_zscore either."""
    try:
        # Get volume_z from flow if available, else simple proxy
        vol_z = asfloat(flow_row.get("volume_zscore_15m", 0)) >= MIN_VOL_Z_FALLBACK
        # Discount proxy: use negative price_change as rough oversold proxy
        pchg = asfloat(static_row.get("price_change_24h_pct", 0))
        discount = pchg < MIN_DISCOUNT_FALLBACK
        turnover = asfloat(static_row.get("quote_volume_24h", 0)) >= MIN_TURNOVER_FALLBACK
        return vol_z and turnover
    except Exception:
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Hybrid flow evaluator")
    parser.add_argument("--runtime-dir", required=True, help="Path to runtime directory")
    parser.add_argument("--top-n", type=int, default=400, help="Max pairs to publish")
    parser.add_argument("--audit-result", action="store_true", help="Add audit debug fields")
    args = parser.parse_args(argv)

    rt = Path(args.runtime_dir)
    top_n = args.top_n
    outlet = rt / "pair_universe_hybrid.json"
    audit_out = rt / "audit_hybrid_publish.json"

    # Load inputs
    top = json.loads((rt / "pair_universe_top100.json").read_text(encoding="utf-8"))
    sticky = json.loads((rt / "pair_universe_sticky_state.json").read_text(encoding="utf-8"))
    flow_ctx = json.loads((rt / "revo_flow_context.json").read_text(encoding="utf-8"))

    pairs_state = sticky.get("pairs", {})
    top_pairs = top.get("pairs", [])
    top_rows = top.get("rows", [])

    # Build hybrid list: sticky pairs first, then missing top100
    seen = set()
    hybrid: list[dict] = []

    for pair, prior in pairs_state.items():
        seen.add(pair)
        flow_row = flow_ctx.get(pair, {})
        static_row = next((r for r in top_rows if r.get("pair") == pair), {})
        perm = "FLOW_ELIGIBLE" if evaluate_flow_real(flow_row) else \
               "PROXY_CANDIDATE" if (proxy_usable(static_row, flow_row) and pair in top_pairs) else "NO_TRADE"
        hybrid.append({
            "pair": pair, "in_top": pair in top_pairs, "entry_permission": perm,
            "flow_data": flow_row, "static_data": static_row
        })

    for row in top_rows:
        pair = row.get("pair")
        if pair in seen:
            continue
        seen.add(pair)
        flow_row = flow_ctx.get(pair, {})
        perm = "FLOW_ELIGIBLE" if evaluate_flow_real(flow_row) else \
               "PROXY_CANDIDATE" if proxy_usable(row, flow_row) else "NO_TRADE"
        hybrid.append({
            "pair": pair, "in_top": True, "entry_permission": perm,
            "flow_data": flow_row, "static_data": row
        })

    # Audit stats
    by_perm = {}
    for h in hybrid:
        by_perm[h["entry_permission"]] = by_perm.get(h["entry_permission"], 0) + 1

    # Sort by abs_price_change, cap at top_n
    hybrid.sort(key=lambda h: asfloat(h["static_data"].get("abs_price_change_24h_pct", 0)), reverse=True)
    published = hybrid[:top_n]
    excluded = hybrid[top_n:]

    # Build output JSON
    out = {
        "generated_at": utc_now_iso(),
        "top_n": top_n,
        "scanner_mode": "HYBRID_FLOW_V1",
        "pairs": []
    }
    for h in published:
        rec = {
            "pair": h["pair"],
            "entry_permission": h["entry_permission"],
            "quote_volume_24h": h["static_data"].get("quote_volume_24h", 0),
            "abs_price_change_24h_pct": h["static_data"].get("abs_price_change_24h_pct", 0),
            "flow_direction": h["flow_data"].get("flow_direction", "NO_TRADE")
        }
        if args.audit_result:
            rec["_static"] = h["static_data"]
            rec["_flow"] = h["flow_data"]
        out["pairs"].append(rec)

    outlet.write_text(json.dumps(out, indent=2))
    audit_out.write_text(json.dumps({
        "published_count": len(published),
        "excluded_count": len(excluded),
        "by_permission": by_perm,
        "excluded_pairs": [h["pair"] for h in excluded]
    }, indent=2))

    # CLI report
    print(f"\n{'─' * 50}")
    print("HYBRID FLOW EVALUATOR v1 — RESULT")
    print(f"{'─' * 50}")
    print(f"Total pairs evaluated:  {len(hybrid)}")
    print(f"Published (top {top_n}): {len(published)}")
    print(f"Excluded (rank cap):    {len(excluded)}")
    print(f"{'─' * 50}")
    print("Permission breakdown:")
    for perm in ["FLOW_ELIGIBLE", "PROXY_CANDIDATE", "NO_TRADE"]:
        cnt = by_perm.get(perm, 0)
        bar = "█" * (cnt // 5)
        print(f"  {perm:22s} : {cnt:4d}  {bar}")
    print(f"{'─' * 50}")
    print(f"Output → {outlet}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
