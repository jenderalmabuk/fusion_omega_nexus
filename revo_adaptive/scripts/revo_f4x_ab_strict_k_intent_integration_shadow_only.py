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


def build_shadow_intent(row: dict) -> dict:
    pair = row.get("pair", "UNKNOWN")
    side = row.get("side", "UNKNOWN")

    valid_aa = (
        row.get("k_shadow_state") == "K_INTENT_SHADOW_REVIEW_READY"
        and row.get("core_ok_after_shadow") is True
        and row.get("expected_missing_only") is True
        and row.get("paper_order_allowed") is False
        and row.get("would_order") is False
    )

    if valid_aa:
        shadow_state = "STRICT_K_SHADOW_INTENT_READY"
        shadow_action = "WRITE_K_COMPATIBLE_SHADOW_INTENT_ONLY"
        review_required = True
        reason = "AA_REVIEW_READY_CONVERTED_TO_K_COMPATIBLE_SHADOW_INTENT"
    else:
        shadow_state = "STRICT_K_SHADOW_INTENT_BLOCKED"
        shadow_action = "KEEP_BLOCKED"
        review_required = False
        reason = "AA_ROW_NOT_VALID_FOR_STRICT_K_SHADOW_INTENT"

    return {
        "pair": pair,
        "side": side,
        "score": row.get("score"),
        "source_file": row.get("source_file"),
        "paper_action_before": row.get("paper_action_before"),
        "cvdoi": row.get("cvdoi"),
        "trigger": row.get("trigger"),
        "smc": row.get("smc"),
        "latest_before": row.get("latest_before"),
        "final_reason_before": row.get("final_reason_before"),
        "shadow_lane": row.get("shadow_lane"),
        "shadow_reason": row.get("shadow_reason"),
        "missing_after_shadow": row.get("missing_after_shadow", []),
        "strict_k_shadow_state": shadow_state,
        "strict_k_shadow_action": shadow_action,
        "strict_k_shadow_reason": reason,
        "manual_review_required": review_required,
        "shadow_would_order_candidate": valid_aa,
        "would_order": False,
        "paper_order_allowed": False,
        "write_to_real_k": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "entry_from_watch_recheck_deny_allowed": False,
        "k_compat_fields": {
            "paper_order_mode": "STRICT_ALLOW_ONLY",
            "intent_source": "F4X_AB_SHADOW_ONLY",
            "intent_state": shadow_state,
            "order_side": side,
            "order_pair": pair,
            "blocked_reason": "SHADOW_ONLY_NOT_ALLOW_PAPER_ENTRY",
            "allow_paper_entry": False,
            "would_order": False,
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)

    aa_active_path = runtime / "F4X_AA_ENTRY_READY_REVIEW_TO_K_INTENT_SHADOW_AUDIT_ACTIVE.json"
    k_active_path = runtime / "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json"
    l_full_path = runtime / "F4X_L_PAPER_BRIDGE_EXECUTION_FULL.json"

    aa_active = read_json(aa_active_path, {})
    k_active = read_json(k_active_path, {})
    l_full = read_json(l_full_path, {})

    aa_rows = aa_active.get("k_shadow_review_ready", []) if isinstance(aa_active, dict) else []
    shadow_intents = [build_shadow_intent(r) for r in aa_rows if isinstance(r, dict)]

    ready = [r for r in shadow_intents if r["strict_k_shadow_state"] == "STRICT_K_SHADOW_INTENT_READY"]
    blocked = [r for r in shadow_intents if r["strict_k_shadow_state"] == "STRICT_K_SHADOW_INTENT_BLOCKED"]

    state_counts = Counter(r["strict_k_shadow_state"] for r in shadow_intents)
    action_counts = Counter(r["strict_k_shadow_action"] for r in shadow_intents)

    k_has_order_intent = bool(k_active.get("has_order_intent")) if isinstance(k_active, dict) else False
    l_decision = l_full.get("decision") if isinstance(l_full, dict) else "UNKNOWN"

    if ready:
        final_decision = "F4X_AB_STRICT_K_SHADOW_INTENT_READY_NO_ORDER"
    elif blocked:
        final_decision = "F4X_AB_STRICT_K_SHADOW_INTENT_BLOCKED"
    else:
        final_decision = "F4X_AB_NO_AA_REVIEW_READY_INPUT"

    payload = {
        "generated_at": now_utc(),
        "mode": "STRICT_K_INTENT_INTEGRATION_SHADOW_ONLY",
        "final_decision": final_decision,
        "paper_order": "HOLD",
        "live": "HOLD",
        "risk_up": "HOLD",
        "gate_loosen": "HOLD",
        "entry_from_watch_recheck_deny": "HOLD",
        "counts": {
            "aa_review_ready_input_count": len(aa_rows),
            "shadow_intent_count": len(shadow_intents),
            "strict_k_shadow_ready_count": len(ready),
            "strict_k_shadow_blocked_count": len(blocked),
            "k_has_order_intent": k_has_order_intent,
            "l_decision": l_decision,
        },
        "state_counts": state_counts.most_common(),
        "action_counts": action_counts.most_common(),
        "strict_k_shadow_ready": ready,
        "strict_k_shadow_blocked": blocked,
        "all_shadow_intents": shadow_intents,
        "tmux_state": {
            "f4x_l_paper_execution_dryrun": tmux_alive("f4x_l_paper_execution_dryrun"),
            "f4x_x_sticky_cycle_guard_shadow": tmux_alive("f4x_x_sticky_cycle_guard_shadow"),
            "f4x_y_stale_sticky_replay": tmux_alive("f4x_y_stale_sticky_replay"),
            "f4x_z_entry_ready_conveyor_shadow": tmux_alive("f4x_z_entry_ready_conveyor_shadow"),
            "f4x_aa_k_intent_shadow_audit": tmux_alive("f4x_aa_k_intent_shadow_audit"),
        },
    }

    full_path = runtime / "F4X_AB_STRICT_K_INTENT_INTEGRATION_SHADOW_FULL.json"
    compact_path = runtime / "F4X_AB_STRICT_K_INTENT_INTEGRATION_SHADOW_COMPACT.txt"
    active_path = runtime / "F4X_AB_STRICT_K_INTENT_INTEGRATION_SHADOW_ACTIVE.json"

    write_json(full_path, payload)
    write_json(active_path, {
        "generated_at": payload["generated_at"],
        "mode": payload["mode"],
        "active": True,
        "paper_order_allowed": False,
        "write_to_real_k": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "final_decision": final_decision,
        "strict_k_shadow_ready_count": len(ready),
        "strict_k_shadow_ready": ready,
    })

    lines = []
    lines.append("F4X_AB_STRICT_K_INTENT_INTEGRATION_SHADOW_COMPACT")
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
    lines.append("STATE_COUNTS")
    for k, v in payload["state_counts"]:
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("ACTION_COUNTS")
    for k, v in payload["action_counts"]:
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("STRICT_K_SHADOW_READY")
    for r in ready:
        lines.append(
            f"{r['pair']}|side={r['side']}|score={r['score']}|state={r['strict_k_shadow_state']}|"
            f"action={r['strict_k_shadow_action']}|cvdoi={r['cvdoi']}|trigger={r['trigger']}|"
            f"smc={r['smc']}|latest_before={r['latest_before']}|shadow_candidate={r['shadow_would_order_candidate']}"
        )
        lines.append(
            f"  reason={r['strict_k_shadow_reason']}|would_order={r['would_order']}|"
            f"paper_order_allowed={r['paper_order_allowed']}|write_to_real_k={r['write_to_real_k']}"
        )
    lines.append("")
    lines.append("STRICT_K_SHADOW_BLOCKED")
    for r in blocked:
        lines.append(f"{r['pair']}|side={r['side']}|reason={r['strict_k_shadow_reason']}")
    lines.append("")
    lines.append("TMUX_STATE")
    for k, v in payload["tmux_state"].items():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("DECISION_POLICY")
    lines.append("Shadow-only K integration.")
    lines.append("This creates K-compatible shadow intent format only.")
    lines.append("This does not write to real F4X-K active signal.")
    lines.append("This does not create paper order.")
    lines.append("Next step may be manual review or strict canary paper-intent patch.")
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
