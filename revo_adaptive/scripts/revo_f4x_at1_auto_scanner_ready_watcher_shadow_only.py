#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT_PREFIX = "F4X_AT1_AUTO_SCANNER_READY_WATCHER_SHADOW_ONLY"
MODE = "AUTO_SCANNER_READY_WATCHER_SHADOW_ONLY"

AS5_SCRIPT = "scripts/revo_f4x_as5_next_non_cooldown_strict_candidate_selector_shadow_only.py"

READY = "F4X_AT1_READY_FOR_AQ_REVIEW_ONLY"
HOLD_NO_CLEAN = "F4X_AT1_HOLD_NO_CLEAN_CANDIDATE_SCANNER_CONTINUE"
HOLD_OPEN_TRADE = "F4X_AT1_HOLD_OPEN_TRADE_ACTIVE"
HOLD_K_NOT_CLEAN = "F4X_AT1_HOLD_K_NOT_CLEAN"
HOLD_L_NOT_CLEAN = "F4X_AT1_HOLD_L_NOT_CLEAN"
HEALTH_FAIL = "F4X_AT1_HEALTH_FAIL_PATCH_FAILED_LAYER_ONLY"
INPUT_FAIL = "F4X_AT1_INPUT_FAIL_HOLD"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def file_age_sec(path: Path) -> float | None:
    try:
        if not path.exists():
            return None
        return max(0.0, datetime.now(timezone.utc).timestamp() - path.stat().st_mtime)
    except Exception:
        return None


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, default=str) + "\n")


def run_as5(repo: Path, runtime: Path, args: argparse.Namespace) -> dict[str, Any]:
    script = repo / AS5_SCRIPT
    if not script.exists():
        return {
            "ok": False,
            "returncode": 999,
            "stdout_tail": "",
            "stderr_tail": f"AS5_SCRIPT_MISSING:{script}",
        }

    cmd = [
        "python3",
        str(script),
        "--repo-dir",
        str(repo),
        "--runtime-dir",
        str(runtime),
    ]

    # F4X_AT1A: AS5 CLI compatibility guard.
    # Detect supported AS5 flags from --help, then pass only supported optional args.
    # This is shadow-only and does not change trading gates, K, L, forceenter,
    # live, risk, or gate logic.
    supported_flags = set()
    help_cp = subprocess.run(
        ["python3", str(script), "--help"],
        cwd=str(repo),
        text=True,
        capture_output=True,
    )
    help_text = (help_cp.stdout or "") + "\n" + (help_cp.stderr or "")
    for flag in (
        "--max-input-age-sec",
        "--pair-cooldown-sec",
        "--repeated-pair-window-sec",
        "--cvd-z-threshold",
        "--low-confluence-score",
    ):
        if flag in help_text:
            supported_flags.add(flag)

    optional_pairs = [
        ("--max-input-age-sec", args.max_input_age_sec),
        ("--pair-cooldown-sec", args.pair_cooldown_sec),
        ("--repeated-pair-window-sec", args.repeated_pair_window_sec),
        ("--cvd-z-threshold", args.cvd_z_threshold),
        ("--low-confluence-score", args.low_confluence_score),
    ]

    skipped_flags = []
    for flag, value in optional_pairs:
        if value is None:
            continue
        if flag in supported_flags:
            cmd.extend([flag, str(value)])
        else:
            skipped_flags.append(flag)

    cp = subprocess.run(cmd, cwd=str(repo), text=True, capture_output=True)

    fallback_used = False
    fallback_cmd = None
    if cp.returncode == 2:
        fallback_cmd = [
            "python3",
            str(script),
            "--repo-dir",
            str(repo),
            "--runtime-dir",
            str(runtime),
        ]
        cp2 = subprocess.run(fallback_cmd, cwd=str(repo), text=True, capture_output=True)
        if cp2.returncode == 0:
            fallback_used = True
            cp = cp2

    return {
        "ok": cp.returncode == 0,
        "returncode": cp.returncode,
        "cmd": cmd,
        "supported_flags": sorted(supported_flags),
        "skipped_flags": skipped_flags,
        "fallback_used": fallback_used,
        "fallback_cmd": fallback_cmd,
        "stdout_tail": (cp.stdout or "")[-5000:],
        "stderr_tail": (cp.stderr or "")[-5000:],
    }


def classify_ready(as5: dict[str, Any]) -> bool:
    fd = str(as5.get("final_decision") or "").upper()
    if "READY_FOR_AQ_REVIEW" in fd:
        return True
    if bool(as5.get("clean_selected")) is True:
        return True
    selected = as5.get("selected_candidate") or as5.get("selected_candidate_shadow") or {}
    if isinstance(selected, dict) and selected:
        blockers = as5.get("blockers") or as5.get("reject_reasons") or selected.get("reject_reasons")
        if not blockers and bool(selected.get("strict_ok", True)) is True:
            return True
    return False


