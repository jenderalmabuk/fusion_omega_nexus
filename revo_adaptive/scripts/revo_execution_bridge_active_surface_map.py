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

from scripts.revo_k_intent_execution_bridge_safety_preview import build_k_intent_execution_bridge_preview

OPTIONAL_INPUTS = (
    "docs/kiro_deep_analysis/EXECUTION_BRIDGE_DISABLE_PLAN.md",
    "docs/kiro_deep_analysis/KIRO_F4X_EXECUTION_BRIDGE_RISK_MAP.md",
)

ACTIVE_RUNTIME_CLASS = "ACTIVE_RUNTIME_SURFACE"
EXECUTION_CAPABLE_CLASS = "EXECUTION_CAPABLE_REQUIRES_DISABLE"
REST_CLASS = "REST_FORCEENTER_FORCEBUY_CAPABLE"
PAPER_CLASS = "PAPER_OR_DRYRUN_BRIDGE"
INTENT_CLASS = "K_L_INTENT_WRITER_OR_CONSUMER"
OPEN_TRADE_CLASS = "OPEN_TRADE_SOURCE_OF_TRUTH_MUTATOR"
DUPLICATE_CLASS = "DUPLICATE_INTENT_PROTECTION_VALIDATOR"
READ_ONLY_CLASS = "READ_ONLY_AUDIT_OK"
ARCHIVE_CLASS = "ARCHIVE_REFERENCE_ONLY"
PATCH_CLASS = "PATCH_PREVIEW_ONLY"
SHADOW_CLASS = "SHADOW_ONLY"
FALSE_POSITIVE_CLASS = "FALSE_POSITIVE_KEYWORD_ONLY"
MANUAL_REVIEW_CLASS = "NEEDS_MANUAL_REVIEW"

LOW_RISK_NAME_MARKERS = (
    "audit",
    "preview",
    "report",
    "diagnostic",
    "smoke",
    "validate",
    "validator",
    "shadow",
    "dryrun_only",
    "dry-run-only",
    "patch",
    "summary",
    "plan",
    "runbook",
)

REST_MARKERS = (
    "forceenter",
    "forcebuy",
    "requests.post",
    "/api/v1/forcebuy",
    "/api/v1/forceenter",
    "api/v1/forcebuy",
    "api/v1/forceenter",
    "rest dryrun endpoint",
)

PAPER_MARKERS = (
    "paper_bridge",
    "paper bridge",
    "paper signal bridge",
    "dryrun bridge",
    "dry-run bridge",
)

INTENT_MARKERS = (
    "k intent",
    "l intent",
    "k/l intent",
    "f4x_k",
    "f4x_l",
    "active_k",
    "active_signal",
    "intent consumption",
)

OPEN_TRADE_MARKERS = (
    "open trade",
    "open_trade",
    "open trade source-of-truth",
)

