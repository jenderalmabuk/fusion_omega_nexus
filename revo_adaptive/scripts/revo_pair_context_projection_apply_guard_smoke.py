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

from scripts.revo_pair_context_projection_apply_guard_preview import build_preview
from user_data.revo_alpha.pair_context.paths import RuntimePaths
from user_data.revo_alpha.pair_context.projections import projection_owner_metadata


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def valid_remote_payload() -> dict[str, Any]:
    return {
        **projection_owner_metadata(),
        "pairs": ["BTC/USDT:USDT"],
        "cycle_id": "SMOKE_CYCLE",
        "projection_hash": "SMOKE_HASH",
    }


def valid_execution_payload() -> dict[str, Any]:
    return {
        **projection_owner_metadata(),
        "pairs": {"BTC/USDT:USDT": {"pair": "BTC/USDT:USDT"}},
        "cycle_id": "SMOKE_CYCLE",
        "projection_hash": "SMOKE_HASH",
    }


def prepare_live_without_owner(paths: RuntimePaths) -> None:
    write_json(paths.remote_pairlist_path, {"pairs": ["BTC/USDT:USDT"], "source": "LEGACY"})
    write_json(paths.execution_context_path, {"pairs": {"BTC/USDT:USDT": {}}, "source": "LEGACY"})


def prepare_valid_candidates(paths: RuntimePaths) -> None:
    write_json(paths.remote_candidate_path, valid_remote_payload())
    write_json(paths.execution_candidate_path, valid_execution_payload())


def run_case(name: str, setup: Any, expected_ready: bool) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"paircontext_apply_guard_{name}_") as tmp:
        runtime = Path(tmp)
        paths = RuntimePaths.from_runtime_dir(runtime)
        setup(paths)
        report = build_preview(runtime_dir=runtime)
        passed = bool(report.get("apply_ready")) is expected_ready
        if expected_ready:
            passed = passed and not report.get("errors") and bool(report.get("warnings"))
        return {
            "name": name,
            "passed": passed,
            "expected_apply_ready": expected_ready,
            "actual_apply_ready": bool(report.get("apply_ready")),
            "error_count": len(report.get("errors") or []),
            "warning_count": len(report.get("warnings") or []),
            "existing_candidate_count": report.get("existing_candidate_count"),
        }


def setup_valid(paths: RuntimePaths) -> None:
    prepare_live_without_owner(paths)
    prepare_valid_candidates(paths)


def setup_missing_owner(paths: RuntimePaths) -> None:
    prepare_live_without_owner(paths)
    write_json(paths.remote_candidate_path, {"pairs": ["BTC/USDT:USDT"]})
    write_json(paths.execution_candidate_path, {"pairs": {"BTC/USDT:USDT": {}}})


def setup_wrong_owner(paths: RuntimePaths) -> None:
    prepare_live_without_owner(paths)
    wrong = {"generated_by_owner": "LEGACY_WRITER"}
    write_json(paths.remote_candidate_path, {**wrong, "pairs": ["BTC/USDT:USDT"]})
    write_json(paths.execution_candidate_path, {**wrong, "pairs": {"BTC/USDT:USDT": {}}})


def setup_invalid_json(paths: RuntimePaths) -> None:
    prepare_live_without_owner(paths)
    paths.remote_candidate_path.write_text("{ invalid json", encoding="utf-8")
    write_json(paths.execution_candidate_path, valid_execution_payload())


def setup_missing_candidate(paths: RuntimePaths) -> None:
    prepare_live_without_owner(paths)
    write_json(paths.remote_candidate_path, valid_remote_payload())


def main() -> None:
    cases = [
        run_case("valid_candidates_live_owner_warning_only", setup_valid, True),
        run_case("missing_owner_marker", setup_missing_owner, False),
        run_case("wrong_owner_marker", setup_wrong_owner, False),
        run_case("invalid_json", setup_invalid_json, False),
        run_case("missing_candidate_file", setup_missing_candidate, False),
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
