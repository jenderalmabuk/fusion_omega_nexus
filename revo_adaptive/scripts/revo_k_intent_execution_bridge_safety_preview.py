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

TARGET = "k_intent_execution_bridge"

OPTIONAL_INPUTS = (
    "docs/kiro_deep_analysis/KIRO_F4X_EXECUTION_BRIDGE_RISK_MAP.md",
    "docs/kiro_deep_analysis/EXECUTION_CONTEXT_WRITER_QUARANTINE_SUMMARY.md",
)

EXECUTION_BRIDGE_TERMS = (
    "K intent",
    "L intent",
    "K/L intent",
    "F4X_K",
    "F4X_L",
    "ACTIVE_K",
    "ACTIVE_SIGNAL",
    "paper bridge",
    "paper signal bridge",
    "paper_bridge",
    "forceenter",
    "forcebuy",
    "REST dryrun endpoint",
    "REST API",
    "REST",
    "dryrun bridge",
    "dry-run bridge",
    "execution bridge",
    "open trade",
    "open trade source-of-truth",
    "duplicate intent",
    "duplicate intent protection",
    "intent consumption",
    "trade outcome",
    "trade outcome attribution",
    "execution consume",
    "execution attempt",
)

BLOCKING_EXECUTION_TERMS = (
    "forceenter",
    "forcebuy",
    "REST dryrun endpoint",
    "REST API",
    "REST",
)

INTENT_TERMS = (
    "K intent",
    "L intent",
    "K/L intent",
    "F4X_K",
    "F4X_L",
    "ACTIVE_K",
    "ACTIVE_SIGNAL",
    "intent consumption",
)

PAPER_BRIDGE_TERMS = (
    "paper bridge",
    "paper signal bridge",
    "paper_bridge",
    "dryrun bridge",
    "dry-run bridge",
)

OPEN_TRADE_TERMS = (
    "open trade",
    "open trade source-of-truth",
)

DUPLICATE_INTENT_TERMS = (
    "duplicate intent",
    "duplicate intent protection",
)

READ_ONLY_CATEGORIES = {
    "READ_ONLY_AUDIT",
    "SHADOW_ONLY",
}