DUPLICATE_MARKERS = (
    "duplicate intent",
    "duplicate event window",
    "idempotency",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def optional_input_warnings() -> list[str]:
    warnings: list[str] = []
    for item in OPTIONAL_INPUTS:
        if not (REPO_ROOT / item).exists():
            warnings.append(f"optional input missing: {item}")
    return warnings


def _safe_text(path: str) -> str:
    candidate = REPO_ROOT / path
    if not candidate.exists() or not candidate.is_file():
        return ""
    if candidate.suffix.lower() not in {".py", ".sh", ".md", ".txt", ".csv", ".json"}:
        return ""
    try:
        return candidate.read_text(encoding="utf-8", errors="replace")[:120_000]
    except Exception:
        return ""


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(marker.lower() in lower for marker in markers)


def _has_low_risk_name(path: str) -> bool:
    name = Path(path).name.lower()
    return any(marker in name for marker in LOW_RISK_NAME_MARKERS)


def _base_haystack(candidate: dict[str, Any]) -> str:
    fields = [
        str(candidate.get("path") or ""),
        " ".join(str(item) for item in candidate.get("categories") or []),
        " ".join(str(item) for item in candidate.get("touched_targets") or []),
        " ".join(str(item) for item in candidate.get("evidence_keywords") or []),
        str(candidate.get("recommended_next_action") or ""),
        str(candidate.get("paircontext_replacement_target") or ""),
    ]
    return "\n".join(fields)


def classify_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    path = str(candidate.get("path") or "")
    categories = sorted(str(category) for category in candidate.get("categories") or [])
    evidence_keywords = sorted(str(item) for item in candidate.get("evidence_keywords") or [])
    text = _base_haystack(candidate)
    file_text = _safe_text(path)
    haystack = f"{text}\n{file_text}"
    lower_path = path.lower()
    category_set = set(categories)

    is_doc = lower_path.startswith("docs/")
    is_archive = lower_path.startswith("fusion_audit_evolution/") or lower_path.startswith("legacy_")
    is_script = lower_path.startswith("scripts/")
    is_user_runtime = lower_path.startswith("user_data/")
    is_patch = "PATCH_PREVIEW" in category_set or "patch" in Path(path).name.lower()
    is_shadow = "SHADOW_ONLY" in category_set or "shadow" in Path(path).name.lower()
    is_read_only = "READ_ONLY_AUDIT" in category_set or _has_low_risk_name(path)

    has_rest_call = _contains_any(haystack, REST_MARKERS)
    has_paper_bridge = _contains_any(haystack, PAPER_MARKERS)
    has_intent = "K_INTENT_WRITER" in category_set or _contains_any(haystack, INTENT_MARKERS)
    has_execution_bridge = "EXECUTION_BRIDGE" in category_set or "execution bridge" in haystack.lower()
    has_open_trade = _contains_any(haystack, OPEN_TRADE_MARKERS)
    has_duplicate = _contains_any(haystack, DUPLICATE_MARKERS)

    active_runtime_path = is_user_runtime or (is_script and not _has_low_risk_name(path))

    if is_doc:
        surface_class = READ_ONLY_CLASS
        revised_risk = "LOW"
        revised_priority = "OBSERVE"
        blocks_dry_run = False
        rationale = "Documentation-only path; not an executable bridge surface."
        action = "Keep as planning/reference material."
    elif is_archive:
        if is_patch:
            surface_class = PATCH_CLASS
            revised_risk = "LOW"
            revised_priority = "OBSERVE"
            blocks_dry_run = False
            rationale = "Historical archive patch preview; not active runtime unless copied or executed manually."
            action = "Keep as archive reference; do not execute during dry-run launch."
        elif is_shadow:
            surface_class = SHADOW_CLASS
            revised_risk = "LOW"
            revised_priority = "OBSERVE"
            blocks_dry_run = False
            rationale = "Historical archive shadow/audit path; defaults to non-active reference."
            action = "Keep as migration evidence."
        else:
            surface_class = ARCHIVE_CLASS
            revised_risk = "LOW"
            revised_priority = "OBSERVE"
            blocks_dry_run = False
            rationale = "Path is under archive or legacy scan output; classify as reference until active runtime linkage is proven."
            action = "Do not execute archive material during dry-run launch."
    elif active_runtime_path and has_rest_call:
        surface_class = REST_CLASS
        revised_risk = "CRITICAL"
        revised_priority = "BLOCK_BEFORE_DRY_RUN"
        blocks_dry_run = True
        rationale = "Active path contains REST/forceenter/forcebuy evidence."
        action = "Hard-disable or guard REST execution bridge before controlled dry-run."
    elif active_runtime_path and has_open_trade:
        surface_class = OPEN_TRADE_CLASS
        revised_risk = "CRITICAL"
        revised_priority = "BLOCK_BEFORE_DRY_RUN"
        blocks_dry_run = True
        rationale = "Active path may influence open trade source-of-truth."
        action = "Keep Freqtrade as open-trade source-of-truth and disable competing mutation path."
    elif active_runtime_path and has_execution_bridge:
        surface_class = EXECUTION_CAPABLE_CLASS
        revised_risk = "CRITICAL"
        revised_priority = "BLOCK_BEFORE_DRY_RUN"
        blocks_dry_run = True
        rationale = "Active runtime/script surface has execution bridge evidence."
        action = "Make bridge inert by default before controlled dry-run."
    elif active_runtime_path and has_paper_bridge:
        surface_class = PAPER_CLASS
        revised_risk = "HIGH"
        revised_priority = "PREVIEW_QUARANTINE"
        blocks_dry_run = True
        rationale = "Active path contains paper/dryrun bridge evidence."
        action = "Require explicit opt-in and bridge policy before use."
    elif active_runtime_path and has_intent:
        surface_class = INTENT_CLASS
        revised_risk = "HIGH"
        revised_priority = "PREVIEW_QUARANTINE"
        blocks_dry_run = True
        rationale = "Active path contains K/L intent writer or consumer evidence."
        action = "Convert to PairContext event-sourced intent or disable before bridge activation."
    elif has_duplicate:
        surface_class = DUPLICATE_CLASS
        revised_risk = "MEDIUM"
        revised_priority = "REVIEW"
        blocks_dry_run = False
        rationale = "Duplicate intent/idempotency evidence is validator material unless coupled to an active writer."
        action = "Convert into PairContext idempotency validator."
    elif is_read_only:
        surface_class = READ_ONLY_CLASS
        revised_risk = "LOW"
        revised_priority = "OBSERVE"
        blocks_dry_run = False
        rationale = "Read-only/audit/diagnostic naming or category with no active runtime path."
        action = "Keep read-only as migration evidence."
    elif evidence_keywords and not (has_rest_call or has_paper_bridge or has_intent or has_execution_bridge or has_open_trade):
        surface_class = FALSE_POSITIVE_CLASS
        revised_risk = "LOW"
        revised_priority = "OBSERVE"
        blocks_dry_run = False
        rationale = "Keyword match does not show actionable execution bridge capability."
        action = "Document as false positive after manual review."
    else:
        surface_class = MANUAL_REVIEW_CLASS
        revised_risk = "MEDIUM"
        revised_priority = "REVIEW"
        blocks_dry_run = False
        rationale = "Insufficient evidence to classify as active or safe."
        action = "Review file directly before dry-run policy signoff."

    return {
        "path": path,
        "active_surface_class": surface_class,
        "original_risk_level": candidate.get("risk_level"),
        "original_quarantine_priority": candidate.get("quarantine_priority"),
        "revised_risk_level": revised_risk,
        "revised_quarantine_priority": revised_priority,
        "evidence_keywords": evidence_keywords,
        "rationale": rationale,
        "recommended_next_action": action,
        "blocks_dry_run": blocks_dry_run,
    }


def build_active_surface_map() -> dict[str, Any]:
    source = build_k_intent_execution_bridge_preview()
    errors = list(source.get("errors") or [])
    warnings = list(source.get("warnings") or [])
    warnings.extend(optional_input_warnings())
    classified = [classify_candidate(candidate) for candidate in source.get("candidates", [])]
    classified = sorted(
        classified,
        key=lambda item: (
            {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}.get(str(item["revised_risk_level"]), 9),
            str(item["active_surface_class"]),
            str(item["path"]),
        ),
    )
    class_counts = Counter(item["active_surface_class"] for item in classified)
    priority_counts = Counter(item["revised_quarantine_priority"] for item in classified)
    return {
        "ok": not errors,
        "generated_at": utc_now(),
        "source_candidate_count": int(source.get("candidate_count") or 0),
        "classified_count": len(classified),
        "active_surface_counts": {key: class_counts[key] for key in sorted(class_counts)},
        "revised_priority_counts": {key: priority_counts[key] for key in sorted(priority_counts)},
        "dry_run_blocking_count": sum(1 for item in classified if item["blocks_dry_run"]),
        "candidates": classified,
        "errors": errors,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only execution bridge active surface map")
    parser.add_argument("--output", default="", help="Optional output path. Default prints JSON to stdout only.")
    args = parser.parse_args(argv)
    report = build_active_surface_map()
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
