#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def atomic_write(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)

def active_flag() -> bool:
    return str(os.environ.get("REVO_STICKY_DROP_NO_TRADE", "0")).strip().lower() in {"1", "true", "yes", "on"}

def classify_pair(pair: str, row: Dict[str, Any]) -> Tuple[str, str]:
    publish_reason = str(row.get("publish_reason", "UNKNOWN"))
    flow_direction = str(row.get("flow_direction", "UNKNOWN"))
    flow_authority = str(row.get("flow_authority", "UNKNOWN"))
    entry_permission = str(row.get("entry_permission", "UNKNOWN"))
    data_quality = str(row.get("data_quality", "UNKNOWN"))

    is_active = publish_reason == "ACTIVE_ACTIONABLE"
    is_flow_eligible = entry_permission == "FLOW_ELIGIBLE" or flow_authority == "ENTRY_ELIGIBLE"
    is_sticky_no_trade = publish_reason == "STICKY_RETAINED" and flow_direction == "NO_TRADE"

    detail = f"{publish_reason}|{flow_authority}|{entry_permission}|{flow_direction}|{data_quality}"

    if is_active or is_flow_eligible:
        return "KEEP_ACTIVE_OR_FLOW_ELIGIBLE", detail
    if is_sticky_no_trade:
        return "DROP_STICKY_RETAINED_CURRENT_NO_TRADE", detail
    return "KEEP_OTHER_STICKY_OR_WATCH", detail

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    pairlist_path = runtime / "pair_universe_remote.json"
    exec_path = runtime / "revo_execution_context.json"
    compact_path = runtime / "F2K_STICKY_HYGIENE_COMPACT.txt"
    report_path = runtime / "f2k_sticky_hygiene_latest.json"

    pairlist = load_json(pairlist_path)
    exec_data = load_json(exec_path)
    exec_pairs = exec_data.get("pairs", {})
    if not isinstance(exec_pairs, dict):
        exec_pairs = {}

    pairs = pairlist.get("pairs", [])
    if not isinstance(pairs, list):
        pairs = []

    keep: List[str] = []
    drop: List[str] = []
    keep_reasons = Counter()
    drop_reasons = Counter()
    detail_lines = []

    for pair in pairs:
        row = exec_pairs.get(pair, {})
        if not isinstance(row, dict):
            keep.append(pair)
            keep_reasons["KEEP_MISSING_EXEC_CONTEXT_SAFE_DEFAULT"] += 1
            detail_lines.append(f"KEEP|{pair}|MISSING_EXEC_CONTEXT_SAFE_DEFAULT")
            continue

        decision, detail = classify_pair(pair, row)
        if decision.startswith("DROP_"):
            drop.append(pair)
            drop_reasons[decision] += 1
            detail_lines.append(f"DROP|{pair}|{detail}")
        else:
            keep.append(pair)
            keep_reasons[decision] += 1
            detail_lines.append(f"KEEP|{pair}|{detail}")

    enabled = active_flag()
    wrote = False

    report = {
        "event": "F2K_STICKY_HYGIENE",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runtime_dir": str(runtime),
        "env_REVO_STICKY_DROP_NO_TRADE": os.environ.get("REVO_STICKY_DROP_NO_TRADE", ""),
        "enabled": enabled,
        "apply_requested": bool(args.apply),
        "writes_pairlist": 0,
        "before_count": len(pairs),
        "after_count": len(keep),
        "drop_count": len(drop),
        "keep_pairs": keep,
        "drop_pairs": drop,
        "keep_reasons": keep_reasons.most_common(),
        "drop_reasons": drop_reasons.most_common(),
        "detail": detail_lines,
    }

    if args.apply and enabled:
        backup = runtime / "pair_universe_remote.pre_f2k_last.json"
        if pairlist_path.exists():
            backup.write_text(pairlist_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")

        new_pairlist = dict(pairlist)
        new_pairlist["pairs"] = keep
        new_pairlist["f2k_sticky_hygiene_enabled"] = True
        new_pairlist["f2k_drop_count"] = len(drop)
        new_pairlist["f2k_drop_reason"] = "STICKY_RETAINED_CURRENT_NO_TRADE"
        new_pairlist["f2k_generated_at"] = report["generated_at"]

        atomic_write(pairlist_path, new_pairlist)
        wrote = True
        report["writes_pairlist"] = 1
        report["backup"] = str(backup)

    report["wrote"] = wrote
    atomic_write(report_path, report)

    lines = []
    lines.append("F2K_STICKY_HYGIENE_COMPACT")
    lines.append(f"generated_at={report['generated_at']}")
    lines.append(f"runtime_dir={runtime}")
    lines.append(f"enabled={enabled}")
    lines.append(f"apply_requested={args.apply}")
    lines.append(f"writes_pairlist={report['writes_pairlist']}")
    lines.append(f"before_count={len(pairs)}")
    lines.append(f"after_count={len(keep)}")
    lines.append(f"drop_count={len(drop)}")
    lines.append(f"keep_reasons={keep_reasons.most_common()}")
    lines.append(f"drop_reasons={drop_reasons.most_common()}")
    lines.append(f"keep_pairs={keep}")
    lines.append(f"drop_pairs={drop}")
    lines.append("")
    lines.append("DETAIL")
    lines.extend(detail_lines)

    compact_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    if enabled and args.apply:
        print("F2K_STICKY_HYGIENE_APPLIED")
    else:
        print("F2K_STICKY_HYGIENE_DISABLED_OR_AUDIT_ONLY")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
