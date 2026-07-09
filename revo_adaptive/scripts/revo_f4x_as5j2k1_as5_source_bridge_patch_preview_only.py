#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT_PREFIX = "F4X_AS5J2K1_AS5_SOURCE_BRIDGE_PATCH_PREVIEW_ONLY"
TARGET_REL = "scripts/revo_f4x_as5_next_non_cooldown_strict_candidate_selector_shadow_only.py"

REQUIRED_MARKERS = [
    "F4X_AS5J2K1_AS5_SOURCE_BRIDGE_PATCH_PREVIEW",
    "F4X_AS5J2K1_PRESERVE_ORIGINAL_INPUTS",
    "F4X_AS5J2K1_ADD_AS5J1_FUEL_READY_BRIDGE",
    "F4X_AS5J2K1_NO_STRICT_GATE_LOOSEN",
]

K_FILE = "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json"
L_FILE = "F4X_L_PAPER_BRIDGE_ACTIVE_EXECUTION.json"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


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


def sha256_file(path: Path) -> str | None:
    try:
        if not path.exists():
            return None
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def py_compile(path: Path) -> dict[str, Any]:
    p = subprocess.run(["python3", "-m", "py_compile", str(path)], text=True, capture_output=True)
    return {
        "ok": p.returncode == 0,
        "returncode": p.returncode,
        "stdout": p.stdout[-4000:],
        "stderr": p.stderr[-4000:],
    }


def semantic_k(runtime: Path) -> dict[str, Any]:
    k = read_json(runtime / K_FILE, {}) or {}
    order_intents = k.get("order_intents") if isinstance(k.get("order_intents"), list) else []
    try:
        cnt = int(k.get("intent_count") or 0)
    except Exception:
        cnt = 0
    return {
        "clean": isinstance(k, dict) and not k.get("has_order_intent") and cnt == 0 and not order_intents,
        "intent_count": k.get("intent_count") if isinstance(k, dict) else None,
        "has_order_intent": k.get("has_order_intent") if isinstance(k, dict) else None,
        "order_intents_len": len(order_intents),
    }


def semantic_l(runtime: Path) -> dict[str, Any]:
    l = read_json(runtime / L_FILE, {}) or {}
    orders = l.get("orders") if isinstance(l.get("orders"), list) else []
    errors = l.get("errors") if isinstance(l.get("errors"), list) else []
    decision = l.get("decision") if isinstance(l, dict) else None
    return {
        "clean": decision in {"NO_VALID_ORDER_INTENT", "HOLD", "", None} and len(orders) == 0 and len(errors) == 0,
        "decision": decision,
        "orders_count": len(orders),
        "errors_count": len(errors),
        "live_allowed": l.get("live_allowed") if isinstance(l, dict) else None,
    }


