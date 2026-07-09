from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.revo_pair_context_projection_apply_dryrun import build_dryrun_report


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _copy_allowed(operation: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    source = Path(str(operation.get("would_copy_from") or ""))
    destination = Path(str(operation.get("would_copy_to") or ""))
    if not operation.get("allowed_by_guard"):
        errors.append(f"{operation.get('name')}: operation not allowed by guard")
    if not source.exists():
        errors.append(f"{operation.get('name')}: source candidate missing: {source}")
    if not destination.parent.exists():
        errors.append(f"{operation.get('name')}: destination parent missing: {destination.parent}")
    return not errors, errors


def run_guarded_apply(
    *,
    runtime_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    apply: bool = False,
    confirm_paircontext_owner: bool = False,
) -> dict[str, Any]:
    dryrun = build_dryrun_report(runtime_dir=runtime_dir, db_path=db_path)
    mode = "apply" if apply else "dry_run"
    planned = list(dryrun.get("planned_operations") or [])
    errors: list[str] = list(dryrun.get("errors") or [])
    warnings: list[str] = list(dryrun.get("warnings") or [])
    copied: list[dict[str, Any]] = []
    apply_ready = bool(dryrun.get("apply_ready"))

    if apply and not confirm_paircontext_owner:
        errors.append("missing --confirm-paircontext-owner")
    if apply and not apply_ready:
        errors.append("guard not ready: apply_ready=false")

    if apply and confirm_paircontext_owner and apply_ready:
        preflight_errors: list[str] = []
        for operation in planned:
            allowed, operation_errors = _copy_allowed(operation)
            if not allowed:
                preflight_errors.extend(operation_errors)

        if preflight_errors:
            errors.extend(preflight_errors)
        else:
            for operation in planned:
                source = Path(str(operation["would_copy_from"]))
                destination = Path(str(operation["would_copy_to"]))
                shutil.copy2(source, destination)
                copied.append(
                    {
                        "name": operation.get("name"),
                        "copied_from": str(source),
                        "copied_to": str(destination),
                    }
                )

    return {
        "ok": not errors and (not apply or len(copied) == len(planned)),
        "generated_at": utc_now(),
        "mode": mode,
        "apply_ready": apply_ready,
        "copied_count": len(copied),
        "planned_operations": planned,
        "copied_operations": copied,
        "errors": errors,
        "warnings": warnings,
        "guard": dryrun.get("guard", {}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guarded PairContext projection candidate apply command")
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-paircontext-owner", action="store_true")
    args = parser.parse_args(argv)
    report = run_guarded_apply(
        runtime_dir=args.runtime_dir,
        db_path=args.db_path,
        apply=bool(args.apply),
        confirm_paircontext_owner=bool(args.confirm_paircontext_owner),
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
