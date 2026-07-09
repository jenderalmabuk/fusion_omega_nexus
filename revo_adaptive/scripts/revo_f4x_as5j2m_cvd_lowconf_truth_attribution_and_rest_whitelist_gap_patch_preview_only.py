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

OUT_PREFIX = "F4X_AS5J2M_CVD_LOWCONF_TRUTH_ATTRIBUTION_AND_REST_WHITELIST_GAP_PATCH_PREVIEW_ONLY"

TARGET_REL = "scripts/revo_f4x_as5_next_non_cooldown_strict_candidate_selector_shadow_only.py"
L_ACTIVE = "F4X_AS5J2L_AS5_WIDENED_INTAKE_BLOCKER_TRUTH_AUDIT_ONLY_ACTIVE.json"
EXPECTED_L = "F4X_AS5J2L_TRUTH_CVD_LOWCONF_SHARED_SATURATION_AND_REST_WHITELIST_GAP_CONFIRMED_READY_FOR_PATCH_PREVIEW"

K_FILE = "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json"
L_FILE = "F4X_L_PAPER_BRIDGE_ACTIVE_EXECUTION.json"

REQUIRED_MARKERS = [
    "F4X_AS5J2M_TRUTH_ATTRIBUTION_PATCH_PREVIEW",
    "F4X_AS5J2M_NO_GATE_LOOSEN",
    "F4X_AS5J2M_CVD_LOWCONF_SHARED_SATURATION_REPORT",
    "F4X_AS5J2M_REST_WHITELIST_GAP_REPORT",
]

DANGEROUS_CALL_NAMES = {
    "open_position",
    "create_order",
    "force_entry",
    "forceenter",
    "forceenter_pair",
    "close_all_positions",
    "force_close_all_positions",
    "market_order",
    "limit_order",
    "place_order",
}

