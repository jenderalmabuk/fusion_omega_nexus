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

OUT_PREFIX = "F4X_AQ2_AS3_APPROVED_SCANNER_STRICT_K_INTENT_DRYRUN_PATCH"
MODE = "AS3_APPROVED_SCANNER_STRICT_K_INTENT_DRYRUN_PATCH"
AS3_READY = "F4X_AS3_READY_FOR_AQ_REVIEW_ONLY"


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
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def request_json(url: str, method: str = "GET", headers: dict[str, str] | None = None, data: bytes | None = None, timeout: int = 8):
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
        "open_count": None,
        "open_pairs": [],
        "whitelist_pairs": [],
    }

    ok, status, _ = request_json(rest_url + "/api/v1/ping")
    out["ping_ok"] = bool(ok and status == 200)

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
        if out["login_ok"]:
            token = str(body.get("access_token") or "")

    headers = {"Authorization": "Bearer " + token} if token else {}

    if token:
        ok, status, body = request_json(rest_url + "/api/v1/show_config", headers=headers)
        out["show_config_ok"] = bool(ok and status == 200 and isinstance(body, dict))
        if isinstance(body, dict):
            out["dry_run"] = body.get("dry_run")
            out["force_entry_enable"] = body.get("force_entry_enable")

        ok, status, body = request_json(rest_url + "/api/v1/status", headers=headers)
        out["status_ok"] = bool(ok and status == 200)
        if isinstance(body, list):
            trades = [x for x in body if isinstance(x, dict)]
        elif isinstance(body, dict):
            raw = body.get("trades") or body.get("data") or body.get("result") or []
            trades = [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []
        else:
            trades = []
        out["open_count"] = len(trades)
        out["open_pairs"] = sorted({str(x.get("pair")) for x in trades if x.get("pair")})

        ok, status, body = request_json(rest_url + "/api/v1/whitelist", headers=headers)
        out["whitelist_ok"] = bool(ok and status == 200)
        pairs: list[str] = []
        if isinstance(body, dict):
            raw = body.get("whitelist") or body.get("pair_whitelist") or body.get("pairs") or []
            if isinstance(raw, list):
                pairs = [norm_pair(x) for x in raw if norm_pair(x)]
        elif isinstance(body, list):
            pairs = [norm_pair(x) for x in body if norm_pair(x)]
        out["whitelist_pairs"] = sorted(set(pairs))

    return out


def compact_text(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("F4X_AQ2_AS3_APPROVED_SCANNER_STRICT_K_INTENT_DRYRUN_PATCH_COMPACT")
    lines.append(f"generated_at={result['generated_at']}")
    lines.append(f"mode={result['mode']}")
    lines.append(f"execute_requested={result['execute_requested']}")
    lines.append("paper_order=WRITE_K_INTENT_ONLY")
    lines.append("l_execute=HOLD")
    lines.append("live=HOLD")
    lines.append("risk_up=HOLD")
    lines.append("gate_loosen=HOLD")
    lines.append("entry_from_watch_recheck_deny=HOLD")
    lines.append("FINAL_DECISION")
    lines.append(f"final_decision={result['final_decision']}")
    lines.append("GUARD_FAILURES")
    lines.extend(result["guard_failures"] or ["NONE"])
    lines.append("SELECTED_INTENT")
    s = result.get("selected_intent") or {}
    if s:
        lines.append(
            f"{s.get('pair')}|side={s.get('side')}|score={s.get('score')}|source={s.get('source')}|"
            f"cvdoi={s.get('cvdoi')}|trigger={s.get('trigger')}|smc={s.get('smc')}|"
            f"allow_paper_entry={s.get('allow_paper_entry')}|would_order={s.get('would_order')}"
        )
    else:
        lines.append("NONE")
    r = result["runtime_checks"]
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
        f"open_trade_count={r.get('open_trade_count')}|open_same_pair={r.get('open_same_pair')}"
    )
    lines.append("WRITE_STATE")
    lines.append(f"k_active_signal_written={result['k_active_signal_written']}")
    lines.append(f"k_active_path={result['k_active_path']}")
    lines.append(f"backup_path={result['backup_path']}")
    lines.append("DECISION_POLICY")
    lines.append("AQ2 writes one K intent only if AS3 is READY_FOR_AQ_REVIEW_ONLY and all runtime guards pass.")
    lines.append("AQ2 does not execute L.")
    lines.append("AQ2 does not create paper order.")
    lines.append("AQ2 does not enable live/risk/gate loosen.")
    lines.append("L remains HOLD until AQ2 PASS and K JSON validates.")
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

    as3 = read_json(runtime / "F4X_AS3_AUTO_AUDIT_CASCADE_V1_SHADOW_ONLY_ACTIVE.json", {})
    rest = rest_probe(runtime)

    guard_failures: list[str] = []
    selected_intent: dict[str, Any] = {}

    if not isinstance(as3, dict):
        guard_failures.append("AS3_ACTIVE_MISSING_OR_INVALID")
    elif as3.get("final_decision") != AS3_READY:
        guard_failures.append("AS3_NOT_READY_FOR_AQ_REVIEW_ONLY")

    candidate = {}
    if isinstance(as3, dict):
        candidate = ((as3.get("candidate") or {}).get("row") or {}) if isinstance(as3.get("candidate"), dict) else {}

    pair = norm_pair(candidate.get("pair") or candidate.get("order_pair"))
    side = norm_side(candidate.get("side") or candidate.get("order_side") or candidate.get("direction"))

    if not pair:
        guard_failures.append("PAIR_MISSING")
    if side not in {"LONG", "SHORT"}:
        guard_failures.append("SIDE_INVALID")

    if not rest.get("ping_ok"):
        guard_failures.append("REST_PING_FAIL")
    if not rest.get("login_ok"):
        guard_failures.append("REST_LOGIN_FAIL")
    if not rest.get("show_config_ok"):
        guard_failures.append("REST_SHOW_CONFIG_FAIL")
    if not rest.get("status_ok"):
        guard_failures.append("REST_STATUS_FAIL")
    if not rest.get("whitelist_ok"):
        guard_failures.append("REST_WHITELIST_FAIL")
    if rest.get("dry_run") is not True:
        guard_failures.append("DRY_RUN_NOT_TRUE")
    if rest.get("force_entry_enable") is not True:
        guard_failures.append("FORCE_ENTRY_ENABLE_NOT_TRUE")

    whitelist = set(rest.get("whitelist_pairs") or [])
    if whitelist and pair not in whitelist:
        guard_failures.append("PAIR_NOT_IN_REST_ACTIVE_WHITELIST")

    open_pairs = set(rest.get("open_pairs") or [])
    if pair in open_pairs:
        guard_failures.append("SAME_PAIR_OPEN_TRADE_ACTIVE")

    if int(rest.get("open_count") or 0) >= 5:
        guard_failures.append("MAX_OPEN_TRADES_SLOT_NOT_AVAILABLE")

    if not guard_failures:
        selected_intent = {
            "pair": pair,
            "side": side,
            "score": float(candidate.get("score") or 0.0),
            "source": "F4X_AS3_READY_FOR_AQ_REVIEW_ONLY",
            "cvdoi": candidate.get("cvdoi"),
            "trigger": candidate.get("trigger"),
            "smc": candidate.get("smc"),
            "latest_before": candidate.get("latest") or candidate.get("latest_before"),
            "allow_paper_entry": True,
            "would_order": True,
        }

    k_path = runtime / "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json"
    backup_path = None
    written = False

    if args.execute and not guard_failures:
        if k_path.exists():
            backup_path = runtime / f"F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL_BACKUP_BEFORE_AQ2_{stamp()}.json"
            shutil.copy2(k_path, backup_path)

        k_signal = {
            "generated_at": now_utc(),
            "mode": "F4X_AQ2_AS3_APPROVED_SCANNER_STRICT_K_INTENT_DRYRUN_ONLY",
            "has_order_intent": True,
            "order_intents": [
                {
                    "pair": pair,
                    "side": side,
                    "order_pair": pair,
                    "order_side": side,
                    "direction": side,
                    "score": selected_intent["score"],
                    "cvdoi": selected_intent["cvdoi"],
                    "trigger": selected_intent["trigger"],
                    "smc": selected_intent["smc"],
                    "latest_before": selected_intent["latest_before"],
                    "intent_source": "F4X_AQ2_AS3_APPROVED_SCANNER_STRICT_K_INTENT_DRYRUN_ONLY",
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
                    "scanner_selection_source": "F4X_AS3_AUTO_AUDIT_CASCADE_V1_SHADOW_ONLY",
                    "canary_reason": "AS3_APPROVED_SCANNER_STRICT_CANDIDATE_READY_FOR_DRYRUN_PAPER"
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
                "as3": str(runtime / "F4X_AS3_AUTO_AUDIT_CASCADE_V1_SHADOW_ONLY_ACTIVE.json")
            }
        }
        write_json(k_path, k_signal)
        written = True

    if guard_failures:
        final_decision = "F4X_AQ2_ABORTED_GUARD_FAILED"
    elif written:
        final_decision = "F4X_AQ2_AS3_APPROVED_K_ACTIVE_SIGNAL_WRITTEN"
    else:
        final_decision = "F4X_AQ2_READY_DRY_RUN_NO_WRITE"

    result = {
        "generated_at": now_utc(),
        "mode": MODE,
        "execute_requested": bool(args.execute),
        "paper_order_allowed": False,
        "k_write_allowed": bool(args.execute and not guard_failures),
        "l_execute_allowed": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "entry_from_watch_recheck_deny_allowed": False,
        "final_decision": final_decision,
        "guard_failures": guard_failures,
        "selected_intent": selected_intent,
        "runtime_checks": {
            "rest_ping_ok": rest.get("ping_ok"),
            "rest_login_ok": rest.get("login_ok"),
            "rest_show_config_ok": rest.get("show_config_ok"),
            "rest_status_ok": rest.get("status_ok"),
            "rest_whitelist_ok": rest.get("whitelist_ok"),
            "dry_run": rest.get("dry_run"),
            "force_entry_enable": rest.get("force_entry_enable"),
            "active_whitelist_count": len(rest.get("whitelist_pairs") or []),
            "pair_in_rest_active_whitelist": pair in whitelist if pair else False,
            "open_trade_count": rest.get("open_count"),
            "open_same_pair": pair in open_pairs if pair else False,
        },
        "k_active_signal_written": written,
        "k_active_path": str(k_path),
        "backup_path": str(backup_path) if backup_path else None,
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
