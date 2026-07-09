#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT_PREFIX = "F4X_AS5J2F2_AS5J1_SOURCE_PATCH_PREVIEW_ONLY"
MODE = "AS5J1_SOURCE_PATCH_PREVIEW_ONLY"

TARGET_REL = "scripts/revo_f4x_as5j1_feeder_metric_coverage_and_source_freshness_repair_preview_audit.py"
BASE_REL = "scripts/revo_f4x_as5j2f1_as5j1_use_cvd_overlay_and_ttl_recheck_preview_only.py"

CANDIDATE_PREFIX = "F4X_AS5J1_FEEDER_METRIC_COVERAGE_AND_SOURCE_FRESHNESS_REPAIR_PREVIEW_AUDIT"
RUN_PREFIX = "F4X_AS5J2F2_AS5J1_PATCH_CANDIDATE_SAFE_RUN"

REQUIRED_MARKERS = [
    "flow_context",
    "F4X_CVD_TAKER_FLOW_OVERLAY_SCHEMA_BRIDGE_PREVIEW_REPORT_ONLY",
    "FUEL_READY_WITH_OVERLAY_PREVIEW",
    "STALE_REQUIRED_METRIC",
    "CVD_MISSING",
]

FORBIDDEN_TOKENS = [
    "open_position(",
    "create_order(",
    "force_entry(",
    "forceenter_pair",
    "live_allowed = True",
    "risk_up_allowed = True",
    "gate_loosen_allowed = True",
    "subprocess.Popen",
    "docker restart",
    "docker run",
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return default


def py_compile(path: Path) -> dict[str, Any]:
    proc = subprocess.run(
        ["python3", "-m", "py_compile", str(path)],
        text=True,
        capture_output=True,
    )
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


def build_source_from_as5j2f1(base_src: str, prefix: str, mode: str, final_tag: str) -> str:
    s = base_src

    s = replace_assignment(s, "OUT_PREFIX", prefix)
    s = replace_assignment(s, "MODE", mode)

    # Rename common final decision tags if present.
    s = s.replace(
        "F4X_AS5J2F1_OVERLAY_TTL_RECHECK_PREVIEW_IMPROVES_CVD_READY_FOR_AS5J1_PATCH_PREVIEW",
        final_tag,
    )
    s = s.replace(
        "F4X_AS5J2F1_OVERLAY_TTL_RECHECK_PREVIEW_NO_CLEAR_IMPROVEMENT_REVIEW",
        final_tag + "_NO_CLEAR_IMPROVEMENT_REVIEW",
    )
    s = s.replace(
        "F4X_AS5J2F1_OVERLAY_TTL_RECHECK_PREVIEW_INPUT_MISSING",
        final_tag + "_INPUT_MISSING",
    )

    header = f'''#!/usr/bin/env python3
# F4X_AS5J2F2_GENERATED_PATCH_CANDIDATE
# generated_at={now_utc()}
# source_base={BASE_REL}
# purpose=AS5J1 reads CVD overlay/flow_context and uses metric-level TTL.
# safety=NO_K_NO_L_NO_ORDER_NO_LIVE_NO_RISK_NO_GATE

'''
    if s.startswith("#!"):
        s = "\n".join(s.splitlines()[1:]) + "\n"

    return header + s


def forbidden_hits(src: str) -> list[str]:
    return [tok for tok in FORBIDDEN_TOKENS if tok in src]


def marker_hits(src: str) -> dict[str, bool]:
    return {m: (m in src) for m in REQUIRED_MARKERS}


def run_preview_source(preview_source: Path, repo: Path, runtime: Path, min_volume: float, max_age: int, top_n: int) -> dict[str, Any]:
    cmd = [
        "python3",
        str(preview_source),
        "--repo-dir",
        str(repo),
        "--runtime-dir",
        str(runtime),
        "--min-volume-usd",
        str(min_volume),
        "--max-age-sec",
        str(max_age),
        "--top-n",
        str(top_n),
    ]

    proc = subprocess.run(
        cmd,
        cwd=str(repo),
        text=True,
        capture_output=True,
        timeout=240,
    )

    if proc.returncode != 0 and ("unrecognized arguments" in proc.stderr or "usage:" in proc.stderr):
        cmd = [
            "python3",
            str(preview_source),
            "--repo-dir",
            str(repo),
            "--runtime-dir",
            str(runtime),
            "--min-volume-usd",
            str(min_volume),
            "--top-n",
            str(top_n),
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(repo),
            text=True,
            capture_output=True,
            timeout=240,
        )

    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "stdout_tail": proc.stdout[-6000:],
        "stderr_tail": proc.stderr[-6000:],
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
    base = repo / BASE_REL

    failures: list[str] = []
    warnings: list[str] = []

    target_src = read_text(target)
    base_src = read_text(base)

    if not target_src:
        failures.append("TARGET_AS5J1_SOURCE_MISSING_OR_EMPTY")
    if not base_src:
        failures.append("BASE_AS5J2F1_SOURCE_MISSING_OR_EMPTY_RUN_AS5J2F1_FIRST")

    candidate_source = runtime / "F4X_AS5J2F2_AS5J1_PATCH_CANDIDATE_SOURCE_PREVIEW.py"
    preview_run_source = runtime / "F4X_AS5J2F2_AS5J1_PATCH_CANDIDATE_SAFE_RUN.py"
    diff_path = runtime / "F4X_AS5J2F2_AS5J1_SOURCE_PATCH_PREVIEW.diff"

    candidate_src = ""
    run_src = ""

    if not failures:
        candidate_src = build_source_from_as5j2f1(
            base_src,
            CANDIDATE_PREFIX,
            "AS5J1_FEEDER_METRIC_COVERAGE_WITH_CVD_OVERLAY_AND_METRIC_TTL",
            "F4X_AS5J1_OVERLAY_TTL_RECHECK_PREVIEW_IMPROVES_CVD_REPORTING",
        )
        run_src = build_source_from_as5j2f1(
            base_src,
            RUN_PREFIX,
            "AS5J2F2_SAFE_RUN_AS5J1_CVD_OVERLAY_METRIC_TTL_PATCH_CANDIDATE",
            "F4X_AS5J2F2_PATCH_PREVIEW_RUN_OVERLAY_TTL_IMPROVES_CVD_REPORTING",
        )

        candidate_source.write_text(candidate_src, encoding="utf-8")
        preview_run_source.write_text(run_src, encoding="utf-8")

        diff_lines = list(difflib.unified_diff(
            target_src.splitlines(),
            candidate_src.splitlines(),
            fromfile=TARGET_REL,
            tofile=TARGET_REL + ".AS5J2F2_PATCH_CANDIDATE_PREVIEW",
            lineterm="",
        ))
        diff_path.write_text("\n".join(diff_lines) + "\n", encoding="utf-8")
    else:
        diff_lines = []
        diff_path.write_text("", encoding="utf-8")

    candidate_compile = py_compile(candidate_source) if candidate_source.exists() else {"ok": False, "stderr": "candidate missing"}
    run_compile = py_compile(preview_run_source) if preview_run_source.exists() else {"ok": False, "stderr": "run source missing"}

    if not candidate_compile.get("ok"):
        failures.append("CANDIDATE_SOURCE_COMPILE_FAILED")
    if not run_compile.get("ok"):
        failures.append("PREVIEW_RUN_SOURCE_COMPILE_FAILED")

    markers = marker_hits(candidate_src)
    missing_markers = [k for k, v in markers.items() if not v]
    if missing_markers:
        warnings.append("CANDIDATE_MARKERS_MISSING:" + ",".join(missing_markers))

    forbidden = forbidden_hits(candidate_src)
    if forbidden:
        failures.append("FORBIDDEN_TOKENS_FOUND:" + ",".join(forbidden))

    safe_run = {}
    safe_run_active = {}
    if not failures:
        safe_run = run_preview_source(
            preview_run_source,
            repo,
            runtime,
            args.min_volume_usd,
            args.max_age_sec,
            args.top_n,
        )
        if not safe_run.get("ok"):
            failures.append("PATCH_CANDIDATE_SAFE_RUN_FAILED")

        safe_run_active = read_json(runtime / f"{RUN_PREFIX}_ACTIVE.json", {}) or {}

    final_decision_from_run = safe_run_active.get("final_decision") if isinstance(safe_run_active, dict) else None
    improvement = safe_run_active.get("improvement") if isinstance(safe_run_active, dict) else None
    new_summary = safe_run_active.get("new_summary") if isinstance(safe_run_active, dict) else None
    old_summary = safe_run_active.get("old_summary") if isinstance(safe_run_active, dict) else None

    if failures:
        final_decision = "F4X_AS5J2F2_HOLD_PATCH_PREVIEW_REVIEW_REQUIRED"
        next_action = "Do not execute. Review failures, candidate source, and safe-run output."
    elif final_decision_from_run and "IMPROVES_CVD" in str(final_decision_from_run):
        final_decision = "F4X_AS5J2F2_AS5J1_SOURCE_PATCH_PREVIEW_READY_FOR_EXECUTE_BACKUP_COMPILE_ONLY"
        next_action = "Candidate compiles and safe-run improves CVD. Next may execute AS5J1 source patch with backup+compile only. No K/L/order."
    else:
        final_decision = "F4X_AS5J2F2_PATCH_PREVIEW_COMPILES_BUT_IMPROVEMENT_REVIEW_REQUIRED"
        next_action = "Review safe-run results before execute. No source overwrite."

    result = {
        "event": OUT_PREFIX,
        "generated_at": now_utc(),
        "mode": MODE,
        "target": str(target),
        "base": str(base),
        "paper_order_allowed": False,
        "k_write_allowed": False,
        "l_execute_allowed": False,
        "forceenter_allowed": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "source_overwrite_allowed": False,
        "final_decision": final_decision,
        "next_action": next_action,
        "failures": failures,
        "warnings": warnings,
        "candidate_source": str(candidate_source),
        "preview_run_source": str(preview_run_source),
        "diff": str(diff_path),
        "diff_line_count": len(diff_lines),
        "compile": {
            "candidate": candidate_compile,
            "preview_run": run_compile,
        },
        "markers": markers,
        "missing_markers": missing_markers,
        "forbidden_hits": forbidden,
        "safe_run": safe_run,
        "safe_run_active": {
            "final_decision": final_decision_from_run,
            "old_summary": old_summary,
            "new_summary": new_summary,
            "improvement": improvement,
        },
        "decision_policy": [
            "AS5J2F2 is source-patch-preview only.",
            "AS5J2F2 does not overwrite AS5J1 source.",
            "AS5J2F2 writes candidate source and diff under runtime dir.",
            "AS5J2F2 compiles candidate source.",
            "AS5J2F2 runs only the safe preview source with AS5J2F2 output prefix.",
            "AS5J2F2 does not write K.",
            "AS5J2F2 does not execute L.",
            "AS5J2F2 does not create paper order.",
            "AS5J2F2 does not enable live, risk-up, or gate-loosen.",
            "Actual source patch requires separate AS5J2F3 approval.",
        ],
    }

    full = runtime / f"{OUT_PREFIX}_FULL.json"
    active = runtime / f"{OUT_PREFIX}_ACTIVE.json"
    compact = runtime / f"{OUT_PREFIX}_COMPACT.txt"

    write_json(full, result)
    write_json(active, result)

    lines = [
        "F4X_AS5J2F2_AS5J1_SOURCE_PATCH_PREVIEW_ONLY_COMPACT",
        f"generated_at={result['generated_at']}",
        f"mode={MODE}",
        f"target={target}",
        f"base={base}",
        "paper_order=HOLD",
        "k_write=HOLD",
        "l_execute=HOLD",
        "forceenter=HOLD",
        "live=HOLD",
        "risk_up=HOLD",
        "gate_loosen=HOLD",
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
        f"preview_run_source={preview_run_source}",
        f"diff={diff_path}",
        f"diff_line_count={len(diff_lines)}",
        "COMPILE",
        f"candidate_compile_ok={candidate_compile.get('ok')}|stderr={str(candidate_compile.get('stderr', '')).strip()}",
        f"preview_run_compile_ok={run_compile.get('ok')}|stderr={str(run_compile.get('stderr', '')).strip()}",
        "MARKERS",
        f"markers={markers}",
        f"missing_markers={missing_markers}",
        "FORBIDDEN_CHECK",
        f"forbidden_hits={forbidden}",
        "SAFE_RUN",
        f"safe_run_ok={safe_run.get('ok') if safe_run else None}",
        f"safe_run_returncode={safe_run.get('returncode') if safe_run else None}",
        f"safe_run_final_decision={final_decision_from_run}",
        f"old_summary={old_summary}",
        f"new_summary={new_summary}",
        f"improvement={improvement}",
        "SAFE_RUN_STDOUT_TAIL",
        str(safe_run.get("stdout_tail", ""))[-4000:] if safe_run else "",
        "SAFE_RUN_STDERR_TAIL",
        str(safe_run.get("stderr_tail", ""))[-4000:] if safe_run else "",
        "DIFF_HEAD",
        *diff_lines[:220],
        "OUTPUT_FILES",
        f"full_json={full}",
        f"compact={compact}",
        f"active={active}",
        f"candidate_source={candidate_source}",
        f"preview_run_source={preview_run_source}",
        f"diff={diff_path}",
        "DECISION_POLICY",
        *result["decision_policy"],
    ]

    compact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(compact.read_text(encoding="utf-8"))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
