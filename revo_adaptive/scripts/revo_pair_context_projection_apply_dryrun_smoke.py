from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.revo_pair_context_projection_apply_dryrun import build_dryrun_report
from user_data.revo_alpha.pair_context.paths import RuntimePaths
from user_data.revo_alpha.pair_context.projections import projection_owner_metadata


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def candidate_remote() -> dict[str, Any]:
    return {**projection_owner_metadata(), "pairs": ["BTC/USDT:USDT"], "projection_hash": "SMOKE_HASH"}


def candidate_execution() -> dict[str, Any]:
    return {**projection_owner_metadata(), "pairs": {"BTC/USDT:USDT": {}}, "projection_hash": "SMOKE_HASH"}


def prepare_live(paths: RuntimePaths) -> None:
    write_json(paths.remote_pairlist_path, {"pairs": ["BTC/USDT:USDT"], "source": "LEGACY"})
    write_json(paths.execution_context_path, {"pairs": {"BTC/USDT:USDT": {}}, "source": "LEGACY"})


def run_case(name: str, setup: Any, expected_allowed: bool) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"paircontext_apply_dryrun_{name}_") as tmp:
        runtime = Path(tmp)
        paths = RuntimePaths.from_runtime_dir(runtime)
        prepare_live(paths)
        setup(paths)
        report = build_dryrun_report(runtime_dir=runtime)
        operation_allowed = [bool(op["allowed_by_guard"]) for op in report["planned_operations"]]
        passed = all(value is expected_allowed for value in operation_allowed)
        return {
            "name": name,
            "passed": passed,
            "expected_allowed": expected_allowed,
            "apply_ready": bool(report.get("apply_ready")),
            "operation_allowed": operation_allowed,
            "error_count": len(report.get("errors") or []),
            "warning_count": len(report.get("warnings") or []),
        }


def setup_valid(paths: RuntimePaths) -> None:
    write_json(paths.remote_candidate_path, candidate_remote())
    write_json(paths.execution_candidate_path, candidate_execution())


def setup_wrong_owner(paths: RuntimePaths) -> None:
    write_json(paths.remote_candidate_path, {"generated_by_owner": "LEGACY_WRITER", "pairs": []})
    write_json(paths.execution_candidate_path, candidate_execution())


def setup_missing_candidate(paths: RuntimePaths) -> None:
    write_json(paths.remote_candidate_path, candidate_remote())


def main() -> None:
    cases = [
        run_case("valid_candidates", setup_valid, True),
        run_case("wrong_owner_candidate", setup_wrong_owner, False),
        run_case("missing_candidate", setup_missing_candidate, False),
    ]
    failed = [case["name"] for case in cases if not case["passed"]]
    summary = {
        "ok": not failed,
        "generated_at": utc_now(),
        "case_names": [case["name"] for case in cases],
        "cases": cases,
        "passed_count": len(cases) - len(failed),
        "failed_count": len(failed),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    assert not failed, failed


if __name__ == "__main__":
    main()
