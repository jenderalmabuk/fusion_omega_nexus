#!/usr/bin/env python3
# CONTROL_TOWER_F4X_AR1_L_WRAPPER
from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import shutil

OUT_PREFIX = "F4X_AR1_L_SUCCESS_CONSUME_K_INTENT_AND_OPEN_TRADE_PRECHECK_GUARD_DRYRUN_ONLY"
MODE = "L_SUCCESS_CONSUME_K_INTENT_AND_OPEN_TRADE_PRECHECK_GUARD_DRYRUN_ONLY"
ORIGINAL_NAME = "revo_f4x_l_paper_bridge_execution_sandbox_dryrun_PRE_AR1_ORIGINAL.py"

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

def read_json(path: Path, default: Any = None) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default

def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")

def append_jsonl(path: Path, data: Any) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(data, sort_keys=False) + "\n")

def norm_pair(v: Any) -> str:
    return str(v or "").strip()

def norm_side(v: Any) -> str:
    s = str(v or "").strip().upper()
    if s in {"LONG", "BUY", "LONG_ONLY"}:
        return "LONG"
    if s in {"SHORT", "SELL", "SHORT_ONLY"}:
        return "SHORT"
    return s

def read_env_file(runtime: Path) -> dict[str, str]:
    p = runtime / "F4X_AE2_REST_API_ENV.sh"
    out: dict[str, str] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("export ") or "=" not in line:
            continue
        body = line.replace("export ", "", 1)
        k, v = body.split("=", 1)
        out[k.strip()] = v.strip().strip('"')
    return out

def request_json(url: str, method: str = "GET", headers: dict[str, str] | None = None, data: bytes | None = None, timeout: int = 8):
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = raw[:1000]
            return True, int(getattr(r, "status", 0)), body
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            body = raw[:1000]
        return False, int(e.code), body
    except Exception as e:
        return False, None, f"{type(e).__name__}: {e}"

def rest_token(runtime: Path) -> tuple[str, str]:
    env = read_env_file(runtime)
    rest_url = env.get("F4X_L_REST_URL", "http://127.0.0.1:8080").rstrip("/")
    user = env.get("F4X_L_REST_USER", "")
    pw = env.get("F4X_L_REST_PASS", "")
    token = ""
    if user and pw:
        basic = base64.b64encode(f"{user}:{pw}".encode()).decode()
        ok, status, body = request_json(
            rest_url + "/api/v1/token/login",
            method="POST",
            headers={"Authorization": "Basic " + basic},
            data=b"",
        )
        if ok and status == 200 and isinstance(body, dict):
            token = str(body.get("access_token") or "")
    return rest_url, token

def extract_open_pairs_from_status(body: Any) -> set[str]:
    rows: list[Any] = []
    if isinstance(body, list):
        rows = body
    elif isinstance(body, dict):
        for key in ("trades", "open_trades", "data", "result", "status"):
            v = body.get(key)
            if isinstance(v, list):
                rows.extend(v)
    pairs: set[str] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        pair = norm_pair(r.get("pair"))
        if not pair:
            continue
        is_open = r.get("is_open")
        close_date = r.get("close_date") or r.get("close_timestamp")
        state = str(r.get("state") or r.get("status") or "").lower()
        if is_open is False or is_open == 0:
            continue
        if close_date:
            continue
        if state and state not in {"open", "running", "active"}:
            continue
        pairs.add(pair)
    return pairs

def rest_open_pairs(runtime: Path) -> tuple[set[str], dict[str, Any]]:
    rest_url, token = rest_token(runtime)
    info: dict[str, Any] = {"rest_url": rest_url, "token_present": bool(token), "status_ok": False}
    if not token:
        return set(), info
    ok, status, body = request_json(rest_url + "/api/v1/status", headers={"Authorization": "Bearer " + token})
    info["status_ok"] = bool(ok and status == 200)
    info["status_code"] = status
    info["status_body_preview"] = str(body)[:1000]
    if not ok:
        return set(), info
    pairs = extract_open_pairs_from_status(body)
    info["open_pairs"] = sorted(pairs)
    return pairs, info

