from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.revo_execution_context_writer_quarantine_preview import (
    classify_execution_context_candidate,
    touches_execution_context,
)
from scripts.revo_execution_bridge_active_surface_map import classify_candidate as classify_bridge_active_surface
from scripts.revo_k_intent_execution_bridge_safety_preview import (
    classify_execution_bridge_candidate,
    optional_input_warnings as bridge_optional_input_warnings,
    touches_execution_bridge,
)
from scripts.revo_pair_context_projection_apply_dryrun import build_dryrun_report
from scripts.revo_pair_context_projection_apply_guard_preview import build_preview as build_apply_guard_preview
from scripts.revo_pair_context_projection_health_diagnostic import build_report as build_projection_health_report
from scripts.revo_pair_context_writer_quarantine_preview import build_preview as build_writer_quarantine_preview
from scripts.revo_pair_universe_writer_quarantine_preview import (
    classify_pair_universe_candidate,
    touches_pair_universe,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts = Counter(str(item.get(key) or "") for item in items)
    counts.pop("", None)
    return {name: counts[name] for name in sorted(counts)}


def _json_safe(payload: dict[str, Any]) -> dict[str, Any]:
    json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return payload


def _safe_check(name: str, builder: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        report = _json_safe(builder())
        return {
            "name": name,
            "ok": bool(report.get("ok", True)),
            "hard_stops": [],
            "warnings": list(report.get("warnings") or []),
            "errors": list(report.get("errors") or []),
            "report": report,
        }
    except Exception as exc:
        message = f"{name}: diagnostic failed: {type(exc).__name__}: {exc}"
        return {
            "name": name,
            "ok": False,
            "hard_stops": [message],
            "warnings": [],
            "errors": [message],
            "report": {},
        }


def _filtered_preview(
    upstream: dict[str, Any],
    *,
    target: str,
    touches: Callable[[dict[str, Any]], bool],
    classify: Callable[[dict[str, Any]], dict[str, Any]],
    extra_warnings: list[str] | None = None,
) -> dict[str, Any]:
    candidates = [
        classify(candidate)
        for candidate in upstream.get("candidates", [])
        if touches(candidate)
    ]
    candidates = sorted(
        candidates,
        key=lambda item: (
            {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}.get(str(item.get("risk_level")), 9),
            str(item.get("path") or ""),
        ),
    )
    return {
        "ok": not upstream.get("errors"),
        "generated_at": utc_now(),
        "target": target,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "quarantine_priority_counts": _count_by(candidates, "quarantine_priority"),
        "risk_level_counts": _count_by(candidates, "risk_level"),
        "errors": list(upstream.get("errors") or []),
        "warnings": list(upstream.get("warnings") or []) + list(extra_warnings or []),
    }


def _active_surface_map_from_preview(upstream: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        classify_bridge_active_surface(candidate)
        for candidate in upstream.get("candidates", [])
    ]
    candidates = sorted(
        candidates,
        key=lambda item: (
            {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}.get(str(item.get("revised_risk_level")), 9),
            str(item.get("active_surface_class") or ""),
            str(item.get("path") or ""),
        ),
    )
    return {
        "ok": not upstream.get("errors"),
        "generated_at": utc_now(),
        "source_candidate_count": int(upstream.get("candidate_count") or 0),
        "classified_count": len(candidates),
        "active_surface_counts": _count_by(candidates, "active_surface_class"),
        "revised_priority_counts": _count_by(candidates, "revised_quarantine_priority"),
        "dry_run_blocking_count": sum(1 for item in candidates if item.get("blocks_dry_run")),
        "candidates": candidates,
        "errors": list(upstream.get("errors") or []),
        "warnings": list(upstream.get("warnings") or []),
    }


def _add_hard_stop(
    hard_stops: list[str],
    next_required_actions: list[str],
    message: str,
    action: str,
) -> None:
    hard_stops.append(message)
    next_required_actions.append(action)


def build_preflight_report(runtime_dir: str | Path | None = None, db_path: str | Path | None = None) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    hard_stops: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    next_required_actions: list[str] = []

    projection_health = _safe_check(
        "projection_health",
        lambda: build_projection_health_report(runtime_dir=runtime_dir, db_path=db_path),
    )
    checks["projection_health"] = projection_health

    projection_guard = _safe_check(
        "projection_apply_guard_preview",
        lambda: build_apply_guard_preview(runtime_dir=runtime_dir, db_path=db_path),
    )
    checks["projection_apply_guard_preview"] = projection_guard

    projection_dryrun = _safe_check(
        "projection_apply_dryrun",
        lambda: build_dryrun_report(runtime_dir=runtime_dir, db_path=db_path),
    )
    checks["projection_apply_dryrun"] = projection_dryrun

    writer_quarantine = _safe_check("writer_quarantine_preview", build_writer_quarantine_preview)
    checks["writer_quarantine_preview"] = writer_quarantine
    upstream_writer = writer_quarantine.get("report") or {}

    pair_universe = _safe_check(
        "pair_universe_writer_quarantine_preview",
        lambda: _filtered_preview(
            upstream_writer,
            target="pair_universe_remote.json",
            touches=touches_pair_universe,
            classify=classify_pair_universe_candidate,
        ),
    )
    checks["pair_universe_writer_quarantine_preview"] = pair_universe

    execution_context = _safe_check(
        "execution_context_writer_quarantine_preview",
        lambda: _filtered_preview(
            upstream_writer,
            target="revo_execution_context.json",
            touches=touches_execution_context,
            classify=classify_execution_context_candidate,
        ),
    )
    checks["execution_context_writer_quarantine_preview"] = execution_context

    k_bridge = _safe_check(
        "k_intent_execution_bridge_safety_preview",
        lambda: _filtered_preview(
            upstream_writer,
            target="k_intent_execution_bridge",
            touches=touches_execution_bridge,
            classify=classify_execution_bridge_candidate,
            extra_warnings=bridge_optional_input_warnings(),
        ),
    )
    checks["k_intent_execution_bridge_safety_preview"] = k_bridge

    bridge_active_surface = _safe_check(
        "execution_bridge_active_surface_map",
        lambda: _active_surface_map_from_preview(k_bridge.get("report") or {}),
    )
    checks["execution_bridge_active_surface_map"] = bridge_active_surface

    for name in sorted(checks):
        check = checks[name]
        errors.extend(f"{name}: {message}" for message in check.get("errors") or [])
        warnings.extend(f"{name}: {message}" for message in check.get("warnings") or [])
        for message in check.get("hard_stops") or []:
            _add_hard_stop(
                hard_stops,
                next_required_actions,
                message,
                f"Fix diagnostic failure for {name} before controlled dry-run.",
            )

    guard_report = projection_guard.get("report") or {}
    if not bool(guard_report.get("apply_ready")):
        _add_hard_stop(
            hard_stops,
            next_required_actions,
            "projection apply guard apply_ready=false",
            "Generate valid PairContext-owned candidate projections before controlled dry-run.",
        )

    dryrun_report = projection_dryrun.get("report") or {}
    if not bool(dryrun_report.get("apply_ready")):
        _add_hard_stop(
            hard_stops,
            next_required_actions,
            "projection apply dry-run apply_ready=false",
            "Resolve projection dry-run guard failures before controlled dry-run.",
        )

    k_report = k_bridge.get("report") or {}
    k_priorities = k_report.get("quarantine_priority_counts") or {}
    raw_block_before_dry_run = int(k_priorities.get("BLOCK_BEFORE_DRY_RUN") or 0)
    if raw_block_before_dry_run:
        warnings.append(
            f"K intent / execution bridge raw BLOCK_BEFORE_DRY_RUN candidates: {raw_block_before_dry_run}"
        )
        next_required_actions.append("Review active surface map classification before controlled dry-run.")
        next_required_actions.append("Keep execution bridge disabled unless explicit policy enables it.")

    active_surface_report = bridge_active_surface.get("report") or {}
    active_bridge_blockers = int(active_surface_report.get("dry_run_blocking_count") or 0)
    active_blocking_candidates = [
        candidate
        for candidate in active_surface_report.get("candidates", [])
        if candidate.get("blocks_dry_run")
    ]
    if active_bridge_blockers or active_blocking_candidates:
        _add_hard_stop(
            hard_stops,
            next_required_actions,
            f"execution bridge active surface has dry-run blocking candidates: {active_bridge_blockers}",
            "Map or neutralize active execution bridge surfaces before controlled dry-run.",
        )

    writer_priorities = (writer_quarantine.get("report") or {}).get("quarantine_priority_counts") or {}
    if writer_priorities:
        warnings.append(f"writer quarantine preview candidates present: {json.dumps(writer_priorities, sort_keys=True)}")

    pair_priorities = (pair_universe.get("report") or {}).get("quarantine_priority_counts") or {}
    pair_live_blockers = int(pair_priorities.get("BLOCK_BEFORE_LIVE_AUTHORITY") or 0)
    if pair_live_blockers:
        warnings.append(f"pair universe BLOCK_BEFORE_LIVE_AUTHORITY candidates: {pair_live_blockers}")
        next_required_actions.append("Map pair universe live-authority blockers before PairContext live authority.")

    execution_priorities = (execution_context.get("report") or {}).get("quarantine_priority_counts") or {}
    execution_live_blockers = int(execution_priorities.get("BLOCK_BEFORE_LIVE_AUTHORITY") or 0)
    if execution_live_blockers:
        warnings.append(f"execution context BLOCK_BEFORE_LIVE_AUTHORITY candidates: {execution_live_blockers}")
        next_required_actions.append("Map execution context live-authority blockers before PairContext live authority.")

    preflight_ready = not hard_stops
    return {
        "ok": preflight_ready and not errors,
        "generated_at": utc_now(),
        "preflight_ready": preflight_ready,
        "hard_stop_count": len(hard_stops),
        "warning_count": len(warnings),
        "checks": {name: checks[name] for name in sorted(checks)},
        "errors": errors,
        "warnings": warnings,
        "hard_stops": hard_stops,
        "next_required_actions": sorted(set(next_required_actions)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only dry-run preflight diagnostic")
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--output", default="", help="Optional output path. Default prints JSON to stdout only.")
    args = parser.parse_args(argv)

    report = build_preflight_report(runtime_dir=args.runtime_dir, db_path=args.db_path)
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
