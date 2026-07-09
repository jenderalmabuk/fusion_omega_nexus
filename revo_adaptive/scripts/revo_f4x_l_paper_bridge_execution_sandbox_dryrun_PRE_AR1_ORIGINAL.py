#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        return {"_load_error": repr(e), "_path": str(path)}


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def recursive_find_key(obj: Any, key: str) -> List[Any]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) == key:
                out.append(v)
            out.extend(recursive_find_key(v, key))
    elif isinstance(obj, list):
        for x in obj:
            out.extend(recursive_find_key(x, key))
    return out


def parse_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in {"true", "1", "yes", "y", "on"}:
            return True
        if s in {"false", "0", "no", "n", "off"}:
            return False
    if isinstance(v, (int, float)):
        return bool(v)
    return None


def discover_config(repo: Path) -> Dict[str, Any]:
    candidates = []
    for pat in [
        "user_data/config*.json",
        "user_data/**/*config*.json",
        "config*.json",
        "**/config*.json",
    ]:
        candidates.extend(repo.glob(pat))

    seen = set()
    configs = []
    for p in candidates:
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        data = load_json(p, {})
        if isinstance(data, dict):
            configs.append((p, data))

    api = {}
    dry_run_static = None
    source = ""

    for p, data in configs:
        if dry_run_static is None:
            vals = recursive_find_key(data, "dry_run")
            for v in vals:
                b = parse_bool(v)
                if b is not None:
                    dry_run_static = b
                    source = str(p)
                    break

        api_server = data.get("api_server")
        if isinstance(api_server, dict) and api_server.get("enabled") is True:
            host = norm(api_server.get("listen_ip_address") or api_server.get("ip_address") or "127.0.0.1")
            if host in {"0.0.0.0", "::"}:
                host = "127.0.0.1"
            port = api_server.get("listen_port") or api_server.get("port") or 8080
            user = norm(api_server.get("username"))
            pw = norm(api_server.get("password"))
            if user and pw:
                api = {
                    "url": f"http://{host}:{port}",
                    "username": user,
                    "password": pw,
                    "source": str(p),
                }
                break

    env_url = norm(os.environ.get("F4X_L_REST_URL"))
    env_user = norm(os.environ.get("F4X_L_REST_USER"))
    env_pass = norm(os.environ.get("F4X_L_REST_PASS"))
    if env_url and env_user and env_pass:
        api = {
            "url": env_url,
            "username": env_user,
            "password": env_pass,
            "source": "ENV",
        }

    return {
        "api": api,
        "static_dry_run": dry_run_static,
        "static_dry_run_source": source,
    }


class FreqtradeClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith("http://") and not self.base_url.startswith("https://"):
            self.base_url = "http://" + self.base_url
        self.username = username
        self.password = password
        self.timeout = timeout
        self.token = ""

    def _basic_header(self) -> str:
        raw = f"{self.username}:{self.password}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None, use_token: bool = True) -> Tuple[int, Any, str]:
        url = self.base_url + path
        data = None
        headers = {"Content-Type": "application/json"}

        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        if use_token and self.token:
            headers["Authorization"] = "Bearer " + self.token
        else:
            headers["Authorization"] = self._basic_header()

        req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    obj = json.loads(raw) if raw else {}
                except Exception:
                    obj = {"_raw": raw}
                return resp.status, obj, raw
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                obj = json.loads(raw) if raw else {}
            except Exception:
                obj = {"_raw": raw}
            return e.code, obj, raw
        except Exception as e:
            return 0, {"error": repr(e)}, repr(e)

    def login(self) -> bool:
        status, obj, _ = self.request("POST", "/api/v1/token/login", payload={}, use_token=False)
        if status in {200, 201} and isinstance(obj, dict):
            token = obj.get("access_token") or obj.get("token")
            if token:
                self.token = str(token)
                return True
        return False

    def show_config(self) -> Tuple[int, Any]:
        status, obj, _ = self.request("GET", "/api/v1/show_config", use_token=True)
        return status, obj

    def status(self) -> Tuple[int, Any]:
        status, obj, _ = self.request("GET", "/api/v1/status", use_token=True)
        return status, obj

    def forceenter(self, pair: str, side: str) -> Tuple[int, Any]:
        payload = {"pair": pair, "side": side.lower()}
        status, obj, _ = self.request("POST", "/api/v1/forceenter", payload=payload, use_token=True)
        return status, obj