PREVIEW_CATEGORIES = {
    "PATCH_PREVIEW",
    "DRYRUN_ONLY",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def optional_input_warnings() -> list[str]:
    warnings: list[str] = []
    for item in OPTIONAL_INPUTS:
        if not (REPO_ROOT / item).exists():
            warnings.append(f"optional input missing: {item}")
    return warnings


def matched_terms(candidate: dict[str, Any]) -> list[str]:
    fields = [
        str(candidate.get("path") or ""),
        " ".join(str(category) for category in candidate.get("categories") or []),
        " ".join(str(target) for target in candidate.get("touched_targets") or []),
        str(candidate.get("recommended_next_action") or ""),
        str(candidate.get("paircontext_replacement_target") or ""),
    ]
    haystack = "\n".join(fields).lower()
    return sorted({term for term in EXECUTION_BRIDGE_TERMS if term.lower() in haystack})


def touches_execution_bridge(candidate: dict[str, Any]) -> bool:
    terms = matched_terms(candidate)
    if terms:
        return True
    categories = set(str(category) for category in candidate.get("categories") or [])
    return bool({"K_INTENT_WRITER", "EXECUTION_BRIDGE"} & categories)


def touched_targets(candidate: dict[str, Any], terms: list[str]) -> list[str]:
    targets = set(str(target) for target in candidate.get("touched_targets") or [])
    categories = set(str(category) for category in candidate.get("categories") or [])
    if "K_INTENT_WRITER" in categories:
        targets.add("K/L intent state")
    if "EXECUTION_BRIDGE" in categories:
        targets.add("forceenter / forcebuy / REST execution bridge")
    for term in terms:
        lower = term.lower()
        if lower in {item.lower() for item in BLOCKING_EXECUTION_TERMS}:
            targets.add("forceenter / forcebuy / REST dryrun endpoint")
        elif lower in {item.lower() for item in INTENT_TERMS}:
            targets.add("K/L intent state")
        elif lower in {item.lower() for item in PAPER_BRIDGE_TERMS}:
            targets.add("paper bridge / dryrun bridge")
        elif lower in {item.lower() for item in OPEN_TRADE_TERMS}:
            targets.add("open trade source-of-truth")
        elif lower in {item.lower() for item in DUPLICATE_INTENT_TERMS}:
            targets.add("duplicate intent protection")
        elif lower in {"trade outcome", "trade outcome attribution"}:
            targets.add("trade outcome attribution")
        elif lower in {"execution bridge", "execution consume", "execution attempt"}:
            targets.add("execution bridge")
    return sorted(targets)


def classify_execution_bridge_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    path = str(candidate.get("path") or "")
    categories = sorted(str(category) for category in candidate.get("categories") or [])
    category_set = set(categories)
    terms = matched_terms(candidate)
    targets = touched_targets(candidate, terms)
    lower_text = "\n".join([path, " ".join(terms), " ".join(targets)]).lower()

    blocking_execution = any(term.lower() in lower_text for term in BLOCKING_EXECUTION_TERMS)
    open_trade_source = any(term.lower() in lower_text for term in OPEN_TRADE_TERMS)
    intent_writer_or_consumer = "K_INTENT_WRITER" in category_set or any(
        term.lower() in lower_text for term in INTENT_TERMS
    )
    paper_or_dryrun_bridge = "EXECUTION_BRIDGE" in category_set or any(
        term.lower() in lower_text for term in PAPER_BRIDGE_TERMS
    )
    duplicate_intent = any(term.lower() in lower_text for term in DUPLICATE_INTENT_TERMS)
    read_only = bool(category_set & READ_ONLY_CATEGORIES) and not (
        blocking_execution or open_trade_source or intent_writer_or_consumer or paper_or_dryrun_bridge
    )
    preview_only = bool(category_set & PREVIEW_CATEGORIES)

    if blocking_execution or open_trade_source:
        risk_level = "CRITICAL"
        quarantine_priority = "BLOCK_BEFORE_DRY_RUN"
        action = "Block from controlled dry-run until ownership, source-of-truth, and no-call guarantees are mapped."
    elif intent_writer_or_consumer and paper_or_dryrun_bridge and not preview_only:
        risk_level = "CRITICAL"
        quarantine_priority = "BLOCK_BEFORE_DRY_RUN"
        action = "Map K/L intent ownership and execution bridge consumption before controlled dry-run."
    elif intent_writer_or_consumer:
        risk_level = "HIGH"
        quarantine_priority = "PREVIEW_QUARANTINE"
        action = "Create intent ownership preview and route future intent production through PairContext events."
    elif paper_or_dryrun_bridge:
        risk_level = "HIGH"
        quarantine_priority = "PREVIEW_QUARANTINE"
        action = "Create bridge quarantine preview and verify it cannot call REST or mutate execution state."
    elif duplicate_intent:
        risk_level = "MEDIUM"
        quarantine_priority = "REVIEW"
        action = "Review duplicate intent protection and convert findings into PairContext idempotency checks."
    elif read_only:
        risk_level = "LOW"
        quarantine_priority = "OBSERVE"
        action = "Keep read-only as migration evidence and validation material."
    else:
        risk_level = "MEDIUM"
        quarantine_priority = "REVIEW"
        action = "Review manually and classify as read-only audit, event producer, or bridge touchpoint."

    return {
        "path": path,
        "categories": categories,
        "touched_targets": targets,
        "risk_level": risk_level,
        "quarantine_priority": quarantine_priority,
        "recommended_next_action": action,
        "paircontext_replacement_target": (
            "PairContext execution intent events, consume/attempt events, idempotency, and replay-safe attribution"
        ),
        "evidence_keywords": terms,
    }


def build_k_intent_execution_bridge_preview() -> dict[str, Any]:
    upstream = build_preview()
    errors = list(upstream.get("errors") or [])
    warnings = list(upstream.get("warnings") or [])
    warnings.extend(optional_input_warnings())
    candidates = [
        classify_execution_bridge_candidate(candidate)
        for candidate in upstream.get("candidates", [])
        if touches_execution_bridge(candidate)
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
    parser = argparse.ArgumentParser(description="Read-only K intent and execution bridge safety preview")
    parser.add_argument("--output", default="", help="Optional output path. Default prints JSON to stdout only.")
    args = parser.parse_args(argv)

    report = build_k_intent_execution_bridge_preview()
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
