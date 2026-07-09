#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT_PREFIX = "F4X_AL3_CONFIRM_TRADE_ENTRY_AQ_SOURCE_ALLOWLIST_PATCH_DRYRUN_ONLY"
MODE = "CONFIRM_TRADE_ENTRY_AQ_SOURCE_ALLOWLIST_PATCH_DRYRUN_ONLY"

AK2_SOURCE = "F4X_AK2_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_DRYRUN_ONLY"
AQ_SOURCE = "F4X_AQ_AUTONOMOUS_SCANNER_STRICT_K_INTENT_DRYRUN_ONLY"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")


def run_cmd(cmd: list[str]) -> dict[str, Any]:
    try:
        p = subprocess.run(cmd, text=True, capture_output=True, timeout=30)
        return {
            "cmd": cmd,
            "returncode": p.returncode,
            "stdout": p.stdout[-4000:],
            "stderr": p.stderr[-4000:],
        }
    except Exception as e:
        return {"cmd": cmd, "returncode": 999, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}


def extract_confirm_trade_entry(txt: str) -> str:
    marker = "def confirm_trade_entry("
    idx = txt.find(marker)
    if idx < 0:
        return ""
    nxt = txt.find("\n    def ", idx + len(marker))
    if nxt < 0:
        nxt = txt.find("\n    async def ", idx + len(marker))
    if nxt < 0:
        nxt = min(len(txt), idx + 12000)
    return txt[idx:nxt]


def patch_text(txt: str) -> tuple[str, list[str], list[str]]:
    actions: list[str] = []
    failures: list[str] = []

    if AQ_SOURCE in txt:
        actions.append("AQ_SOURCE_ALREADY_PRESENT_NOOP")
        return txt, actions, failures

    if AK2_SOURCE not in txt:
        failures.append("AK2_SOURCE_ANCHOR_NOT_FOUND")
        return txt, actions, failures

    original = txt

    # Case 1: strict equality check
    patterns = [
        (f'intent_source == "{AK2_SOURCE}"',
         f'intent_source in {{"{AK2_SOURCE}", "{AQ_SOURCE}"}}',
         "PATCHED_INTENT_SOURCE_EQUAL_DOUBLE_QUOTE"),
        (f"intent_source == '{AK2_SOURCE}'",
         f"intent_source in {{'{AK2_SOURCE}', '{AQ_SOURCE}'}}",
         "PATCHED_INTENT_SOURCE_EQUAL_SINGLE_QUOTE"),
        (f'str(intent_source) == "{AK2_SOURCE}"',
         f'str(intent_source) in {{"{AK2_SOURCE}", "{AQ_SOURCE}"}}',
         "PATCHED_STR_INTENT_SOURCE_EQUAL_DOUBLE_QUOTE"),
        (f"str(intent_source) == '{AK2_SOURCE}'",
         f"str(intent_source) in {{'{AK2_SOURCE}', '{AQ_SOURCE}'}}",
         "PATCHED_STR_INTENT_SOURCE_EQUAL_SINGLE_QUOTE"),
    ]

    for old, new, action in patterns:
        if old in txt:
            txt = txt.replace(old, new, 1)
            actions.append(action)
            return txt, actions, failures

    # Case 2: strict inequality check
    patterns = [
        (f'intent_source != "{AK2_SOURCE}"',
         f'intent_source not in {{"{AK2_SOURCE}", "{AQ_SOURCE}"}}',
         "PATCHED_INTENT_SOURCE_NOT_EQUAL_DOUBLE_QUOTE"),
        (f"intent_source != '{AK2_SOURCE}'",
         f"intent_source not in {{'{AK2_SOURCE}', '{AQ_SOURCE}'}}",
         "PATCHED_INTENT_SOURCE_NOT_EQUAL_SINGLE_QUOTE"),
        (f'str(intent_source) != "{AK2_SOURCE}"',
         f'str(intent_source) not in {{"{AK2_SOURCE}", "{AQ_SOURCE}"}}',
         "PATCHED_STR_INTENT_SOURCE_NOT_EQUAL_DOUBLE_QUOTE"),
        (f"str(intent_source) != '{AK2_SOURCE}'",
         f"str(intent_source) not in {{'{AK2_SOURCE}', '{AQ_SOURCE}'}}",
         "PATCHED_STR_INTENT_SOURCE_NOT_EQUAL_SINGLE_QUOTE"),
    ]

    for old, new, action in patterns:
        if old in txt:
            txt = txt.replace(old, new, 1)
            actions.append(action)
            return txt, actions, failures

    # Case 3: AK2 source is an item in a multiline allowlist/set/tuple.
    lines = txt.splitlines()
    out: list[str] = []
    inserted = False

    for line in lines:
        out.append(line)
        if AK2_SOURCE in line and not inserted:
            stripped = line.strip()
            # Only safe if line looks like a standalone quoted list item.
            if stripped.startswith(("\"", "'")) and stripped.endswith((",", "},", "],", "),")):
                quote = "\"" if stripped.startswith("\"") else "'"
                indent = line[: len(line) - len(line.lstrip())]
                out.append(f"{indent}{quote}{AQ_SOURCE}{quote},")
                inserted = True
                actions.append("PATCHED_MULTILINE_SOURCE_ALLOWLIST_ITEM")
            elif stripped.startswith(("\"", "'")) and stripped.endswith(("\"", "'")):
                quote = "\"" if stripped.startswith("\"") else "'"
                indent = line[: len(line) - len(line.lstrip())]
                out[-1] = line + ","
                out.append(f"{indent}{quote}{AQ_SOURCE}{quote}")
                inserted = True
                actions.append("PATCHED_MULTILINE_SOURCE_ALLOWLIST_LAST_ITEM")

    if inserted:
        return "\n".join(out) + ("\n" if txt.endswith("\n") else ""), actions, failures

    if txt == original:
        failures.append("NO_SAFE_PATCH_PATTERN_FOUND")
    return txt, actions, failures


