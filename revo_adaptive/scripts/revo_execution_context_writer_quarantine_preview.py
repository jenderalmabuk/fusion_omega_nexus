from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.revo_pair_context_writer_quarantine_preview import build_preview

TARGET = "revo_execution_context.json"

EXECUTION_CONTEXT_TERMS = (
    "revo_execution_context.json",
    "execution_context",
    "execution context",
    "ACTIVE.json",
    "FULL.json",
    "latest",
    "sticky",
    "sticky state",
    "active",
    "watch",
    "readiness",
    "gate",
    "lifecycle",
    "lifecycle mutator",
)

DIRECT_EXECUTION_CONTEXT_TERMS = (
    "revo_execution_context.json",
    "execution_context",
    "execution context",
)

LATEST_AUTHORITY_TERMS = (
    "ACTIVE.json",
    "FULL.json",
    "latest",
)

GATE_LIFECYCLE_TERMS = (
    "gate",
    "readiness",
    "active",
    "watch",
    "lifecycle",
    "lifecycle mutator",
)

READ_ONLY_CATEGORIES = {
    "READ_ONLY_AUDIT",
    "SHADOW_ONLY",
    "DRYRUN_ONLY",
}

PREVIEW_CATEGORIES = {
    "PATCH_PREVIEW",
}

HIGH_AUTHORITY_CATEGORIES = {
    "EXECUTION_CONTEXT_WRITER",
    "PROJECTION_WRITER",
    "K_INTENT_WRITER",
    "EXECUTION_BRIDGE",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def matched_terms(candidate: dict[str, Any]) -> list[str]:
    fields = [
        str(candidate.get("path") or ""),
        " ".join(str(category) for category in candidate.get("categories") or []),
        " ".join(str(target) for target in candidate.get("touched_targets") or []),
        str(candidate.get("recommended_next_action") or ""),
        str(candidate.get("paircontext_replacement_target") or ""),
    ]
    haystack = "\n".join(fields).lower()
    return sorted({term for term in EXECUTION_CONTEXT_TERMS if term.lower() in haystack})


def touches_execution_context(candidate: dict[str, Any]) -> bool:
    terms = matched_terms(candidate)
    if terms:
        return True
    categories = set(str(category) for category in candidate.get("categories") or [])
    targets = " ".join(str(target) for target in candidate.get("touched_targets") or []).lower()
    return (
        "EXECUTION_CONTEXT_WRITER" in categories
        or "STICKY_LIFECYCLE_MUTATOR" in categories
        or TARGET in targets
    )


def touched_targets(candidate: dict[str, Any], terms: list[str]) -> list[str]:
    targets = set(str(target) for target in candidate.get("touched_targets") or [])
    categories = set(str(category) for category in candidate.get("categories") or [])
    if "EXECUTION_CONTEXT_WRITER" in categories:
        targets.add(TARGET)
    if "STICKY_LIFECYCLE_MUTATOR" in categories:
        targets.add("sticky state")
    for term in terms:
        lower = term.lower()
        if lower in {"revo_execution_context.json", "execution_context", "execution context"}:
            targets.add(TARGET)
        elif lower == "active.json":
            targets.add("ACTIVE.json")
        elif lower == "full.json":
            targets.add("FULL.json")
        elif lower == "latest":
            targets.add("latest*.json")
        elif lower in {"sticky", "sticky state"}:
            targets.add("sticky state")
        elif lower in {"active", "watch", "readiness", "gate", "lifecycle", "lifecycle mutator"}:
            targets.add("active/watch/readiness lifecycle state")
    return sorted(targets)


def classify_execution_context_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    path = str(candidate.get("path") or "")
    categories = sorted(str(category) for category in candidate.get("categories") or [])
    terms = matched_terms(candidate)
    targets = touched_targets(candidate, terms)
    lower_text = "\n".join([path, " ".join(terms), " ".join(targets)]).lower()
    category_set = set(categories)

    direct_execution_context = any(term.lower() in lower_text for term in DIRECT_EXECUTION_CONTEXT_TERMS)
    latest_authority = any(term.lower() in lower_text for term in LATEST_AUTHORITY_TERMS)
    sticky_mutator = "STICKY_LIFECYCLE_MUTATOR" in category_set or "sticky state" in lower_text
    gate_lifecycle = any(term.lower() in lower_text for term in GATE_LIFECYCLE_TERMS)
    patch_preview = bool(category_set & PREVIEW_CATEGORIES)
    high_authority_category = bool(category_set & HIGH_AUTHORITY_CATEGORIES)
    read_only = bool(category_set & READ_ONLY_CATEGORIES) and not (direct_execution_context or latest_authority)

    if direct_execution_context and not patch_preview:
        risk_level = "CRITICAL"
        quarantine_priority = "BLOCK_BEFORE_LIVE_AUTHORITY"
        action = "Map owner and add explicit execution context writer quarantine before PairContext live authority."
    elif latest_authority and high_authority_category and not patch_preview:
        risk_level = "CRITICAL"
        quarantine_priority = "BLOCK_BEFORE_LIVE_AUTHORITY"
        action = "Map latest/ACTIVE/FULL authority and remove it from live ownership before PairContext authority."
    elif direct_execution_context and patch_preview:
        risk_level = "HIGH"
        quarantine_priority = "PREVIEW_QUARANTINE"
        action = "Keep preview-only; verify it cannot write live execution context outside guarded projection apply."
    elif latest_authority or sticky_mutator or gate_lifecycle:
        risk_level = "HIGH"
        quarantine_priority = "PREVIEW_QUARANTINE"
        action = "Create a focused quarantine preview and map lifecycle state to PairContext reducers/projections."
    elif read_only:
        risk_level = "LOW"
        quarantine_priority = "OBSERVE"
        action = "Keep read-only as migration evidence and validation material."
    else:
        risk_level = "MEDIUM"
        quarantine_priority = "REVIEW"
        action = "Review manually and classify as read-only audit, lifecycle producer, or projection writer."

    return {
        "path": path,
        "categories": categories,
        "touched_targets": targets,
        "risk_level": risk_level,
        "quarantine_priority": quarantine_priority,
        "recommended_next_action": action,
        "paircontext_replacement_target": "PairContext reducers and deterministic projection own revo_execution_context.json",
        "evidence_keywords": terms,
    }


def build_execution_context_preview() -> dict[str, Any]:
    upstream = build_preview()
    errors = list(upstream.get("errors") or [])
    warnings = list(upstream.get("warnings") or [])
    candidates = [
        classify_execution_context_candidate(candidate)
        for candidate in upstream.get("candidates", [])
        if touches_execution_context(candidate)
    ]
    candidates = sorted(
        candidates,
        key=lambda item: (
            {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}.get(item["risk_level"], 9),
            item["path"],
        ),
    )

    priority_counts = Counter(candidate["quarantine_priority"] for candidate in candidates)
    risk_counts = Counter(candidate["risk_level"] for candidate in candidates)

    return {
        "ok": not errors,
        "generated_at": utc_now(),
        "target": TARGET,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "quarantine_priority_counts": {key: priority_counts[key] for key in sorted(priority_counts)},
        "risk_level_counts": {key: risk_counts[key] for key in sorted(risk_counts)},
        "errors": errors,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only execution context writer quarantine preview")
    parser.add_argument("--output", default="", help="Optional output path. Default prints JSON to stdout only.")
    args = parser.parse_args(argv)

    report = build_execution_context_preview()
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