def build_patch_block() -> str:
    return r'''
# F4X_AS5J2K1_AS5_SOURCE_BRIDGE_PATCH_PREVIEW
# Purpose: widen AS5 shadow candidate intake with AS5J1 fuel-ready pairs.
# Safety: candidate intake/reporting only; strict_check, flow_state, cooldown, K/L/order/live/risk/gate remain unchanged.

def _f4x_as5j2k1_as_num(v, default=None):
    try:
        if v is None or v == "":
            return default
        x = float(v)
        if x != x:
            return default
        return x
    except Exception:
        return default


def _f4x_as5j2k1_context_candidates(runtime: Path) -> dict[str, dict[str, Any]]:
    ctx: dict[str, dict[str, Any]] = {}
    for p in [
        runtime / "F4X_FULL_CONFLUENCE_FINAL_FULL.json",
        runtime / "F4X_PAPER_DECISION_SIGNALS.json",
    ]:
        obj = read_json(p, None)
        if obj is None:
            continue
        for c in collect_candidates(obj, str(p)):
            pair = str(c.get("pair") or "")
            old = ctx.get(pair)
            if old is None or (_f4x_as5j2k1_as_num(c.get("score"), -999999) or -999999) > (_f4x_as5j2k1_as_num(old.get("score"), -999999) or -999999):
                ctx[pair] = c
    return ctx


def _f4x_as5j2k1_infer_side_from_flow(flow: dict[str, Any]) -> str:
    p15 = _f4x_as5j2k1_as_num(flow.get("price_delta_15m_pct") or flow.get("price_change_15m_pct") or flow.get("p15"), 0.0) or 0.0
    cvd = _f4x_as5j2k1_as_num(flow.get("cvd_delta_15m") or flow.get("cvd_delta"), 0.0) or 0.0
    cvdz = _f4x_as5j2k1_as_num(flow.get("cvd_zscore_15m") or flow.get("cvd_zscore") or flow.get("cvd_z_15m") or flow.get("cvd_z"), 0.0) or 0.0
    oi15 = _f4x_as5j2k1_as_num(flow.get("oi_delta_15m_pct") or flow.get("open_interest_delta_15m_pct") or flow.get("oi15"), 0.0) or 0.0

    long_score = 0
    short_score = 0

    if p15 > 0:
        long_score += 1
    elif p15 < 0:
        short_score += 1

    if cvd > 0 or cvdz >= 1.0:
        long_score += 1
    elif cvd < 0 or cvdz <= -1.0:
        short_score += 1

    if oi15 > 0 and p15 > 0:
        long_score += 1
    elif oi15 > 0 and p15 < 0:
        short_score += 1

    if long_score > short_score:
        return "LONG"
    if short_score > long_score:
        return "SHORT"
    return ""


def _f4x_as5j2k1_cvdoi_from_flow(side: str, flow: dict[str, Any]) -> str:
    cvd = _f4x_as5j2k1_as_num(flow.get("cvd_delta_15m") or flow.get("cvd_delta"), 0.0) or 0.0
    cvdz = _f4x_as5j2k1_as_num(flow.get("cvd_zscore_15m") or flow.get("cvd_zscore") or flow.get("cvd_z_15m") or flow.get("cvd_z"), 0.0) or 0.0
    if side == "LONG" and (cvd > 0 or cvdz >= 1.0):
        return "BULLISH_CONTINUATION_AS5J1_BRIDGE_CVD_OBSERVED"
    if side == "SHORT" and (cvd < 0 or cvdz <= -1.0):
        return "BEARISH_CONTINUATION_AS5J1_BRIDGE_CVD_OBSERVED"
    return "AS5J1_BRIDGE_FLOW_OBSERVED_NOT_STRONG"


def _f4x_as5j2k1_bridge_candidates(runtime: Path, max_age_sec: int) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    out: list[dict[str, Any]] = []

    as5j1_path = runtime / "F4X_AS5J1_FEEDER_METRIC_COVERAGE_AND_SOURCE_FRESHNESS_REPAIR_PREVIEW_AUDIT_FULL.json"
    flow_path = runtime / "revo_flow_context_collector.json"

    as5j1_age = file_age_sec(as5j1_path)
    if not as5j1_path.exists():
        warnings.append("MISSING_INPUT:F4X_AS5J1_FUEL_READY_BRIDGE_SOURCE")
        return out, warnings
    if as5j1_age is not None and as5j1_age > max_age_sec:
        warnings.append(f"STALE_INPUT:F4X_AS5J1_FUEL_READY_BRIDGE_SOURCE:{int(as5j1_age)}s")

    as5j1 = read_json(as5j1_path, {}) or {}
    rows = as5j1.get("rows") if isinstance(as5j1, dict) else []
    if not isinstance(rows, list):
        warnings.append("AS5J1_BRIDGE_ROWS_INVALID")
        return out, warnings

    flow_context = read_json(flow_path, {}) or {}
    if not isinstance(flow_context, dict):
        flow_context = {}

    context_by_pair = _f4x_as5j2k1_context_candidates(runtime)

    fuel_seen = 0
    bridge_added = 0
    bridge_no_side = 0
    bridge_context_enriched = 0

    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("state") != "FUEL_READY_WITH_OVERLAY_PREVIEW":
            continue
        if row.get("missing"):
            continue

        pair = str(row.get("pair") or "").strip()
        if not pair:
            continue
        fuel_seen += 1

        ctx = context_by_pair.get(pair) or {}
        flow = flow_context.get(pair) if isinstance(flow_context.get(pair), dict) else {}

        side = normalize_side(ctx.get("side")) if ctx else ""
        if side not in {"LONG", "SHORT"}:
            side = _f4x_as5j2k1_infer_side_from_flow(flow)

        if side not in {"LONG", "SHORT"}:
            bridge_no_side += 1
            continue

        cvdoi = ctx.get("cvdoi") if ctx else None
        if not cvdoi:
            cvdoi = _f4x_as5j2k1_cvdoi_from_flow(side, flow)

        trigger = ctx.get("trigger") if ctx else None
        if not trigger:
            trigger = "AS5J1_BRIDGE_TRIGGER_PENDING"

        smc = ctx.get("smc") if ctx else None
        if not smc:
            smc = "AS5J1_BRIDGE_SMC_PENDING"

        score = _f4x_as5j2k1_as_num(ctx.get("score") if ctx else None, None)
        if score is None:
            vol = _f4x_as5j2k1_as_num(row.get("volume_usd"), 0.0) or 0.0
            score = min(34.0, max(1.0, vol / 10000000.0))

        raw = {
            "source": "AS5J2K1_AS5J1_FUEL_READY_SOURCE_BRIDGE",
            "as5j1_row": row,
            "flow_context": flow,
            "context_candidate": ctx,
            "bridge_note": "Intake only. Strict AS5 gates remain unchanged; pending trigger/SMC remains reject until real upstream context confirms.",
        }

        out.append({
            "pair": pair,
            "side": side,
            "score": score,
            "cvdoi": cvdoi,
            "trigger": trigger,
            "smc": smc,
            "latest": "AS5J1_FUEL_READY_BRIDGE",
            "source_file": "F4X_AS5J2K1_AS5J1_FUEL_READY_SOURCE_BRIDGE",
            "source_age_sec": as5j1_age,
            "raw": raw,
        })
        bridge_added += 1
        if ctx:
            bridge_context_enriched += 1

    warnings.append(f"F4X_AS5J2K1_BRIDGE_FUEL_READY_SEEN:{fuel_seen}")
    warnings.append(f"F4X_AS5J2K1_BRIDGE_ADDED:{bridge_added}")
    warnings.append(f"F4X_AS5J2K1_BRIDGE_NO_SIDE:{bridge_no_side}")
    warnings.append(f"F4X_AS5J2K1_BRIDGE_CONTEXT_ENRICHED:{bridge_context_enriched}")
    return out, warnings


def load_all_candidates(runtime: Path, max_age_sec: int) -> tuple[list[dict[str, Any]], list[str]]:
    # F4X_AS5J2K1_PRESERVE_ORIGINAL_INPUTS
    # F4X_AS5J2K1_ADD_AS5J1_FUEL_READY_BRIDGE
    # F4X_AS5J2K1_NO_STRICT_GATE_LOOSEN
    files = [
        runtime / "F4X_AP_AUTONOMOUS_SCANNER_DRIVEN_NEXT_CANDIDATE_LOOP_SHADOW_FULL.json",
        runtime / "F4X_AP_AUTONOMOUS_SCANNER_DRIVEN_NEXT_CANDIDATE_LOOP_SHADOW_ACTIVE.json",
        runtime / "F4X_AJ_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_CONVEYOR_SHADOW_FULL.json",
        runtime / "F4X_AJ_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_CONVEYOR_SHADOW_ACTIVE.json",
        runtime / "F4X_FULL_CONFLUENCE_FINAL_FULL.json",
        runtime / "F4X_PAPER_DECISION_SIGNALS.json",
    ]
    warnings = []
    candidates = []
    for p in files:
        age = file_age_sec(p)
        if not p.exists():
            warnings.append(f"MISSING_INPUT:{p.name}")
            continue
        if age is not None and age > max_age_sec:
            warnings.append(f"STALE_INPUT:{p.name}:{int(age)}s")
        obj = read_json(p, None)
        for c in collect_candidates(obj, str(p)):
            c["source_age_sec"] = age
            candidates.append(c)

    bridge_candidates, bridge_warnings = _f4x_as5j2k1_bridge_candidates(runtime, max_age_sec)
    candidates.extend(bridge_candidates)
    warnings.extend(bridge_warnings)

    dedup = {}
    for c in candidates:
        key = (c["pair"], c["side"])
        old = dedup.get(key)
        if old is None or (safe_float(c.get("score"), -999999) or -999999) > (safe_float(old.get("score"), -999999) or -999999):
            dedup[key] = c

    ranked = sorted(dedup.values(), key=lambda x: safe_float(x.get("score"), -999999) or -999999, reverse=True)
    return ranked, warnings
'''.strip("\n")


