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

from scripts.revo_pair_context_projection_apply_guard_preview import build_preview
from user_data.revo_alpha.pair_context.paths import resolve_runtime_paths


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def planned_operations(runtime_dir: str | Path | None = None, db_path: str | Path | None = None, *, allowed_by_guard: bool) -> list[dict[str, Any]]:
    paths = resolve_runtime_paths(runtime_dir=runtime_dir, db_path=db_path)
    plans = [
        ("pair_universe_remote", paths.remote_candidate_path, paths.remote_pairlist_path),
        ("revo_execution_context", paths.execution_candidate_path, paths.execution_context_path),
    ]
    return [
        {
            "name": name,
            "would_copy_from": str(source),
            "would_copy_to": str(destination),
            "source_exists": source.exists(),
            "destination_exists": destination.exists(),
            "allowed_by_guard": bool(allowed_by_guard),
        }
        for name, source, destination in plans
    ]


def build_dryrun_report(runtime_dir: str | Path | None = None, db_path: str | Path | None = None) -> dict[str, Any]:
    guard = build_preview(runtime_dir=runtime_dir, db_path=db_path)
    apply_ready = bool(guard.get("apply_ready"))
    return {
        "ok": bool(guard.get("ok")) and apply_ready,
        "generated_at": utc_now(),
        "apply_ready": apply_ready,
        "guard": guard,
        "planned_operations": planned_operations(runtime_dir=runtime_dir, db_path=db_path, allowed_by_guard=apply_ready),
        "errors": list(guard.get("errors") or []),
        "warnings": list(guard.get("warnings") or []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only PairContext projection apply dry-run")
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--db-path", default=None)
    args = parser.parse_args(argv)
    report = build_dryrun_report(runtime_dir=args.runtime_dir, db_path=args.db_path)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
