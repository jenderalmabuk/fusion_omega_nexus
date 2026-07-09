#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT_PREFIX = "F4X_BA5_SHADOW_AUTO_PAPER_RULE_SIMULATION_ONLY"
MODE = "SHADOW_AUTO_PAPER_RULE_SIMULATION_ONLY"

BA4_READY = "F4X_BA4_GUARDED_RULE_DRAFT_READY_FOR_BA5_SHADOW_SIMULATION_ONLY"
BA4A_CLEAN = "F4X_BA4A_K_ALREADY_CLEAN_READY_FOR_BA5_SHADOW_ONLY"


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
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def file_age_sec(path: Path) -> float | None:
    try:
        if not path.exists():
            return None
        return max(0.0, datetime.now(timezone.utc).timestamp() - path.stat().st_mtime)
    except Exception:
        return None


def as_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in {"true", "1", "yes", "y"}:
        return True
    if s in {"false", "0", "no", "n"}:
        return False
    return None


def as_int(v: Any) -> int:
    try:
        if v is None or v == "":
            return 0
        return int(v)
    except Exception:
        return 0


def k_clean(k: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    if not isinstance(k, dict) or not k:
        return False, ["K_MISSING_OR_INVALID"]

    order_intents = k.get("order_intents") if isinstance(k.get("order_intents"), list) else []

    if as_bool(k.get("has_order_intent")) is not False:
        reasons.append(f"HAS_ORDER_INTENT_NOT_FALSE:{k.get('has_order_intent')}")
    if len(order_intents) != 0:
        reasons.append(f"ORDER_INTENTS_NOT_EMPTY:{len(order_intents)}")
    if as_int(k.get("intent_count")) != 0:
        reasons.append(f"INTENT_COUNT_NOT_ZERO:{k.get('intent_count')}")
    if as_int(k.get("would_order_intent_count")) != 0:
        reasons.append(f"WOULD_ORDER_INTENT_COUNT_NOT_ZERO:{k.get('would_order_intent_count')}")
    if as_bool(k.get("paper_order_allowed")) is not False:
        reasons.append(f"PAPER_ORDER_ALLOWED_NOT_FALSE:{k.get('paper_order_allowed')}")
    if as_bool(k.get("allow_paper_entry")) is not False:
        reasons.append(f"ALLOW_PAPER_ENTRY_NOT_FALSE:{k.get('allow_paper_entry')}")
    if as_bool(k.get("would_order")) is not False:
        reasons.append(f"WOULD_ORDER_NOT_FALSE:{k.get('would_order')}")
    if as_bool(k.get("dry_run_only")) is not True:
        reasons.append(f"DRY_RUN_ONLY_NOT_TRUE:{k.get('dry_run_only')}")
    if as_bool(k.get("live_allowed")) is not False:
        reasons.append(f"LIVE_ALLOWED_NOT_FALSE:{k.get('live_allowed')}")
    if as_bool(k.get("risk_up_allowed")) is not False:
        reasons.append(f"RISK_UP_ALLOWED_NOT_FALSE:{k.get('risk_up_allowed')}")
    if as_bool(k.get("gate_loosen_allowed")) is not False:
        reasons.append(f"GATE_LOOSEN_ALLOWED_NOT_FALSE:{k.get('gate_loosen_allowed')}")
    if as_bool(k.get("entry_from_watch_recheck_deny_allowed")) is not False:
        reasons.append(f"ENTRY_FROM_WATCH_RECHECK_DENY_ALLOWED_NOT_FALSE:{k.get('entry_from_watch_recheck_deny_allowed')}")

    return len(reasons) == 0, reasons or ["K_CLEAN"]


def l_clean(l: dict[str, Any]) -> tuple[bool, list[str]]:
    if not isinstance(l, dict) or not l:
        return True, ["L_MISSING_OR_EMPTY_TREATED_CLEAN_FOR_SHADOW_SIM"]

    reasons = []
    decision = str(l.get("decision") or "NO_VALID_ORDER_INTENT")
    orders = l.get("orders") if isinstance(l.get("orders"), list) else []
    errors = l.get("errors") if isinstance(l.get("errors"), list) else []

    if decision not in {"NO_VALID_ORDER_INTENT", "HOLD", ""}:
        reasons.append(f"L_DECISION_NOT_CLEAN:{decision}")
    if orders:
        reasons.append(f"L_ACTIVE_ORDERS_PRESENT:{len(orders)}")
    if errors:
        reasons.append(f"L_ACTIVE_ERRORS_PRESENT:{len(errors)}")

    return len(reasons) == 0, reasons or ["L_CLEAN"]


def find_nested_candidate(obj: Any) -> dict[str, Any] | None:
    if not isinstance(obj, dict):
        return None

    direct_keys = [
        "clean_selected_candidate",
        "selected_candidate_shadow",
        "selected_candidate",
        "candidate",
    ]
    for key in direct_keys:
        v = obj.get(key)
        if isinstance(v, dict) and (v.get("pair") or v.get("order_pair")):
            return v

    for key in ("top_evaluated", "candidates", "candidate_records", "records"):
        v = obj.get(key)
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict) and (item.get("pair") or item.get("order_pair")):
                    return item

    return None


