#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT_PREFIX = "F4X_AQ_AUTONOMOUS_SCANNER_STRICT_K_INTENT_DRYRUN_PATCH"
MODE = "AUTONOMOUS_SCANNER_STRICT_K_INTENT_DRYRUN_PATCH"
AP_READY = "F4X_AP_NEXT_STRICT_SCANNER_CANDIDATE_READY_FOR_AQ_SHADOW_ONLY"


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


def norm_pair(v: Any) -> str:
    return str(v or "").strip()


def norm_side(v: Any) -> str:
    s = str(v or "").strip().upper()
    if s in {"LONG", "BUY", "LONG_ONLY"}:
        return "LONG"
    if s in {"SHORT", "SELL", "SHORT_ONLY"}:
        return "SHORT"
    return ""


def read_env_file(runtime: Path) -> dict[str, str]:
    env_path = runtime / "F4X_AE2_REST_API_ENV.sh"
    out: dict[str, str] = {}
    if not env_path.exists():
        return out
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("export "):
            continue
        body = line.replace("export ", "", 1)
        if "=" not in body:
            continue
        k, v = body.split("=", 1)
        out[k.strip()] = v.strip().strip('"')
    return out


def request_json(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 8,
):
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


def rest_probe(runtime: Path) -> dict[str, Any]:
    env = read_env_file(runtime)
    rest_url = env.get("F4X_L_REST_URL", "http://127.0.0.1:8080").rstrip("/")
    user = env.get("F4X_L_REST_USER", "")
    pw = env.get("F4X_L_REST_PASS", "")

    out: dict[str, Any] = {
        "rest_url": rest_url,
        "ping_ok": False,
        "login_ok": False,
        "show_config_ok": False,
        "status_ok": False,
        "whitelist_ok": False,
        "dry_run": None,
        "force_entry_enable": None,
        "open_trade_count": None,
        "open_pairs": [],
        "whitelist_pairs": [],
    }

    ok, status, body = request_json(rest_url + "/api/v1/ping")
    out["ping_ok"] = bool(ok and status == 200)
    out["ping_status"] = status
    out["ping_body"] = body

    token = ""
    if user and pw:
        basic = base64.b64encode(f"{user}:{pw}".encode()).decode()
        ok, status, body = request_json(
            rest_url + "/api/v1/token/login",
            method="POST",
            headers={"Authorization": "Basic " + basic},
            data=b"",
        )
        out["login_ok"] = bool(ok and status == 200 and isinstance(body, dict) and body.get("access_token"))
        out["login_status"] = status
        if out["login_ok"]:
            token = str(body.get("access_token") or "")

    headers = {"Authorization": "Bearer " + token} if token else {}

    if token:
        ok, status, body = request_json(rest_url + "/api/v1/show_config", headers=headers)
        out["show_config_ok"] = bool(ok and status == 200 and isinstance(body, dict))
        out["show_config_status"] = status
        out["show_config"] = body
        if isinstance(body, dict):
            out["dry_run"] = body.get("dry_run")
            out["force_entry_enable"] = body.get("force_entry_enable")

        ok, status, body = request_json(rest_url + "/api/v1/status", headers=headers)
        out["status_ok"] = bool(ok and status == 200)
        out["status_status"] = status
        out["status"] = body

        open_pairs: list[str] = []
        if isinstance(body, list):
            for row in body:
                if isinstance(row, dict):
                    pair = norm_pair(row.get("pair"))
                    if pair:
                        open_pairs.append(pair)
        out["open_pairs"] = sorted(set(open_pairs))
        out["open_trade_count"] = len(open_pairs)

        ok, status, body = request_json(rest_url + "/api/v1/whitelist", headers=headers)
        out["whitelist_ok"] = bool(ok and status == 200)
        out["whitelist_status"] = status
        out["whitelist_body"] = body

        pairs: list[str] = []
        if isinstance(body, dict):
            raw = body.get("whitelist") or body.get("pair_whitelist") or body.get("pairs") or []
            if isinstance(raw, list):
                pairs = [norm_pair(x) for x in raw if norm_pair(x)]
        elif isinstance(body, list):
            pairs = [norm_pair(x) for x in body if norm_pair(x)]
        out["whitelist_pairs"] = sorted(set(pairs))

    return out


