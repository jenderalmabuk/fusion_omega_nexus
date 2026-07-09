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

TARGET = "pair_universe_remote.json"

PAIR_UNIVERSE_TERMS = (
    "pair_universe_remote.json",
    "pair_universe_remote",
    "pairlist",
    "remote pairlist",
    "remotepairlist",
    "active whitelist",
    "whitelist",
    "pair universe",
    "scanner reselection",
    "reselection",
    "scanner",
)

DIRECT_WRITE_TERMS = (
    "pair_universe_remote.json",
    "pair_universe_remote",
)

SCANNER_SELECTION_TERMS = (
    "scanner",
    "reselection",
    "active whitelist",
    "whitelist",
    "pair universe source",
)

READ_ONLY_CATEGORIES = {
    "READ_ONLY_AUDIT",
    "SHADOW_ONLY",
    "DRYRUN_ONLY",
}

PREVIEW_CATEGORIES = {
    "PATCH_PREVIEW",
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
    return sorted({term for term in PAIR_UNIVERSE_TERMS if term.lower() in haystack})


def touches_pair_universe(candidate: dict[str, Any]) -> bool:
    terms = matched_terms(candidate)
    if terms:
        return True
    categories = set(str(category) for category in candidate.get("categories") or [])
    targets = " ".join(str(target) for target in candidate.get("touched_targets") or []).lower()
    return "PAIRLIST_WRITER" in categories or TARGET in targets


def touched_targets(candidate: dict[str, Any], terms: list[str]) -> list[str]:
    targets = set(str(target) for target in candidate.get("touched_targets") or [])
    if "PAIRLIST_WRITER" in set(candidate.get("categories") or []):
        targets.add(TARGET)
    for term in terms:
        if term in {"pair_universe_remote.json", "pair_universe_remote"}:
            targets.add(TARGET)
        elif term in {"remote pairlist", "remotepairlist", "pairlist"}:
            targets.add("remote pairlist / pairlist writer")
        elif term in {"active whitelist", "whitelist"}:
            targets.add("active whitelist / pair universe source")
        elif term in {"scanner", "scanner reselection", "reselection"}:
            targets.add("scanner reselection affecting pair universe")
    return sorted(targets)


def classify_pair_universe_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    path = str(candidate.get("path") or "")
    categories = sorted(str(category) for category in candidate.get("categories") or [])
    terms = matched_terms(candidate)
    targets = touched_targets(candidate, terms)
    lower_text = "\n".join([path, " ".join(terms), " ".join(targets)]).lower()
    category_set = set(categories)

    direct_pair_universe = any(term in lower_text for term in DIRECT_WRITE_TERMS)
    scanner_selection = any(term in lower_text for term in SCANNER_SELECTION_TERMS)
    patch_preview = bool(category_set & PREVIEW_CATEGORIES)
    read_only = bool(category_set & READ_ONLY_CATEGORIES) and not direct_pair_universe

    if direct_pair_universe and not patch_preview:
        risk_level = "CRITICAL"
        quarantine_priority = "BLOCK_BEFORE_LIVE_AUTHORITY"
        action = "Map owner and add explicit pair universe writer quarantine before PairContext live authority."
    elif direct_pair_universe and patch_preview:
        risk_level = "HIGH"
        quarantine_priority = "PREVIEW_QUARANTINE"
        action = "Keep preview-only; verify it cannot write live pair universe outside guarded projection apply."
    elif scanner_selection:
        risk_level = "HIGH"
        quarantine_priority = "PREVIEW_QUARANTINE"
        action = "Map scanner source-selection to PairContext events and deterministic pair universe projection."
    elif read_only:
        risk_level = "LOW"
        quarantine_priority = "OBSERVE"
        action = "Keep read-only as migration evidence and validation material."
    else:
        risk_level = "MEDIUM"
        quarantine_priority = "REVIEW"
        action = "Review manually and classify as read-only audit or PairContext projection producer."

    return {
        "path": path,
        "categories": categories,
        "touched_targets": targets,
        "risk_level": risk_level,
        "quarantine_priority": quarantine_priority,
        "recommended_next_action": action,
        "paircontext_replacement_target": "PairContext deterministic projection owns pair_universe_remote.json",
        "evidence_keywords": terms,
    }


def build_pair_universe_preview() -> dict[str, Any]:
    upstream = build_preview()
    errors = list(upstream.get("errors") or [])
    warnings = list(upstream.get("warnings") or [])
    candidates = [
        classify_pair_universe_candidate(candidate)
        for candidate in upstream.get("candidates", [])
        if touches_pair_universe(candidate)
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
    parser = argparse.ArgumentParser(description="Read-only pair universe writer quarantine preview")
    parser.add_argument("--output", default="", help="Optional output path. Default prints JSON to stdout only.")
    args = parser.parse_args(argv)

    report = build_pair_universe_preview()
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