def norm_side(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip().upper()
    if s in {"BUY", "LONG"}:
        return "LONG"
    if s in {"SELL", "SHORT"}:
        return "SHORT"
    return s or None


def candidate_summary(c: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(c, dict):
        return {}

    return {
        "pair": c.get("pair") or c.get("order_pair"),
        "side": norm_side(c.get("side") or c.get("order_side") or c.get("direction")),
        "score": c.get("score"),
        "cvdoi": c.get("cvdoi") or c.get("cvdoi_label"),
        "trigger": c.get("trigger"),
        "smc": c.get("smc"),
        "latest": c.get("latest") or c.get("latest_before"),
        "strict_ok": c.get("strict_ok"),
        "reject_reasons": c.get("reject_reasons") or c.get("reasons") or [],
        "source": c.get("source") or c.get("source_file") or c.get("scanner_selection_source"),
    }


def collect_blockers(texts: list[Any]) -> list[str]:
    joined = " ".join(str(x) for x in texts if x is not None).upper()
    blockers = []
    known = [
        "LOW_CONFLUENCE",
        "HARD_BLOCKER_PRESENT",
        "TRIGGER_NOT_CONFIRMED",
        "TRIGGER_NOT_READY",
        "SMC_NOT_GOOD",
        "SIDE_FLOW_NOT_STRONG",
        "RECENT_PAIR_COOLDOWN_ACTIVE",
        "CVD_DEGRADATION_ACTIVE",
        "SHARED_METRIC_CAUTION",
        "AS5_TOP_EVALUATED_NO_EXPLICIT_REASON",
        "CALIBRATED_FLOW_WATCH_ONLY",
        "PARTIAL_CONFLUENCE_RECHECK",
        "FLOW_DIRECTION_BLOCK",
        "BULL_TRAP_RISK",
        "BEAR_TRAP_RISK",
    ]
    for b in known:
        if b in joined:
            blockers.append(b)
    return blockers


def simulate_ba4_rule(
    ba4: dict[str, Any],
    ba4a: dict[str, Any],
    as5: dict[str, Any],
    at1: dict[str, Any],
    k_ok: bool,
    l_ok: bool,
    max_input_age_sec: int,
) -> dict[str, Any]:
    reasons = []
    warnings = []

    ba4_fd = ba4.get("final_decision")
    ba4a_fd = ba4a.get("final_decision")

    if ba4_fd != BA4_READY:
        reasons.append(f"BA4_NOT_READY:{ba4_fd}")
    if ba4a_fd != BA4A_CLEAN:
        reasons.append(f"BA4A_K_NOT_CLEAN_READY:{ba4a_fd}")
    if not k_ok:
        reasons.append("K_NOT_CLEAN")
    if not l_ok:
        reasons.append("L_NOT_CLEAN")

    as5_fd = as5.get("final_decision") if isinstance(as5, dict) else None
    at1_fd = at1.get("final_decision") if isinstance(at1, dict) else None

    candidate = find_nested_candidate(as5) or find_nested_candidate(at1)
    cs = candidate_summary(candidate)

    as5_age = as5.get("_age_sec")
    at1_age = at1.get("_age_sec")
    freshest_age = None
    for x in (as5_age, at1_age):
        if isinstance(x, (int, float)):
            freshest_age = x if freshest_age is None else min(freshest_age, x)

    if freshest_age is None:
        reasons.append("NO_AS5_OR_AT1_ACTIVE_INPUT")
    elif freshest_age > max_input_age_sec:
        reasons.append(f"SCANNER_INPUT_STALE:{freshest_age:.1f}s>{max_input_age_sec}s")

    if not cs:
        reasons.append("NO_CANDIDATE_FOR_SHADOW_SIMULATION")

    runtime_texts = [
        as5_fd,
        at1_fd,
        as5.get("blockers") if isinstance(as5, dict) else None,
        at1.get("blockers") if isinstance(at1, dict) else None,
        cs.get("reject_reasons") if cs else None,
        cs.get("latest") if cs else None,
    ]
    blockers = collect_blockers(runtime_texts)

    hard_blockers = [
        "LOW_CONFLUENCE",
        "HARD_BLOCKER_PRESENT",
        "TRIGGER_NOT_CONFIRMED",
        "TRIGGER_NOT_READY",
        "SMC_NOT_GOOD",
        "SIDE_FLOW_NOT_STRONG",
        "RECENT_PAIR_COOLDOWN_ACTIVE",
        "CVD_DEGRADATION_ACTIVE",
    ]
    watch_only = [
        "SHARED_METRIC_CAUTION",
        "AS5_TOP_EVALUATED_NO_EXPLICIT_REASON",
        "CALIBRATED_FLOW_WATCH_ONLY",
        "PARTIAL_CONFLUENCE_RECHECK",
    ]

    for b in blockers:
        if b in hard_blockers:
            reasons.append(f"HARD_BLOCKER:{b}")
        elif b in watch_only:
            reasons.append(f"WATCH_ONLY_NOT_ORDERABLE:{b}")
        elif b == "FLOW_DIRECTION_BLOCK":
            warnings.append("FLOW_DIRECTION_BLOCK_PRESENT_REVIEW_ONLY_NOT_AUTO_ORDER")

    strict_ok = cs.get("strict_ok")
    if strict_ok is False:
        reasons.append("CANDIDATE_STRICT_OK_FALSE")

    trigger = str(cs.get("trigger") or "").upper()
    smc = str(cs.get("smc") or "").upper()
    if cs and trigger and "TRIGGER_CONFIRMED" not in trigger:
        reasons.append(f"TRIGGER_NOT_CONFIRMED_BY_VALUE:{trigger}")
    if cs and smc and "GOOD" not in smc and "SMC_A" not in smc and "SMC_B" not in smc:
        reasons.append(f"SMC_NOT_GOOD_BY_VALUE:{smc}")

    ready_markers = [
        "READY_FOR_AQ_REVIEW_ONLY",
        "READY_FOR_BA5",
        "READY_SHADOW",
    ]
    ready_signal = any(m in str(as5_fd or "") for m in ready_markers) or any(m in str(at1_fd or "") for m in ready_markers)

    if "HOLD_NO_CLEAN" in str(as5_fd or "") or "HOLD_NO_CLEAN" in str(at1_fd or ""):
        reasons.append("UPSTREAM_HOLD_NO_CLEAN_CANDIDATE")
    if "HOLD" in str(as5_fd or "") and not ready_signal:
        reasons.append(f"AS5_HOLD:{as5_fd}")
    if "HOLD" in str(at1_fd or "") and not ready_signal:
        warnings.append(f"AT1_HOLD:{at1_fd}")

    if not reasons and ready_signal and cs:
        final = "F4X_BA5_READY_SHADOW_ONLY_NO_K_WRITE_REVIEW_REQUIRED"
        next_action = "Shadow rule says candidate could be reviewed. Do not write K. Next would be BA6 temp K draft only after explicit approval."
    else:
        final = "F4X_BA5_HOLD_SHADOW_RULE_NOT_READY"
        next_action = "Hold. Keep scanner/watcher running; no K write and no L execute."

    return {
        "final_decision": final,
        "next_action": next_action,
        "shadow_reasons": reasons or ["NONE"],
        "shadow_warnings": warnings or ["NONE"],
        "upstream": {
            "ba4_final_decision": ba4_fd,
            "ba4a_final_decision": ba4a_fd,
            "as5_final_decision": as5_fd,
            "at1_final_decision": at1_fd,
            "as5_age_sec": as5_age,
            "at1_age_sec": at1_age,
        },
        "candidate_shadow": cs,
        "blockers_detected": blockers,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--max-input-age-sec", type=int, default=900)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    paths = {
        "ba4": runtime / "F4X_BA4_GUARDED_AUTO_PAPER_RULE_DRAFT_REVIEW_AUDIT_ACTIVE.json",
        "ba4a": runtime / "F4X_BA4A_K_ACTIVE_SIGNAL_CLEAN_STATE_PRECHECK_AUDIT_ACTIVE.json",
        "as5": runtime / "F4X_AS5_NEXT_NON_COOLDOWN_STRICT_CANDIDATE_SELECTOR_SHADOW_ONLY_ACTIVE.json",
        "at1": runtime / "F4X_AT1_AUTO_SCANNER_READY_WATCHER_SHADOW_ONLY_ACTIVE.json",
        "k": runtime / "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json",
        "l": runtime / "F4X_L_PAPER_BRIDGE_ACTIVE_EXECUTION.json",
    }

    ba4 = read_json(paths["ba4"], {})
    ba4a = read_json(paths["ba4a"], {})
    as5 = read_json(paths["as5"], {})
    at1 = read_json(paths["at1"], {})
    k = read_json(paths["k"], {})
    l = read_json(paths["l"], {})

    if isinstance(as5, dict):
        as5["_age_sec"] = file_age_sec(paths["as5"])
    if isinstance(at1, dict):
        at1["_age_sec"] = file_age_sec(paths["at1"])

    failures = []
    warnings = []

    for key in ("ba4", "ba4a", "k"):
        if not paths[key].exists():
            failures.append(f"{key.upper()}_INPUT_MISSING")

    k_ok, k_reasons = k_clean(k)
    l_ok, l_reasons = l_clean(l)

    if not k_ok:
        failures.append("K_NOT_CLEAN_PRECONDITION")
    if not l_ok:
        failures.append("L_NOT_CLEAN_PRECONDITION")

    sim = simulate_ba4_rule(
        ba4=ba4 if isinstance(ba4, dict) else {},
        ba4a=ba4a if isinstance(ba4a, dict) else {},
        as5=as5 if isinstance(as5, dict) else {},
        at1=at1 if isinstance(at1, dict) else {},
        k_ok=k_ok,
        l_ok=l_ok,
        max_input_age_sec=args.max_input_age_sec,
    )

    if failures:
        final_decision = "F4X_BA5_HOLD_PRECONDITION_FAIL"
        next_action = "Fix BA5 preconditions. No K write or L execute."
    else:
        final_decision = sim["final_decision"]
        next_action = sim["next_action"]

    result = {
        "event": OUT_PREFIX,
        "generated_at": now_utc(),
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
        "preconditions": {
            "k_clean": k_ok,
            "k_reasons": k_reasons,
            "l_clean": l_ok,
            "l_reasons": l_reasons,
            "max_input_age_sec": args.max_input_age_sec,
        },
        "paths": {k: str(v) for k, v in paths.items()},
        "path_ages": {k: file_age_sec(v) for k, v in paths.items()},
        "simulation": sim,
        "decision_policy": [
            "BA5 is shadow-only.",
            "BA5 reads runtime and simulates BA4 guarded rules only.",
            "BA5 does not write active K.",
            "BA5 does not execute L.",
            "BA5 does not forceenter.",
            "BA5 does not create paper order.",
            "BA5 does not enable live, risk-up, or gate-loosen.",
            "If BA5 returns READY_SHADOW, next is BA6 temp K draft only after explicit approval.",
            "If BA5 returns HOLD, keep AT1/AS5 watcher and collect evidence.",
        ],
    }

    full = runtime / f"{OUT_PREFIX}_FULL.json"
    active = runtime / f"{OUT_PREFIX}_ACTIVE.json"
    compact = runtime / f"{OUT_PREFIX}_COMPACT.txt"

    write_json(full, result)
    write_json(active, result)

    cs = sim.get("candidate_shadow") or {}

    lines = [
        "F4X_BA5_SHADOW_AUTO_PAPER_RULE_SIMULATION_ONLY_COMPACT",
        f"generated_at={result['generated_at']}",
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
        "PRECONDITIONS",
        f"k_clean={k_ok}|k_reasons={k_reasons}",
        f"l_clean={l_ok}|l_reasons={l_reasons}",
        f"max_input_age_sec={args.max_input_age_sec}",
        "UPSTREAM",
        f"ba4={sim['upstream'].get('ba4_final_decision')}",
        f"ba4a={sim['upstream'].get('ba4a_final_decision')}",
        f"as5={sim['upstream'].get('as5_final_decision')}|age_sec={sim['upstream'].get('as5_age_sec')}",
        f"at1={sim['upstream'].get('at1_final_decision')}|age_sec={sim['upstream'].get('at1_age_sec')}",
        "SHADOW_CANDIDATE",
        f"pair={cs.get('pair')}|side={cs.get('side')}|score={cs.get('score')}|cvdoi={cs.get('cvdoi')}|trigger={cs.get('trigger')}|smc={cs.get('smc')}|strict_ok={cs.get('strict_ok')}|latest={cs.get('latest')}",
        "BLOCKERS_DETECTED",
        *(sim.get("blockers_detected") or ["NONE"]),
        "SHADOW_REASONS",
        *(sim.get("shadow_reasons") or ["NONE"]),
        "SHADOW_WARNINGS",
        *(sim.get("shadow_warnings") or ["NONE"]),
        "DECISION_POLICY",
        *result["decision_policy"],
        "OUTPUT_FILES",
        f"full_json={full}",
        f"compact={compact}",
        f"active={active}",
    ]

    compact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(compact.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