def replace_load_all_candidates(src: str) -> tuple[str, dict[str, int]]:
    lines = src.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^def\s+load_all_candidates\s*\(", line):
            start = i
            break
    if start is None:
        raise RuntimeError("load_all_candidates not found")

    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^def\s+", lines[j]) or re.match(r"^class\s+", lines[j]) or re.match(r"^if __name__", lines[j]):
            end = j
            break

    block = build_patch_block().splitlines()
    out = "\n".join(lines[:start] + block + lines[end:]) + "\n"
    return out, {"start_line": start + 1, "end_line": end}


def simulate_bridge(runtime: Path) -> dict[str, Any]:
    as5j1 = read_json(runtime / "F4X_AS5J1_FEEDER_METRIC_COVERAGE_AND_SOURCE_FRESHNESS_REPAIR_PREVIEW_AUDIT_FULL.json", {}) or {}
    flow = read_json(runtime / "revo_flow_context_collector.json", {}) or {}
    full = read_json(runtime / "F4X_FULL_CONFLUENCE_FINAL_FULL.json", {}) or {}
    paper = read_json(runtime / "F4X_PAPER_DECISION_SIGNALS.json", {}) or {}

    rows = as5j1.get("rows") if isinstance(as5j1, dict) else []
    if not isinstance(rows, list):
        rows = []

    def pairs_from_obj(obj: Any) -> set[str]:
        out = set()
        def walk(x: Any):
            if isinstance(x, dict):
                p = x.get("pair") or x.get("order_pair") or x.get("symbol")
                if isinstance(p, str) and "/USDT" in p:
                    out.add(p if ":" in p else p + ":USDT")
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        walk(obj)
        return out

    fuel = {r.get("pair") for r in rows if isinstance(r, dict) and r.get("state") == "FUEL_READY_WITH_OVERLAY_PREVIEW" and not r.get("missing")}
    fuel = {str(x) for x in fuel if x}
    full_pairs = pairs_from_obj(full)
    paper_pairs = pairs_from_obj(paper)
    flow_pairs = set(flow.keys()) if isinstance(flow, dict) else set()

    return {
        "fuel_ready_count": len(fuel),
        "fuel_in_flow": len(fuel & flow_pairs),
        "fuel_in_full": len(fuel & full_pairs),
        "fuel_in_paper": len(fuel & paper_pairs),
        "fuel_missing_from_full_paper": sorted(fuel - (full_pairs | paper_pairs))[:120],
        "bridge_expected_min_new_pairs": len(fuel - (full_pairs | paper_pairs)),
        "flow_pair_count": len(flow_pairs),
        "full_pair_count": len(full_pairs),
        "paper_pair_count": len(paper_pairs),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    args = ap.parse_args()

    repo = Path(args.repo_dir)
    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    warnings: list[str] = []
    actions = ["PATCH_PREVIEW_ONLY", "NO_SOURCE_OVERWRITE", "NO_RESTART", "NO_ORDER"]

    k_before = sha256_file(runtime / K_FILE)
    l_before = sha256_file(runtime / L_FILE)

    target = repo / TARGET_REL
    target_src = read_text(target)
    if not target_src:
        failures.append("TARGET_SOURCE_MISSING_OR_EMPTY")

    candidate_src = ""
    span = {}
    candidate_path = runtime / "F4X_AS5J2K1_AS5_SOURCE_BRIDGE_CANDIDATE_SOURCE_PREVIEW.py"
    diff_path = runtime / "F4X_AS5J2K1_AS5_SOURCE_BRIDGE_PATCH_PREVIEW.diff"

    if not failures:
        try:
            candidate_src, span = replace_load_all_candidates(target_src)
            candidate_path.write_text(candidate_src, encoding="utf-8")
            diff_lines = list(difflib.unified_diff(
                target_src.splitlines(),
                candidate_src.splitlines(),
                fromfile=TARGET_REL,
                tofile=TARGET_REL + ".AS5J2K1_SOURCE_BRIDGE_CANDIDATE",
                lineterm="",
            ))
            diff_path.write_text("\n".join(diff_lines) + "\n", encoding="utf-8")
        except Exception as e:
            failures.append(f"PATCH_PREVIEW_BUILD_FAILED:{type(e).__name__}:{e}")
            diff_lines = []
    else:
        diff_lines = []
        diff_path.write_text("", encoding="utf-8")

    compile_result = py_compile(candidate_path) if candidate_path.exists() else {"ok": False, "stderr": "candidate missing"}
    if not compile_result.get("ok"):
        failures.append("CANDIDATE_COMPILE_FAILED")

    markers = {m: (m in candidate_src) for m in REQUIRED_MARKERS}
    missing_markers = [m for m, ok in markers.items() if not ok]
    if missing_markers:
        failures.append("CANDIDATE_MARKERS_MISSING:" + ",".join(missing_markers))

    simulation = simulate_bridge(runtime)

    k_after = sha256_file(runtime / K_FILE)
    l_after = sha256_file(runtime / L_FILE)
    k_state = semantic_k(runtime)
    l_state = semantic_l(runtime)

    if k_before != k_after:
        failures.append("K_FILE_CHANGED_DURING_K1")
    if l_before != l_after:
        failures.append("L_FILE_CHANGED_DURING_K1")
    if not k_state.get("clean"):
        failures.append("K_SEMANTIC_NOT_CLEAN")
    if not l_state.get("clean"):
        failures.append("L_SEMANTIC_NOT_CLEAN")

    if failures:
        final_decision = "F4X_AS5J2K1_AS5_SOURCE_BRIDGE_PATCH_PREVIEW_FAILED_REVIEW_REQUIRED"
        next_action = "Do not execute patch. Review failures and diff."
    else:
        final_decision = "F4X_AS5J2K1_AS5_SOURCE_BRIDGE_PATCH_PREVIEW_READY_FOR_EXECUTE_BACKUP_COMPILE_ONLY"
        next_action = "Candidate source compiles and adds AS5J1 fuel-ready bridge without loosening gates. Next apply backup+compile only; no runpaper restart."

    result = {
        "event": OUT_PREFIX,
        "generated_at": now_utc(),
        "mode": "AS5_SOURCE_BRIDGE_PATCH_PREVIEW_ONLY",
        "docker_mutation_allowed": False,
        "collector_restart_allowed": False,
        "runpaper_restart_allowed": False,
        "source_overwrite_allowed": False,
        "paper_order_allowed": False,
        "k_write_allowed": False,
        "l_write_allowed": False,
        "l_execute_allowed": False,
        "forceenter_allowed": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "final_decision": final_decision,
        "next_action": next_action,
        "failures": failures,
        "warnings": warnings,
        "actions": actions,
        "target": str(target),
        "candidate_source": str(candidate_path),
        "diff": str(diff_path),
        "replaced_span": span,
        "compile": compile_result,
        "markers": markers,
        "missing_markers": missing_markers,
        "diff_line_count": len(diff_lines),
        "simulation": simulation,
        "k_l_integrity": {
            "k_changed": k_before != k_after,
            "l_changed": l_before != l_after,
            "k_state": k_state,
            "l_state": l_state,
        },
        "decision_policy": [
            "K1 is patch-preview only.",
            "K1 does not overwrite AS5 source.",
            "K1 does not restart runpaper or collector.",
            "K1 does not change strict_check, flow_state, cooldown, K, L, order, live, risk, or gate logic.",
            "K1 only proposes widening AS5 candidate intake using AS5J1 fuel-ready bridge.",
        ],
    }

    full = runtime / f"{OUT_PREFIX}_FULL.json"
    active = runtime / f"{OUT_PREFIX}_ACTIVE.json"
    compact = runtime / f"{OUT_PREFIX}_COMPACT.txt"

    write_json(full, result)
    write_json(active, result)

    lines = [
        f"{OUT_PREFIX}_COMPACT",
        f"generated_at={result['generated_at']}",
        "docker_mutation=HOLD",
        "collector_restart=HOLD",
        "runpaper_restart=HOLD",
        "source_overwrite=HOLD",
        "paper_order=HOLD",
        "k_write=HOLD",
        "l_write=HOLD",
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
        "PATCH_PREVIEW",
        f"target={target}",
        f"candidate_source={candidate_path}",
        f"diff={diff_path}",
        f"replaced_span={span}",
        f"diff_line_count={len(diff_lines)}",
        "COMPILE",
        f"candidate_compile_ok={compile_result.get('ok')}",
        f"candidate_compile_stderr={str(compile_result.get('stderr', '')).strip()}",
        "MARKERS",
        f"markers={markers}",
        f"missing_markers={missing_markers}",
        "SIMULATION",
        json.dumps(simulation, indent=2),
        "K_L_INTEGRITY",
        json.dumps(result["k_l_integrity"], indent=2),
        "DIFF_HEAD",
        *diff_lines[:260],
        "OUTPUT_FILES",
        f"full_json={full}",
        f"compact={compact}",
        f"active={active}",
        f"candidate_source={candidate_path}",
        f"diff={diff_path}",
        "DECISION_POLICY",
        *result["decision_policy"],
    ]

    compact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(compact.read_text(encoding="utf-8"))

    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