def parse_dt(v: Any):
    if not v:
        return None
    s = str(v).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        try:
            dt = datetime.strptime(str(v), "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None


def db_trade_guard(repo: Path, pair: str, close_cooldown_sec: int) -> dict[str, Any]:
    paths = sorted((repo / "user_data").glob("*.dryrun.sqlite"))
    out = {
        "db_paths": [str(p) for p in paths],
        "open_same_pair": False,
        "latest_closed_same_pair": None,
        "latest_closed_age_sec": None,
        "recent_close_block": False,
    }

    now = datetime.now(timezone.utc)
    latest_close = None

    for db in paths:
        try:
            con = sqlite3.connect(str(db))
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                SELECT id, pair, is_open, open_date, close_date, close_profit, close_profit_abs, enter_tag, exit_reason
                FROM trades
                WHERE pair = ?
                ORDER BY id DESC
                LIMIT 20
                """,
                (pair,),
            ).fetchall()
            con.close()
        except Exception:
            continue

        for r in rows:
            row = dict(r)
            row["_db"] = str(db)
            if int(row.get("is_open") or 0) == 1:
                out["open_same_pair"] = True
            if row.get("close_date"):
                dt = parse_dt(row.get("close_date"))
                if dt and (latest_close is None or dt > latest_close[0]):
                    latest_close = (dt, row)

    if latest_close:
        dt, row = latest_close
        age = (now - dt).total_seconds()
        out["latest_closed_same_pair"] = row
        out["latest_closed_age_sec"] = age
        out["recent_close_block"] = bool(age < close_cooldown_sec)

    return out


def get_ap_candidate(runtime: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    ap_path = runtime / "F4X_AP_AUTONOMOUS_SCANNER_DRIVEN_NEXT_CANDIDATE_LOOP_SHADOW_ACTIVE.json"
    ap = read_json(ap_path, {})
    if not isinstance(ap, dict):
        return {}, {}

    cand = None
    for key in ["selected_candidate", "selected_shadow_candidate", "selected_strict_candidate"]:
        if isinstance(ap.get(key), dict):
            cand = ap.get(key)
            break

    if cand is None:
        arr = ap.get("selected_candidates")
        if isinstance(arr, list) and arr and isinstance(arr[0], dict):
            cand = arr[0]

    if not isinstance(cand, dict):
        cand = {}

    return ap, cand


def candidate_core_checks(c: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []

    pair = norm_pair(c.get("pair") or c.get("order_pair"))
    side = norm_side(c.get("side") or c.get("order_side") or c.get("direction"))
    score = float(c.get("score") or 0.0)
    trigger = str(c.get("trigger") or "")
    smc = str(c.get("smc") or "")
    cvdoi = str(c.get("cvdoi") or "")

    if not pair:
        failures.append("PAIR_MISSING")
    if side not in {"LONG", "SHORT"}:
        failures.append("SIDE_INVALID")
    if score < 55:
        failures.append("SCORE_BELOW_55")
    if trigger != "TRIGGER_CONFIRMED":
        failures.append("TRIGGER_NOT_CONFIRMED")
    if not smc.startswith("SMC_GOOD_LOCATION"):
        failures.append("SMC_NOT_GOOD")
    if not (
        "BULLISH_CONTINUATION_STRONG" in cvdoi
        or "BEARISH_CONTINUATION_STRONG" in cvdoi
        or "SHORT_SQUEEZE" in cvdoi
        or "LONG_UNWIND" in cvdoi
    ):
        failures.append("SIDE_FLOW_NOT_STRONG")

    return len(failures) == 0, failures


def build_compact(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("F4X_AQ_AUTONOMOUS_SCANNER_STRICT_K_INTENT_DRYRUN_PATCH_COMPACT")
    lines.append(f"generated_at={result['generated_at']}")
    lines.append(f"mode={result['mode']}")
    lines.append(f"execute_requested={result['execute_requested']}")
    lines.append("paper_order=WRITE_K_INTENT_ONLY")
    lines.append("live=HOLD")
    lines.append("risk_up=HOLD")
    lines.append("gate_loosen=HOLD")
    lines.append("entry_from_watch_recheck_deny=HOLD")
    lines.append("FINAL_DECISION")
    lines.append(f"final_decision={result['final_decision']}")
    lines.append("GUARD_FAILURES")
    lines.append(",".join(result["guard_failures"]) if result["guard_failures"] else "NONE")

    c = result.get("selected_intent") or {}
    lines.append("SELECTED_INTENT")
    if c:
        lines.append(
            f"pair={c.get('pair')}|side={c.get('side')}|score={c.get('score')}|"
            f"source={c.get('source')}|cvdoi={c.get('cvdoi')}|trigger={c.get('trigger')}|"
            f"smc={c.get('smc')}|allow_paper_entry={c.get('allow_paper_entry')}|would_order={c.get('would_order')}"
        )
    else:
        lines.append("NONE")

    r = result.get("runtime_checks") or {}
    lines.append("RUNTIME_CHECKS")
    lines.append(
        f"rest_ping_ok={r.get('rest_ping_ok')}|rest_login_ok={r.get('rest_login_ok')}|"
        f"rest_show_config_ok={r.get('rest_show_config_ok')}|rest_status_ok={r.get('rest_status_ok')}|"
        f"rest_whitelist_ok={r.get('rest_whitelist_ok')}"
    )
    lines.append(
        f"dry_run={r.get('dry_run')}|force_entry_enable={r.get('force_entry_enable')}|"
        f"active_whitelist_count={r.get('active_whitelist_count')}|pair_in_rest_active_whitelist={r.get('pair_in_rest_active_whitelist')}"
    )
    lines.append(
        f"open_trade_count={r.get('open_trade_count')}|open_same_pair={r.get('open_same_pair')}|"
        f"recent_close_block={r.get('recent_close_block')}|latest_closed_age_sec={r.get('latest_closed_age_sec')}"
    )

    lines.append("WRITE_STATE")
    lines.append(f"k_active_signal_written={result.get('k_active_signal_written')}")
    lines.append(f"k_active_path={result.get('k_active_path')}")
    lines.append(f"backup_path={result.get('backup_path')}")

    lines.append("DECISION_POLICY")
    lines.append("AQ writes one autonomous scanner-driven strict K paper intent only if AP, REST active whitelist, dry-run, and trade guards pass.")
    lines.append("AQ does not execute order by itself; F4X-L executes dry-run.")
    lines.append("AQ does not enable live.")
    lines.append("AQ does not risk-up.")
    lines.append("AQ does not loosen gate.")
    lines.append("AQ does not manually inject whitelist.")
    lines.append("OUTPUT_FILES")
    for k, v in result["output_files"].items():
        lines.append(f"{k}={v}")

    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default=os.environ.get("REVO_RUNTIME_DIR", "/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"))
    ap.add_argument("--close-cooldown-sec", type=int, default=1800)
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo_dir)
    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    ap_state, candidate = get_ap_candidate(runtime)
    rest = rest_probe(runtime)

    pair = norm_pair(candidate.get("pair") or candidate.get("order_pair"))
    side = norm_side(candidate.get("side") or candidate.get("order_side") or candidate.get("direction"))
    score = float(candidate.get("score") or 0.0)

    core_ok, core_failures = candidate_core_checks(candidate)
    db_guard = db_trade_guard(repo, pair, args.close_cooldown_sec) if pair else {
        "open_same_pair": False,
        "recent_close_block": False,
        "latest_closed_age_sec": None,
    }

    active_whitelist = set(rest.get("whitelist_pairs") or [])
    pair_in_whitelist = pair in active_whitelist if active_whitelist else False

    failures: list[str] = []

    if ap_state.get("final_decision") != AP_READY:
        failures.append("AP_NOT_READY_FOR_AQ")
    if not candidate:
        failures.append("AP_SELECTED_CANDIDATE_MISSING")
    if not core_ok:
        failures.extend(core_failures)
    if not rest.get("ping_ok"):
        failures.append("REST_PING_NOT_OK")
    if not rest.get("login_ok"):
        failures.append("REST_LOGIN_NOT_OK")
    if not rest.get("show_config_ok"):
        failures.append("REST_SHOW_CONFIG_NOT_OK")
    if not rest.get("status_ok"):
        failures.append("REST_STATUS_NOT_OK")
    if not rest.get("whitelist_ok"):
        failures.append("REST_WHITELIST_NOT_OK")
    if rest.get("dry_run") is not True:
        failures.append("DRY_RUN_NOT_TRUE")
    if rest.get("force_entry_enable") is not True:
        failures.append("FORCE_ENTRY_ENABLE_NOT_TRUE")
    if not pair_in_whitelist:
        failures.append("PAIR_NOT_IN_REST_ACTIVE_WHITELIST")
    if bool(db_guard.get("open_same_pair")):
        failures.append("OPEN_SAME_PAIR_IN_DB")
    if pair and pair in set(rest.get("open_pairs") or []):
        failures.append("OPEN_SAME_PAIR_IN_REST_STATUS")
    if bool(db_guard.get("recent_close_block")):
        failures.append("RECENT_CLOSE_COOLDOWN_ACTIVE")

    k_active = runtime / "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json"
    backup_path = None
    wrote = False

    selected_intent = {
        "pair": pair,
        "side": side,
        "score": score,
        "source": "F4X_AP_AUTONOMOUS_NEXT_CANDIDATE_SHADOW",
        "cvdoi": candidate.get("cvdoi"),
        "trigger": candidate.get("trigger"),
        "smc": candidate.get("smc"),
        "latest_before": candidate.get("latest") or candidate.get("latest_before"),
        "allow_paper_entry": True,
        "would_order": True,
    }

    final_decision = "F4X_AQ_READY_BUT_NOT_EXECUTED"

    if failures:
        final_decision = "F4X_AQ_ABORTED_GUARD_FAILED"
    elif args.execute:
        if k_active.exists():
            backup_path = runtime / f"F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL_BACKUP_BEFORE_AQ_{stamp()}.json"
            shutil.copy2(k_active, backup_path)

        payload = {
            "generated_at": now_utc(),
            "mode": "F4X_AQ_AUTONOMOUS_SCANNER_STRICT_K_INTENT_DRYRUN_ONLY",
            "has_order_intent": True,
            "order_intents": [
                {
                    "pair": pair,
                    "side": side,
                    "order_pair": pair,
                    "order_side": side,
                    "direction": side,
                    "score": score,
                    "cvdoi": candidate.get("cvdoi"),
                    "trigger": candidate.get("trigger"),
                    "smc": candidate.get("smc"),
                    "latest_before": candidate.get("latest") or candidate.get("latest_before"),
                    "intent_source": "F4X_AQ_AUTONOMOUS_SCANNER_STRICT_K_INTENT_DRYRUN_ONLY",
                    "intent_state": "ALLOW_PAPER_ENTRY",
                    "paper_action": "ALLOW_PAPER_ENTRY",
                    "allow_paper_entry": True,
                    "would_order": True,
                    "dry_run_only": True,
                    "live_allowed": False,
                    "risk_up_allowed": False,
                    "gate_loosen_allowed": False,
                    "entry_from_watch_recheck_deny_allowed": False,
                    "max_pair_count": 1,
                    "cooldown_sec": args.close_cooldown_sec,
                    "whitelist_source": "REST_ACTIVE_WHITELIST",
                    "scanner_selection_source": "F4X_AP_AUTONOMOUS_NEXT_CANDIDATE_SHADOW",
                    "canary_reason": "AUTONOMOUS_SCANNER_STRICT_CANDIDATE_READY_FOR_DRYRUN_PAPER",
                }
            ],
            "would_order_intent_count": 1,
            "intent_count": 1,
            "blocked_count": 0,
            "paper_order_mode": "STRICT_ALLOW_ONLY",
            "paper_bridge": "RUNNING",
            "paper_order_allowed": True,
            "dry_run_only": True,
            "live_allowed": False,
            "risk_up_allowed": False,
            "gate_loosen_allowed": False,
            "entry_from_watch_recheck_deny_allowed": False,
            "pair": pair,
            "side": side,
            "order_pair": pair,
            "order_side": side,
            "allow_paper_entry": True,
            "would_order": True,
            "source_files": {
                "ap": str(runtime / "F4X_AP_AUTONOMOUS_SCANNER_DRIVEN_NEXT_CANDIDATE_LOOP_SHADOW_ACTIVE.json"),
            },
        }
        write_json(k_active, payload)
        wrote = True
        final_decision = "F4X_AQ_AUTONOMOUS_SCANNER_K_ACTIVE_SIGNAL_WRITTEN"

    result = {
        "generated_at": now_utc(),
        "mode": MODE,
        "execute_requested": bool(args.execute),
        "paper_order_allowed": False,
        "k_write_allowed": bool(wrote),
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "entry_from_watch_recheck_deny_allowed": False,
        "final_decision": final_decision,
        "guard_failures": failures,
        "selected_intent": selected_intent if candidate else None,
        "runtime_checks": {
            "rest_ping_ok": rest.get("ping_ok"),
            "rest_login_ok": rest.get("login_ok"),
            "rest_show_config_ok": rest.get("show_config_ok"),
            "rest_status_ok": rest.get("status_ok"),
            "rest_whitelist_ok": rest.get("whitelist_ok"),
            "dry_run": rest.get("dry_run"),
            "force_entry_enable": rest.get("force_entry_enable"),
            "active_whitelist_count": len(active_whitelist),
            "pair_in_rest_active_whitelist": pair_in_whitelist,
            "open_trade_count": rest.get("open_trade_count"),
            "open_pairs": rest.get("open_pairs"),
            "open_same_pair": bool(db_guard.get("open_same_pair")) or pair in set(rest.get("open_pairs") or []),
            "recent_close_block": db_guard.get("recent_close_block"),
            "latest_closed_age_sec": db_guard.get("latest_closed_age_sec"),
        },
        "ap_final_decision": ap_state.get("final_decision"),
        "k_active_signal_written": wrote,
        "k_active_path": str(k_active),
        "backup_path": str(backup_path) if backup_path else None,
        "output_files": {
            "full_json": str(runtime / f"{OUT_PREFIX}_FULL.json"),
            "compact": str(runtime / f"{OUT_PREFIX}_COMPACT.txt"),
            "active": str(runtime / f"{OUT_PREFIX}_ACTIVE.json"),
        },
    }

    write_json(runtime / f"{OUT_PREFIX}_FULL.json", result)
    write_json(runtime / f"{OUT_PREFIX}_ACTIVE.json", result)
    compact = build_compact(result)
    (runtime / f"{OUT_PREFIX}_COMPACT.txt").write_text(compact, encoding="utf-8")
    print(compact)


if __name__ == "__main__":
    main()
