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

OUT_PREFIX = "F4X_AK2_PATCH_AK_TO_USE_REST_ACTIVE_WHITELIST_AND_WRITE_SCANNER_K_INTENT_DRYRUN_ONLY"
MODE = "PATCH_AK_TO_USE_REST_ACTIVE_WHITELIST_AND_WRITE_SCANNER_K_INTENT_DRYRUN_ONLY"


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
                body = raw[:2000]
            return True, int(getattr(r, "status", 0)), body
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            body = raw[:2000]
        return False, int(e.code), body
    except Exception as e:
        return False, None, f"{type(e).__name__}: {e}"


def rest_context(runtime: Path) -> dict[str, Any]:
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
        "whitelist_pairs": [],
        "errors": [],
    }

    ok, status, body = request_json(rest_url + "/api/v1/ping")
    out["ping_ok"] = bool(ok and status == 200)
    out["ping_status"] = status
    out["ping_body"] = body
    if not out["ping_ok"]:
        out["errors"].append("REST_PING_FAILED")
        return out

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
        else:
            out["errors"].append("REST_TOKEN_LOGIN_FAILED")
    else:
        out["errors"].append("REST_ENV_CREDENTIALS_MISSING")

    headers = {"Authorization": "Bearer " + token} if token else {}

    if token:
        ok, status, body = request_json(rest_url + "/api/v1/show_config", headers=headers)
        out["show_config_ok"] = bool(ok and status == 200 and isinstance(body, dict))
        out["show_config_status"] = status
        out["show_config"] = body
        if out["show_config_ok"]:
            out["dry_run"] = body.get("dry_run")
            out["force_entry_enable"] = body.get("force_entry_enable")
        else:
            out["errors"].append("REST_SHOW_CONFIG_FAILED")

        ok, status, body = request_json(rest_url + "/api/v1/status", headers=headers)
        out["status_ok"] = bool(ok and status == 200)
        out["status_status"] = status
        out["status"] = body
        if isinstance(body, list):
            out["open_trade_count"] = len(body)
        elif isinstance(body, dict):
            raw = body.get("trades") or body.get("data") or []
            out["open_trade_count"] = len(raw) if isinstance(raw, list) else 0
        else:
            out["open_trade_count"] = 0

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

        if not out["whitelist_ok"]:
            out["errors"].append("REST_ACTIVE_WHITELIST_FAILED")

    return out


def load_selected_candidate(runtime: Path) -> dict[str, Any]:
    ak1 = read_json(runtime / "F4X_AK1_ACTIVE_WHITELIST_SOURCE_ALIGNMENT_AND_SCANNER_RESELECT_AUDIT_ACTIVE.json", {})
    aj = read_json(runtime / "F4X_AJ_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_CONVEYOR_SHADOW_ACTIVE.json", {})

    cand = None
    source = ""

    if isinstance(ak1, dict) and isinstance(ak1.get("reselected_candidate"), dict):
        cand = dict(ak1["reselected_candidate"])
        source = "F4X_AK1_RESELECTED_CANDIDATE"

    if cand is None and isinstance(aj, dict) and isinstance(aj.get("selected_candidate"), dict):
        cand = dict(aj["selected_candidate"])
        source = "F4X_AJ_SELECTED_CANDIDATE"

    if cand is None:
        return {}

    pair = norm_pair(cand.get("pair") or cand.get("order_pair"))
    side = norm_side(cand.get("side") or cand.get("order_side") or cand.get("direction"))
    score = float(cand.get("score") or 0.0)

    return {
        "pair": pair,
        "side": side,
        "score": score,
        "cvdoi": cand.get("cvdoi"),
        "trigger": cand.get("trigger"),
        "smc": cand.get("smc"),
        "latest_before": cand.get("latest") or cand.get("latest_before"),
        "source_path": cand.get("source_path") or cand.get("source_file") or cand.get("_source_path"),
        "selection_source": source,
        "raw": cand,
    }


