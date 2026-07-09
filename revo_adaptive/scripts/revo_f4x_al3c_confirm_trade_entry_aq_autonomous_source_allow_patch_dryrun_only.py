#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

OUT_PREFIX = "F4X_AL3C_CONFIRM_TRADE_ENTRY_AQ_AUTONOMOUS_SOURCE_ALLOW_PATCH_DRYRUN_ONLY"
MODE = "CONFIRM_TRADE_ENTRY_AQ_AUTONOMOUS_SOURCE_ALLOW_PATCH_DRYRUN_ONLY"

OLD = '("F4X_AK2" in _source_text or "SCANNER_DRIVEN_STRICT" in _source_text)'
NEW = '("F4X_AK2" in _source_text or "SCANNER_DRIVEN_STRICT" in _source_text or "F4X_AQ" in _source_text or "AUTONOMOUS_SCANNER_STRICT" in _source_text)'

def now_utc():
    return datetime.now(timezone.utc).isoformat()

def write_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo_dir)
    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    strategy = repo / "user_data/strategies/RevoAlphaStrategy.py"
    backup = None
    failures = []
    actions = []

    text = strategy.read_text(encoding="utf-8", errors="replace")

    old_present = OLD in text
    new_present = NEW in text
    aq_literal_present_before = '"F4X_AQ" in _source_text' in text or "'F4X_AQ' in _source_text" in text
    auto_literal_present_before = "AUTONOMOUS_SCANNER_STRICT" in text

    if new_present:
        final_decision = "F4X_AL3C_ALREADY_PATCHED"
        patched = False
    elif not old_present:
        final_decision = "F4X_AL3C_ABORTED_ANCHOR_NOT_FOUND"
        patched = False
        failures.append("SOURCE_CONDITION_ANCHOR_NOT_FOUND")
    elif not args.execute:
        final_decision = "F4X_AL3C_DRYRUN_READY_EXECUTE_REQUIRED"
        patched = False
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup = strategy.with_suffix(strategy.suffix + f".F4X_AL3C_BACKUP_{ts}")
        shutil.copy2(strategy, backup)
        text2 = text.replace(OLD, NEW, 1)
        strategy.write_text(text2, encoding="utf-8")
        actions.append("PATCHED_AQ_AUTONOMOUS_SOURCE_ALLOW")
        patched = True
        final_decision = "F4X_AL3C_PATCHED_RESTART_REQUIRED"

    compile_check = {"returncode": 999, "stdout": "", "stderr": "NOT_RUN"}
    if args.execute and (patched or new_present):
        p = subprocess.run(
            ["python3", "-m", "py_compile", str(strategy)],
            cwd=str(repo),
            text=True,
            capture_output=True,
        )
        compile_check = {
            "returncode": p.returncode,
            "stdout": p.stdout[-2000:],
            "stderr": p.stderr[-4000:],
        }
        if p.returncode != 0:
            final_decision = "F4X_AL3C_PATCHED_BUT_COMPILE_FAILED_RESTORE_REQUIRED"
            failures.append("PY_COMPILE_FAILED")

    text_after = strategy.read_text(encoding="utf-8", errors="replace")
    result = {
        "generated_at": now_utc(),
        "mode": MODE,
        "execute_requested": bool(args.execute),
        "paper_order": "HOLD",
        "live": "HOLD",
        "risk_up": "HOLD",
        "gate_loosen": "HOLD",
        "final_decision": final_decision,
        "patch_actions": actions,
        "failures": failures,
        "source_checks": {
            "old_condition_present_before": old_present,
            "new_condition_present_before": new_present,
            "aq_literal_present_before": aq_literal_present_before,
            "autonomous_literal_present_before": auto_literal_present_before,
            "aq_literal_present_after": "F4X_AQ" in text_after,
            "autonomous_literal_present_after": "AUTONOMOUS_SCANNER_STRICT" in text_after,
        },
        "compile_check": compile_check,
        "files": {
            "strategy_path": str(strategy),
            "backup_path": str(backup) if backup else None,
        },
        "decision_policy": [
            "AL3C only extends AL2 paper-bridge strict bypass to AQ autonomous scanner strict source.",
            "No global gate unlock.",
            "No live.",
            "No risk-up.",
            "No gate-loosen.",
            "No manual whitelist injection.",
        ],
    }

    full = runtime / f"{OUT_PREFIX}_FULL.json"
    active = runtime / f"{OUT_PREFIX}_ACTIVE.json"
    compact = runtime / f"{OUT_PREFIX}_COMPACT.txt"
    result["output_files"] = {"full_json": str(full), "compact": str(compact), "active": str(active)}
    write_json(full, result)
    write_json(active, result)

    lines = [
        f"{OUT_PREFIX}_COMPACT",
        f"generated_at={result['generated_at']}",
        f"mode={MODE}",
        f"execute_requested={result['execute_requested']}",
        "paper_order=HOLD",
        "live=HOLD",
        "risk_up=HOLD",
        "gate_loosen=HOLD",
        "FINAL_DECISION",
        f"final_decision={final_decision}",
        "PATCH_ACTIONS",
        ",".join(actions) if actions else "NONE",
        "FAILURES",
        ",".join(failures) if failures else "NONE",
        "SOURCE_CHECKS",
        "|".join(f"{k}={v}" for k, v in result["source_checks"].items()),
        "COMPILE_CHECK",
        f"returncode={compile_check['returncode']}|stderr={compile_check['stderr'][:300]}",
        "FILES",
        f"strategy_path={strategy}",
        f"backup_path={backup}",
        "DECISION_POLICY",
        "No global gate unlock. No live. No risk-up. No gate-loosen. Dry-run paper bridge only.",
        "OUTPUT_FILES",
        f"full_json={full}",
        f"compact={compact}",
        f"active={active}",
    ]
    compact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(compact.read_text())

if __name__ == "__main__":
    main()