def build_compact(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("F4X_AL3_CONFIRM_TRADE_ENTRY_AQ_SOURCE_ALLOWLIST_PATCH_DRYRUN_ONLY_COMPACT")
    lines.append(f"generated_at={result['generated_at']}")
    lines.append(f"mode={result['mode']}")
    lines.append(f"execute_requested={result['execute_requested']}")
    lines.append("paper_order=HOLD")
    lines.append("live=HOLD")
    lines.append("risk_up=HOLD")
    lines.append("gate_loosen=HOLD")
    lines.append("FINAL_DECISION")
    lines.append(f"final_decision={result['final_decision']}")
    lines.append("PATCH_ACTIONS")
    lines.append(",".join(result["patch_actions"]) if result["patch_actions"] else "NONE")
    lines.append("FAILURES")
    lines.append(",".join(result["failures"]) if result["failures"] else "NONE")
    lines.append("SOURCE_CHECKS")
    lines.append(f"ak2_source_present_before={result['ak2_source_present_before']}")
    lines.append(f"aq_source_present_before={result['aq_source_present_before']}")
    lines.append(f"aq_source_present_after={result['aq_source_present_after']}")
    lines.append("COMPILE_CHECK")
    lines.append(f"returncode={result['compile_check']['returncode']}")
    if result["compile_check"].get("stderr"):
        lines.append(f"stderr={result['compile_check']['stderr'][:1000]}")
    lines.append("FILES")
    lines.append(f"strategy_path={result['strategy_path']}")
    lines.append(f"backup_path={result['backup_path']}")
    lines.append("DECISION_POLICY")
    lines.append("AL3 only adds AQ intent_source to paper-bridge strict confirm_trade_entry bypass allowlist.")
    lines.append("No global gate unlock.")
    lines.append("No live.")
    lines.append("No risk-up.")
    lines.append("No gate-loosen.")
    lines.append("No manual whitelist injection.")
    lines.append("OUTPUT_FILES")
    for k, v in result["output_files"].items():
        lines.append(f"{k}={v}")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo_dir)
    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    strategy = repo / "user_data/strategies/RevoAlphaStrategy.py"
    failures: list[str] = []
    actions: list[str] = []
    backup_path = None
    compile_check = {"returncode": 999, "stdout": "", "stderr": "NOT_RUN"}

    if not strategy.exists():
        failures.append("STRATEGY_FILE_NOT_FOUND")
        txt = ""
        new_txt = ""
    else:
        txt = strategy.read_text(encoding="utf-8", errors="replace")
        confirm_block = extract_confirm_trade_entry(txt)
        if not confirm_block:
            failures.append("CONFIRM_TRADE_ENTRY_NOT_FOUND")
        if AK2_SOURCE not in txt:
            failures.append("AK2_SOURCE_NOT_FOUND_IN_STRATEGY")
        new_txt, patch_actions, patch_failures = patch_text(txt)
        actions.extend(patch_actions)
        failures.extend(patch_failures)

        if args.execute and not failures:
            backup_path = strategy.with_suffix(strategy.suffix + f".F4X_AL3_BACKUP_{stamp()}")
            shutil.copy2(strategy, backup_path)
            strategy.write_text(new_txt, encoding="utf-8")
            compile_check = run_cmd(["python3", "-m", "py_compile", str(strategy)])
            if compile_check["returncode"] != 0:
                # rollback immediately on compile failure
                shutil.copy2(backup_path, strategy)
                failures.append("COMPILE_FAILED_ROLLED_BACK")
        elif not args.execute and not failures:
            actions.append("DRY_RUN_PATCH_READY_NOT_WRITTEN")

    final_decision = "F4X_AL3_ABORTED_GUARD_FAILED"
    if not failures and args.execute:
        final_decision = "F4X_AL3_AQ_SOURCE_ALLOWLIST_PATCHED_COMPILE_PASS"
    elif not failures and not args.execute:
        final_decision = "F4X_AL3_DRYRUN_PATCH_READY"

    after_txt = strategy.read_text(encoding="utf-8", errors="replace") if strategy.exists() else ""

    result = {
        "generated_at": now_utc(),
        "mode": MODE,
        "execute_requested": bool(args.execute),
        "final_decision": final_decision,
        "failures": failures,
        "patch_actions": actions,
        "strategy_path": str(strategy),
        "backup_path": str(backup_path) if backup_path else None,
        "ak2_source_present_before": AK2_SOURCE in txt,
        "aq_source_present_before": AQ_SOURCE in txt,
        "aq_source_present_after": AQ_SOURCE in after_txt,
        "compile_check": compile_check,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "output_files": {
            "full_json": str(runtime / f"{OUT_PREFIX}_FULL.json"),
            "compact": str(runtime / f"{OUT_PREFIX}_COMPACT.txt"),
            "active": str(runtime / f"{OUT_PREFIX}_ACTIVE.json"),
        },
    }

    write_json(runtime / f"{OUT_PREFIX}_FULL.json", result)
    write_json(runtime / f"{OUT_PREFIX}_ACTIVE.json", result)
    compact = build_compact(result)
    (runtime / f"{OUT_PREFIX}_COMPACT.txt").write_text(compact, encoding="utf-8")
    print(compact)


if __name__ == "__main__":
    main()
