#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from datetime import datetime, timezone


PAIR = "AAVE/USDT:USDT"
SIDE = "LONG"
COOLDOWN_SEC = 1800


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


def find_pair_side(rows, pair=PAIR, side=SIDE):
    if not isinstance(rows, list):
        return None
    for r in rows:
        if isinstance(r, dict) and r.get("pair") == pair and r.get("side") == side:
            return r
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--execute", action="store_true", help="Actually write F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    ac_path = runtime / "F4X_AC_AAVE_MANUAL_REVIEW_PACKET_AND_STRICT_CANARY_POLICY_ACTIVE.json"
    ab_path = runtime / "F4X_AB_STRICT_K_INTENT_INTEGRATION_SHADOW_ACTIVE.json"
    k_path = runtime / "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json"
    l_path = runtime / "F4X_L_PAPER_BRIDGE_EXECUTION_FULL.json"

    ac = read_json(ac_path, {})
    ab = read_json(ab_path, {})
    k_existing = read_json(k_path, {})
    l = read_json(l_path, {})

    ab_row = find_pair_side(ab.get("strict_k_shadow_ready", []), PAIR, SIDE)

    failures = []

    if not ac.get("ready_for_strict_canary_policy"):
        failures.append("AC_NOT_READY")
    if ac.get("paper_order_allowed_now") is not False:
        failures.append("AC_UNEXPECTED_PAPER_ALLOWED")
    if not ac.get("requires_manual_approval"):
        failures.append("AC_MANUAL_APPROVAL_FLAG_MISSING")
    if not ab_row:
        failures.append("AB_STRICT_SHADOW_ROW_MISSING")
    if ab_row and ab_row.get("would_order") is not False:
        failures.append("AB_ALREADY_WOULD_ORDER_UNEXPECTED")
    if ab_row and ab_row.get("paper_order_allowed") is not False:
        failures.append("AB_ALREADY_PAPER_ALLOWED_UNEXPECTED")
    if k_existing.get("has_order_intent") is True:
        failures.append("EXISTING_K_HAS_ORDER_INTENT")
    if l.get("decision") not in (None, "NO_VALID_ORDER_INTENT"):
        failures.append(f"L_DECISION_NOT_IDLE:{l.get('decision')}")

    candidate = {
        "pair": PAIR,
        "side": SIDE,
        "order_pair": PAIR,
        "order_side": SIDE,
        "direction": SIDE,
        "score": float((ab_row or {}).get("score", 57.0)),
        "cvdoi": (ab_row or {}).get("cvdoi", "BULLISH_CONTINUATION_STRONG"),
        "trigger": (ab_row or {}).get("trigger", "TRIGGER_CONFIRMED"),
        "smc": (ab_row or {}).get("smc", "SMC_GOOD_LOCATION_LONG"),
        "latest_before": (ab_row or {}).get("latest_before", "FLOW_DIRECTION_BLOCK"),
        "intent_source": "F4X_AD_STRICT_CANARY_PAPER_INTENT_PATCH_DRYRUN_ONLY",
        "intent_state": "ALLOW_PAPER_ENTRY",
        "paper_action": "ALLOW_PAPER_ENTRY",
        "allow_paper_entry": True,
        "would_order": True,
        "dry_run_only": True,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "entry_from_watch_recheck_deny_allowed": False,
        "max_pair_count": 1,
        "cooldown_sec": COOLDOWN_SEC,
        "manual_approval_source": "F4X_AC_STRICT_CANARY_POLICY_READY_MANUAL_APPROVAL_REQUIRED",
        "canary_reason": "AAVE_LONG_STALE_STICKY_DOWNGRADE_CORE_OK_STRICT_CANARY",
    }

    k_new = {
        "generated_at": now_utc(),
        "mode": "F4X_AD_STRICT_CANARY_PAPER_INTENT_PATCH_DRYRUN_ONLY",
        "has_order_intent": True,
        "order_intents": [candidate],
        "would_order_intent_count": 1,
        "intent_count": 1,
        "blocked_count": 0,
        "paper_order_mode": "STRICT_ALLOW_ONLY",
        "paper_bridge": "RUNNING",
        "paper_order_allowed": True,
        "dry_run_only": True,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "entry_from_watch_recheck_deny_allowed": False,
        "pair": PAIR,
        "side": SIDE,
        "order_pair": PAIR,
        "order_side": SIDE,
        "allow_paper_entry": True,
        "would_order": True,
        "source_files": {
            "ac": str(ac_path),
            "ab": str(ab_path),
        },
    }

    decision = "F4X_AD_ABORTED_GUARD_FAILED" if failures else "F4X_AD_STRICT_CANARY_K_ACTIVE_SIGNAL_WRITTEN" if args.execute else "F4X_AD_READY_DRY_RUN_PREVIEW_ONLY"

    backup_path = None
    if args.execute and not failures:
        if k_path.exists():
            backup_path = runtime / f"F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL_BACKUP_BEFORE_AD_{ts}.json"
            shutil.copy2(k_path, backup_path)
        write_json(k_path, k_new)

    full = {
        "generated_at": now_utc(),
        "mode": "STRICT_CANARY_PAPER_INTENT_PATCH_DRYRUN_ONLY",
        "execute_requested": args.execute,
        "final_decision": decision,
        "failures": failures,
        "paper_order": "ALLOW_DRYRUN_CANARY_ONLY" if args.execute and not failures else "HOLD",
        "live": "HOLD",
        "risk_up": "HOLD",
        "gate_loosen": "HOLD",
        "entry_from_watch_recheck_deny": "HOLD_EXCEPT_APPROVED_AC_AB_CANARY_PATH",
        "candidate": candidate,
        "k_active_signal_written": bool(args.execute and not failures),
        "k_active_path": str(k_path),
        "backup_path": str(backup_path) if backup_path else None,
        "k_payload": k_new,
    }

    full_path = runtime / "F4X_AD_STRICT_CANARY_PAPER_INTENT_PATCH_DRYRUN_ONLY_FULL.json"
    compact_path = runtime / "F4X_AD_STRICT_CANARY_PAPER_INTENT_PATCH_DRYRUN_ONLY_COMPACT.txt"
    active_path = runtime / "F4X_AD_STRICT_CANARY_PAPER_INTENT_PATCH_DRYRUN_ONLY_ACTIVE.json"

    write_json(full_path, full)
    write_json(active_path, {
        "generated_at": full["generated_at"],
        "mode": full["mode"],
        "active": True,
        "execute_requested": args.execute,
        "final_decision": decision,
        "failures": failures,
        "k_active_signal_written": bool(args.execute and not failures),
        "paper_order_allowed": bool(args.execute and not failures),
        "dry_run_only": True,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "candidate": candidate,
        "k_active_path": str(k_path),
        "backup_path": str(backup_path) if backup_path else None,
    })

    lines = []
    lines.append("F4X_AD_STRICT_CANARY_PAPER_INTENT_PATCH_DRYRUN_ONLY_COMPACT")
    lines.append(f"generated_at={full['generated_at']}")
    lines.append(f"mode={full['mode']}")
    lines.append(f"execute_requested={args.execute}")
    lines.append("")
    lines.append("FINAL_DECISION")
    lines.append(f"final_decision={decision}")
    lines.append("")
    lines.append("GUARD_FAILURES")
    if failures:
        for f in failures:
            lines.append(f)
    else:
        lines.append("NONE")
    lines.append("")
    lines.append("CANARY_INTENT")
    lines.append(f"pair={PAIR}")
    lines.append(f"side={SIDE}")
    lines.append(f"score={candidate['score']}")
    lines.append(f"cvdoi={candidate['cvdoi']}")
    lines.append(f"trigger={candidate['trigger']}")
    lines.append(f"smc={candidate['smc']}")
    lines.append(f"allow_paper_entry={candidate['allow_paper_entry']}")
    lines.append(f"would_order={candidate['would_order']}")
    lines.append("dry_run_only=True")
    lines.append("live=False")
    lines.append("risk_up=False")
    lines.append("gate_loosen=False")
    lines.append("")
    lines.append("WRITE_STATE")
    lines.append(f"k_active_signal_written={bool(args.execute and not failures)}")
    lines.append(f"k_active_path={k_path}")
    lines.append(f"backup_path={backup_path}")
    lines.append("")
    lines.append("DECISION_POLICY")
    lines.append("AD writes one strict canary paper intent only if guards pass.")
    lines.append("AD does not enable live.")
    lines.append("AD does not risk-up.")
    lines.append("AD does not loosen gate.")
    lines.append("AD does not generally allow WATCH/RECHECK/DENY.")
    lines.append("Only approved AAVE LONG canary path is allowed.")
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
