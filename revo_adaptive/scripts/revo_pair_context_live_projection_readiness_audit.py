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
from user_data.revo_alpha.pair_context.projections import OWNER, read_json, validate_projection_owner_file


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pairs_from(data: Any) -> list[str]:
    if isinstance(data, dict):
        pairs = data.get("pairs")
        if isinstance(pairs, list):
            return sorted(str(pair) for pair in pairs)
        if isinstance(pairs, dict):
            return sorted(str(pair) for pair in pairs)
        for key in ("pair_context", "execution_context", "contexts", "entries"):
            value = data.get(key)
            if isinstance(value, dict):
                return sorted(str(pair) for pair in value)
        top_level_pairs = [str(key) for key in data if "/" in str(key)]
        if top_level_pairs:
            return sorted(top_level_pairs)
    if isinstance(data, list):
        out: list[str] = []
        for item in data:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict) and item.get("pair"):
                out.append(str(item["pair"]))
        return sorted(out)
    return []


def _owner_info(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"owner": None, "source": None, "generated_at": None, "cycle_id": None}
    return {
        "owner": data.get("generated_by_owner") or data.get("projection_owner") or data.get("generated_by"),
        "source": data.get("source"),
        "generated_at": data.get("generated_at"),
        "cycle_id": data.get("cycle_id"),
    }


def _backup_commands(path: Path, stamp: str) -> dict[str, str]:
    backup = f"{path}.bak_pre_paircontext_{stamp}"
    return {
        "path": str(path),
        "backup_path": backup,
        "windows_cmd": f'copy "{path}" "{backup}"',
        "posix_cmd": f'cp "{path}" "{backup}"',
    }


