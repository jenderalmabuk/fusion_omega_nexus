#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Tuple

OUT = Path("F2O_SCORE_GATE_ARBITRATION_SOURCE_TRACE_COMPACT.txt")

TARGET_TERMS = [
    "DENY_SCORE_GATE_MISMATCH_SAFETY",
    "score_would_allow_long",
    "score_would_allow_short",
    "gate_allow_long",
    "gate_allow_short",
    "ALLOW_FLOW_TIMING_GEOMETRY",
    "v139_recommended_action_long",
    "v139_family_grade_long",
    "KEEP_DENY",
    "shadow_trade_grade_long",
    "shadow_mandatory_pass_long",
]

SEARCH_ROOTS = [
    Path("user_data/strategies"),
    Path("user_data/revo_alpha"),
    Path("scripts"),
]


def safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def iter_py_files() -> List[Path]:
    files = []
    for root in SEARCH_ROOTS:
        if root.exists():
            files.extend(sorted(root.rglob("*.py")))
            files.extend(sorted(root.rglob("*.sh")))
    return files


def grep_terms() -> List[str]:
    rows = []
    for path in iter_py_files():
        text = safe_read(path)
        if not text:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            for term in TARGET_TERMS:
                if term in line:
                    start = max(1, i - 3)
                    end = min(len(lines), i + 3)
                    rows.append(f"--- {path}:{i} term={term} ---")
                    for ln in range(start, end + 1):
                        rows.append(f"{ln}: {lines[ln-1]}")
                    rows.append("")
                    break
    return rows


def extract_functions_around_terms() -> List[str]:
    rows = []
    for path in iter_py_files():
        text = safe_read(path)
        if not text or not any(t in text for t in TARGET_TERMS):
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            if any(t in line for t in TARGET_TERMS):
                fn_start = i
                for j in range(i - 1, 0, -1):
                    if re.match(r"^\s*def\s+\w+\(", lines[j-1]) or re.match(r"^\s*class\s+\w+", lines[j-1]):
                        fn_start = j
                        break
                start = max(1, fn_start)
                end = min(len(lines), i + 80)
                rows.append(f"=== CONTEXT_BLOCK {path}:{i} ===")
                for ln in range(start, end + 1):
                    rows.append(f"{ln}: {lines[ln-1]}")
                rows.append("")
    return rows


def load_f2n_event_summary() -> List[str]:
    f = Path("F2N_GATE_SCORE_MISMATCH_MICRO_AUDIT_COMPACT.txt")
    if not f.exists():
        return ["F2N_FILE_MISSING"]
    text = safe_read(f)
    keep = []
    capture = False
    for line in text.splitlines():
        if line.startswith("MISMATCH_COUNTS"):
            capture = True
        if capture:
            keep.append(line)
        if line.startswith("ALLOW_FLOW_TIMING_GEOMETRY_DETAILS"):
            break
    return keep[:120]


def main() -> int:
    lines = []
    lines.append("F2O_SCORE_GATE_ARBITRATION_SOURCE_TRACE_COMPACT")
    lines.append("mode=READ_ONLY")
    lines.append("writes=0")
    lines.append("")

    lines.append("F2N_MISMATCH_SUMMARY")
    lines.extend(load_f2n_event_summary())
    lines.append("")

    lines.append("SOURCE_GREP_HITS")
    hits = grep_terms()
    lines.extend(hits if hits else ["NO_SOURCE_HITS"])
    lines.append("")

    lines.append("SOURCE_CONTEXT_BLOCKS")
    blocks = extract_functions_around_terms()
    lines.extend(blocks[:1200] if blocks else ["NO_CONTEXT_BLOCKS"])
    lines.append("")

    lines.append("DECISION_HINT")
    lines.append("If DENY_SCORE_GATE_MISMATCH_SAFETY requires both score and gate allow, trace why score_would_allow_long=0 while A+ and gate allow.")
    lines.append("If v139_recommended_action_long=KEEP_DENY despite grade A+ and hard_veto NONE, inspect recommendation decision tree.")
    lines.append("Do not patch behavior until source trace identifies exact condition.")

    OUT.write_text('\\n'.join(lines) + '\\n', encoding='utf-8')
    print(OUT.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