def dry_run_verified(client: FreqtradeClient, static_dry: Optional[bool]) -> Tuple[bool, str, Any]:
    status, cfg = client.show_config()
    if status in {200, 201} and isinstance(cfg, dict):
        vals = recursive_find_key(cfg, "dry_run")
        for v in vals:
            b = parse_bool(v)
            if b is True:
                return True, "REST_SHOW_CONFIG_DRY_RUN_TRUE", cfg
            if b is False:
                return False, "REST_SHOW_CONFIG_DRY_RUN_FALSE", cfg
        return False, "REST_SHOW_CONFIG_NO_DRY_RUN_FIELD", cfg

    if static_dry is True and os.environ.get("F4X_L_ALLOW_STATIC_DRYRUN_ASSERT") == "1":
        return True, "STATIC_CONFIG_DRY_RUN_TRUE_WITH_OVERRIDE", {"show_config_status": status, "static_dry_run": static_dry}

    return False, "DRY_RUN_NOT_VERIFIED_BY_REST", {"show_config_status": status, "static_dry_run": static_dry, "show_config": cfg}


def load_order_intents(runtime: Path) -> List[Dict[str, Any]]:
    active = load_json(runtime / "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json", {})
    intents = []

    if isinstance(active, dict):
        for x in active.get("order_intents", []) or []:
            if isinstance(x, dict):
                intents.append(x)

    full = load_json(runtime / "F4X_K_PAPER_BRIDGE_INTENTS_FULL.json", {})
    if isinstance(full, dict):
        for x in full.get("intents", []) or []:
            if isinstance(x, dict) and norm(x.get("intent_action")).startswith("WOULD_ORDER"):
                intents.append(x)

    # Direct strict fallback from F4X if K has not run yet.
    f4x = load_json(runtime / "F4X_PAPER_DECISION_SIGNALS.json", {})
    if isinstance(f4x, dict):
        for s in f4x.get("signals", []) or []:
            if isinstance(s, dict) and norm(s.get("paper_action")) == "ALLOW_PAPER_ENTRY":
                intents.append({
                    "pair": s.get("pair"),
                    "side": s.get("side"),
                    "intent_action": "WOULD_ORDER",
                    "intent_reason": "ALLOW_PAPER_ENTRY_DIRECT_F4X_FALLBACK",
                    "paper_action": "ALLOW_PAPER_ENTRY",
                    "score": s.get("score", 0),
                })

    dedup = []
    seen = set()
    for x in intents:
        pair = norm(x.get("pair"))
        side = norm(x.get("side")).upper()
        reason = norm(x.get("intent_reason"))
        k = f"{pair}|{side}|{reason}"
        if not pair or side not in {"LONG", "SHORT"} or k in seen:
            continue
        seen.add(k)
        dedup.append(x)

    return dedup


def open_trade_exists(status_obj: Any, pair: str, side: str) -> bool:
    rows = status_obj if isinstance(status_obj, list) else status_obj.get("trades", []) if isinstance(status_obj, dict) else []
    if not isinstance(rows, list):
        return False

    want_short = side.upper() == "SHORT"
    for r in rows:
        if not isinstance(r, dict):
            continue
        if norm(r.get("pair")) != pair:
            continue
        is_short = r.get("is_short")
        trade_side = norm(r.get("trade_direction") or r.get("side")).upper()
        if isinstance(is_short, bool):
            if is_short == want_short:
                return True
        if trade_side and trade_side == side.upper():
            return True
        # If side field absent, avoid duplicate same pair.
        if not trade_side and is_short is None:
            return True
    return False


def load_cooldown(runtime: Path) -> Dict[str, Any]:
    p = runtime / "F4X_L_EXECUTION_COOLDOWN.json"
    data = load_json(p, {})
    return data if isinstance(data, dict) else {}


