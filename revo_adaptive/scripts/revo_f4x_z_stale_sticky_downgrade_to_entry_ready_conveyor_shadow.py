#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def write_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def tmux_alive(name: str) -> bool:
    try:
        out = subprocess.check_output(["tmux", "list-sessions"], stderr=subprocess.DEVNULL).decode()
        return name in out
    except Exception:
        return False


def is_true(v):
    return bool(v) is True


def classify_near_entry(row):
    pair = row.get("pair", "UNKNOWN")
    side = row.get("side", "UNKNOWN")
    missing = row.get("missing_after_shadow", [])

    core_pass = (
        is_true(row.get("stale_flow_removed_shadow"))
        and is_true(row.get("side_aligned_strong"))
        and is_true(row.get("smc_good"))
        and is_true(row.get("trigger_confirmed"))
        and is_true(row.get("no_side_hard_after_shadow"))
    )

    only_missing_entry_permission = sorted(missing) == sorted(["latest_entry_ready", "paper_action_allow"])

    if core_pass and only_missing_entry_permission:
        shadow_lane = "ENTRY_READY_REVIEW_SHADOW"
        shadow_action = "PROMOTE_TO_ENTRY_READY_REVIEW_SHADOW_ONLY"
        reason = "STALE_STICKY_REMOVED_ALL_CORE_CHECKS_PASS_ONLY_ENTRY_PERMISSION_MISSING"
    elif core_pass:
        shadow_lane = "NEAR_ENTRY_REVIEW_SHADOW"
        shadow_action = "KEEP_REVIEW_SHADOW_ONLY"
        reason = "CORE_CHECKS_PASS_BUT_EXTRA_MISSING_FIELDS"
    else:
        shadow_lane = "NOT_ENTRY_READY_SHADOW"
        shadow_action = "KEEP_BLOCKED_OR_RECHECK"
        reason = "CORE_CHECKS_NOT_COMPLETE"

    return {
        "pair": pair,
        "side": side,
        "score": row.get("score"),
        "source_file": row.get("source_file"),
        "paper_action_before": row.get("paper_action"),
        "cvdoi": row.get("cvdoi"),
        "align": row.get("align"),
        "trigger": row.get("trigger"),
        "smc": row.get("smc"),
        "latest_before": row.get("latest_before"),
        "final_reason_before": row.get("final_reason_before"),
        "lane_before": row.get("lane_before"),
        "replay_lane": row.get("replay_lane"),
        "replay_decision": row.get("replay_decision"),
        "stale_flow_removed_shadow": row.get("stale_flow_removed_shadow"),
        "side_aligned_strong": row.get("side_aligned_strong"),
        "smc_good": row.get("smc_good"),
        "trigger_confirmed": row.get("trigger_confirmed"),
        "no_side_hard_after_shadow": row.get("no_side_hard_after_shadow"),
        "missing_after_shadow": missing,
        "shadow_lane": shadow_lane,
        "shadow_action": shadow_action,
        "shadow_reason": reason,
        "paper_order_allowed": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)

    y_active_path = runtime / "F4X_Y_STALE_STICKY_DOWNGRADE_EFFECT_REPLAY_ACTIVE.json"
    x_active_path = runtime / "F4X_X_STICKY_SOURCE_CYCLE_GUARD_SHADOW_ACTIVE.json"
    k_active_path = runtime / "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json"
    l_full_path = runtime / "F4X_L_PAPER_BRIDGE_EXECUTION_FULL.json"

    y_active = read_json(y_active_path, {})
    x_active = read_json(x_active_path, {})
    k_active = read_json(k_active_path, {})
    l_full = read_json(l_full_path, {})

    near_rows = y_active.get("near_entry_after_downgrade", []) if isinstance(y_active, dict) else []
    classified = [classify_near_entry(r) for r in near_rows if isinstance(r, dict)]

    lane_counts = Counter(r["shadow_lane"] for r in classified)
    action_counts = Counter(r["shadow_action"] for r in classified)

    entry_ready_review = [r for r in classified if r["shadow_lane"] == "ENTRY_READY_REVIEW_SHADOW"]
    near_review = [r for r in classified if r["shadow_lane"] == "NEAR_ENTRY_REVIEW_SHADOW"]
    blocked = [r for r in classified if r["shadow_lane"] == "NOT_ENTRY_READY_SHADOW"]

    if entry_ready_review:
        final_decision = "F4X_Z_ENTRY_READY_REVIEW_SHADOW_CREATED"
    elif near_review:
        final_decision = "F4X_Z_NEAR_ENTRY_REVIEW_SHADOW_ONLY"
    elif blocked:
        final_decision = "F4X_Z_NO_ENTRY_READY_AFTER_CLASSIFICATION"
    else:
        final_decision = "F4X_Z_NO_NEAR_ENTRY_INPUT"

    payload = {
        "event": "F4X_Z_STALE_STICKY_DOWNGRADE_TO_ENTRY_READY_CONVEYOR_SHADOW_ONLY",
        "generated_at": now_utc(),
        "mode": "STALE_STICKY_DOWNGRADE_TO_ENTRY_READY_CONVEYOR_SHADOW_ONLY",
        "final_decision": final_decision,
        "policy": {
            "paper_order": "HOLD",
            "live": "HOLD",
            "risk_up": "HOLD",
            "gate_loosen": "HOLD",
            "entry_from_watch_recheck_deny": "HOLD",
        },
        "counts": {
            "x_guard_rows": len(x_active.get("guard_rows", [])) if isinstance(x_active, dict) else 0,
            "y_near_entry_input_count": len(near_rows),
            "classified_count": len(classified),
            "entry_ready_review_shadow_count": len(entry_ready_review),
            "near_review_shadow_count": len(near_review),
            "blocked_shadow_count": len(blocked),
            "k_has_order_intent": bool(k_active.get("has_order_intent")) if isinstance(k_active, dict) else False,
            "l_decision": l_full.get("decision") if isinstance(l_full, dict) else "UNKNOWN",
        },
        "lane_counts": lane_counts.most_common(),
        "action_counts": action_counts.most_common(),
        "entry_ready_review_shadow": entry_ready_review,
        "near_review_shadow": near_review,
        "blocked_shadow": blocked,
        "all_rows": classified,
        "tmux_state": {
            "f4x_l_paper_execution_dryrun": tmux_alive("f4x_l_paper_execution_dryrun"),
            "f4x_x_sticky_cycle_guard_shadow": tmux_alive("f4x_x_sticky_cycle_guard_shadow"),
            "f4x_y_stale_sticky_replay": tmux_alive("f4x_y_stale_sticky_replay"),
            "f4x_o4_snapshot_guard": tmux_alive("f4x_o4_snapshot_guard"),
            "f4x_p_entry_blocker_conveyor": tmux_alive("f4x_p_entry_blocker_conveyor"),
            "f4x_q_latest_source_attribution": tmux_alive("f4x_q_latest_source_attribution"),
        },
    }

    full_path = runtime / "F4X_Z_STALE_STICKY_DOWNGRADE_TO_ENTRY_READY_CONVEYOR_SHADOW_FULL.json"
    compact_path = runtime / "F4X_Z_STALE_STICKY_DOWNGRADE_TO_ENTRY_READY_CONVEYOR_SHADOW_COMPACT.txt"
    active_path = runtime / "F4X_Z_STALE_STICKY_DOWNGRADE_TO_ENTRY_READY_CONVEYOR_SHADOW_ACTIVE.json"

    write_json(full_path, payload)
    write_json(active_path, {
        "generated_at": payload["generated_at"],
        "mode": payload["mode"],
        "active": True,
        "paper_order_allowed": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "final_decision": final_decision,
        "entry_ready_review_shadow_count": len(entry_ready_review),
        "entry_ready_review_shadow": entry_ready_review,
    })

    lines = []
    lines.append("F4X_Z_STALE_STICKY_DOWNGRADE_TO_ENTRY_READY_CONVEYOR_SHADOW_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append(f"mode={payload['mode']}")
    lines.append("paper_order=HOLD")
    lines.append("live=HOLD")
    lines.append("risk_up=HOLD")
    lines.append("gate_loosen=HOLD")
    lines.append("entry_from_watch_recheck_deny=HOLD")
    lines.append("")
    lines.append("FINAL_DECISION")
    lines.append(f"final_decision={final_decision}")
    lines.append("")
    lines.append("COUNTS")
    for k, v in payload["counts"].items():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("LANE_COUNTS")
    for k, v in payload["lane_counts"]:
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("ACTION_COUNTS")
    for k, v in payload["action_counts"]:
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("ENTRY_READY_REVIEW_SHADOW")
    for r in entry_ready_review:
        lines.append(
            f"{r['pair']}|side={r['side']}|score={r['score']}|shadow_lane={r['shadow_lane']}|"
            f"action={r['shadow_action']}|cvdoi={r['cvdoi']}|trigger={r['trigger']}|smc={r['smc']}|"
            f"latest_before={r['latest_before']}|missing={','.join(r['missing_after_shadow'])}"
        )
        lines.append(
            f"  reason={r['shadow_reason']}|paper_order_allowed={r['paper_order_allowed']}|"
            f"live_allowed={r['live_allowed']}"
        )
    lines.append("")
    lines.append("NEAR_REVIEW_SHADOW")
    for r in near_review:
        lines.append(
            f"{r['pair']}|side={r['side']}|score={r['score']}|shadow_lane={r['shadow_lane']}|"
            f"reason={r['shadow_reason']}|missing={','.join(r['missing_after_shadow'])}"
        )
    lines.append("")
    lines.append("BLOCKED_SHADOW")
    for r in blocked:
        lines.append(
            f"{r['pair']}|side={r['side']}|score={r['score']}|shadow_lane={r['shadow_lane']}|"
            f"reason={r['shadow_reason']}|missing={','.join(r['missing_after_shadow'])}"
        )
    lines.append("")
    lines.append("TMUX_STATE")
    for k, v in payload["tmux_state"].items():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("DECISION_POLICY")
    lines.append("Shadow-only conveyor integration.")
    lines.append("This does not create paper order.")
    lines.append("This does not loosen gate.")
    lines.append("Only future F4X-K ALLOW_PAPER_ENTRY / WOULD_ORDER may execute through F4X-L.")
    lines.append("Next candidate may connect ENTRY_READY_REVIEW_SHADOW into K intent review, still strict.")
    lines.append("No live. No risk-up. No gate-loosen.")
    lines.append("")
    lines.append("OUTPUT_FILES")
    lines.append(f"full_json={full_path}")
    lines.append(f"compact={compact_path}")
    lines.append(f"active={active_path}")

    text = "\n".join(lines) + "\n"
    compact_path.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
