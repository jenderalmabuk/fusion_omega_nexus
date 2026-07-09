#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter


PAIR = "AAVE/USDT:USDT"
SIDE = "LONG"


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


def find_pair_side(rows, pair=PAIR, side=SIDE):
    if not isinstance(rows, list):
        return None
    for r in rows:
        if isinstance(r, dict) and r.get("pair") == pair and r.get("side") == side:
            return r
    return None


def find_x_guard(rows, pair=PAIR):
    if not isinstance(rows, list):
        return None
    for r in rows:
        if isinstance(r, dict) and r.get("pair") == pair:
            return r
    return None


def norm_missing(row):
    v = row.get("missing_after_shadow", []) if isinstance(row, dict) else []
    if isinstance(v, list):
        return sorted(str(x) for x in v)
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)

    x = read_json(runtime / "F4X_X_STICKY_SOURCE_CYCLE_GUARD_SHADOW_ACTIVE.json", {})
    y = read_json(runtime / "F4X_Y_STALE_STICKY_DOWNGRADE_EFFECT_REPLAY_ACTIVE.json", {})
    z = read_json(runtime / "F4X_Z_STALE_STICKY_DOWNGRADE_TO_ENTRY_READY_CONVEYOR_SHADOW_ACTIVE.json", {})
    aa = read_json(runtime / "F4X_AA_ENTRY_READY_REVIEW_TO_K_INTENT_SHADOW_AUDIT_ACTIVE.json", {})
    ab = read_json(runtime / "F4X_AB_STRICT_K_INTENT_INTEGRATION_SHADOW_ACTIVE.json", {})
    k = read_json(runtime / "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json", {})
    l = read_json(runtime / "F4X_L_PAPER_BRIDGE_EXECUTION_FULL.json", {})
    trade = read_json(runtime / "F4X_L_PAPER_TRADE_OUTCOME_AUDIT_FULL.json", {})

    x_row = find_x_guard(x.get("guard_rows", []), PAIR)
    y_row = find_pair_side(y.get("near_entry_after_downgrade", []), PAIR, SIDE)
    z_row = find_pair_side(z.get("entry_ready_review_shadow", []), PAIR, SIDE)
    aa_row = find_pair_side(aa.get("k_shadow_review_ready", []), PAIR, SIDE)
    ab_row = find_pair_side(ab.get("strict_k_shadow_ready", []), PAIR, SIDE)

    k_has_order_intent = bool(k.get("has_order_intent")) if isinstance(k, dict) else False
    l_decision = l.get("decision") if isinstance(l, dict) else "UNKNOWN"

    open_trades = []
    if isinstance(trade, dict):
        for key in ("open_trades", "recent_trades", "trades"):
            val = trade.get(key)
            if isinstance(val, list):
                for t in val:
                    if isinstance(t, dict):
                        is_open = t.get("open") in (1, True, "1", "true", "True") or t.get("is_open") in (1, True, "1", "true", "True")
                        if is_open:
                            open_trades.append(t)

    open_same_pair = []
    for t in open_trades:
        if str(t.get("pair", "")) == PAIR:
            open_same_pair.append(t)

    chain_checks = {
        "x_stale_sticky_guard_exists": bool(x_row),
        "x_stale_sticky_confirmed": bool(x_row and x_row.get("guard_state") == "STALE_STICKY_REFRESH_RESET_CONFIRMED"),
        "y_near_entry_after_downgrade_exists": bool(y_row),
        "z_entry_ready_review_shadow_exists": bool(z_row),
        "aa_k_shadow_review_ready_exists": bool(aa_row),
        "ab_strict_k_shadow_ready_exists": bool(ab_row),
        "ab_no_real_order": bool(ab_row and ab_row.get("would_order") is False and ab_row.get("paper_order_allowed") is False),
        "k_no_current_order_intent": not k_has_order_intent,
        "l_no_valid_order_intent": str(l_decision) == "NO_VALID_ORDER_INTENT",
        "no_open_same_pair_in_trade_sample": len(open_same_pair) == 0,
    }

    missing_ab = norm_missing(ab_row or {})
    expected_missing_only = missing_ab == ["latest_entry_ready", "paper_action_allow"]

    quality_checks = {
        "pair": PAIR,
        "side": SIDE,
        "score": (ab_row or {}).get("score"),
        "cvdoi": (ab_row or {}).get("cvdoi"),
        "trigger": (ab_row or {}).get("trigger"),
        "smc": (ab_row or {}).get("smc"),
        "latest_before": (ab_row or {}).get("latest_before"),
        "shadow_reason": (ab_row or {}).get("shadow_reason"),
        "cvdoi_strong": (ab_row or {}).get("cvdoi") == "BULLISH_CONTINUATION_STRONG",
        "trigger_confirmed": (ab_row or {}).get("trigger") == "TRIGGER_CONFIRMED",
        "smc_good": (ab_row or {}).get("smc") == "SMC_GOOD_LOCATION_LONG",
        "expected_missing_only": expected_missing_only,
    }

    ready_for_strict_canary_policy = all(chain_checks.values()) and all([
        quality_checks["cvdoi_strong"],
        quality_checks["trigger_confirmed"],
        quality_checks["smc_good"],
        quality_checks["expected_missing_only"],
    ])

    if ready_for_strict_canary_policy:
        final_decision = "F4X_AC_STRICT_CANARY_POLICY_READY_MANUAL_APPROVAL_REQUIRED"
        next_action = "PREPARE_AD_STRICT_CANARY_PAPER_INTENT_PATCH_AFTER_MANUAL_APPROVAL"
    else:
        final_decision = "F4X_AC_MANUAL_REVIEW_INCOMPLETE_KEEP_SHADOW_ONLY"
        next_action = "KEEP_SHADOW_AND_REPAIR_MISSING_CHECKS"

    policy = {
        "policy_name": "AAVE_LONG_STRICT_CANARY_POLICY",
        "pair": PAIR,
        "side": SIDE,
        "dry_run_only": True,
        "paper_order_allowed_now": False,
        "requires_manual_approval": True,
        "requires_future_patch": "F4X_AD_STRICT_CANARY_PAPER_INTENT_PATCH",
        "max_pair_count": 1,
        "max_side_count": 1,
        "cooldown_sec": 1800,
        "allow_live": False,
        "allow_risk_up": False,
        "allow_gate_loosen": False,
        "allow_watch_recheck_deny_entry": False,
        "allow_only_if": [
            "same pair and side: AAVE/USDT:USDT LONG",
            "X stale sticky guard confirms stale opposite sticky",
            "Y downgrade replay creates near-entry",
            "Z entry-ready review shadow exists",
            "AA K shadow review ready exists",
            "AB strict K shadow intent ready exists",
            "no open same pair in trade sample",
            "manual approval is given explicitly",
        ],
        "deny_if": [
            "K real already has another order intent",
            "L already has valid order intent",
            "open AAVE trade exists",
            "candidate changes pair or side",
            "missing checks differ from latest_entry_ready,paper_action_allow",
        ],
    }

    packet = {
        "generated_at": now_utc(),
        "mode": "AAVE_MANUAL_REVIEW_PACKET_AND_STRICT_CANARY_POLICY",
        "final_decision": final_decision,
        "next_action": next_action,
        "paper_order": "HOLD",
        "live": "HOLD",
        "risk_up": "HOLD",
        "gate_loosen": "HOLD",
        "entry_from_watch_recheck_deny": "HOLD",
        "chain_checks": chain_checks,
        "quality_checks": quality_checks,
        "strict_canary_policy": policy,
        "candidate_rows": {
            "x": x_row,
            "y": y_row,
            "z": z_row,
            "aa": aa_row,
            "ab": ab_row,
        },
        "runtime_state": {
            "k_has_order_intent": k_has_order_intent,
            "l_decision": l_decision,
            "open_trade_count_sample": len(open_trades),
            "open_same_pair_count_sample": len(open_same_pair),
        },
        "tmux_state": {
            "f4x_l_paper_execution_dryrun": tmux_alive("f4x_l_paper_execution_dryrun"),
            "f4x_x_sticky_cycle_guard_shadow": tmux_alive("f4x_x_sticky_cycle_guard_shadow"),
            "f4x_y_stale_sticky_replay": tmux_alive("f4x_y_stale_sticky_replay"),
            "f4x_z_entry_ready_conveyor_shadow": tmux_alive("f4x_z_entry_ready_conveyor_shadow"),
            "f4x_aa_k_intent_shadow_audit": tmux_alive("f4x_aa_k_intent_shadow_audit"),
            "f4x_ab_strict_k_intent_shadow": tmux_alive("f4x_ab_strict_k_intent_shadow"),
        },
    }

    full_path = runtime / "F4X_AC_AAVE_MANUAL_REVIEW_PACKET_AND_STRICT_CANARY_POLICY_FULL.json"
    compact_path = runtime / "F4X_AC_AAVE_MANUAL_REVIEW_PACKET_AND_STRICT_CANARY_POLICY_COMPACT.txt"
    active_path = runtime / "F4X_AC_AAVE_MANUAL_REVIEW_PACKET_AND_STRICT_CANARY_POLICY_ACTIVE.json"

    write_json(full_path, packet)
    write_json(active_path, {
        "generated_at": packet["generated_at"],
        "mode": packet["mode"],
        "active": True,
        "final_decision": final_decision,
        "next_action": next_action,
        "ready_for_strict_canary_policy": ready_for_strict_canary_policy,
        "paper_order_allowed_now": False,
        "requires_manual_approval": True,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "entry_from_watch_recheck_deny_allowed": False,
        "candidate": {
            "pair": PAIR,
            "side": SIDE,
            "score": quality_checks["score"],
            "cvdoi": quality_checks["cvdoi"],
            "trigger": quality_checks["trigger"],
            "smc": quality_checks["smc"],
            "latest_before": quality_checks["latest_before"],
            "missing_after_shadow": missing_ab,
        },
        "strict_canary_policy": policy,
    })

    lines = []
    lines.append("F4X_AC_AAVE_MANUAL_REVIEW_PACKET_AND_STRICT_CANARY_POLICY_COMPACT")
    lines.append(f"generated_at={packet['generated_at']}")
    lines.append(f"mode={packet['mode']}")
    lines.append("paper_order=HOLD")
    lines.append("live=HOLD")
    lines.append("risk_up=HOLD")
    lines.append("gate_loosen=HOLD")
    lines.append("entry_from_watch_recheck_deny=HOLD")
    lines.append("")
    lines.append("FINAL_DECISION")
    lines.append(f"final_decision={final_decision}")
    lines.append(f"next_action={next_action}")
    lines.append("")
    lines.append("RUNTIME_STATE")
    for k2, v2 in packet["runtime_state"].items():
        lines.append(f"{k2}={v2}")
    lines.append("")
    lines.append("CHAIN_CHECKS")
    for k2, v2 in chain_checks.items():
        lines.append(f"{k2}={v2}")
    lines.append("")
    lines.append("QUALITY_CHECKS")
    for k2, v2 in quality_checks.items():
        lines.append(f"{k2}={v2}")
    lines.append("")
    lines.append("STRICT_CANARY_POLICY")
    lines.append(f"pair={PAIR}")
    lines.append(f"side={SIDE}")
    lines.append("dry_run_only=True")
    lines.append("paper_order_allowed_now=False")
    lines.append("requires_manual_approval=True")
    lines.append("max_pair_count=1")
    lines.append("cooldown_sec=1800")
    lines.append("allow_live=False")
    lines.append("allow_risk_up=False")
    lines.append("allow_gate_loosen=False")
    lines.append("allow_watch_recheck_deny_entry=False")
    lines.append("")
    lines.append("CANDIDATE_SUMMARY")
    lines.append(
        f"{PAIR}|side={SIDE}|score={quality_checks['score']}|cvdoi={quality_checks['cvdoi']}|"
        f"trigger={quality_checks['trigger']}|smc={quality_checks['smc']}|"
        f"latest_before={quality_checks['latest_before']}|missing_after_shadow={','.join(missing_ab)}"
    )
    lines.append("")
    lines.append("TMUX_STATE")
    for k2, v2 in packet["tmux_state"].items():
        lines.append(f"{k2}={v2}")
    lines.append("")
    lines.append("DECISION_POLICY")
    lines.append("AC is manual review and policy only.")
    lines.append("AC does not write to real F4X-K.")
    lines.append("AC does not create paper order.")
    lines.append("If AC is ready, next patch may create AD strict canary paper-intent after explicit approval.")
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