def normalize_list(v: Any) -> list[Any]:
    if isinstance(v, list):
        return v
    if v is None:
        return []
    return [v]


def get_open_count_from_as5(as5: dict[str, Any]) -> int | None:
    rest = as5.get("rest_runtime") or as5.get("runtime_checks") or {}
    if isinstance(rest, dict):
        for k in ("open_count", "open_trade_count", "active_open_count"):
            try:
                if rest.get(k) is not None:
                    return int(rest.get(k))
            except Exception:
                pass
    try:
        return int(as5.get("open_count"))
    except Exception:
        return None


def main_once(args: argparse.Namespace) -> dict[str, Any]:
    repo = Path(args.repo_dir)
    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    generated_at = now_utc()
    failures: list[str] = []
    warnings: list[str] = []
    blockers: list[str] = []

    as5_run = {"ok": False, "returncode": 999, "stdout_tail": "", "stderr_tail": "NOT_RUN"}
    if args.run_as5:
        as5_run = run_as5(repo, runtime, args)
        if not as5_run.get("ok"):
            failures.append("AS5_RUN_FAILED")

    as5_active_path = runtime / "F4X_AS5_NEXT_NON_COOLDOWN_STRICT_CANDIDATE_SELECTOR_SHADOW_ONLY_ACTIVE.json"
    as5_compact_path = runtime / "F4X_AS5_NEXT_NON_COOLDOWN_STRICT_CANDIDATE_SELECTOR_SHADOW_ONLY_COMPACT.txt"
    as5d_active_path = runtime / "F4X_AS5D_AUTO_SHARED_METRIC_CAUTION_REPORT_INTEGRATION_ONLY_ACTIVE.json"
    as5c_active_path = runtime / "F4X_AS5C_SHARED_METRIC_CAUTION_REPORT_ONLY_ACTIVE.json"
    k_path = runtime / "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json"
    l_active_path = runtime / "F4X_L_PAPER_BRIDGE_ACTIVE_EXECUTION.json"

    as5 = read_json(as5_active_path, {})
    as5d = read_json(as5d_active_path, {})
    as5c = read_json(as5c_active_path, {})
    k = read_json(k_path, {})
    l = read_json(l_active_path, {})

    if not as5:
        failures.append("AS5_ACTIVE_MISSING_OR_INVALID")

    as5_fd = as5.get("final_decision") if isinstance(as5, dict) else None
    as5_ready = classify_ready(as5) if isinstance(as5, dict) else False

    rest = as5.get("rest_runtime") or as5.get("runtime_checks") or {}
    if not isinstance(rest, dict):
        rest = {}

    rest_health_flags = {
        "ping_ok": rest.get("ping_ok") or rest.get("rest_ping_ok"),
        "login_ok": rest.get("login_ok") or rest.get("rest_login_ok"),
        "show_config_ok": rest.get("show_config_ok") or rest.get("rest_show_config_ok"),
        "status_ok": rest.get("status_ok") or rest.get("rest_status_ok"),
        "whitelist_ok": rest.get("whitelist_ok") or rest.get("rest_whitelist_ok"),
        "dry_run": rest.get("dry_run"),
        "force_entry_enable": rest.get("force_entry_enable"),
    }

    runtime_health_ok = True
    for key in ("ping_ok", "login_ok", "show_config_ok", "status_ok", "whitelist_ok"):
        if rest_health_flags.get(key) is False:
            runtime_health_ok = False

    dry_run = rest_health_flags.get("dry_run")
    force_entry_enable = rest_health_flags.get("force_entry_enable")
    if dry_run is False:
        runtime_health_ok = False
        blockers.append("DRY_RUN_FALSE")
    if force_entry_enable is False:
        runtime_health_ok = False
        blockers.append("FORCE_ENTRY_DISABLED")

    open_count = get_open_count_from_as5(as5)
    if open_count is not None and open_count > 0:
        blockers.append("OPEN_TRADE_ACTIVE")

    k_clean = True
    if isinstance(k, dict) and k:
        if bool(k.get("has_order_intent")) is True:
            k_clean = False
        try:
            if int(k.get("would_order_intent_count") or 0) > 0:
                k_clean = False
        except Exception:
            pass
        if bool(k.get("live_allowed")) is True:
            k_clean = False
    else:
        warnings.append("K_ACTIVE_MISSING_OR_EMPTY")

    l_clean = True
    if isinstance(l, dict) and l:
        orders = normalize_list(l.get("orders"))
        errors = normalize_list(l.get("errors"))
        decision = str(l.get("decision") or "").upper()
        if orders:
            l_clean = False
        if errors:
            l_clean = False
        if "DRY_RUN_ORDER_SENT" in decision:
            l_clean = False
        if bool(l.get("live_allowed")) is True:
            l_clean = False
    else:
        warnings.append("L_ACTIVE_MISSING_OR_EMPTY")

    shared_caution_count = 0
    false_positive_count = 0
    try:
        shared_caution_count = int(as5d.get("shared_caution_count") or as5c.get("shared_caution_count") or 0)
        false_positive_count = int(as5d.get("false_positive_count") or as5c.get("false_positive_count") or 0)
    except Exception:
        pass

    if false_positive_count > 0:
        blockers.append("ATTRIBUTION_FALSE_POSITIVE_RISK")
    if shared_caution_count > 0:
        warnings.append("SHARED_METRIC_CAUTION_PRESENT")

    as5_blockers = normalize_list(as5.get("blockers") if isinstance(as5, dict) else [])
    as5_reject_counts = as5.get("reject_reason_counts") if isinstance(as5, dict) else {}
    if isinstance(as5_blockers, list):
        blockers.extend([str(x) for x in as5_blockers if x])

    selected = (
        as5.get("selected_candidate")
        or as5.get("selected_candidate_shadow")
        or as5.get("clean_selected_candidate")
        or {}
        if isinstance(as5, dict)
        else {}
    )

    if failures:
        final_decision = INPUT_FAIL
        next_action = "Fix missing/failed AT1/AS5 input. No AQ/K/L."
    elif not runtime_health_ok:
        final_decision = HEALTH_FAIL
        next_action = "Patch runtime/REST health only. No AQ/K/L."
    elif open_count is not None and open_count > 0:
        final_decision = HOLD_OPEN_TRADE
        next_action = "Hold while open trade exists. No AQ/K/L."
    elif not k_clean:
        final_decision = HOLD_K_NOT_CLEAN
        next_action = "Patch/clear K safely. No AQ/L until K clean."
    elif not l_clean:
        final_decision = HOLD_L_NOT_CLEAN
        next_action = "Patch L state/active execution cleanliness only. No new order."
    elif as5_ready:
        final_decision = READY
        next_action = "Upload AT1 compact/active for review. If approved, next step is AQ only; L remains manual."
    else:
        final_decision = HOLD_NO_CLEAN
        next_action = "No clean candidate. Scanner continues. No AQ/K/L."

    result = {
        "event": OUT_PREFIX,
        "generated_at": generated_at,
        "mode": MODE,
        "paper_order_allowed": False,
        "k_write_allowed": False,
        "l_execute_allowed": False,
        "forceenter_allowed": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "final_decision": final_decision,
        "next_action": next_action,
        "failures": failures,
        "warnings": warnings,
        "blockers": blockers,
        "as5_run": as5_run,
        "as5": {
            "active_path": str(as5_active_path),
            "compact_path": str(as5_compact_path),
            "active_age_sec": file_age_sec(as5_active_path),
            "compact_age_sec": file_age_sec(as5_compact_path),
            "final_decision": as5_fd,
            "ready_detected": as5_ready,
            "candidate_count": as5.get("candidate_count") if isinstance(as5, dict) else None,
            "evaluated_count": as5.get("evaluated_count") if isinstance(as5, dict) else None,
            "clean_selected": as5.get("clean_selected") if isinstance(as5, dict) else None,
            "reject_reason_counts": as5_reject_counts,
            "selected_candidate": selected,
        },
        "rest_runtime": {
            **rest_health_flags,
            "open_count": open_count,
            "open_pairs": rest.get("open_pairs"),
            "whitelist_count": rest.get("whitelist_count") or rest.get("active_whitelist_count"),
        },
        "k_state": {
            "exists": k_path.exists(),
            "has_order_intent": k.get("has_order_intent") if isinstance(k, dict) else None,
            "intent_count": k.get("intent_count") if isinstance(k, dict) else None,
            "would_order_intent_count": k.get("would_order_intent_count") if isinstance(k, dict) else None,
            "live_allowed": k.get("live_allowed") if isinstance(k, dict) else None,
            "clean": k_clean,
        },
        "l_state": {
            "exists": l_active_path.exists(),
            "decision": l.get("decision") if isinstance(l, dict) else None,
            "orders_count": len(normalize_list(l.get("orders"))) if isinstance(l, dict) else None,
            "errors_count": len(normalize_list(l.get("errors"))) if isinstance(l, dict) else None,
            "live_allowed": l.get("live_allowed") if isinstance(l, dict) else None,
            "clean": l_clean,
        },
        "shared_metric_context": {
            "as5d_active_path": str(as5d_active_path),
            "as5d_final_decision": as5d.get("final_decision") if isinstance(as5d, dict) else None,
            "as5c_active_path": str(as5c_active_path),
            "as5c_final_decision": as5c.get("final_decision") if isinstance(as5c, dict) else None,
            "shared_caution_count": shared_caution_count,
            "false_positive_count": false_positive_count,
        },
        "decision_policy": [
            "AT1 is shadow-only.",
            "AT1 may rerun AS5 and read AS5D/AS5C context.",
            "AT1 does not write K.",
            "AT1 does not execute L.",
            "AT1 does not forceenter.",
            "AT1 does not create paper order.",
            "AT1 does not enable live, risk-up, or gate-loosen.",
            "If READY, next action is manual AQ review only.",
        ],
    }

    full = runtime / f"{OUT_PREFIX}_FULL.json"
    active = runtime / f"{OUT_PREFIX}_ACTIVE.json"
    compact = runtime / f"{OUT_PREFIX}_COMPACT.txt"
    events = runtime / f"{OUT_PREFIX}_EVENTS.jsonl"

    write_json(full, result)
    write_json(active, result)
    append_jsonl(events, result)

    lines = [
        "F4X_AT1_AUTO_SCANNER_READY_WATCHER_SHADOW_ONLY_COMPACT",
        f"generated_at={generated_at}",
        f"mode={MODE}",
        "paper_order=HOLD",
        "k_write=HOLD",
        "l_execute=HOLD",
        "forceenter=HOLD",
        "live=HOLD",
        "risk_up=HOLD",
        "gate_loosen=HOLD",
        "FINAL_DECISION",
        f"final_decision={final_decision}",
        f"next_action={next_action}",
        "FAILURES",
        *(failures if failures else ["NONE"]),
        "WARNINGS",
        *(warnings if warnings else ["NONE"]),
        "BLOCKERS",
        *(blockers if blockers else ["NONE"]),
        "AS5_RUN",
        f"run_as5={args.run_as5}|ok={as5_run.get('ok')}|returncode={as5_run.get('returncode')}",
        "AS5_STATE",
        f"final_decision={as5_fd}|ready_detected={as5_ready}|candidate_count={result['as5']['candidate_count']}|evaluated_count={result['as5']['evaluated_count']}|clean_selected={result['as5']['clean_selected']}",
        f"reject_reason_counts={as5_reject_counts}",
        f"selected_candidate={selected}",
        "REST_RUNTIME",
        f"ping_ok={rest_health_flags.get('ping_ok')}|login_ok={rest_health_flags.get('login_ok')}|show_config_ok={rest_health_flags.get('show_config_ok')}|status_ok={rest_health_flags.get('status_ok')}|whitelist_ok={rest_health_flags.get('whitelist_ok')}|dry_run={dry_run}|force_entry_enable={force_entry_enable}|open_count={open_count}|open_pairs={rest.get('open_pairs')}|whitelist_count={rest.get('whitelist_count') or rest.get('active_whitelist_count')}",
        "K_STATE",
        f"exists={k_path.exists()}|has_order_intent={result['k_state']['has_order_intent']}|intent_count={result['k_state']['intent_count']}|would_order_intent_count={result['k_state']['would_order_intent_count']}|live_allowed={result['k_state']['live_allowed']}|clean={k_clean}",
        "L_STATE",
        f"exists={l_active_path.exists()}|decision={result['l_state']['decision']}|orders_count={result['l_state']['orders_count']}|errors_count={result['l_state']['errors_count']}|live_allowed={result['l_state']['live_allowed']}|clean={l_clean}",
        "SHARED_METRIC_CONTEXT",
        f"as5d_final_decision={result['shared_metric_context']['as5d_final_decision']}|as5c_final_decision={result['shared_metric_context']['as5c_final_decision']}|shared_caution_count={shared_caution_count}|false_positive_count={false_positive_count}",
        "DECISION_POLICY",
        *result["decision_policy"],
        "OUTPUT_FILES",
        f"full_json={full}",
        f"compact={compact}",
        f"active={active}",
        f"events_jsonl={events}",
    ]
    compact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(compact.read_text(encoding="utf-8"))
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--run-as5", action="store_true", help="Run AS5 before classifying AT1 state.")
    ap.add_argument("--loop", action="store_true", help="Run repeatedly. Default is one cycle only.")
    ap.add_argument("--interval-sec", type=int, default=300)
    ap.add_argument("--max-cycles", type=int, default=1)
    ap.add_argument("--max-input-age-sec", type=int, default=900)
    ap.add_argument("--pair-cooldown-sec", type=int, default=21600)
    ap.add_argument("--repeated-pair-window-sec", type=int, default=86400)
    ap.add_argument("--cvd-z-threshold", type=float, default=1.5)
    ap.add_argument("--low-confluence-score", type=float, default=35)
    args = ap.parse_args()

    cycles = 0
    while True:
        main_once(args)
        cycles += 1
        if not args.loop:
            break
        if args.max_cycles > 0 and cycles >= args.max_cycles:
            break
        time.sleep(max(10, int(args.interval_sec)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