def candidate_guard(candidate: dict[str, Any], rest: dict[str, Any]) -> list[str]:
    failures: list[str] = []

    pair = candidate.get("pair")
    side = candidate.get("side")
    score = float(candidate.get("score") or 0.0)
    trigger = str(candidate.get("trigger") or "")
    smc = str(candidate.get("smc") or "")
    cvdoi = str(candidate.get("cvdoi") or "")
    whitelist_pairs = set(rest.get("whitelist_pairs") or [])

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

    strong_tokens = [
        "BULLISH_CONTINUATION_STRONG",
        "BEARISH_CONTINUATION_STRONG",
        "SHORT_SQUEEZE",
        "LONG_UNWIND",
    ]
    if not any(t in cvdoi for t in strong_tokens):
        failures.append("SIDE_FLOW_NOT_STRONG")

    if not rest.get("ping_ok"):
        failures.append("REST_PING_NOT_OK")
    if not rest.get("login_ok"):
        failures.append("REST_LOGIN_NOT_OK")
    if not rest.get("show_config_ok"):
        failures.append("REST_SHOW_CONFIG_NOT_OK")
    if rest.get("dry_run") is not True:
        failures.append("REST_DRY_RUN_NOT_TRUE")
    if rest.get("force_entry_enable") is not True:
        failures.append("REST_FORCE_ENTRY_ENABLE_NOT_TRUE")
    if not rest.get("whitelist_ok"):
        failures.append("REST_ACTIVE_WHITELIST_NOT_OK")
    if pair and whitelist_pairs and pair not in whitelist_pairs:
        failures.append("PAIR_NOT_IN_REST_ACTIVE_WHITELIST")

    return failures


