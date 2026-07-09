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


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        return {"_load_error": repr(e), "_path": str(path)}


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    j_path = runtime / "F4X_J_SIDE_AWARE_MAPPING_SHADOW_CLASSIFIER_FULL.json"
    j = load_json(j_path, {})

    rows = j.get("rows", []) if isinstance(j, dict) else []
    if not isinstance(rows, list):
        rows = []

    generated_at = now_utc()
    intents: List[Dict[str, Any]] = []

    for r in rows:
        if not isinstance(r, dict):
            continue

        pair = norm(r.get("pair"))
        side = norm(r.get("side")).upper()
        paper_action = norm(r.get("paper_action"))
        bridge_state = norm(r.get("strict_bridge_state"))
        mapped_lane = norm(r.get("mapped_lane"))

        intent_action = "NO_ORDER"
        intent_reason = norm(r.get("strict_bridge_reason"))

        if paper_action == "ALLOW_PAPER_ENTRY" and bridge_state == "ALLOW_FROM_F4X":
            intent_action = "WOULD_ORDER"
            intent_reason = "ALLOW_PAPER_ENTRY_STRICT"

        intents.append({
            "event": "F4X_K_PAPER_BRIDGE_INTENT",
            "generated_at": generated_at,
            "pair": pair,
            "side": side,
            "intent_action": intent_action,
            "intent_reason": intent_reason,
            "paper_action": paper_action,
            "bridge_state": bridge_state,
            "mapped_lane": mapped_lane,
            "mapped_smc": norm(r.get("mapped_smc")),
            "mapped_latest": norm(r.get("mapped_latest")),
            "cvdoi_alignment": norm(r.get("cvdoi_alignment")),
            "score": r.get("score", 0),
            "live_allowed": False,
            "risk_change": "NONE",
            "gate_loosen": "NONE",
        })

    counts = Counter(x["intent_action"] for x in intents)
    would_orders = [x for x in intents if x["intent_action"] == "WOULD_ORDER"]

    out_full = runtime / "F4X_K_PAPER_BRIDGE_INTENTS_FULL.json"
    out_compact = runtime / "F4X_K_PAPER_BRIDGE_INTENTS_COMPACT.txt"
    out_jsonl = runtime / "F4X_K_PAPER_BRIDGE_INTENTS.jsonl"
    legacy_active_path = runtime / "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json"  # F4X_BA5G_NO_ACTIVE_K_WRITE_LEGACY_K_RUNNER_PATCH: reference only; do not write active K.
    shadow_active = runtime / "F4X_K_PAPER_BRIDGE_INTENT_RUNNER_SHADOW_ACTIVE_SIGNAL.json"

    payload = {
        "event": "F4X_K_PAPER_BRIDGE_INTENT_RUNNER",
        "generated_at": generated_at,
        "source_j": str(j_path),
        "intent_count": len(intents),
        "intent_action_counts": counts.most_common(),
        "intents": intents,
        "paper_bridge": "RUNNING",
        "paper_order_mode": "STRICT_ALLOW_ONLY",
        "live": "HOLD",
        "risk_up": "HOLD",
        "gate_loosen": "HOLD",
    }

    write_json(out_full, payload)

    with out_jsonl.open("a", encoding="utf-8") as f:
        for x in intents:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")

    active_payload = {
        "generated_at": generated_at,
        "has_order_intent": bool(would_orders),
        "order_intents": would_orders,
        "blocked_count": len([x for x in intents if x["intent_action"] == "NO_ORDER"]),
        "intent_count": len(intents),
        "paper_order_mode": "STRICT_ALLOW_ONLY",
        "live_allowed": False,
    }
    # F4X_BA5G_NO_ACTIVE_K_WRITE_LEGACY_K_RUNNER_PATCH: legacy K runner must not overwrite active K control state.
    # It writes shadow/report output only. Active K is owned by BA4B clean reset or later BA6B approved write.
    write_json(shadow_active, active_payload)

    lines = []
    lines.append("F4X_K_PAPER_BRIDGE_INTENTS_COMPACT")
    lines.append(f"generated_at={generated_at}")
    lines.append("paper_bridge=RUNNING")
    lines.append("paper_order_mode=STRICT_ALLOW_ONLY")
    lines.append("live=HOLD")
    lines.append("risk_up=HOLD")
    lines.append("gate_loosen=HOLD")
    lines.append("")
    lines.append("COUNTS")
    lines.append(f"intent_count={len(intents)}")
    for k, v in counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("WOULD_ORDER")
    for x in would_orders:
        lines.append(
            f"{x['pair']}|side={x['side']}|score={x['score']}|reason={x['intent_reason']}|"
            f"mapped_lane={x['mapped_lane']}|smc={x['mapped_smc']}|latest={x['mapped_latest']}"
        )
    lines.append("")
    lines.append("BLOCKED_SAMPLE")
    for x in intents[:60]:
        if x["intent_action"] == "NO_ORDER":
            lines.append(
                f"{x['pair']}|side={x['side']}|score={x['score']}|paper_action={x['paper_action']}|"
                f"mapped_lane={x['mapped_lane']}|reason={x['intent_reason']}"
            )

    out_compact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