def db_open_pairs(repo: Path) -> tuple[set[str], dict[str, Any]]:
    info: dict[str, Any] = {"db_checked": False, "db_path": None}
    pairs: set[str] = set()
    dbs = sorted((repo / "user_data").glob("*.sqlite"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    for db in dbs:
        try:
            con = sqlite3.connect(str(db))
            cur = con.cursor()
            tables = [r[0] for r in cur.execute("select name from sqlite_master where type='table'").fetchall()]
            if "trades" not in tables:
                con.close()
                continue
            cols = [r[1] for r in cur.execute("pragma table_info(trades)").fetchall()]
            if "pair" not in cols or "is_open" not in cols:
                con.close()
                continue
            for row in cur.execute("select pair from trades where is_open = 1").fetchall():
                if row and row[0]:
                    pairs.add(norm_pair(row[0]))
            con.close()
            info["db_checked"] = True
            info["db_path"] = str(db)
            info["open_pairs"] = sorted(pairs)
            return pairs, info
        except Exception as e:
            info["db_error"] = f"{type(e).__name__}: {e}"
            continue
    return pairs, info

def active_intents(runtime: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    p = runtime / "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json"
    k = read_json(p, {})
    intents: list[dict[str, Any]] = []
    if isinstance(k, dict) and bool(k.get("has_order_intent")) is True:
        raw = k.get("order_intents") or []
        if isinstance(raw, list):
            for x in raw:
                if not isinstance(x, dict):
                    continue
                pair = norm_pair(x.get("order_pair") or x.get("pair"))
                side = norm_side(x.get("order_side") or x.get("side") or x.get("direction"))
                if (
                    pair and side in {"LONG", "SHORT"}
                    and bool(x.get("allow_paper_entry")) is True
                    and bool(x.get("would_order")) is True
                    and bool(x.get("dry_run_only")) is True
                    and bool(x.get("live_allowed")) is False
                    and bool(x.get("risk_up_allowed")) is False
                    and bool(x.get("gate_loosen_allowed")) is False
                ):
                    y = dict(x)
                    y["_pair"] = pair
                    y["_side"] = side
                    intents.append(y)
    return intents, k if isinstance(k, dict) else {}

def write_l_block_outputs(runtime: Path, result: dict[str, Any]) -> None:
    full = runtime / "F4X_L_PAPER_BRIDGE_EXECUTION_FULL.json"
    active = runtime / "F4X_L_PAPER_BRIDGE_ACTIVE_EXECUTION.json"
    compact = runtime / "F4X_L_PAPER_BRIDGE_EXECUTION_COMPACT.txt"
    events = runtime / "F4X_L_EXECUTION_EVENTS.jsonl"

    write_json(full, result)
    write_json(active, {
        "generated_at": result["generated_at"],
        "decision": result["decision"],
        "dry_run_verified": result.get("dry_run_verified"),
        "orders": result.get("orders", []),
        "blocked": result.get("blocked", []),
        "errors": result.get("errors", []),
        "live_allowed": False,
    })
    append_jsonl(events, result)

    lines = []
    lines.append("F4X_L_PAPER_BRIDGE_EXECUTION_COMPACT")
    lines.append(f"generated_at={result['generated_at']}")
    lines.append("mode=ACTUAL_FREQTRADE_DRY_RUN_EXECUTION_SANDBOX")
    lines.append("execute_requested=True")
    lines.append(f"decision={result['decision']}")
    lines.append(f"dry_run_verified={result.get('dry_run_verified')}")
    lines.append(f"dry_run_reason={result.get('dry_run_reason')}")
    lines.append(f"would_order_intent_count={result.get('would_order_intent_count', 0)}")
    lines.append("live=HOLD")
    lines.append("risk_up=HOLD")
    lines.append("gate_loosen=HOLD")
    lines.append("ORDERS")
    for o in result.get("orders", []):
        lines.append(f"{o.get('pair')}|side={o.get('side')}|decision={o.get('decision')}|status={o.get('rest_status')}|reason={o.get('reason','')}")
    lines.append("BLOCKED")
    for b in result.get("blocked", []):
        lines.append(f"{b.get('pair','NA')}|side={b.get('side','NA')}|decision={b.get('decision')}|reason={b.get('reason')}")
    lines.append("ERRORS")
    for e in result.get("errors", []):
        lines.append(f"{e.get('pair','NA')}|side={e.get('side','NA')}|reason={e.get('reason')}|status={e.get('rest_status')}")
    lines.append("OUTPUT_FILES")
    lines.append(f"full_json={full}")
    lines.append(f"active_execution={active}")
    lines.append(f"events_jsonl={events}")
    compact.write_text("\n".join(lines) + "\n", encoding="utf-8")

def consume_k_after_success(runtime: Path, l_full: dict[str, Any]) -> dict[str, Any]:
    k_path = runtime / "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json"
    old = read_json(k_path, {})
    ts = stamp()
    backup = runtime / f"F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL_BACKUP_BEFORE_AR1_CONSUME_{ts}.json"
    if k_path.exists():
        shutil.copy2(k_path, backup)

    consumed = {
        "generated_at": now_utc(),
        "mode": "F4X_AR1_L_CONSUMED_K_INTENT_AFTER_DRY_RUN_ORDER_SENT",
        "has_order_intent": False,
        "order_intents": [],
        "would_order_intent_count": 0,
        "intent_count": 0,
        "blocked_count": 0,
        "paper_order_mode": "STRICT_ALLOW_ONLY",
        "paper_bridge": "CONSUMED_AFTER_ORDER_SENT",
        "paper_order_allowed": False,
        "dry_run_only": True,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "entry_from_watch_recheck_deny_allowed": False,
        "consumed_by": "F4X_AR1_L_SUCCESS_CONSUME_K_INTENT_AND_OPEN_TRADE_PRECHECK_GUARD",
        "consumed_reason": "DRY_RUN_ORDER_SENT",
        "l_generated_at": l_full.get("generated_at"),
        "orders": l_full.get("orders", []),
        "backup_path": str(backup),
        "previous_signal_preview": old if isinstance(old, dict) else {},
    }
    write_json(k_path, consumed)
    return consumed

def write_ar1_report(runtime: Path, result: dict[str, Any]) -> None:
    full = runtime / f"{OUT_PREFIX}_FULL.json"
    active = runtime / f"{OUT_PREFIX}_ACTIVE.json"
    compact = runtime / f"{OUT_PREFIX}_COMPACT.txt"
    write_json(full, result)
    write_json(active, result)
    lines = []
    lines.append("F4X_AR1_L_SUCCESS_CONSUME_K_INTENT_AND_OPEN_TRADE_PRECHECK_GUARD_DRYRUN_ONLY_COMPACT")
    lines.append(f"generated_at={result['generated_at']}")
    lines.append(f"mode={MODE}")
    lines.append(f"final_decision={result['final_decision']}")
    lines.append("paper_order=HOLD")
    lines.append("live=HOLD")
    lines.append("risk_up=HOLD")
    lines.append("gate_loosen=HOLD")
    lines.append("ACTIONS")
    for a in result.get("actions", []):
        lines.append(str(a))
    lines.append("PRECHECK")
    pc = result.get("precheck", {})
    lines.append(f"intent_count={pc.get('intent_count')}|open_pairs={','.join(pc.get('open_pairs', [])) if pc.get('open_pairs') else 'NONE'}|same_pair_open={pc.get('same_pair_open')}")
    lines.append("CONSUME")
    c = result.get("consume", {})
    lines.append(f"consumed={c.get('consumed')}|backup_path={c.get('backup_path')}")
    lines.append("DECISION_POLICY")
    lines.append("AR1 guards L only: same-pair open precheck before forceenter and K consume after successful dry-run order.")
    lines.append("No live. No risk-up. No gate-loosen.")
    lines.append("OUTPUT_FILES")
    lines.append(f"full_json={full}")
    lines.append(f"compact={compact}")
    lines.append(f"active={active}")
    compact.write_text("\n".join(lines) + "\n", encoding="utf-8")

def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default=os.environ.get("REVO_RUNTIME_DIR", "/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"))
    ap.add_argument("--execute", action="store_true")
    ns, _unknown = ap.parse_known_args()

    repo = Path(ns.repo_dir)
    runtime = Path(ns.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)
    original = Path(__file__).with_name(ORIGINAL_NAME)

    if not original.exists():
        result = {
            "generated_at": now_utc(),
            "mode": MODE,
            "final_decision": "F4X_AR1_ORIGINAL_L_SCRIPT_MISSING_HOLD",
            "actions": ["HOLD_ORIGINAL_MISSING"],
            "precheck": {},
            "consume": {},
        }
        write_ar1_report(runtime, result)
        print((runtime / f"{OUT_PREFIX}_COMPACT.txt").read_text())
        return 2

    intents, _k = active_intents(runtime)
    rest_pairs, rest_info = rest_open_pairs(runtime)
    db_pairs, db_info = db_open_pairs(repo)
    open_pairs = set(rest_pairs) | set(db_pairs)
    same = sorted({i["_pair"] for i in intents if i.get("_pair") in open_pairs})

    if ns.execute and same:
        blocked = []
        for i in intents:
            if i.get("_pair") in open_pairs:
                blocked.append({
                    "pair": i.get("_pair"),
                    "side": i.get("_side"),
                    "decision": "SAME_PAIR_OPEN_TRADE_BLOCKED",
                    "reason": "AR1_PRECHECK_BLOCKED_FORCEENTER_BECAUSE_SAME_PAIR_OPEN",
                })
        l_result = {
            "event": "F4X_L_PAPER_BRIDGE_EXECUTION_SANDBOX_DRYRUN",
            "generated_at": now_utc(),
            "execute_requested": True,
            "paper_execution_mode": "FREQTRADE_DRY_RUN_FORCEENTER",
            "live": "HOLD",
            "risk_up": "HOLD",
            "gate_loosen": "HOLD",
            "orders": [],
            "blocked": blocked,
            "errors": [],
            "api_source": "AR1_PRECHECK",
            "would_order_intent_count": len(intents),
            "dry_run_verified": None,
            "dry_run_reason": None,
            "decision": "SAME_PAIR_OPEN_TRADE_BLOCKED",
            "ar1_precheck": {
                "rest": rest_info,
                "db": db_info,
                "open_pairs": sorted(open_pairs),
                "same_pair_open": same,
            },
        }
        write_l_block_outputs(runtime, l_result)
        ar1 = {
            "generated_at": now_utc(),
            "mode": MODE,
            "final_decision": "F4X_AR1_BLOCKED_SAME_PAIR_OPEN_NO_FORCEENTER",
            "actions": ["BLOCKED_BEFORE_FORCEENTER"],
            "precheck": {"intent_count": len(intents), "open_pairs": sorted(open_pairs), "same_pair_open": same},
            "consume": {"consumed": False, "reason": "NO_ORDER_SENT"},
        }
        write_ar1_report(runtime, ar1)
        print((runtime / f"{OUT_PREFIX}_COMPACT.txt").read_text())
        return 0

    rc = subprocess.call([sys.executable, str(original)] + sys.argv[1:])

    consumed_info: dict[str, Any] = {"consumed": False}
    if ns.execute and rc == 0:
        l_full = read_json(runtime / "F4X_L_PAPER_BRIDGE_EXECUTION_FULL.json", {})
        if isinstance(l_full, dict) and l_full.get("decision") == "DRY_RUN_ORDER_SENT" and l_full.get("orders"):
            consumed = consume_k_after_success(runtime, l_full)
            consumed_info = {"consumed": True, "backup_path": consumed.get("backup_path"), "orders": consumed.get("orders", [])}

    ar1 = {
        "generated_at": now_utc(),
        "mode": MODE,
        "final_decision": "F4X_AR1_ORIGINAL_L_EXECUTED_AND_POST_GUARD_APPLIED",
        "original_returncode": rc,
        "actions": ["ORIGINAL_L_EXECUTED", "POST_SUCCESS_CONSUME_CHECK_DONE"],
        "precheck": {"intent_count": len(intents), "open_pairs": sorted(open_pairs), "same_pair_open": same},
        "consume": consumed_info,
    }
    write_ar1_report(runtime, ar1)
    return rc

if __name__ == "__main__":
    raise SystemExit(main())
