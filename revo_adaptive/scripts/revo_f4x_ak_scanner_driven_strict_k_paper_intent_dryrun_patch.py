#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OUT_PREFIX = "F4X_AK_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_DRYRUN_PATCH"
MODE = "SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_DRYRUN_PATCH"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y", "ok"}
    return False


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
) -> tuple[bool, int | None, Any]:
    req = urllib.request.Request(url, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = raw[:500]
            return True, int(getattr(r, "status", 0)), body
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            body = raw[:500]
        return False, int(e.code), body
    except Exception as e:
        return False, None, f"{type(e).__name__}: {e}"


def rest_context(runtime: Path) -> dict[str, Any]:
    env = read_env_file(runtime)
    rest_url = env.get("F4X_L_REST_URL", "http://127.0.0.1:8080").rstrip("/")
    user = env.get("F4X_L_REST_USER", "")
    pw = env.get("F4X_L_REST_PASS", "")

    ctx: dict[str, Any] = {
        "rest_url": rest_url,
        "ping_ok": False,
        "token_login_ok": False,
        "show_config_ok": False,
        "status_ok": False,
        "dry_run_verified": False,
        "force_entry_enable_verified": False,
        "open_same_pair": False,
        "open_trade_count": 0,
        "status": None,
        "show_config": None,
    }

    ok, status, body = request_json(rest_url + "/api/v1/ping")
    ctx["ping_ok"] = bool(ok and status == 200)
    ctx["ping_status"] = status

    token = ""
    if user and pw:
        basic = base64.b64encode(f"{user}:{pw}".encode()).decode()
        ok, status, body = request_json(
            rest_url + "/api/v1/token/login",
            method="POST",
            headers={"Authorization": "Basic " + basic},
            data=b"",
        )
        ctx["token_login_ok"] = bool(ok and status == 200 and isinstance(body, dict) and body.get("access_token"))
        ctx["token_login_status"] = status
        if ctx["token_login_ok"]:
            token = str(body.get("access_token") or "")

    headers = {"Authorization": "Bearer " + token} if token else {}

    if token:
        ok, status, body = request_json(rest_url + "/api/v1/show_config", headers=headers)
        ctx["show_config_ok"] = bool(ok and status == 200 and isinstance(body, dict))
        ctx["show_config_status"] = status
        ctx["show_config"] = body if isinstance(body, dict) else None
        if isinstance(body, dict):
            ctx["dry_run_verified"] = body.get("dry_run") is True
            ctx["force_entry_enable_verified"] = body.get("force_entry_enable") is True

        ok, status, body = request_json(rest_url + "/api/v1/status", headers=headers)
        ctx["status_ok"] = bool(ok and status == 200)
        ctx["status_status"] = status
        ctx["status"] = body
        if isinstance(body, list):
            ctx["open_trade_count"] = len(body)

    return ctx


def active_whitelist_from_config(repo: Path) -> set[str]:
    p = repo / "user_data/config.bybit.dynamic-universe.paper.json"
    d = read_json(p, {})
    ex = d.get("exchange") if isinstance(d.get("exchange"), dict) else {}
    wl = ex.get("pair_whitelist") or []
    if not isinstance(wl, list):
        return set()
    return {norm_pair(x) for x in wl if norm_pair(x)}


def has_open_same_pair(status_body: Any, pair: str) -> bool:
    if not isinstance(status_body, list):
        return False
    for row in status_body:
        if not isinstance(row, dict):
            continue
        if norm_pair(row.get("pair")) != pair:
            continue
        if row.get("is_open") is None:
            return True
        if as_bool(row.get("is_open")):
            return True
    return False


def compact_text(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("F4X_AK_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_DRYRUN_PATCH_COMPACT")
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
    if result["guard_failures"]:
        for x in result["guard_failures"]:
            lines.append(str(x))
    else:
        lines.append("NONE")
    lines.append("SELECTED_INTENT")
    c = result.get("selected_intent")
    if c:
        lines.append(
            f"pair={c['pair']}|side={c['side']}|score={c['score']}|source={c['source_file']}|"
            f"cvdoi={c['cvdoi']}|trigger={c['trigger']}|smc={c['smc']}|allow_paper_entry={c['allow_paper_entry']}|would_order={c['would_order']}"
        )
    else:
        lines.append("NONE")
    lines.append("RUNTIME_CHECKS")
    r = result["runtime_checks"]
    for k in [
        "rest_ping_ok",
        "rest_token_login_ok",
        "rest_show_config_ok",
        "dry_run_verified",
        "force_entry_enable_verified",
        "pair_in_whitelist",
        "open_same_pair",
        "open_trade_count",
    ]:
        lines.append(f"{k}={r.get(k)}")
    lines.append("WRITE_STATE")
    lines.append(f"k_active_signal_written={result['write_state']['k_active_signal_written']}")
    lines.append(f"k_active_path={result['write_state']['k_active_path']}")
    lines.append(f"backup_path={result['write_state']['backup_path']}")
    lines.append("DECISION_POLICY")
    lines.append("AK writes one scanner-driven strict K paper intent only if all guards pass.")
    lines.append("AK does not execute order by itself; F4X-L executes dry-run.")
    lines.append("AK does not enable live.")
    lines.append("AK does not risk-up.")
    lines.append("AK does not loosen gate.")
    lines.append("OUTPUT_FILES")
    for k, v in result["output_files"].items():
        lines.append(f"{k}={v}")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default=os.environ.get("REVO_RUNTIME_DIR", "/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"))
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo_dir)
    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    aj_path = runtime / "F4X_AJ_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_CONVEYOR_SHADOW_ACTIVE.json"
    k_path = runtime / "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json"

    aj = read_json(aj_path, {})
    selected = aj.get("selected_candidate") if isinstance(aj, dict) else None

    guard_failures: list[str] = []

    if not isinstance(aj, dict) or not aj:
        guard_failures.append("AJ_ACTIVE_MISSING")
    if not aj.get("ready_for_AK_real_K_paper_intent"):
        guard_failures.append("AJ_NOT_READY_FOR_AK")
    if not isinstance(selected, dict):
        guard_failures.append("AJ_SELECTED_CANDIDATE_MISSING")

    pair = norm_pair(selected.get("pair")) if isinstance(selected, dict) else ""
    side = norm_side(selected.get("side")) if isinstance(selected, dict) else ""
    score = float(selected.get("score", 0.0)) if isinstance(selected, dict) else 0.0

    if not pair:
        guard_failures.append("PAIR_MISSING")
    if side not in {"LONG", "SHORT"}:
        guard_failures.append("SIDE_INVALID")
    if score < 55:
        guard_failures.append("SCORE_BELOW_55")

    wl = active_whitelist_from_config(repo)
    pair_in_whitelist = pair in wl if pair else False
    if not pair_in_whitelist:
        guard_failures.append("PAIR_NOT_IN_CONFIG_WHITELIST")

    rest = rest_context(runtime)
    if not rest.get("ping_ok"):
        guard_failures.append("REST_PING_NOT_OK")
    if not rest.get("token_login_ok"):
        guard_failures.append("REST_TOKEN_LOGIN_NOT_OK")
    if not rest.get("show_config_ok"):
        guard_failures.append("REST_SHOW_CONFIG_NOT_OK")
    if not rest.get("dry_run_verified"):
        guard_failures.append("REST_DRY_RUN_NOT_TRUE")
    if not rest.get("force_entry_enable_verified"):
        guard_failures.append("REST_FORCE_ENTRY_ENABLE_NOT_TRUE")

    open_same_pair = has_open_same_pair(rest.get("status"), pair)
    if open_same_pair:
        guard_failures.append("OPEN_SAME_PAIR_EXISTS")

    final_decision = "F4X_AK_ABORTED_GUARD_FAILED"
    backup_path = None
    wrote = False
    selected_intent = None

    if isinstance(selected, dict):
        selected_intent = {
            "pair": pair,
            "side": side,
            "order_pair": pair,
            "order_side": side,
            "direction": side,
            "score": score,
            "cvdoi": selected.get("cvdoi"),
            "trigger": selected.get("trigger"),
            "smc": selected.get("smc"),
            "latest_before": selected.get("latest"),
            "source_file": selected.get("source_file"),
            "source_path": selected.get("source_path"),
            "intent_source": "F4X_AK_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_DRYRUN_PATCH",
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
            "cooldown_sec": 1800,
            "scanner_driven": True,
            "canary_reason": "SCANNER_DRIVEN_STRICT_AJ_SELECTED_CANDIDATE",
        }

    if not guard_failures and args.execute:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        if k_path.exists():
            backup_path = runtime / f"F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL_BACKUP_BEFORE_AK_{ts}.json"
            shutil.copy2(k_path, backup_path)

        k_payload = {
            "generated_at": now_utc(),
            "mode": "F4X_AK_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_DRYRUN_PATCH",
            "has_order_intent": True,
            "order_intents": [selected_intent],
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
                "aj": str(aj_path),
            },
        }
        write_json(k_path, k_payload)
        wrote = True
        final_decision = "F4X_AK_SCANNER_DRIVEN_K_ACTIVE_SIGNAL_WRITTEN"
    elif not guard_failures:
        final_decision = "F4X_AK_READY_DRYRUN_EXECUTE_NOT_REQUESTED"

    result = {
        "generated_at": now_utc(),
        "mode": MODE,
        "execute_requested": args.execute,
        "paper_order_allowed": bool(wrote),
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "entry_from_watch_recheck_deny_allowed": False,
        "final_decision": final_decision,
        "guard_failures": guard_failures,
        "selected_intent": selected_intent,
        "runtime_checks": {
            "rest_ping_ok": rest.get("ping_ok"),
            "rest_token_login_ok": rest.get("token_login_ok"),
            "rest_show_config_ok": rest.get("show_config_ok"),
            "dry_run_verified": rest.get("dry_run_verified"),
            "force_entry_enable_verified": rest.get("force_entry_enable_verified"),
            "pair_in_whitelist": pair_in_whitelist,
            "open_same_pair": open_same_pair,
            "open_trade_count": rest.get("open_trade_count"),
            "rest_url": rest.get("rest_url"),
        },
        "write_state": {
            "k_active_signal_written": wrote,
            "k_active_path": str(k_path),
            "backup_path": str(backup_path) if backup_path else None,
        },
        "decision_policy": {
            "write_real_k": bool(wrote),
            "execute_order": False,
            "max_pair_count": 1,
            "dry_run_only": True,
            "live_allowed": False,
            "risk_up_allowed": False,
            "gate_loosen_allowed": False,
        },
        "output_files": {
            "full_json": str(runtime / f"{OUT_PREFIX}_FULL.json"),
            "compact": str(runtime / f"{OUT_PREFIX}_COMPACT.txt"),
            "active": str(runtime / f"{OUT_PREFIX}_ACTIVE.json"),
        },
    }

    write_json(runtime / f"{OUT_PREFIX}_FULL.json", result)
    write_json(runtime / f"{OUT_PREFIX}_ACTIVE.json", result)
    txt = compact_text(result)
    (runtime / f"{OUT_PREFIX}_COMPACT.txt").write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
