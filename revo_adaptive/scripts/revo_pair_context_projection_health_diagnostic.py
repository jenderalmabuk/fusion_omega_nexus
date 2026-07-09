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

from user_data.revo_alpha.pair_context.paths import resolve_runtime_paths, validate_pair_context_paths
from user_data.revo_alpha.pair_context.projections import validate_projection_owner_file


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def projection_paths(runtime_dir: str | Path | None = None, db_path: str | Path | None = None) -> dict[str, Path]:
    paths = resolve_runtime_paths(runtime_dir=runtime_dir, db_path=db_path)
    return {
        "pair_universe_remote": paths.remote_pairlist_path,
        "revo_execution_context": paths.execution_context_path,
        "pair_universe_remote_candidate": paths.remote_candidate_path,
        "revo_execution_context_candidate": paths.execution_candidate_path,
        "projection_diff_report": paths.projection_diff_path,
    }


def compact_owner_check(name: str, path: Path) -> dict[str, Any]:
    result = validate_projection_owner_file(path, missing_is_error=False)
    return {
        "name": name,
        "path": str(path),
        "exists": bool(result.get("exists")),
        "ok": bool(result.get("ok")),
        "expected_owner": result.get("expected_owner"),
        "actual_owner": result.get("actual_owner"),
        "owner_field": result.get("owner_field"),
        "errors": list(result.get("errors") or []),
        "warnings": list(result.get("warnings") or []),
    }


def build_report(runtime_dir: str | Path | None = None, db_path: str | Path | None = None) -> dict[str, Any]:
    path_validation = validate_pair_context_paths(runtime_dir=runtime_dir, db_path=db_path)
    owner_checks = [
        compact_owner_check(name, path)
        for name, path in sorted(projection_paths(runtime_dir=runtime_dir, db_path=db_path).items())
    ]
    errors: list[str] = list(path_validation.get("errors") or [])
    warnings: list[str] = list(path_validation.get("warnings") or [])
    for check in owner_checks:
        errors.extend(f"{check['name']}: {message}" for message in check["errors"])
        warnings.extend(f"{check['name']}: {message}" for message in check["warnings"])

    return {
        "ok": not errors,
        "generated_at": utc_now(),
        "path_validation": path_validation,
        "projection_owner_checks": owner_checks,
        "errors": errors,
        "warnings": warnings,
        "checked_count": len(owner_checks),
        "existing_count": sum(1 for check in owner_checks if check["exists"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only PairContext projection path and owner diagnostic")
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--db-path", default=None)
    args = parser.parse_args(argv)
    report = build_report(runtime_dir=args.runtime_dir, db_path=args.db_path)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
