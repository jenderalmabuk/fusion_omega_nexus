#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime, timezone


def parse_dt(v):
    if not v:
        return None
    try:
        t = str(v)
        if t.endswith("Z"):
            t = t[:-1] + "+00:00"
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    p = runtime / "F4X_PAPER_DECISION_SIGNALS.json"
    full = runtime / "F4X_FULL_CONFLUENCE_FINAL_FULL.json"

    failures = []

    print("F4X_FULL_CONFLUENCE_PAPER_VALIDATION")
    print("runtime=", runtime)
    print("signals_exists=", p.exists())
    print("full_exists=", full.exists())

    if not p.exists():
        failures.append("MISSING_SIGNALS")
    if not full.exists():
        failures.append("MISSING_FULL_JSON")

    if failures:
        print("failures=", len(failures))
        for f in failures:
            print("FAIL:" + f)
        return 1

    data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    now = datetime.now(timezone.utc)
    exp = parse_dt(data.get("expires_at"))
    expired = bool(exp and now > exp)

    print("final_decision=", data.get("final_decision"))
    print("paper_mode_only=", data.get("paper_mode_only"))
    print("live_allowed=", data.get("live_allowed"))
    print("signal_count=", len(data.get("signals", [])))
    print("allow_entry_count=", len(data.get("allow_entries", [])))
    print("watch_only_count=", len(data.get("watch_only", [])))
    print("recheck_count=", len(data.get("recheck", [])))
    print("deny_count=", len(data.get("deny", [])))
    print("expired=", expired)

    if data.get("paper_mode_only") is not True:
        failures.append("NOT_PAPER_MODE_ONLY")
    if data.get("live_allowed") is not False:
        failures.append("LIVE_ALLOWED_NOT_FALSE")
    if expired:
        failures.append("SIGNALS_EXPIRED")

    for s in data.get("allow_entries", []):
        if s.get("paper_action") != "ALLOW_PAPER_ENTRY":
            failures.append("BAD_ALLOW_ACTION")
        if s.get("live_allowed") is not False:
            failures.append("ALLOW_SIGNAL_LIVE_NOT_FALSE")
        if s.get("risk_mode") != "PAPER_MIN_RISK":
            failures.append("ALLOW_BAD_RISK_MODE")
        if s.get("hard_blockers"):
            failures.append("ALLOW_WITH_HARD_BLOCKERS")

    print("failures=", len(failures))
    for f in failures:
        print("FAIL:" + f)

    if failures:
        return 1

    print("F4X_FULL_CONFLUENCE_PAPER_VALIDATION_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