def compact_text(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("F4X_AK2_PATCH_AK_TO_USE_REST_ACTIVE_WHITELIST_AND_WRITE_SCANNER_K_INTENT_DRYRUN_ONLY_COMPACT")
    lines.append(f"generated_at={result['generated_at']}")
    lines.append(f"mode={result['mode']}")
    lines.append(f"execute_requested={result['execute_requested']}")
    lines.append("paper_order=WRITE_K_INTENT_ONLY" if result["k_active_signal_written"] else "paper_order=HOLD")
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
    c = result.get("candidate") or {}
    lines.append("SELECTED_INTENT")
    if c:
        lines.append(
            f"pair={c.get('pair')}|side={c.get('side')}|score={c.get('score')}|"
            f"source={c.get('selection_source')}|cvdoi={c.get('cvdoi')}|"
            f"trigger={c.get('trigger')}|smc={c.get('smc')}|"
            f"allow_paper_entry={result['allow_paper_entry']}|would_order={result['would_order']}"
        )
    else:
        lines.append("NONE")
    r = result["runtime_checks"]
    lines.append("RUNTIME_CHECKS")
    lines.append(
        f"rest_ping_ok={r.get('rest_ping_ok')}|rest_token_login_ok={r.get('rest_token_login_ok')}|"
        f"rest_show_config_ok={r.get('rest_show_config_ok')}|dry_run_verified={r.get('dry_run_verified')}|"
        f"force_entry_enable_verified={r.get('force_entry_enable_verified')}"
    )
    lines.append(
        f"active_whitelist_ok={r.get('active_whitelist_ok')}|active_whitelist_count={r.get('active_whitelist_count')}|"
        f"pair_in_rest_active_whitelist={r.get('pair_in_rest_active_whitelist')}|open_trade_count={r.get('open_trade_count')}"
    )
    lines.append("WRITE_STATE")
    lines.append(f"k_active_signal_written={result['k_active_signal_written']}")
    lines.append(f"k_active_path={result['k_active_path']}")
    lines.append(f"backup_path={result['backup_path']}")
    lines.append("DECISION_POLICY")
    lines.append("AK2 writes one scanner-driven strict K paper intent only if REST active whitelist and dry-run guards pass.")
    lines.append("AK2 does not execute order by itself; F4X-L executes dry-run.")
    lines.append("AK2 does not enable live.")
    lines.append("AK2 does not risk-up.")
    lines.append("AK2 does not loosen gate.")
    lines.append("No manual AAVE whitelist injection.")
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

    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    candidate = load_selected_candidate(runtime)
    rest = rest_context(runtime)

    failures = []
    if not candidate:
        failures.append("NO_AK1_OR_AJ_SELECTED_CANDIDATE")
    else:
        failures.extend(candidate_guard(candidate, rest))

    pair = candidate.get("pair") if candidate else ""
    side = candidate.get("side") if candidate else ""

    allow = len(failures) == 0
    k_active_path = runtime / "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json"
    backup_path = None
    k_written = False

    if allow and args.execute:
        if k_active_path.exists():
            backup_path = runtime / f"F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL_BACKUP_BEFORE_AK2_{stamp()}.json"
            shutil.copy2(k_active_path, backup_path)

        signal = {
            "generated_at": now_utc(),
            "mode": "F4X_AK2_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_DRYRUN_ONLY",
            "has_order_intent": True,
            "order_intents": [
                {
                    "pair": pair,
                    "side": side,
                    "order_pair": pair,
                    "order_side": side,
                    "direction": side,
                    "score": candidate.get("score"),
                    "cvdoi": candidate.get("cvdoi"),
                    "trigger": candidate.get("trigger"),
                    "smc": candidate.get("smc"),
                    "latest_before": candidate.get("latest_before"),
                    "intent_source": "F4X_AK2_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_DRYRUN_ONLY",
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
                    "whitelist_source": "REST_ACTIVE_WHITELIST",
                    "scanner_selection_source": candidate.get("selection_source"),
                    "canary_reason": "SCANNER_DRIVEN_STRICT_CANDIDATE_REST_ACTIVE_WHITELIST_VALID",
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
                "ak1": str(runtime / "F4X_AK1_ACTIVE_WHITELIST_SOURCE_ALIGNMENT_AND_SCANNER_RESELECT_AUDIT_ACTIVE.json"),
                "aj": str(runtime / "F4X_AJ_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_CONVEYOR_SHADOW_ACTIVE.json"),
            },
        }
        write_json(k_active_path, signal)
        k_written = True

    final_decision = "F4X_AK2_ABORTED_GUARD_FAILED"
    if allow and not args.execute:
        final_decision = "F4X_AK2_READY_EXECUTE_REQUIRED"
    if allow and args.execute and k_written:
        final_decision = "F4X_AK2_SCANNER_DRIVEN_K_ACTIVE_SIGNAL_WRITTEN"

    result = {
        "generated_at": now_utc(),
        "mode": MODE,
        "execute_requested": bool(args.execute),
        "final_decision": final_decision,
        "guard_failures": failures,
        "candidate": candidate,
        "allow_paper_entry": bool(allow),
        "would_order": bool(allow),
        "k_active_signal_written": bool(k_written),
        "k_active_path": str(k_active_path),
        "backup_path": str(backup_path) if backup_path else None,
        "runtime_checks": {
            "rest_ping_ok": rest.get("ping_ok"),
            "rest_token_login_ok": rest.get("login_ok"),
            "rest_show_config_ok": rest.get("show_config_ok"),
            "dry_run_verified": rest.get("dry_run") is True,
            "force_entry_enable_verified": rest.get("force_entry_enable") is True,
            "active_whitelist_ok": rest.get("whitelist_ok"),
            "active_whitelist_count": len(rest.get("whitelist_pairs") or []),
            "pair_in_rest_active_whitelist": pair in set(rest.get("whitelist_pairs") or []) if pair else False,
            "open_trade_count": rest.get("open_trade_count"),
        },
        "rest_context": rest,
        "paper_order_allowed": bool(allow),
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "entry_from_watch_recheck_deny_allowed": False,
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
