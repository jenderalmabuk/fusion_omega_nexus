from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

OPTIONAL_INPUTS = (
    "LEGACY_AUDIT_SIDE_EFFECT_SCAN.csv",
    "LEGACY_F3_FLOW_SIDE_EFFECT_SCAN.csv",
    "LEGACY_AUDIT_SCRIPT_INVENTORY.csv",
    "LEGACY_F3_FLOW_SCRIPT_INVENTORY.csv",
    "docs/kiro_deep_analysis/KIRO_WRITER_AUTHORITY_MAP.md",
    "docs/kiro_deep_analysis/KIRO_PAIRCONTEXT_MIGRATION_PLAN.md",
)

TARGET_PATTERNS = (
    "pair_universe_remote.json",
    "revo_execution_context.json",
    "revo_flow_context.json",
    "ACTIVE.json",
    "FULL.json",
    "latest",
    "sticky",
    "F4X_K",
    "F4X_L",
    "forceenter",
    "forcebuy",
    "sqlite",
    ".sqlite",
    ".jsonl",
)

WRITE_MARKERS = (
    "write_text",
    "json.dump",
    "atomic_write",
    "replace(",
    "shutil.copy",
    "open(",
    "insert into",
    "update ",
    "delete from",
    "create table",
    "mkdir",
    "touch",
)

CATEGORIES = (
    "PAIRLIST_WRITER",
    "EXECUTION_CONTEXT_WRITER",
    "FLOW_CONTEXT_TOUCHPOINT",
    "STICKY_LIFECYCLE_MUTATOR",
    "K_INTENT_WRITER",
    "EXECUTION_BRIDGE",
    "PROJECTION_WRITER",
    "SQLITE_WRITER",
    "AUDIT_LOG_WRITER",
    "READ_ONLY_AUDIT",
    "PATCH_PREVIEW",
    "SHADOW_ONLY",
    "DRYRUN_ONLY",
)

REPLACEMENT_TARGETS = {
    "PAIRLIST_WRITER": "PairContext projection engine owns pair_universe_remote.json",
    "EXECUTION_CONTEXT_WRITER": "PairContext projection engine owns revo_execution_context.json",
    "FLOW_CONTEXT_TOUCHPOINT": "PairContext flow events and flow_latest projection",
    "STICKY_LIFECYCLE_MUTATOR": "PairContext lifecycle events and sticky retention reducer",
    "K_INTENT_WRITER": "PairContext execution.intent_created events and intent projection",
    "EXECUTION_BRIDGE": "PairContext execution consume/attempt events with replay-safe attribution",
    "PROJECTION_WRITER": "PairContext projection owner guarded writer",
    "SQLITE_WRITER": "PairContext append-only event bus or explicit read-only adapter",
    "AUDIT_LOG_WRITER": "PairContext event lineage and audit tables",
}