def build_report(runtime_dir: str | Path | None = None, db_path: str | Path | None = None) -> dict[str, Any]:
    paths = resolve_runtime_paths(runtime_dir=runtime_dir, db_path=db_path)
    path_validation = validate_pair_context_paths(runtime_dir=runtime_dir, db_path=db_path)

    store_exists = paths.db_path.exists()
    store_info = {
        "path": str(paths.db_path),
        "exists": store_exists,
        "size_bytes": paths.db_path.stat().st_size if store_exists else 0,
        "create_command": (
            "ALREADY_EXISTS"
            if store_exists
            else f"python scripts/revo_pair_context_phase0_mirror.py --runtime-dir {paths.runtime_dir}"
        ),
    }

    candidate_remote = validate_projection_owner_file(paths.remote_candidate_path, expected_owner=OWNER, missing_is_error=True)
    candidate_exec = validate_projection_owner_file(paths.execution_candidate_path, expected_owner=OWNER, missing_is_error=True)
    candidate_remote_data = read_json(paths.remote_candidate_path, {})
    candidate_exec_data = read_json(paths.execution_candidate_path, {})

    live_remote = validate_projection_owner_file(paths.remote_pairlist_path, expected_owner=OWNER, missing_is_error=False)
    live_exec = validate_projection_owner_file(paths.execution_context_path, expected_owner=OWNER, missing_is_error=False)
    live_remote_data = read_json(paths.remote_pairlist_path, {})
    live_exec_data = read_json(paths.execution_context_path, {})

    candidate_pairs = set(_pairs_from(candidate_remote_data))
    live_pairs = set(_pairs_from(live_remote_data))
    candidate_exec_pairs = set(_pairs_from(candidate_exec_data))
    live_exec_pairs = set(_pairs_from(live_exec_data))

    diff = {
        "candidate_pair_count": len(candidate_pairs),
        "live_pair_count": len(live_pairs),
        "overlap_count": len(candidate_pairs & live_pairs),
        "candidate_only": sorted(candidate_pairs - live_pairs)[:50],
        "live_only": sorted(live_pairs - candidate_pairs)[:50],
        "candidate_exec_pair_count": len(candidate_exec_pairs),
        "live_exec_pair_count": len(live_exec_pairs),
        "execution_overlap_count": len(candidate_exec_pairs & live_exec_pairs),
        "candidate_exec_only": sorted(candidate_exec_pairs - live_exec_pairs)[:50],
        "live_exec_only": sorted(live_exec_pairs - candidate_exec_pairs)[:50],
    }

    apply_prerequisites: list[str] = []
    if not store_exists:
        apply_prerequisites.append(
            f"RUN: python scripts/revo_pair_context_phase0_mirror.py --runtime-dir {paths.runtime_dir}"
        )
    if not candidate_remote.get("ok"):
        apply_prerequisites.append("FIX: candidate pair_universe_remote.json.candidate.json must have valid owner")
    if not candidate_exec.get("ok"):
        apply_prerequisites.append("FIX: candidate revo_execution_context.json.candidate.json must have valid owner")
    if not paths.remote_pairlist_path.exists():
        apply_prerequisites.append("WARNING: live pair_universe_remote.json does not exist; first apply will create it")
    if not paths.execution_context_path.exists():
        apply_prerequisites.append("WARNING: live revo_execution_context.json does not exist; first apply will create it")

    apply_ready = len(apply_prerequisites) == 0 or all(item.startswith("WARNING:") for item in apply_prerequisites)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_commands = []
    if paths.remote_pairlist_path.exists():
        backup_commands.append(_backup_commands(paths.remote_pairlist_path, stamp))
    if paths.execution_context_path.exists():
        backup_commands.append(_backup_commands(paths.execution_context_path, stamp))

    return {
        "ok": apply_ready,
        "generated_at": utc_now(),
        "runtime_dir": str(paths.runtime_dir),
        "store": store_info,
        "candidate_projections": {
            "remote": {
                "path": str(paths.remote_candidate_path),
                "exists": candidate_remote.get("exists", False),
                "owner_ok": candidate_remote.get("ok", False),
                "owner": candidate_remote.get("actual_owner"),
                "pair_count": len(candidate_pairs),
                "errors": candidate_remote.get("errors", []),
                "warnings": candidate_remote.get("warnings", []),
            },
            "execution_context": {
                "path": str(paths.execution_candidate_path),
                "exists": candidate_exec.get("exists", False),
                "owner_ok": candidate_exec.get("ok", False),
                "owner": candidate_exec.get("actual_owner"),
                "pair_count": len(candidate_exec_pairs),
                "errors": candidate_exec.get("errors", []),
                "warnings": candidate_exec.get("warnings", []),
            },
        },
        "live_projections": {
            "remote": {
                "path": str(paths.remote_pairlist_path),
                "exists": live_remote.get("exists", False),
                "owner_ok": live_remote.get("ok", False),
                "owner": _owner_info(live_remote_data),
                "pair_count": len(live_pairs),
                "errors": live_remote.get("errors", []),
                "warnings": live_remote.get("warnings", []),
            },
            "execution_context": {
                "path": str(paths.execution_context_path),
                "exists": live_exec.get("exists", False),
                "owner_ok": live_exec.get("ok", False),
                "owner": _owner_info(live_exec_data),
                "pair_count": len(live_exec_pairs),
                "errors": live_exec.get("errors", []),
                "warnings": live_exec.get("warnings", []),
            },
        },
        "diff": diff,
        "apply_readiness": {
            "ready": apply_ready,
            "prerequisites": apply_prerequisites,
            "backup_commands": backup_commands,
            "apply_command": "python scripts/revo_pair_context_projection_guarded_apply.py --apply --confirm-paircontext-owner",
            "dry_run_command": "python scripts/revo_pair_context_projection_guarded_apply.py",
        },
        "path_validation": path_validation,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only live projection readiness audit")
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--output", default="", help="Optional output path")
    args = parser.parse_args(argv)

    report = build_report(runtime_dir=args.runtime_dir, db_path=args.db_path)
    payload = json.dumps(report, ensure_ascii=False, sort_keys=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
