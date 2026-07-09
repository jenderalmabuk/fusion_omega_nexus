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

from scripts.revo_pair_context_projection_guarded_apply import run_guarded_apply
from user_data.revo_alpha.pair_context.paths import RuntimePaths
from user_data.revo_alpha.pair_context.projections import projection_owner_metadata


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def live_payload(label: str) -> dict[str, Any]:
    return {"source": "LEGACY", "label": label, "pairs": []}


def candidate_remote(owner: dict[str, str] | None = None) -> dict[str, Any]:
    return {**(owner or projection_owner_metadata()), "pairs": ["BTC/USDT:USDT"], "label": "candidate_remote"}


def candidate_execution(owner: dict[str, str] | None = None) -> dict[str, Any]:
    return {**(owner or projection_owner_metadata()), "pairs": {"BTC/USDT:USDT": {}}, "label": "candidate_execution"}


def prepare_live(paths: RuntimePaths) -> None:
    write_json(paths.remote_pairlist_path, live_payload("live_remote"))
    write_json(paths.execution_context_path, live_payload("live_execution"))


def prepare_valid_candidates(paths: RuntimePaths) -> None:
    write_json(paths.remote_candidate_path, candidate_remote())
    write_json(paths.execution_candidate_path, candidate_execution())


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_case(name: str, setup: Any, *, apply: bool, confirm: bool, expect_copy: bool) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"paircontext_guarded_apply_{name}_") as tmp:
        runtime = Path(tmp)
        paths = RuntimePaths.from_runtime_dir(runtime)
        prepare_live(paths)
        setup(paths)
        before_remote = read_json(paths.remote_pairlist_path)
        before_execution = read_json(paths.execution_context_path)
        report = run_guarded_apply(
            runtime_dir=runtime,
            apply=apply,
            confirm_paircontext_owner=confirm,
        )
        after_remote = read_json(paths.remote_pairlist_path)
        after_execution = read_json(paths.execution_context_path)
        copied = after_remote != before_remote or after_execution != before_execution
        candidate_copied = (
            after_remote == read_json(paths.remote_candidate_path)
            and paths.execution_candidate_path.exists()
            and after_execution == read_json(paths.execution_candidate_path)
        )
        passed = copied is expect_copy
        if expect_copy:
            passed = passed and candidate_copied and report["copied_count"] == 2 and report["ok"] is True
        else:
            passed = passed and report["copied_count"] == 0
        return {
            "name": name,
            "passed": passed,
            "expected_copy": expect_copy,
            "copied": copied,
            "mode": report.get("mode"),
            "ok": report.get("ok"),
            "apply_ready": report.get("apply_ready"),
            "copied_count": report.get("copied_count"),
            "error_count": len(report.get("errors") or []),
            "warning_count": len(report.get("warnings") or []),
        }


def setup_valid(paths: RuntimePaths) -> None:
    prepare_valid_candidates(paths)


def setup_wrong_owner(paths: RuntimePaths) -> None:
    wrong = {"generated_by_owner": "LEGACY_WRITER"}
    write_json(paths.remote_candidate_path, candidate_remote(wrong))
    write_json(paths.execution_candidate_path, candidate_execution())


def setup_missing_candidate(paths: RuntimePaths) -> None:
    write_json(paths.remote_candidate_path, candidate_remote())


def main() -> None:
    cases = [
        run_case("default_dry_run_copies_nothing", setup_valid, apply=False, confirm=False, expect_copy=False),
        run_case("apply_without_confirm_copies_nothing", setup_valid, apply=True, confirm=False, expect_copy=False),
        run_case("apply_with_confirm_valid_candidates_copies", setup_valid, apply=True, confirm=True, expect_copy=True),
        run_case("wrong_owner_candidate_copies_nothing", setup_wrong_owner, apply=True, confirm=True, expect_copy=False),
        run_case("missing_candidate_copies_nothing", setup_missing_candidate, apply=True, confirm=True, expect_copy=False),
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