def save_cooldown(runtime: Path, data: Dict[str, Any]) -> None:
    write_json(runtime / "F4X_L_EXECUTION_COOLDOWN.json", data)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--cooldown-sec", type=int, default=1800)
    args = ap.parse_args()

    repo = Path(args.repo_dir)
    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    generated_at = now_utc()
    events_path = runtime / "F4X_L_EXECUTION_EVENTS.jsonl"

    result = {
        "event": "F4X_L_PAPER_BRIDGE_EXECUTION_SANDBOX_DRYRUN",
        "generated_at": generated_at,
        "execute_requested": bool(args.execute),
        "paper_execution_mode": "FREQTRADE_DRY_RUN_FORCEENTER",
        "live": "HOLD",
        "risk_up": "HOLD",
        "gate_loosen": "HOLD",
        "orders": [],
        "blocked": [],
        "errors": [],
    }

    discovered = discover_config(repo)
    api = discovered.get("api", {})
    result["api_source"] = api.get("source", "")
    result["static_dry_run"] = discovered.get("static_dry_run")
    result["static_dry_run_source"] = discovered.get("static_dry_run_source")

    intents = load_order_intents(runtime)
    result["would_order_intent_count"] = len(intents)

    if not intents:
        result["decision"] = "NO_VALID_ORDER_INTENT"
        result["blocked"].append({"reason": "NO_WOULD_ORDER_FROM_F4X_K_OR_ALLOW_PAPER_ENTRY"})
        append_jsonl(events_path, result)
        write_outputs(runtime, result)
        return 0

    if not api:
        result["decision"] = "HOLD_REST_API_NOT_CONFIGURED"
        result["errors"].append({
            "reason": "MISSING_FREQTRADE_REST_API_CONFIG",
            "hint": "Set F4X_L_REST_URL/F4X_L_REST_USER/F4X_L_REST_PASS or enable api_server in config dry_run bot.",
        })
        append_jsonl(events_path, result)
        write_outputs(runtime, result)
        return 1

    client = FreqtradeClient(api["url"], api["username"], api["password"])
    login_ok = client.login()
    result["rest_url"] = api["url"]
    result["rest_login_ok"] = login_ok

    if not login_ok:
        result["decision"] = "HOLD_REST_LOGIN_FAILED"
        result["errors"].append({"reason": "REST_LOGIN_FAILED"})
        append_jsonl(events_path, result)
        write_outputs(runtime, result)
        return 1

    dry_ok, dry_reason, dry_obj = dry_run_verified(client, discovered.get("static_dry_run"))
    result["dry_run_verified"] = dry_ok
    result["dry_run_reason"] = dry_reason

    if not dry_ok:
        result["decision"] = "REFUSE_EXECUTION_DRY_RUN_NOT_VERIFIED"
        result["errors"].append({"reason": dry_reason})
        append_jsonl(events_path, result)
        write_outputs(runtime, result)
        return 1

    status_code, status_obj = client.status()
    result["status_code"] = status_code

    cooldown = load_cooldown(runtime)
    now_ts = time.time()

    for intent in intents:
        pair = norm(intent.get("pair"))
        side = norm(intent.get("side")).upper()
        paper_action = norm(intent.get("paper_action"))
        intent_reason = norm(intent.get("intent_reason"))

        order_event = {
            "pair": pair,
            "side": side,
            "paper_action": paper_action,
            "intent_reason": intent_reason,
            "score": intent.get("score", 0),
            "generated_at": generated_at,
        }

        if paper_action not in {"ALLOW_PAPER_ENTRY", ""} and "ALLOW_PAPER_ENTRY" not in intent_reason:
            order_event["decision"] = "BLOCKED_NON_ALLOW_PAPER_ENTRY"
            result["blocked"].append(order_event)
            continue

        if open_trade_exists(status_obj, pair, side):
            order_event["decision"] = "SKIP_OPEN_TRADE_ALREADY_EXISTS"
            result["blocked"].append(order_event)
            continue

        ck = f"{pair}|{side}"
        last = float(cooldown.get(ck, 0) or 0)
        if now_ts - last < args.cooldown_sec:
            order_event["decision"] = "SKIP_COOLDOWN_ACTIVE"
            order_event["cooldown_remaining_sec"] = max(0, int(args.cooldown_sec - (now_ts - last)))
            result["blocked"].append(order_event)
            continue

        if not args.execute:
            order_event["decision"] = "WOULD_EXECUTE_BUT_EXECUTE_FLAG_FALSE"
            result["orders"].append(order_event)
            continue

        status, obj = client.forceenter(pair, side)
        order_event["rest_status"] = status
        order_event["rest_response"] = obj

        if status in {200, 201}:
            order_event["decision"] = "DRY_RUN_FORCEENTER_SENT"
            cooldown[ck] = now_ts
            result["orders"].append(order_event)
        else:
            order_event["decision"] = "FORCEENTER_FAILED"
            result["errors"].append(order_event)

    save_cooldown(runtime, cooldown)

    if any(x.get("decision") == "DRY_RUN_FORCEENTER_SENT" for x in result["orders"]):
        result["decision"] = "DRY_RUN_ORDER_SENT"
    elif result["orders"]:
        result["decision"] = "WOULD_EXECUTE_ONLY"
    elif result["errors"]:
        result["decision"] = "EXECUTION_ERRORS"
    else:
        result["decision"] = "NO_ORDER_AFTER_GUARDS"

    append_jsonl(events_path, result)
    write_outputs(runtime, result)

    return 0 if not result["errors"] else 1