HIGH_RISK_CATEGORIES = {
    "PAIRLIST_WRITER",
    "EXECUTION_CONTEXT_WRITER",
    "STICKY_LIFECYCLE_MUTATOR",
    "K_INTENT_WRITER",
    "EXECUTION_BRIDGE",
    "PROJECTION_WRITER",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def text_matches_interest(text: str) -> bool:
    upper = text.upper()
    lower = text.lower()
    return any(pattern.upper() in upper for pattern in TARGET_PATTERNS) or any(marker in lower for marker in WRITE_MARKERS)


def classify(path: str, text: str) -> set[str]:
    joined = f"{path}\n{text}"
    lower = joined.lower()
    upper = joined.upper()
    categories: set[str] = set()

    if "pair_universe_remote.json" in lower or "remotepairlist" in lower or "pairlist" in lower:
        categories.add("PAIRLIST_WRITER")
        categories.add("PROJECTION_WRITER")
    if "revo_execution_context.json" in lower or "execution_context" in lower:
        categories.add("EXECUTION_CONTEXT_WRITER")
        categories.add("PROJECTION_WRITER")
    if "revo_flow_context.json" in lower or "flow_context" in lower:
        categories.add("FLOW_CONTEXT_TOUCHPOINT")
    if "sticky" in lower or "pair_universe_sticky_state" in lower:
        categories.add("STICKY_LIFECYCLE_MUTATOR")
    if "F4X_K" in upper or "ACTIVE_K" in upper or "ACTIVE_SIGNAL" in upper:
        categories.add("K_INTENT_WRITER")
    if "F4X_L" in upper or "forceenter" in lower or "forcebuy" in lower or "rest" in lower and "execution" in lower:
        categories.add("EXECUTION_BRIDGE")
    if "projection" in lower or "generated_by_owner" in lower:
        categories.add("PROJECTION_WRITER")
    if "sqlite" in lower or ".sqlite" in lower or "insert into" in lower or "create table" in lower or "update " in lower:
        categories.add("SQLITE_WRITER")
    if ".jsonl" in lower or "audit" in lower and ("log" in lower or "json" in lower):
        categories.add("AUDIT_LOG_WRITER")
    if "patch_preview" in lower or "patch preview" in lower or ".patch" in lower:
        categories.add("PATCH_PREVIEW")
    if "shadow" in lower:
        categories.add("SHADOW_ONLY")
    if "dryrun" in lower or "dry-run" in lower or "dry_run" in lower:
        categories.add("DRYRUN_ONLY")
    if "audit" in lower and not any(marker in lower for marker in WRITE_MARKERS):
        categories.add("READ_ONLY_AUDIT")
    return categories


def add_match(matches: dict[str, dict[str, Any]], path: str, line: int, text: str, source: str) -> None:
    categories = classify(path, text)
    if not categories:
        return
    item = matches.setdefault(
        path,
        {
            "path": path,
            "categories": set(),
            "sources": set(),
            "match_count": 0,
            "examples": [],
        },
    )
    item["categories"].update(categories)
    item["sources"].add(source)
    item["match_count"] += 1
    if len(item["examples"]) < 6:
        item["examples"].append({"line": int(line), "source": source, "text": text.strip()[:220]})


def scan_csv(path: Path, matches: dict[str, dict[str, Any]], warnings: list[str]) -> int:
    scanned = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                scanned += 1
                target_path = str(row.get("path") or rel(path))
                text = str(row.get("text") or row.get("match") or "")
                if text_matches_interest(f"{target_path}\n{text}"):
                    line = int(str(row.get("line") or "0").strip() or 0)
                    add_match(matches, target_path, line, text, rel(path))
    except Exception as exc:
        warnings.append(f"failed to read {rel(path)}: {type(exc).__name__}: {exc}")
    return scanned


def scan_text_file(path: Path, matches: dict[str, dict[str, Any]], warnings: list[str]) -> int:
    scanned = 0
    try:
        for line_no, text in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            scanned += 1
            joined = f"{rel(path)}\n{text}"
            if text_matches_interest(joined):
                add_match(matches, rel(path), line_no, text, rel(path))
    except Exception as exc:
        warnings.append(f"failed to read {rel(path)}: {type(exc).__name__}: {exc}")
    return scanned


def input_files(warnings: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in OPTIONAL_INPUTS:
        path = REPO_ROOT / item
        if path.exists():
            files.append(path)
        else:
            warnings.append(f"optional input missing: {item}")
    archive = REPO_ROOT / "fusion_audit_evolution"
    if archive.exists():
        files.extend(sorted(path for path in archive.rglob("*") if path.is_file() and path.suffix.lower() in {".py", ".sh", ".md", ".txt"}))
    else:
        warnings.append("optional input missing: fusion_audit_evolution/")
    return sorted(set(files), key=rel)


def build_report() -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    matches: dict[str, dict[str, Any]] = {}
    files = input_files(warnings)
    scanned_units = 0

    for path in files:
        if path.suffix.lower() == ".csv":
            scanned_units += scan_csv(path, matches, warnings)
        else:
            scanned_units += scan_text_file(path, matches, warnings)

    category_counts: dict[str, int] = {category: 0 for category in CATEGORIES}
    high_risk: list[dict[str, Any]] = []
    matched_files: list[dict[str, Any]] = []
    replacement_targets: dict[str, list[str]] = defaultdict(list)

    for path in sorted(matches):
        item = matches[path]
        categories = sorted(item["categories"])
        for category in categories:
            category_counts[category] += 1
            if category in REPLACEMENT_TARGETS:
                replacement_targets[category].append(path)
        normalized = {
            "path": path,
            "categories": categories,
            "sources": sorted(item["sources"]),
            "match_count": item["match_count"],
        }
        matched_files.append(normalized)
        risky = sorted(set(categories) & HIGH_RISK_CATEGORIES)
        if risky:
            high_risk.append({"path": path, "categories": risky, "match_count": item["match_count"]})

    report = {
        "ok": not errors,
        "generated_at": utc_now(),
        "scanned_files": len(files),
        "scanned_units": scanned_units,
        "matched_files": matched_files,
        "matched_file_count": len(matched_files),
        "categories": {key: category_counts[key] for key in sorted(category_counts) if category_counts[key]},
        "high_risk_touchpoints": sorted(high_risk, key=lambda item: (item["path"], item["categories"])),
        "paircontext_replacement_targets": {
            category: {
                "target": REPLACEMENT_TARGETS[category],
                "file_count": len(set(paths)),
                "sample_files": sorted(set(paths))[:80],
            }
            for category, paths in sorted(replacement_targets.items())
        },
        "errors": errors,
        "warnings": warnings,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only PairContext historical writer authority report")
    parser.add_argument("--output", default="", help="Optional path to write the JSON report. Default prints stdout only.")
    args = parser.parse_args(argv)
    report = build_report()
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
