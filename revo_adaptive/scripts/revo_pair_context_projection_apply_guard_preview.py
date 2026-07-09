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

from user_data.revo_alpha.pair_context.paths import resolve_runtime_paths
from user_data.revo_alpha.pair_context.projections import validate_projection_owner_file


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _owner_check(name: str, path: Path, *, required: bool, fatal_owner: bool) -> dict[str, Any]:
    result = validate_projection_owner_file(path, missing_is_error=required)
    errors = list(result.get("errors") or [])
    warnings = list(result.get("warnings") or [])
    if not fatal_owner and errors:
        warnings.extend(errors)
        errors = []
    return {
        "name": name,
        "path": str(path),
        "required": required,
        "exists": bool(result.get("exists")),
        "ok": not errors,
        "expected_owner": result.get("expected_owner"),
        "actual_owner": result.get("actual_owner"),
        "owner_field": result.get("owner_field"),
        "errors": errors,
        "warnings": warnings,
    }


def build_preview(runtime_dir: str | Path | None = None, db_path: str | Path | None = None) -> dict[str, Any]:
    paths = resolve_runtime_paths(runtime_dir=runtime_dir, db_path=db_path)
    candidate_paths = {
        "pair_universe_remote_candidate": paths.remote_candidate_path,
        "revo_execution_context_candidate": paths.execution_candidate_path,
    }
    live_paths = {
        "pair_universe_remote": paths.remote_pairlist_path,
        "revo_execution_context": paths.execution_context_path,
    }

    candidate_checks = [
        _owner_check(name, path, required=True, fatal_owner=True)
        for name, path in sorted(candidate_paths.items())
    ]
    live_checks = [
        _owner_check(name, path, required=False, fatal_owner=False)
        for name, path in sorted(live_paths.items())
    ]

    errors: list[str] = []
    warnings: list[str] = []
    for check in candidate_checks:
        errors.extend(f"{check['name']}: {message}" for message in check["errors"])
        warnings.extend(f"{check['name']}: {message}" for message in check["warnings"])
    for check in live_checks:
        errors.extend(f"{check['name']}: {message}" for message in check["errors"])
        warnings.extend(f"{check['name']}: {message}" for message in check["warnings"])

    apply_ready = all(check["exists"] and check["ok"] for check in candidate_checks)
    return {
        "ok": not errors,
        "generated_at": utc_now(),
        "candidate_checks": candidate_checks,
        "live_checks": live_checks,
        "errors": errors,
        "warnings": warnings,
        "apply_ready": apply_ready,
        "checked_count": len(candidate_checks) + len(live_checks),
        "existing_candidate_count": sum(1 for check in candidate_checks if check["exists"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only PairContext projection apply guard preview")
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--db-path", default=None)
    args = parser.parse_args(argv)
    report = build_preview(runtime_dir=args.runtime_dir, db_path=args.db_path)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