def write_outputs(runtime: Path, result: Dict[str, Any]) -> None:
    full = runtime / "F4X_L_PAPER_BRIDGE_EXECUTION_FULL.json"
    compact = runtime / "F4X_L_PAPER_BRIDGE_EXECUTION_COMPACT.txt"
    active = runtime / "F4X_L_PAPER_BRIDGE_ACTIVE_EXECUTION.json"

    write_json(full, result)

    write_json(active, {
        "generated_at": result.get("generated_at"),
        "decision": result.get("decision"),
        "dry_run_verified": result.get("dry_run_verified"),
        "orders": result.get("orders", []),
        "blocked": result.get("blocked", []),
        "errors": result.get("errors", []),
        "live_allowed": False,
    })

    lines = []
    lines.append("F4X_L_PAPER_BRIDGE_EXECUTION_COMPACT")
    lines.append(f"generated_at={result.get('generated_at')}")
    lines.append("mode=ACTUAL_FREQTRADE_DRY_RUN_EXECUTION_SANDBOX")
    lines.append(f"execute_requested={result.get('execute_requested')}")
    lines.append(f"decision={result.get('decision')}")
    lines.append(f"dry_run_verified={result.get('dry_run_verified')}")
    lines.append(f"dry_run_reason={result.get('dry_run_reason')}")
    lines.append(f"would_order_intent_count={result.get('would_order_intent_count')}")
    lines.append("live=HOLD")
    lines.append("risk_up=HOLD")
    lines.append("gate_loosen=HOLD")
    lines.append("")
    lines.append("ORDERS")
    for x in result.get("orders", []):
        lines.append(f"{x.get('pair')}|side={x.get('side')}|decision={x.get('decision')}|status={x.get('rest_status')}|reason={x.get('intent_reason')}")
    lines.append("")
    lines.append("BLOCKED")
    for x in result.get("blocked", [])[:80]:
        lines.append(f"{x.get('pair','') or 'NA'}|side={x.get('side','') or 'NA'}|decision={x.get('decision', x.get('reason'))}|reason={x.get('intent_reason', x.get('reason',''))}")
    lines.append("")
    lines.append("ERRORS")
    for x in result.get("errors", [])[:40]:
        lines.append(f"{x.get('pair','') or 'NA'}|side={x.get('side','') or 'NA'}|reason={x.get('decision', x.get('reason'))}|status={x.get('rest_status')}")
    lines.append("")
    lines.append("OUTPUT_FILES")
    lines.append(f"full_json={full}")
    lines.append(f"active_execution={active}")
    lines.append(f"events_jsonl={runtime / 'F4X_L_EXECUTION_EVENTS.jsonl'}")

    compact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    raise SystemExit(main())