DANGEROUS_TRUE_ASSIGN_NAMES = {
    "live_allowed",
    "risk_up_allowed",
    "gate_loosen_allowed",
    "forceenter_allowed",
    "paper_order_allowed",
    "k_write_allowed",
    "l_execute_allowed",
    "allow_paper_entry",
    "would_order",
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
    p = subprocess.run(["python3", "-m", "py_compile", str(path)], text=True, capture_output=True)
    return {
        "ok": p.returncode == 0,
        "returncode": p.returncode,
        "stdout": p.stdout[-4000:],
        "stderr": p.stderr[-4000:],
    }


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


def marker_state(src: str) -> dict[str, bool]:
    return {m: (m in src) for m in REQUIRED_MARKERS}


def semantic_k(runtime: Path) -> dict[str, Any]:
    k = read_json(runtime / K_FILE, {}) or {}
    order_intents = k.get("order_intents") if isinstance(k.get("order_intents"), list) else []
    try:
        intent_count = int(k.get("intent_count") or 0)
    except Exception:
        intent_count = 0

    clean = isinstance(k, dict) and not k.get("has_order_intent") and intent_count == 0 and not order_intents

    return {
        "clean": clean,
        "mode": k.get("mode") if isinstance(k, dict) else None,
        "intent_count": k.get("intent_count") if isinstance(k, dict) else None,
        "has_order_intent": k.get("has_order_intent") if isinstance(k, dict) else None,
        "order_intents_len": len(order_intents),
    }


def semantic_l(runtime: Path) -> dict[str, Any]:
    l = read_json(runtime / L_FILE, {}) or {}
    orders = l.get("orders") if isinstance(l.get("orders"), list) else []
    errors = l.get("errors") if isinstance(l.get("errors"), list) else []
    decision = l.get("decision") if isinstance(l, dict) else None

    clean = decision in {"NO_VALID_ORDER_INTENT", "HOLD", "", None} and len(orders) == 0 and len(errors) == 0

    return {
        "clean": clean,
        "decision": decision,
        "orders_count": len(orders),
        "errors_count": len(errors),
        "live_allowed": l.get("live_allowed") if isinstance(l, dict) else None,
        "blocked": l.get("blocked") if isinstance(l, dict) else None,
    }


def build_helper_block() -> str:
    return r'''
# F4X_AS5J2M_TRUTH_ATTRIBUTION_PATCH_PREVIEW
# F4X_AS5J2M_NO_GATE_LOOSEN
# Report-only attribution helpers. These functions do not remove or soften any AS5 reject reason.
# They only classify whether CVD/low-confluence looks pair-specific or shared/global, and expose REST whitelist gaps.

def _f4x_as5j2m_as_list(v):
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, tuple):
        return [str(x) for x in v]
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return []


def _f4x_as5j2m_candidate_from_item(item):
    if isinstance(item, dict):
        c = item.get("candidate") or item.get("c") or item.get("raw") or item
        return c if isinstance(c, dict) else item
    return {}


def _f4x_as5j2m_pair_from_item(item):
    c = _f4x_as5j2m_candidate_from_item(item)
    for obj in (item, c):
        if isinstance(obj, dict):
            for key in ("pair", "symbol", "market", "asset", "order_pair"):
                v = obj.get(key)
                if isinstance(v, str) and v:
                    return v
    return ""


def _f4x_as5j2m_side_from_item(item):
    c = _f4x_as5j2m_candidate_from_item(item)
    for obj in (item, c):
        if isinstance(obj, dict):
            v = obj.get("side")
            if isinstance(v, str) and v:
                return v
    return ""


def _f4x_as5j2m_score_from_item(item):
    c = _f4x_as5j2m_candidate_from_item(item)
    for obj in (item, c):
        if isinstance(obj, dict):
            v = obj.get("score")
            if v is not None:
                return v
    return None


def _f4x_as5j2m_reasons_from_item(item):
    if not isinstance(item, dict):
        return []
    for key in ("reasons", "reason", "reject_reasons", "blocked_reasons"):
        if key in item:
            return _f4x_as5j2m_as_list(item.get(key))
    return []


def _f4x_as5j2m_flow_from_item(item):
    if isinstance(item, dict):
        fl = item.get("flow") or item.get("flow_state") or item.get("flow_context") or {}
        return fl if isinstance(fl, dict) else {}
    return {}


def _f4x_as5j2m_truth_report(evaluated, rest):
    # F4X_AS5J2M_CVD_LOWCONF_SHARED_SATURATION_REPORT
    # F4X_AS5J2M_REST_WHITELIST_GAP_REPORT
    rows = evaluated if isinstance(evaluated, list) else []
    n = len(rows)

    reason_counts = {}
    pair_rows = []
    whitelist_raw = []
    if isinstance(rest, dict):
        whitelist_raw = rest.get("whitelist_pairs") or []
    whitelist = set(str(x) for x in whitelist_raw)

    cvd_pairs = []
    low_pairs = []
    whitelist_gap_pairs = []
    pair_specific_cvd = []
    pair_specific_low = []
    shared_cvd_only = []
    shared_low_only = []

    for item in rows:
        if not isinstance(item, dict):
            continue

        pair = _f4x_as5j2m_pair_from_item(item)
        side = _f4x_as5j2m_side_from_item(item)
        score = _f4x_as5j2m_score_from_item(item)
        reasons = _f4x_as5j2m_reasons_from_item(item)
        flow = _f4x_as5j2m_flow_from_item(item)

        for r in reasons:
            reason_counts[r] = int(reason_counts.get(r, 0)) + 1

        has_cvd = "CVD_DEGRADATION_ACTIVE" in reasons
        has_low = "LOW_CONFLUENCE_ACTIVE" in reasons

        cvd_hits = _f4x_as5j2m_as_list(flow.get("cvd_hits"))
        low_hits = _f4x_as5j2m_as_list(flow.get("low_confluence_hits"))

        if has_cvd:
            cvd_pairs.append(pair)
            if cvd_hits:
                pair_specific_cvd.append(pair)
            else:
                shared_cvd_only.append(pair)

        if has_low:
            low_pairs.append(pair)
            if low_hits:
                pair_specific_low.append(pair)
            else:
                shared_low_only.append(pair)

        in_whitelist = pair in whitelist if pair else False
        if "PAIR_NOT_IN_REST_ACTIVE_WHITELIST" in reasons or (whitelist and pair and not in_whitelist):
            whitelist_gap_pairs.append(pair)

        pair_rows.append({
            "pair": pair,
            "side": side,
            "score": score,
            "reasons": reasons,
            "in_rest_whitelist": in_whitelist,
            "cvd_degradation_active": has_cvd,
            "low_confluence_active": has_low,
            "cvd_pair_specific_hits": cvd_hits[:5],
            "low_confluence_pair_specific_hits": low_hits[:5],
            "cvd_truth_class": (
                "PAIR_SPECIFIC_CVD_DEGRADATION" if has_cvd and cvd_hits
                else "SHARED_OR_GLOBAL_CVD_CAUTION" if has_cvd
                else "NO_CVD_DEGRADATION_REASON"
            ),
            "low_conf_truth_class": (
                "PAIR_SPECIFIC_LOW_CONFLUENCE" if has_low and low_hits
                else "SHARED_OR_GLOBAL_LOW_CONFLUENCE_CAUTION" if has_low
                else "NO_LOW_CONFLUENCE_REASON"
            ),
            "rest_whitelist_class": (
                "PAIR_NOT_IN_REST_ACTIVE_WHITELIST" if pair in whitelist_gap_pairs
                else "PAIR_IN_REST_ACTIVE_WHITELIST"
            ),
        })

    cvd_count = int(reason_counts.get("CVD_DEGRADATION_ACTIVE", 0))
    low_count = int(reason_counts.get("LOW_CONFLUENCE_ACTIVE", 0))
    whitelist_gap_count = int(reason_counts.get("PAIR_NOT_IN_REST_ACTIVE_WHITELIST", 0))

    cvd_ratio = cvd_count / max(1, n)
    low_ratio = low_count / max(1, n)
    whitelist_gap_ratio = max(whitelist_gap_count, len(set(whitelist_gap_pairs))) / max(1, n)

    report = {
        "candidate_count": n,
        "reason_counts": reason_counts,
        "cvd_degradation_count": cvd_count,
        "low_confluence_active_count": low_count,
        "cvd_ratio": cvd_ratio,
        "low_ratio": low_ratio,
        "cvd_shared_saturation": bool(n and cvd_ratio >= 0.95),
        "low_conf_shared_saturation": bool(n and low_ratio >= 0.95),
        "cvd_pair_specific_count": len(set(pair_specific_cvd)),
        "low_conf_pair_specific_count": len(set(pair_specific_low)),
        "cvd_shared_or_global_only_count": len(set(shared_cvd_only)),
        "low_conf_shared_or_global_only_count": len(set(shared_low_only)),
        "rest_whitelist_count": len(whitelist),
        "rest_whitelist_gap_count": len(set(whitelist_gap_pairs)),
        "rest_whitelist_gap_ratio": whitelist_gap_ratio,
        "rest_whitelist_gap_major": bool(n and whitelist_gap_ratio >= 0.40),
        "sample_pair_attribution": pair_rows[:80],
        "rest_whitelist_gap_sample": sorted(set(whitelist_gap_pairs))[:80],
    }
    return report


def _f4x_as5j2m_append_truth_attribution_section(lines, evaluated, rest):
    # Report-only output section. Does not alter selected candidate, reject reasons, K/L, or order path.
    rep = _f4x_as5j2m_truth_report(evaluated, rest)
    lines.append("AS5J2M_TRUTH_ATTRIBUTION_REPORT")
    lines.append(f"candidate_count={rep.get('candidate_count')}")
    lines.append(f"cvd_degradation_count={rep.get('cvd_degradation_count')}|ratio={rep.get('cvd_ratio')}")
    lines.append(f"low_confluence_active_count={rep.get('low_confluence_active_count')}|ratio={rep.get('low_ratio')}")
    lines.append(f"cvd_shared_saturation={rep.get('cvd_shared_saturation')}")
    lines.append(f"low_conf_shared_saturation={rep.get('low_conf_shared_saturation')}")
    lines.append(f"cvd_pair_specific_count={rep.get('cvd_pair_specific_count')}")
    lines.append(f"low_conf_pair_specific_count={rep.get('low_conf_pair_specific_count')}")
    lines.append(f"cvd_shared_or_global_only_count={rep.get('cvd_shared_or_global_only_count')}")
    lines.append(f"low_conf_shared_or_global_only_count={rep.get('low_conf_shared_or_global_only_count')}")
    lines.append("AS5J2M_REST_WHITELIST_GAP_REPORT")
    lines.append(f"rest_whitelist_count={rep.get('rest_whitelist_count')}")
    lines.append(f"rest_whitelist_gap_count={rep.get('rest_whitelist_gap_count')}|ratio={rep.get('rest_whitelist_gap_ratio')}")
    lines.append(f"rest_whitelist_gap_major={rep.get('rest_whitelist_gap_major')}")
    lines.append(f"rest_whitelist_gap_sample={rep.get('rest_whitelist_gap_sample')}")
    lines.append("AS5J2M_PAIR_ATTRIBUTION_SAMPLE")
    for row in rep.get("sample_pair_attribution", [])[:40]:
        try:
            lines.append(json.dumps(row, default=str))
        except Exception:
            lines.append(str(row))
'''.strip("\n")


def inject_helper(src: str) -> tuple[str, bool]:
    if "F4X_AS5J2M_TRUTH_ATTRIBUTION_PATCH_PREVIEW" in src:
        return src, False

    helper = build_helper_block()
    m = re.search(r"^def\s+main\s*\(", src, flags=re.MULTILINE)
    if m:
        idx = m.start()
        return src[:idx].rstrip() + "\n\n\n" + helper + "\n\n\n" + src[idx:], True

    return src.rstrip() + "\n\n\n" + helper + "\n", True


def patch_output_section(src: str) -> tuple[str, bool, str]:
    # Add report section immediately before TOP_EVALUATED_SAMPLE in compact output.
    if "_f4x_as5j2m_append_truth_attribution_section" in src and "locals().get(\"evaluated\"" in src:
        return src, False, "ALREADY_PATCHED"

    needle = '    lines.append("TOP_EVALUATED_SAMPLE")'
    insert = (
        '    _f4x_as5j2m_append_truth_attribution_section('
        'lines, locals().get("evaluated", []), locals().get("rest", {}))\n'
        '    lines.append("TOP_EVALUATED_SAMPLE")'
    )

    if needle in src:
        return src.replace(needle, insert, 1), True, "PATCHED_BEFORE_TOP_EVALUATED_SAMPLE"

    # Fallback: patch before OUTPUT_FILES if sample anchor changed.
    needle2 = '    lines.append("OUTPUT_FILES")'
    insert2 = (
        '    _f4x_as5j2m_append_truth_attribution_section('
        'lines, locals().get("evaluated", []), locals().get("rest", {}))\n'
        '    lines.append("OUTPUT_FILES")'
    )
    if needle2 in src:
        return src.replace(needle2, insert2, 1), True, "PATCHED_BEFORE_OUTPUT_FILES_FALLBACK"

    return src, False, "NO_OUTPUT_ANCHOR_FOUND"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    args = ap.parse_args()

    repo = Path(args.repo_dir)
    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    target = repo / TARGET_REL
    candidate = runtime / "F4X_AS5J2M_CVD_LOWCONF_TRUTH_ATTRIBUTION_AND_REST_WHITELIST_GAP_CANDIDATE_SOURCE_PREVIEW.py"
    diff_path = runtime / "F4X_AS5J2M_CVD_LOWCONF_TRUTH_ATTRIBUTION_AND_REST_WHITELIST_GAP_PATCH_PREVIEW.diff"

    failures: list[str] = []
    warnings: list[str] = []
    actions: list[str] = ["PATCH_PREVIEW_ONLY", "NO_SOURCE_OVERWRITE", "NO_AS5_RERUN", "NO_K_L_ORDER"]

    k_hash_before = sha256_file(runtime / K_FILE)
    l_hash_before = sha256_file(runtime / L_FILE)

    l_active = read_json(runtime / L_ACTIVE, {}) or {}
    l_decision = l_active.get("final_decision") if isinstance(l_active, dict) else None
    if l_decision != EXPECTED_L:
        warnings.append(f"L_FINAL_DECISION_NOT_READY:{l_decision}")

    src = read_text(target)
    if not src:
        failures.append("TARGET_AS5_SOURCE_MISSING_OR_EMPTY")

    candidate_src = ""
    helper_inserted = False
    output_patched = False
    patch_anchor = ""

    if not failures:
        candidate_src, helper_inserted = inject_helper(src)
        candidate_src, output_patched, patch_anchor = patch_output_section(candidate_src)
        if not output_patched:
            failures.append("OUTPUT_SECTION_PATCH_ANCHOR_NOT_FOUND")

    if not failures:
        candidate.write_text(candidate_src, encoding="utf-8")
        diff_lines = list(difflib.unified_diff(
            src.splitlines(),
            candidate_src.splitlines(),
            fromfile=TARGET_REL,
            tofile=TARGET_REL + ".AS5J2M_TRUTH_ATTRIBUTION_CANDIDATE",
            lineterm="",
        ))
        diff_path.write_text("\n".join(diff_lines) + "\n", encoding="utf-8")
    else:
        candidate.write_text(candidate_src or src, encoding="utf-8")
        diff_lines = []
        diff_path.write_text("", encoding="utf-8")

    compile_result = py_compile(candidate) if candidate.exists() else {"ok": False, "stderr": "candidate missing"}
    if not compile_result.get("ok"):
        failures.append("CANDIDATE_COMPILE_FAILED")

    markers = marker_state(candidate_src)
    missing_markers = [m for m, ok in markers.items() if not ok]
    if missing_markers:
        failures.append("CANDIDATE_MARKERS_MISSING:" + ",".join(missing_markers))

    scan = smart_forbidden_scan(candidate_src)
    if not scan.get("ok"):
        failures.append("CANDIDATE_SMART_FORBIDDEN_SCAN_FAILED")

    k_hash_after = sha256_file(runtime / K_FILE)
    l_hash_after = sha256_file(runtime / L_FILE)
    k_state = semantic_k(runtime)
    l_state = semantic_l(runtime)

    if k_hash_before != k_hash_after:
        failures.append("K_FILE_CHANGED_DURING_M_PREVIEW")
    if l_hash_before != l_hash_after:
        failures.append("L_FILE_CHANGED_DURING_M_PREVIEW")
    if not k_state.get("clean"):
        failures.append("K_SEMANTIC_NOT_CLEAN")
    if not l_state.get("clean"):
        failures.append("L_SEMANTIC_NOT_CLEAN")

    if failures:
        final_decision = "F4X_AS5J2M_TRUTH_ATTRIBUTION_PATCH_PREVIEW_FAILED_REVIEW_REQUIRED"
        next_action = "Do not execute patch. Review compile/marker/anchor failures."
    else:
        final_decision = "F4X_AS5J2M_TRUTH_ATTRIBUTION_AND_REST_WHITELIST_GAP_PATCH_PREVIEW_READY_FOR_EXECUTE_BACKUP_COMPILE_ONLY"
        next_action = "Candidate source compiles and adds report-only attribution. Next apply backup+compile only; no AS5 rerun yet."

    result = {
        "event": OUT_PREFIX,
        "generated_at": now_utc(),
        "mode": "CVD_LOWCONF_TRUTH_ATTRIBUTION_AND_REST_WHITELIST_GAP_PATCH_PREVIEW_ONLY",
        "docker_mutation_allowed": False,
        "collector_restart_allowed": False,
        "runpaper_restart_allowed": False,
        "source_overwrite_allowed": False,
        "as5_rerun_allowed": False,
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
        "l_final_decision": l_decision,
        "target": str(target),
        "candidate_source": str(candidate),
        "diff": str(diff_path),
        "patch": {
            "helper_inserted": helper_inserted,
            "output_patched": output_patched,
            "patch_anchor": patch_anchor,
            "diff_line_count": len(diff_lines),
        },
        "compile": compile_result,
        "markers": markers,
        "missing_markers": missing_markers,
        "smart_forbidden_scan": scan,
        "k_l_integrity": {
            "k_hash_before": k_hash_before,
            "k_hash_after": k_hash_after,
            "l_hash_before": l_hash_before,
            "l_hash_after": l_hash_after,
            "k_changed": k_hash_before != k_hash_after,
            "l_changed": l_hash_before != l_hash_after,
            "k_state": k_state,
            "l_state": l_state,
        },
        "decision_policy": [
            "M is patch-preview only.",
            "M does not overwrite source.",
            "M does not rerun AS5.",
            "M does not restart runpaper or collector.",
            "M does not mutate Docker.",
            "M does not write K.",
            "M does not write L.",
            "M does not execute L.",
            "M does not create paper order.",
            "M does not enable live/risk/gate.",
            "M only adds compact report-only attribution for CVD/low-conf saturation and REST whitelist gap.",
            "M does not remove CVD_DEGRADATION_ACTIVE or LOW_CONFLUENCE_ACTIVE from reject reasons.",
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
        "as5_rerun=HOLD",
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
        "ACTIONS",
        *(actions if actions else ["NONE"]),
        "CHAIN",
        f"l_final_decision={l_decision}",
        "PATCH_PREVIEW",
        f"target={target}",
        f"candidate_source={candidate}",
        f"diff={diff_path}",
        f"helper_inserted={helper_inserted}",
        f"output_patched={output_patched}",
        f"patch_anchor={patch_anchor}",
        f"diff_line_count={len(diff_lines)}",
        "COMPILE",
        f"candidate_compile_ok={compile_result.get('ok')}",
        f"candidate_compile_stderr={str(compile_result.get('stderr', '')).strip()}",
        "MARKERS",
        f"markers={markers}",
        f"missing_markers={missing_markers}",
        "SMART_FORBIDDEN_SCAN",
        f"ok={scan.get('ok')}|hits={scan.get('hits')}",
        "K_L_INTEGRITY",
        json.dumps(result["k_l_integrity"], indent=2),
        "DIFF_HEAD",
        *diff_lines[:320],
        "OUTPUT_FILES",
        f"full_json={full}",
        f"compact={compact}",
        f"active={active}",
        f"candidate_source={candidate}",
        f"diff={diff_path}",
        "DECISION_POLICY",
        *result["decision_policy"],
    ]

    compact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(compact.read_text(encoding="utf-8"))

    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
