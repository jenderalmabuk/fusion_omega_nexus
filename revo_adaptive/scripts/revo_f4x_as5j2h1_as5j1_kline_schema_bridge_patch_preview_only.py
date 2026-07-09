#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT_PREFIX = "F4X_AS5J2H1_AS5J1_KLINE_SCHEMA_BRIDGE_PATCH_PREVIEW_ONLY"
MODE = "AS5J1_KLINE_SCHEMA_BRIDGE_PATCH_PREVIEW_ONLY"

TARGET_REL = "scripts/revo_f4x_as5j1_feeder_metric_coverage_and_source_freshness_repair_preview_audit.py"
TARGET_OFFICIAL_PREFIX = "F4X_AS5J1_FEEDER_METRIC_COVERAGE_AND_SOURCE_FRESHNESS_REPAIR_PREVIEW_AUDIT"
SAFE_PREFIX = "F4X_AS5J2H1_AS5J1_KLINE_SCHEMA_BRIDGE_SAFE_RUN"

K_FILE = "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json"
L_FILE = "F4X_L_PAPER_BRIDGE_ACTIVE_EXECUTION.json"

RAW_KLINE_KEYS = [
    "kline", "klines", "candles", "tf_1m", "tf_5m", "tf_15m",
    "kline_1m", "kline_5m", "kline_15m", "last_close", "prev_close",
]

DERIVED_KLINE_KEYS = [
    # F3A/F3B/F3C/F3D kline-derived price context.
    "last_price", "mark_price", "markPrice",
    "price_1m_delta_pct", "price_5m_delta_pct", "price_15m_delta_pct", "price_1h_delta_pct",
    "price_change_1m_pct", "price_change_5m_pct", "price_change_15m_pct", "price_change_1h_pct",
    # Flow collector aliases.
    "price_delta_15m_pct", "price_delta_1h_pct",
    "price_delta_pct_15m", "price_delta_pct_1h",
    "p15", "p1h",
    # Close/context aliases from full/paper/trigger.
    "close", "event_close", "snapshot_close", "event_price",
]

REQUIRED_MARKERS = [
    "F4X_AS5J2H1_KLINE_SCHEMA_BRIDGE_CANDIDATE",
    "KLINE_DERIVED_PRICE_CONTEXT_KEYS",
    "price_1m_delta_pct",
    "price_5m_delta_pct",
    "price_15m_delta_pct",
    "price_change_15m_pct",
    "last_price",
]

DANGEROUS_CALL_NAMES = {
    "open_position", "create_order", "force_entry", "forceenter",
    "forceenter_pair", "close_all_positions", "force_close_all_positions",
}
DANGEROUS_TRUE_ASSIGN_NAMES = {
    "live_allowed", "risk_up_allowed", "gate_loosen_allowed",
    "forceenter_allowed", "paper_order_allowed", "k_write_allowed", "l_execute_allowed",
}


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
    proc = subprocess.run(["python3", "-m", "py_compile", str(path)], text=True, capture_output=True)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def replace_assignment(src: str, name: str, value: str) -> str:
    pattern = rf'^{name}\s*=\s*["\'][^"\']*["\']'
    repl = f'{name} = "{value}"'
    new, n = re.subn(pattern, repl, src, count=1, flags=re.M)
    if n == 0:
        new = repl + "\n" + src
    return new


def build_kline_block() -> str:
    raw = json.dumps(RAW_KLINE_KEYS, indent=4)
    derived = json.dumps(DERIVED_KLINE_KEYS, indent=4)
    return f'''# F4X_AS5J2H1_KLINE_SCHEMA_BRIDGE_CANDIDATE
# KLINE_DERIVED_PRICE_CONTEXT = price deltas derived from actual kline fetches.
# This is valid for AS5J1 fuel-readiness coverage only; it does NOT replace trigger RSI/Stoch raw candle logic.
KLINE_RAW_CONTEXT_KEYS = {raw}
KLINE_DERIVED_PRICE_CONTEXT_KEYS = {derived}
KLINE_KEYS = sorted(set(KLINE_RAW_CONTEXT_KEYS + KLINE_DERIVED_PRICE_CONTEXT_KEYS))'''


