#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
from typing import Any, Dict, List


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm(v: Any) -> str:
    if v is None:
        return "UNKNOWN"
    s = str(v).strip()
    return s if s else "UNKNOWN"


def key(pair: str, side: str) -> str:
    return f"{norm(pair).upper()}|{norm(side).upper()}"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        return {"_load_error": repr(e), "_path": str(path)}


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)

    i2_path = runtime / "F4X_I2_SIDE_AWARE_GATE_MAPPING_REPLAY_FULL.json"
    f4x_path = runtime / "F4X_PAPER_DECISION_SIGNALS.json"

    i2 = load_json(i2_path, {})
    f4x = load_json(f4x_path, {})

    i2_rows = i2.get("rows", []) if isinstance(i2, dict) else []
    f4x_rows = f4x.get("signals", []) if isinstance(f4x, dict) else []

    if not isinstance(i2_rows, list):
        i2_rows = []
    if not isinstance(f4x_rows, list):
        f4x_rows = []

    mapped = {}
    for r in i2_rows:
        if not isinstance(r, dict):
            continue
        mapped[key(r.get("pair"), r.get("side"))] = r

    rows: List[Dict[str, Any]] = []

    for s in f4x_rows:
        if not isinstance(s, dict):
            continue

        pair = norm(s.get("pair"))
        side = norm(s.get("side")).upper()
        m = mapped.get(key(pair, side), {})

        paper_action = norm(s.get("paper_action"))
        mapped_lane = norm(m.get("mapped_lane"))
        mapped_smc = norm(m.get("mapped_smc"))
        mapped_latest = norm(m.get("mapped_latest"))
        cvdoi_alignment = norm(m.get("cvdoi_alignment"))

        bridge_state = "BLOCKED"
        bridge_reason = "NO_ALLOW_PAPER_ENTRY"

        if paper_action == "ALLOW_PAPER_ENTRY":
            bridge_state = "ALLOW_FROM_F4X"
            bridge_reason = "F4X_ALLOW_PAPER_ENTRY"
        elif mapped_lane == "ENTRY_READY_REVIEW":
            bridge_state = "REVIEW_REQUIRED"
            bridge_reason = "ENTRY_READY_REVIEW_NOT_AUTO_ORDER"
        elif mapped_lane == "EXECUTION_WATCH":
            bridge_state = "WATCH_ONLY"
            bridge_reason = "EXECUTION_WATCH_NOT_ENTRY"
        elif mapped_lane == "WAIT_LOCATION":
            bridge_state = "WAIT_LOCATION"
            bridge_reason = "WAIT_LOCATION_NOT_ENTRY"
        elif mapped_lane == "DENY_HARD":
            bridge_state = "BLOCKED"
            bridge_reason = "DENY_HARD"
        elif paper_action in {"WATCH_ONLY", "RECHECK", "DENY"}:
            bridge_state = "BLOCKED"
            bridge_reason = f"F4X_{paper_action}_NOT_ENTRY"

        rows.append({
            "pair": pair,
            "side": side,
            "score": s.get("score", 0),
            "paper_action": paper_action,
            "mapped_lane": mapped_lane,
            "mapped_smc": mapped_smc,
            "mapped_latest": mapped_latest,
            "cvdoi_alignment": cvdoi_alignment,
            "strict_bridge_state": bridge_state,
            "strict_bridge_reason": bridge_reason,
            "live_allowed": False,
            "risk_change": "NONE",
            "gate_loosen": "NONE",
        })

    state_counts = Counter(r["strict_bridge_state"] for r in rows)
    lane_counts = Counter(r["mapped_lane"] for r in rows)

    payload = {
        "event": "F4X_J_SIDE_AWARE_MAPPING_SHADOW_CLASSIFIER",
        "generated_at": now_utc(),
        "runtime_dir": str(runtime),
        "source_i2": str(i2_path),
        "source_f4x": str(f4x_path),
        "row_count": len(rows),
        "strict_bridge_state_counts": state_counts.most_common(),
        "mapped_lane_counts": lane_counts.most_common(),
        "rows": rows,
        "paper_bridge": "RUNNING_SHADOW",
        "paper_order_execution": "STRICT_ALLOW_ONLY",
        "live": "HOLD",
        "risk_up": "HOLD",
        "gate_loosen": "HOLD",
    }

    out_full = runtime / "F4X_J_SIDE_AWARE_MAPPING_SHADOW_CLASSIFIER_FULL.json"
    out_compact = runtime / "F4X_J_SIDE_AWARE_MAPPING_SHADOW_CLASSIFIER_COMPACT.txt"
    write_json(out_full, payload)

    lines = []
    lines.append("F4X_J_SIDE_AWARE_MAPPING_SHADOW_CLASSIFIER_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append("paper_bridge=RUNNING_SHADOW")
    lines.append("paper_order_execution=STRICT_ALLOW_ONLY")
    lines.append("live=HOLD")
    lines.append("risk_up=HOLD")
    lines.append("gate_loosen=HOLD")
    lines.append("")
    lines.append("COUNTS")
    lines.append(f"row_count={len(rows)}")
    lines.append("")
    lines.append("STRICT_BRIDGE_STATE_COUNTS")
    for k, v in state_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("MAPPED_LANE_COUNTS")
    for k, v in lane_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("ROWS")
    for r in rows:
        lines.append(
            f"{r['pair']}|side={r['side']}|score={r['score']}|paper_action={r['paper_action']}|"
            f"mapped_lane={r['mapped_lane']}|mapped_smc={r['mapped_smc']}|mapped_latest={r['mapped_latest']}|"
            f"align={r['cvdoi_alignment']}|bridge_state={r['strict_bridge_state']}|reason={r['strict_bridge_reason']}"
        )

    out_compact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
