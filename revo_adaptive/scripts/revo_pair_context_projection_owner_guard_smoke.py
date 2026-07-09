from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from user_data.revo_alpha.pair_context.projections import (
    OWNER,
    projection_owner_metadata,
    validate_projection_owner_file,
    validate_projection_owner_payload,
)


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def main() -> None:
    payload_results = {
        "candidate_owner_metadata_payload": validate_projection_owner_payload({**projection_owner_metadata(), "pairs": []}),
        "valid_payload": validate_projection_owner_payload({"generated_by_owner": OWNER}),
        "missing_owner_payload": validate_projection_owner_payload({"pairs": []}),
        "wrong_owner_payload": validate_projection_owner_payload({"projection_owner": "LEGACY_STICKY_WRITER"}),
        "invalid_payload": validate_projection_owner_payload(["not", "a", "dict"]),
    }

    with tempfile.TemporaryDirectory(prefix="paircontext_owner_guard_") as tmp:
        root = Path(tmp)
        valid_path = root / "valid_projection.json"
        missing_owner_path = root / "missing_owner_projection.json"
        wrong_owner_path = root / "wrong_owner_projection.json"
        invalid_json_path = root / "invalid_projection.json"
        missing_path = root / "missing_projection.json"

        write_json(valid_path, {"generated_by_owner": OWNER, "pairs": []})
        write_json(missing_owner_path, {"pairs": []})
        write_json(wrong_owner_path, {"generated_by": "LEGACY_SCANNER_WRITER", "pairs": []})
        invalid_json_path.write_text("{ invalid json", encoding="utf-8")

        file_results = {
            "valid_file": validate_projection_owner_file(valid_path),
            "missing_owner_file": validate_projection_owner_file(missing_owner_path),
            "wrong_owner_file": validate_projection_owner_file(wrong_owner_path),
            "invalid_json_file": validate_projection_owner_file(invalid_json_path),
            "missing_file": validate_projection_owner_file(missing_path),
        }

    assert payload_results["valid_payload"]["ok"] is True
    assert payload_results["candidate_owner_metadata_payload"]["ok"] is True
    assert payload_results["missing_owner_payload"]["ok"] is False
    assert payload_results["wrong_owner_payload"]["ok"] is False
    assert payload_results["invalid_payload"]["ok"] is False
    assert file_results["valid_file"]["ok"] is True
    assert file_results["missing_owner_file"]["ok"] is False
    assert file_results["wrong_owner_file"]["ok"] is False
    assert file_results["invalid_json_file"]["ok"] is False
    assert file_results["missing_file"]["ok"] is True
    assert file_results["missing_file"]["warnings"]

    summary = {
        "expected_owner": OWNER,
        "file_cases": {
            name: {
                "actual_owner": result.get("actual_owner"),
                "error_count": len(result.get("errors", [])),
                "ok": result.get("ok"),
                "owner_field": result.get("owner_field"),
                "warning_count": len(result.get("warnings", [])),
            }
            for name, result in sorted(file_results.items())
        },
        "payload_cases": {
            name: {
                "actual_owner": result.get("actual_owner"),
                "error_count": len(result.get("errors", [])),
                "ok": result.get("ok"),
                "owner_field": result.get("owner_field"),
                "warning_count": len(result.get("warnings", [])),
            }
            for name, result in sorted(payload_results.items())
        },
    }
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
