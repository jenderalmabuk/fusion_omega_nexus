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

from user_data.revo_alpha.pair_context.event_bus import init_event_schema
from user_data.revo_alpha.pair_context.paths import resolve_runtime_paths
from user_data.revo_alpha.pair_context.reducers import check_sticky_expiry
from user_data.revo_alpha.pair_context.store import connect


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_report(
    runtime_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    sticky_ttl_sec: int = 1800,
    cycle_id: str = "",
) -> dict[str, Any]:
    paths = resolve_runtime_paths(runtime_dir=runtime_dir, db_path=db_path)
    if not paths.db_path.exists():
        return {
            "ok": False,
            "generated_at": utc_now(),
            "error": f"SQLite store not found: {paths.db_path}",
            "db_path": str(paths.db_path),
            "runtime_dir": str(paths.runtime_dir),
            "action": "Run scripts/revo_pair_context_phase0_mirror.py first",
        }

    con = connect(paths.db_path)
    try:
        init_event_schema(con)

        now = datetime.now(timezone.utc)
        if not cycle_id:
            cycle_id = now.strftime("%Y%m%dT%H%M%SZ")

        results = check_sticky_expiry(
            con,
            now=now,
            sticky_ttl_sec=sticky_ttl_sec,
            producer="STICKY_EXPIRY_RUNNER_CLI",
            cycle_id=cycle_id,
        )
        con.commit()
    finally:
        con.close()

    expired = [item for item in results if item.get("action") == "EXPIRED"]
    retained = [item for item in results if item.get("action") == "RETAINED"]
    errors = [item for item in results if "error" in item]

    return {
        "ok": not errors,
        "generated_at": utc_now(),
        "db_path": str(paths.db_path),
        "runtime_dir": str(paths.runtime_dir),
        "sticky_ttl_sec": sticky_ttl_sec,
        "cycle_id": cycle_id,
        "total_sticky_checked": len(results),
        "expired_count": len(expired),
        "retained_count": len(retained),
        "error_count": len(errors),
        "expired": expired[:50],
        "retained_sample": retained[:10],
        "errors": errors,
    }


def compact_text(report: dict[str, Any]) -> str:
    lines = [
        "PAIR_CONTEXT_STICKY_EXPIRY_RUNNER_COMPACT",
        f"generated_at={report.get('generated_at')}",
        f"db_path={report.get('db_path')}",
        f"sticky_ttl_sec={report.get('sticky_ttl_sec')}",
        f"cycle_id={report.get('cycle_id')}",
        f"total_sticky_checked={report.get('total_sticky_checked')}",
        f"expired_count={report.get('expired_count')}",
        f"retained_count={report.get('retained_count')}",
        f"error_count={report.get('error_count')}",
        "",
        "EXPIRED",
    ]
    for item in report.get("expired", [])[:50]:
        lines.append(f"  {item.get('pair')}|reason={item.get('reason')}|sticky_until={item.get('sticky_until')}")
    if report.get("errors"):
        lines.append("")
        lines.append("ERRORS")
        for item in report.get("errors", []):
            lines.append(f"  {item}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PairContext sticky expiry runner (mutates SQLite store only)")
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--sticky-ttl-sec", type=int, default=1800)
    parser.add_argument("--cycle-id", default="")
    parser.add_argument("--output", default="", help="Optional JSON output path")
    parser.add_argument("--compact", default="", help="Optional compact text output path")
    args = parser.parse_args(argv)

    report = build_report(
        runtime_dir=args.runtime_dir,
        db_path=args.db_path,
        sticky_ttl_sec=args.sticky_ttl_sec,
        cycle_id=args.cycle_id,
    )

    payload = json.dumps(report, ensure_ascii=False, sort_keys=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    if args.compact:
        Path(args.compact).write_text(compact_text(report), encoding="utf-8")

    print(payload)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