def patch_source(src: str) -> tuple[str, list[str]]:
    notes = []
    s = src

    s = replace_assignment(s, "OUT_PREFIX", SAFE_PREFIX)
    s = replace_assignment(s, "MODE", "AS5J2H1_SAFE_RUN_AS5J1_KLINE_SCHEMA_BRIDGE")

    block = build_kline_block()

    # Most AS5J1 variants define KLINE_KEYS on one line.
    s2, n = re.subn(r'^KLINE_KEYS\s*=\s*\[[^\n]*\]', block, s, count=1, flags=re.M)
    if n == 0:
        # Fallback for short multi-line list. Stop before next ALL_CAPS assignment or def.
        pattern = r'^KLINE_KEYS\s*=\s*\[(?:.|\n)*?\]\s*(?=\n[A-Z_]+\s*=|\ndef |\nclass )'
        s2, n = re.subn(pattern, block + "\n", s, count=1, flags=re.M)
    if n == 0:
        raise RuntimeError("KLINE_KEYS_ASSIGNMENT_NOT_FOUND")

    notes.append("KLINE_KEYS_REPLACED_WITH_RAW_PLUS_DERIVED_PRICE_CONTEXT")
    return s2, notes


def ast_call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = ast_call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def smart_forbidden_scan(src: str) -> dict[str, Any]:
    hits = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {"ok": False, "hits": [{"type": "syntax_error", "detail": str(e)}]}

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = ast_call_name(node.func) or ""
            short = name.rsplit(".", 1)[-1]
            if short in DANGEROUS_CALL_NAMES:
                hits.append({"type": "dangerous_call", "name": name, "lineno": getattr(node, "lineno", None)})
            if name == "subprocess.Popen":
                hits.append({"type": "subprocess_popen", "name": name, "lineno": getattr(node, "lineno", None)})

        if isinstance(node, ast.Assign):
            val_true = isinstance(node.value, ast.Constant) and node.value is True
            if val_true:
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id in DANGEROUS_TRUE_ASSIGN_NAMES:
                        hits.append({"type": "dangerous_true_assignment", "name": t.id, "lineno": getattr(node, "lineno", None)})

        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                try:
                    key = ast.literal_eval(k) if k is not None else None
                except Exception:
                    key = None
                if key in DANGEROUS_TRUE_ASSIGN_NAMES and isinstance(v, ast.Constant) and v.value is True:
                    hits.append({"type": "dangerous_true_dict_value", "name": key, "lineno": getattr(node, "lineno", None)})

    return {"ok": len(hits) == 0, "hits": hits}


def get_summary(active: Any) -> dict[str, Any]:
    if not isinstance(active, dict):
        return {}
    if isinstance(active.get("new_summary"), dict):
        return active["new_summary"]
    if isinstance(active.get("pair_coverage_summary"), dict):
        return active["pair_coverage_summary"]
    return {}


def state_counts(summary: dict[str, Any]) -> dict[str, int]:
    sc = summary.get("state_counts") if isinstance(summary, dict) else {}
    return {str(k): int(v) for k, v in sc.items()} if isinstance(sc, dict) else {}


def missing_counts(summary: dict[str, Any]) -> dict[str, int]:
    mc = summary.get("missing_counts") if isinstance(summary, dict) else {}
    out = {}
    if isinstance(mc, dict):
        items = mc.items()
    elif isinstance(mc, list):
        items = mc
    else:
        items = []
    for item in items:
        try:
            out[str(item[0])] = int(item[1])
        except Exception:
            pass
    return out


def run_candidate(candidate: Path, repo: Path, runtime: Path, min_volume: float, max_age: int, top_n: int) -> dict[str, Any]:
    cmd = [
        "python3", str(candidate),
        "--repo-dir", str(repo),
        "--runtime-dir", str(runtime),
        "--min-volume-usd", str(min_volume),
        "--max-age-sec", str(max_age),
        "--top-n", str(top_n),
    ]
    proc = subprocess.run(cmd, cwd=str(repo), text=True, capture_output=True, timeout=300)
    if proc.returncode != 0 and ("unrecognized arguments" in proc.stderr or "usage:" in proc.stderr):
        cmd = [
            "python3", str(candidate),
            "--repo-dir", str(repo),
            "--runtime-dir", str(runtime),
            "--min-volume-usd", str(min_volume),
            "--top-n", str(top_n),
        ]
        proc = subprocess.run(cmd, cwd=str(repo), text=True, capture_output=True, timeout=300)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "cmd": cmd,
        "stdout_tail": proc.stdout[-8000:],
        "stderr_tail": proc.stderr[-8000:],
    }


