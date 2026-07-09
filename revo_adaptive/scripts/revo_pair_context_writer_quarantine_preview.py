from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.revo_pair_context_writer_authority_report import build_report

CRITICAL_TARGETS = (
    "pair_universe_remote.json",
    "revo_execution_context.json",
    "ACTIVE.json",
    "FULL.json",
    "forceenter",
    "forcebuy",
    "REST",
)

HIGH_TARGETS = (
    "latest",
    "F4X_K",
    "F4X_L",
    "ACTIVE_K",
    "ACTIVE_SIGNAL",
    "execution_context",
    "pairlist",
    "sticky",
)

CATEGORY_REPLACEMENTS = {
    "PAIRLIST_WRITER": "PairContext deterministic projection owns pair_universe_remote.json",
    "EXECUTION_CONTEXT_WRITER": "PairContext deterministic projection owns revo_execution_context.json",
    "PROJECTION_WRITER": "PairContext projection owner guarded writer",
    "K_INTENT_WRITER": "PairContext execution intent event producer and projection",
    "EXECUTION_BRIDGE": "PairContext execution consume/attempt event lineage",
    "STICKY_LIFECYCLE_MUTATOR": "PairContext lifecycle/sticky reducers",
    "FLOW_CONTEXT_TOUCHPOINT": "PairContext flow events and flow_latest state",
    "SQLITE_WRITER": "PairContext append-only event bus or read-only adapter",
    "AUDIT_LOG_WRITER": "Read-only diagnostic lineage",
    "READ_ONLY_AUDIT": "Read-only diagnostic validator",
    "PATCH_PREVIEW": "Preview-only migration artifact",
    "SHADOW_ONLY": "Shadow-only diagnostic script",
    "DRYRUN_ONLY": "Dry-run-only diagnostic script",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def touched_targets(path: str, categories: list[str]) -> list[str]:
    haystack = path.upper()
    targets: set[str] = set()
    for target in (*CRITICAL_TARGETS, *HIGH_TARGETS):
        if target.upper() in haystack:
            targets.add(target)
    if "PAIRLIST_WRITER" in categories:
        targets.add("pair_universe_remote.json")
    if "EXECUTION_CONTEXT_WRITER" in categories:
        targets.add("revo_execution_context.json")
    if "K_INTENT_WRITER" in categories:
        targets.add("K/L intent state")
    if "EXECUTION_BRIDGE" in categories:
        targets.add("forceenter / forcebuy / REST execution bridge")
    if "STICKY_LIFECYCLE_MUTATOR" in categories:
        targets.add("sticky state")
    if "FLOW_CONTEXT_TOUCHPOINT" in categories:
        targets.add("revo_flow_context.json")
    return sorted(targets)


def risk_level(categories: list[str], targets: list[str]) -> str:
    target_text = " ".join(targets).upper()
    if any(target.upper() in target_text for target in CRITICAL_TARGETS):
        return "CRITICAL"
    if {"EXECUTION_BRIDGE", "K_INTENT_WRITER"} & set(categories):
        return "CRITICAL"
    if {"PAIRLIST_WRITER", "EXECUTION_CONTEXT_WRITER", "PROJECTION_WRITER", "STICKY_LIFECYCLE_MUTATOR"} & set(categories):
        return "HIGH"
    if {"SQLITE_WRITER", "FLOW_CONTEXT_TOUCHPOINT"} & set(categories):
        return "MEDIUM"
    return "LOW"


def quarantine_priority(level: str, categories: list[str]) -> str:
    if level == "CRITICAL":
        return "BLOCK_BEFORE_LIVE_AUTHORITY"
    if level == "HIGH":
        return "PREVIEW_QUARANTINE"
    if {"READ_ONLY_AUDIT", "SHADOW_ONLY", "DRYRUN_ONLY", "PATCH_PREVIEW"} & set(categories):
        return "OBSERVE"
    return "REVIEW"


def next_action(level: str, categories: list[str]) -> str:
    if level == "CRITICAL":
        return "Map owner and add explicit preview quarantine before PairContext live authority."
    if level == "HIGH":
        return "Create quarantine preview and convert writer path to PairContext event/projection ownership."
    if {"READ_ONLY_AUDIT", "SHADOW_ONLY", "DRYRUN_ONLY", "PATCH_PREVIEW"} & set(categories):
        return "Keep read-only; use as migration evidence and validation material."
    return "Review manually and classify as read-only adapter or event producer."


def replacement_target(categories: list[str]) -> str:
    targets = [CATEGORY_REPLACEMENTS[category] for category in categories if category in CATEGORY_REPLACEMENTS]
    return "; ".join(sorted(set(targets))) if targets else "Manual PairContext ownership review"


def candidate_from_match(match: dict[str, Any]) -> dict[str, Any]:
    path = str(match.get("path") or "")
    categories = sorted(str(category) for category in match.get("categories") or [])
    targets = touched_targets(path, categories)
    level = risk_level(categories, targets)
    return {
        "path": path,
        "categories": categories,
        "touched_targets": targets,
        "risk_level": level,
        "quarantine_priority": quarantine_priority(level, categories),
        "recommended_next_action": next_action(level, categories),
        "paircontext_replacement_target": replacement_target(categories),
    }


def build_preview() -> dict[str, Any]:
    authority = build_report()
    errors = list(authority.get("errors") or [])
    warnings = list(authority.get("warnings") or [])
    candidates = [
        candidate_from_match(match)
        for match in authority.get("matched_files", [])
        if match.get("categories")
    ]
    candidates = sorted(
        candidates,
        key=lambda item: (
            {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}.get(item["risk_level"], 9),
            item["path"],
        ),
    )
    category_counts: dict[str, int] = {}
    priority_counts: dict[str, int] = {}
    for candidate in candidates:
        priority_counts[candidate["quarantine_priority"]] = priority_counts.get(candidate["quarantine_priority"], 0) + 1
        for category in candidate["categories"]:
            category_counts[category] = category_counts.get(category, 0) + 1
    return {
        "ok": not errors,
        "generated_at": utc_now(),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "categories": {key: category_counts[key] for key in sorted(category_counts)},
        "quarantine_priority_counts": {key: priority_counts[key] for key in sorted(priority_counts)},
        "errors": errors,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only PairContext writer quarantine preview")
    parser.add_argument("--output", default="", help="Optional output path. Default prints JSON to stdout only.")
    args = parser.parse_args(argv)
    report = build_preview()
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