def inspect_k_l(runtime: Path) -> dict[str, Any]:
    k = read_json(runtime / K_FILE, {})
    l = read_json(runtime / L_FILE, {})
    k_clean = isinstance(k, dict) and not k.get("has_order_intent") and int(k.get("intent_count") or 0) == 0 and not k.get("order_intents")
    l_clean = not isinstance(l, dict) or str(l.get("decision") or "NO_VALID_ORDER_INTENT") in {"NO_VALID_ORDER_INTENT", "HOLD", ""}
    return {
        "k_clean": k_clean,
        "k_mode": k.get("mode") if isinstance(k, dict) else None,
        "k_intent_count": k.get("intent_count") if isinstance(k, dict) else None,
        "k_has_order_intent": k.get("has_order_intent") if isinstance(k, dict) else None,
        "l_clean": l_clean,
        "l_decision": l.get("decision") if isinstance(l, dict) else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--min-volume-usd", type=float, default=4000000.0)
    ap.add_argument("--max-age-sec", type=int, default=1800)
    ap.add_argument("--top-n", type=int, default=80)
    args = ap.parse_args()

    repo = Path(args.repo_dir)
    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    target = repo / TARGET_REL
    target_src = read_text(target)

    failures = []
    warnings = []
    patch_notes = []

    k_hash_before = sha256_file(runtime / K_FILE)
    l_hash_before = sha256_file(runtime / L_FILE)

    official_active_path = runtime / f"{TARGET_OFFICIAL_PREFIX}_ACTIVE.json"
    old_active = read_json(official_active_path, {}) or {}
    old_summary = get_summary(old_active)
    old_sc = state_counts(old_summary)
    old_mc = missing_counts(old_summary)

    candidate_source = runtime / "F4X_AS5J2H1_AS5J1_KLINE_SCHEMA_BRIDGE_CANDIDATE_SOURCE_PREVIEW.py"
    diff_path = runtime / "F4X_AS5J2H1_AS5J1_KLINE_SCHEMA_BRIDGE_PATCH_PREVIEW.diff"

    try:
        candidate_src, patch_notes = patch_source(target_src)
    except Exception as e:
        failures.append(f"PATCH_BUILD_FAILED:{type(e).__name__}:{e}")
        candidate_src = ""

    if candidate_src:
        candidate_source.write_text(candidate_src, encoding="utf-8")
        diff_lines = list(difflib.unified_diff(
            target_src.splitlines(),
            candidate_src.splitlines(),
            fromfile=TARGET_REL,
            tofile=TARGET_REL + ".AS5J2H1_KLINE_SCHEMA_BRIDGE_CANDIDATE",
            lineterm="",
        ))
        diff_path.write_text("\n".join(diff_lines) + "\n", encoding="utf-8")
    else:
        diff_lines = []
        diff_path.write_text("", encoding="utf-8")

    compile_result = py_compile(candidate_source) if candidate_source.exists() else {"ok": False, "stderr": "candidate missing"}
    if not compile_result.get("ok"):
        failures.append("CANDIDATE_COMPILE_FAILED")

    markers = {m: (m in candidate_src) for m in REQUIRED_MARKERS}
    missing_markers = [k for k, v in markers.items() if not v]
    if missing_markers:
        failures.append("CANDIDATE_MARKERS_MISSING:" + ",".join(missing_markers))

    forbidden_scan = smart_forbidden_scan(candidate_src)
    if not forbidden_scan.get("ok"):
        failures.append("CANDIDATE_SMART_FORBIDDEN_SCAN_FAILED")

    candidate_run = {}
    candidate_active = {}
    if not failures:
        candidate_run = run_candidate(candidate_source, repo, runtime, args.min_volume_usd, args.max_age_sec, args.top_n)
        if not candidate_run.get("ok"):
            failures.append("CANDIDATE_SAFE_RUN_FAILED")
        candidate_active = read_json(runtime / f"{SAFE_PREFIX}_ACTIVE.json", {}) or {}

    k_hash_after = sha256_file(runtime / K_FILE)
    l_hash_after = sha256_file(runtime / L_FILE)

    if k_hash_before != k_hash_after:
        failures.append("K_FILE_CHANGED_DURING_SAFE_RUN")
    if l_hash_before != l_hash_after:
        failures.append("L_FILE_CHANGED_DURING_SAFE_RUN")

    new_summary = get_summary(candidate_active)
    new_sc = state_counts(new_summary)
    new_mc = missing_counts(new_summary)

    old_kline_missing = old_mc.get("KLINE_CONTEXT_MISSING")
    new_kline_missing = new_mc.get("KLINE_CONTEXT_MISSING")
    old_fuel_ready = old_sc.get("FUEL_READY_WITH_OVERLAY_PREVIEW", 0)
    new_fuel_ready = new_sc.get("FUEL_READY_WITH_OVERLAY_PREVIEW", 0)

    kline_delta = None if old_kline_missing is None or new_kline_missing is None else new_kline_missing - old_kline_missing
    fuel_delta = new_fuel_ready - old_fuel_ready

    if old_kline_missing is None or new_kline_missing is None:
        warnings.append("KLINE_CONTEXT_MISSING_COUNT_NOT_FOUND_FOR_COMPARE")
    elif new_kline_missing >= old_kline_missing:
        warnings.append(f"KLINE_MISSING_NOT_IMPROVED:{old_kline_missing}->{new_kline_missing}")

    if new_fuel_ready <= old_fuel_ready:
        warnings.append(f"FUEL_READY_NOT_INCREASED:{old_fuel_ready}->{new_fuel_ready}")

    if failures:
        final_decision = "F4X_AS5J2H1_KLINE_SCHEMA_BRIDGE_PATCH_PREVIEW_FAILED_REVIEW_REQUIRED"
        next_action = "Do not execute patch. Review failures."
    elif (kline_delta is not None and kline_delta < 0) or fuel_delta > 0:
        final_decision = "F4X_AS5J2H1_KLINE_SCHEMA_BRIDGE_PATCH_PREVIEW_READY_FOR_EXECUTE_BACKUP_COMPILE_ONLY"
        next_action = "Candidate compiles and safe-run improves kline/fuel coverage. Next execute backup+compile only, no K/L/order."
    else:
        final_decision = "F4X_AS5J2H1_KLINE_SCHEMA_BRIDGE_PATCH_PREVIEW_COMPILES_BUT_IMPROVEMENT_REVIEW_REQUIRED"
        next_action = "Candidate compiles but improvement is unclear. Review active/full output before execute."

    result = {
        "event": OUT_PREFIX,
        "generated_at": now_utc(),
        "mode": MODE,
        "target": str(target),
        "paper_order_allowed": False,
        "k_write_allowed": False,
        "l_execute_allowed": False,
        "forceenter_allowed": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "collector_restart_allowed": False,
        "runpaper_restart_allowed": False,
        "source_overwrite_allowed": False,
        "final_decision": final_decision,
        "next_action": next_action,
        "failures": failures,
        "warnings": warnings,
        "patch_notes": patch_notes,
        "candidate_source": str(candidate_source),
        "diff": str(diff_path),
        "diff_line_count": len(diff_lines),
        "compile": compile_result,
        "markers": markers,
        "missing_markers": missing_markers,
        "smart_forbidden_scan": forbidden_scan,
        "safe_run": candidate_run,
        "k_l_integrity": {
            "k_hash_before": k_hash_before,
            "k_hash_after": k_hash_after,
            "l_hash_before": l_hash_before,
            "l_hash_after": l_hash_after,
            "k_unchanged": k_hash_before == k_hash_after,
            "l_unchanged": l_hash_before == l_hash_after,
            "state": inspect_k_l(runtime),
        },
        "compare": {
            "old_final_decision": old_active.get("final_decision") if isinstance(old_active, dict) else None,
            "new_final_decision": candidate_active.get("final_decision") if isinstance(candidate_active, dict) else None,
            "old_state_counts": old_sc,
            "new_state_counts": new_sc,
            "old_missing_counts": old_mc,
            "new_missing_counts": new_mc,
            "kline_missing_delta_new_minus_old": kline_delta,
            "fuel_ready_delta_new_minus_old": fuel_delta,
        },
        "decision_policy": [
            "AS5J2H1 is patch-preview only.",
            "AS5J2H1 does not overwrite AS5J1 source.",
            "AS5J2H1 writes candidate source and diff under runtime dir.",
            "AS5J2H1 safe-runs candidate with separate output prefix.",
            "AS5J2H1 does not write K.",
            "AS5J2H1 does not execute L.",
            "AS5J2H1 does not create paper order.",
            "AS5J2H1 does not enable live, risk-up, or gate-loosen.",
            "AS5J2H1 does not restart collector or runpaper.",
        ],
    }

    full = runtime / f"{OUT_PREFIX}_FULL.json"
    active = runtime / f"{OUT_PREFIX}_ACTIVE.json"
    compact = runtime / f"{OUT_PREFIX}_COMPACT.txt"

    write_json(full, result)
    write_json(active, result)

    lines = [
        "F4X_AS5J2H1_AS5J1_KLINE_SCHEMA_BRIDGE_PATCH_PREVIEW_ONLY_COMPACT",
        f"generated_at={result['generated_at']}",
        f"mode={MODE}",
        "paper_order=HOLD",
        "k_write=HOLD",
        "l_execute=HOLD",
        "forceenter=HOLD",
        "live=HOLD",
        "risk_up=HOLD",
        "gate_loosen=HOLD",
        "collector_restart=HOLD",
        "runpaper_restart=HOLD",
        "source_overwrite=HOLD",
        "FINAL_DECISION",
        f"final_decision={final_decision}",
        f"next_action={next_action}",
        "FAILURES",
        *(failures if failures else ["NONE"]),
        "WARNINGS",
        *(warnings if warnings else ["NONE"]),
        "PATCH_PREVIEW",
        f"candidate_source={candidate_source}",
        f"diff={diff_path}",
        f"diff_line_count={len(diff_lines)}",
        f"patch_notes={patch_notes}",
        "COMPILE",
        f"candidate_compile_ok={compile_result.get('ok')}|stderr={str(compile_result.get('stderr', '')).strip()}",
        "MARKERS",
        f"markers={markers}",
        f"missing_markers={missing_markers}",
        "SMART_FORBIDDEN_SCAN",
        f"ok={forbidden_scan.get('ok')}|hits={forbidden_scan.get('hits')}",
        "SAFE_RUN",
        f"safe_run_ok={candidate_run.get('ok') if candidate_run else None}|returncode={candidate_run.get('returncode') if candidate_run else None}",
        "K_L_INTEGRITY",
        str(result["k_l_integrity"]),
        "COMPARE",
        f"old_final_decision={result['compare']['old_final_decision']}",
        f"new_final_decision={result['compare']['new_final_decision']}",
        f"old_state_counts={old_sc}",
        f"new_state_counts={new_sc}",
        f"old_missing_counts={old_mc}",
        f"new_missing_counts={new_mc}",
        f"kline_missing_delta_new_minus_old={kline_delta}",
        f"fuel_ready_delta_new_minus_old={fuel_delta}",
        "SAFE_RUN_STDOUT_TAIL",
        str(candidate_run.get("stdout_tail", ""))[-4000:] if candidate_run else "",
        "SAFE_RUN_STDERR_TAIL",
        str(candidate_run.get("stderr_tail", ""))[-4000:] if candidate_run else "",
        "DIFF_HEAD",
        *diff_lines[:220],
        "OUTPUT_FILES",
        f"full_json={full}",
        f"compact={compact}",
        f"active={active}",
        f"candidate_source={candidate_source}",
        f"diff={diff_path}",
        "DECISION_POLICY",
        *result["decision_policy"],
    ]

    compact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(compact.read_text(encoding="utf-8"))

    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
