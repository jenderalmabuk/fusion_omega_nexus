#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT_PREFIX = "F4X_AS4_REFRESHED_CANDIDATE_RECHECK_AND_RECENT_PAIR_COOLDOWN_SHADOW_ONLY"
MODE = "REFRESHED_CANDIDATE_RECHECK_AND_RECENT_PAIR_COOLDOWN_SHADOW_ONLY"

READY = "F4X_AS4_READY_FOR_AQ_REVIEW_ONLY"
HOLD_INPUT_STALE = "F4X_AS4_HOLD_INPUT_STALE"
HOLD_RUNTIME_HEALTH = "F4X_AS4_HOLD_RUNTIME_HEALTH_FAIL"
HOLD_OPEN_TRADE = "F4X_AS4_HOLD_OPEN_TRADE"
HOLD_NO_STRICT = "F4X_AS4_HOLD_NO_STRICT_CANDIDATE"
HOLD_RECENT_PAIR = "F4X_AS4_HOLD_RECENT_PAIR_COOLDOWN"
HOLD_CVD = "F4X_AS4_HOLD_CVD_DEGRADATION"
HOLD_LOW_CONF = "F4X_AS4_HOLD_LOW_CONFLUENCE"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_dt(v: Any):
    if not v:
        return None
    s = str(v).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            if fmt is None:
                dt = datetime.fromisoformat(s)
            else:
                dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            continue
    return None


def age_sec_from_ts(v: Any) -> float | None:
    dt = parse_dt(v)
    if not dt:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def file_age_sec(path: Path) -> float | None:
    try:
        if not path.exists():
            return None
        return max(0.0, datetime.now(timezone.utc).timestamp() - path.stat().st_mtime)
    except Exception:
        return None


def safe_float(v: Any, default: float | None = None) -> float | None:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def read_json(path: Path, default: Any = None) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")


def pair_to_symbol(pair: str) -> str:
    s = str(pair or "").upper()
    s = s.replace("/", "").replace(":USDT", "")
    return s


def normalize_side(v: Any) -> str:
    s = str(v or "").strip().upper()
    if s in {"BUY", "LONG", "LONG_ONLY"}:
        return "LONG"
    if s in {"SELL", "SHORT", "SHORT_ONLY"}:
        return "SHORT"
    return s


def load_env_file(path: Path) -> dict[str, str]:
    env = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.replace("export ", "", 1)
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        env[k.strip()] = v
    return env


def rest_request(url: str, path: str, token: str | None = None, basic: tuple[str, str] | None = None, timeout: int = 5, method: str = "GET") -> tuple[bool, Any, str]:
    full = url.rstrip("/") + path
    req = urllib.request.Request(full, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    elif basic:
        raw = f"{basic[0]}:{basic[1]}".encode()
        req.add_header("Authorization", "Basic " + base64.b64encode(raw).decode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
            try:
                return True, json.loads(body), ""
            except Exception:
                return True, body, ""
    except Exception as e:
        return False, None, str(e)


def rest_runtime(runtime: Path) -> dict[str, Any]:
    env = dict(os.environ)
    env.update(load_env_file(runtime / "F4X_AE2_REST_API_ENV.sh"))

    # F4X-AS4B:
    # Align AS4 REST auth source with known-good L/AQ/AS1/AS2 usage.
    # Prefer F4X_L_REST_* from F4X_AE2_REST_API_ENV.sh, then generic aliases.
    url = (
        env.get("F4X_L_REST_URL")
        or env.get("FREQTRADE_REST_URL")
        or env.get("FREQTRADE_API_URL")
        or env.get("FT_REST_URL")
        or env.get("FT_API_URL")
        or "http://127.0.0.1:8080"
    )
    user = (
        env.get("F4X_L_REST_USER")
        or env.get("FREQTRADE_USERNAME")
        or env.get("FT_USERNAME")
        or env.get("FREQTRADE_REST_USERNAME")
        or env.get("API_USERNAME")
        or ""
    )
    pwd = (
        env.get("F4X_L_REST_PASS")
        or env.get("FREQTRADE_PASSWORD")
        or env.get("FT_PASSWORD")
        or env.get("FREQTRADE_REST_PASSWORD")
        or env.get("API_PASSWORD")
        or ""
    )

    # Config fallback if env file exists but names are not mapped.
    if not (user and pwd):
        try:
            _cfg_path = Path("/home/fusion_omega/revo_adaptive/user_data/config.bybit.dynamic-universe.paper.json")
            _cfg = json.loads(_cfg_path.read_text(encoding="utf-8", errors="replace")) if _cfg_path.exists() else {}
            _api = _cfg.get("api_server") if isinstance(_cfg, dict) else {}
            if isinstance(_api, dict):
                user = user or str(_api.get("username") or "")
                pwd = pwd or str(_api.get("password") or "")
                _ip = str(_api.get("listen_ip_address") or "127.0.0.1")
                if _ip in {"0.0.0.0", "::"}:
                    _ip = "127.0.0.1"
                _port = _api.get("listen_port") or 8080
                url = url or f"http://{_ip}:{_port}"
        except Exception:
            pass

    ping_ok, ping, ping_err = rest_request(url, "/api/v1/ping")
    token = None
    login_ok = False
    login_err = ""
    if user and pwd:
        login_ok, login, login_err = rest_request(url, "/api/v1/token/login", basic=(user, pwd), method="POST")
        if isinstance(login, dict):
            token = login.get("access_token") or login.get("access")
    else:
        login_err = "NO_REST_CREDENTIALS_FOUND"

    show_ok, show, show_err = rest_request(url, "/api/v1/show_config", token=token)
    status_ok, status, status_err = rest_request(url, "/api/v1/status", token=token)
    whitelist_ok, whitelist, whitelist_err = rest_request(url, "/api/v1/whitelist", token=token)

    open_pairs = []
    if isinstance(status, list):
        for x in status:
            if isinstance(x, dict):
                p = x.get("pair")
                if p:
                    open_pairs.append(str(p))
    elif isinstance(status, dict):
        raw = status.get("open_trades") or status.get("trades") or status.get("data") or []
        if isinstance(raw, list):
            for x in raw:
                if isinstance(x, dict) and x.get("pair"):
                    open_pairs.append(str(x.get("pair")))

    whitelist_pairs = []
    if isinstance(whitelist, dict):
        raw = whitelist.get("whitelist") or whitelist.get("pairs") or whitelist.get("data") or []
        if isinstance(raw, list):
            whitelist_pairs = [str(x) for x in raw]
    if not whitelist_pairs and isinstance(show, dict):
        raw = show.get("exchange", {}).get("pair_whitelist") if isinstance(show.get("exchange"), dict) else None
        if isinstance(raw, list):
            whitelist_pairs = [str(x) for x in raw]

    return {
        "url": url,
        "ping_ok": ping_ok,
        "login_ok": login_ok,
        "show_config_ok": show_ok,
        "status_ok": status_ok,
        "whitelist_ok": whitelist_ok,
        "errors": {
            "ping": ping_err,
            "login": login_err,
            "show_config": show_err,
            "status": status_err,
            "whitelist": whitelist_err,
        },
        "dry_run": bool(show.get("dry_run")) if isinstance(show, dict) else None,
        "force_entry_enable": bool(show.get("force_entry_enable")) if isinstance(show, dict) else None,
        "max_open_trades": show.get("max_open_trades") if isinstance(show, dict) else None,
        "stake_amount": show.get("stake_amount") if isinstance(show, dict) else None,
        "open_count": len(open_pairs),
        "open_pairs": open_pairs,
        "whitelist_count": len(whitelist_pairs),
        "whitelist_pairs": whitelist_pairs,
    }


def candidate_from_obj(obj: Any) -> dict[str, Any] | None:
    if isinstance(obj, dict):
        pair = obj.get("pair") or obj.get("order_pair") or obj.get("symbol")
        side = obj.get("side") or obj.get("order_side") or obj.get("direction")
        if pair and side:
            return {
                "pair": str(pair),
                "side": normalize_side(side),
                "score": safe_float(obj.get("score") or obj.get("rank") or obj.get("max_score")),
                "cvdoi": obj.get("cvdoi") or obj.get("cvdoi_label") or obj.get("cvd_oi") or obj.get("flow"),
                "trigger": obj.get("trigger") or obj.get("trigger_state"),
                "smc": obj.get("smc") or obj.get("smc_state") or obj.get("location"),
                "latest": obj.get("latest") or obj.get("latest_before") or obj.get("reason"),
                "source": obj.get("source") or obj.get("scanner_selection_source") or "UNKNOWN",
                "raw": obj,
            }
        for k in ("selected_candidate", "selected_shadow_candidate", "selected_candidate_shadow", "candidate", "selected"):
            c = candidate_from_obj(obj.get(k))
            if c:
                return c
        for v in obj.values():
            c = candidate_from_obj(v)
            if c:
                return c
    elif isinstance(obj, list):
        for x in obj:
            c = candidate_from_obj(x)
            if c:
                return c
    return None


def load_refreshed_candidate(runtime: Path) -> dict[str, Any]:
    sources = [
        runtime / "F4X_AP_AUTONOMOUS_SCANNER_DRIVEN_NEXT_CANDIDATE_LOOP_SHADOW_ACTIVE.json",
        runtime / "F4X_AS3_AUTO_AUDIT_CASCADE_V1_SHADOW_ONLY_ACTIVE.json",
        runtime / "F4X_AJ_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_CONVEYOR_SHADOW_ACTIVE.json",
        runtime / "F4X_AJ_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_CONVEYOR_SHADOW_FULL.json",
    ]
    for p in sources:
        obj = read_json(p, None)
        c = candidate_from_obj(obj)
        if c:
            c["candidate_source_file"] = str(p)
            c["candidate_source_age_sec"] = file_age_sec(p)
            c["candidate_generated_at_age_sec"] = age_sec_from_ts(obj.get("generated_at")) if isinstance(obj, dict) else None
            return c
    return {}


def strict_candidate_ok(c: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    if not c:
        return False, ["NO_CANDIDATE_FOUND"]
    pair = str(c.get("pair") or "")
    side = normalize_side(c.get("side"))
    cvdoi = str(c.get("cvdoi") or "").upper()
    trigger = str(c.get("trigger") or "").upper()
    smc = str(c.get("smc") or "").upper()

    if not pair:
        reasons.append("PAIR_MISSING")
    if side not in {"LONG", "SHORT"}:
        reasons.append("SIDE_INVALID")
    if "STRONG" not in cvdoi and "BULLISH_CONTINUATION" not in cvdoi and "BEARISH_CONTINUATION" not in cvdoi:
        reasons.append("SIDE_FLOW_NOT_STRONG")
    if "TRIGGER_CONFIRMED" not in trigger:
        reasons.append("TRIGGER_NOT_CONFIRMED")
    if "GOOD" not in smc and "SMC_A" not in smc and "SMC_B" not in smc:
        reasons.append("SMC_NOT_GOOD")
    return len(reasons) == 0, reasons


def sqlite_pair_trades(repo: Path, pair: str) -> list[dict[str, Any]]:
    rows = []
    for db in sorted((repo / "user_data").glob("tradesv3*.sqlite")):
        try:
            con = sqlite3.connect(str(db))
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {str(x[0]) for x in cur.fetchall()}
            if "trades" not in tables:
                con.close()
                continue
            cur.execute("PRAGMA table_info(trades)")
            cols = [str(r[1]) for r in cur.fetchall()]
            wanted = [
                "id", "pair", "is_open", "open_date", "close_date", "stake_amount",
                "amount", "open_rate", "close_rate", "close_profit", "close_profit_abs",
                "enter_tag", "exit_reason", "leverage",
            ]
            select_cols = [c for c in wanted if c in cols]
            cur.execute(f"SELECT {','.join(select_cols)} FROM trades WHERE pair=?", (pair,))
            for r in cur.fetchall():
                d = {k: r[k] for k in r.keys()}
                d["_db"] = str(db)
                rows.append(d)
            con.close()
        except Exception:
            continue

    rows.sort(key=lambda x: (parse_dt(x.get("open_date")) or datetime(1970, 1, 1, tzinfo=timezone.utc)).timestamp(), reverse=True)
    return rows


def recent_pair_cooldown(repo: Path, pair: str, cooldown_sec: int, repeated_window_sec: int) -> dict[str, Any]:
    rows = sqlite_pair_trades(repo, pair)
    now = datetime.now(timezone.utc)
    latest_close = None
    latest_open = None
    recent_count = 0

    for r in rows:
        odt = parse_dt(r.get("open_date"))
        cdt = parse_dt(r.get("close_date"))
        if odt and latest_open is None:
            latest_open = odt
        if cdt and latest_close is None:
            latest_close = cdt
        if odt and (now - odt).total_seconds() <= repeated_window_sec:
            recent_count += 1

    latest_close_age = (now - latest_close).total_seconds() if latest_close else None
    cooldown_active = latest_close_age is not None and latest_close_age < cooldown_sec
    repeated_pair_active = recent_count >= 3

    return {
        "trade_count": len(rows),
        "latest_trade": rows[0] if rows else None,
        "latest_close_utc": latest_close.isoformat() if latest_close else None,
        "latest_close_age_sec": latest_close_age,
        "cooldown_sec": cooldown_sec,
        "cooldown_active": cooldown_active,
        "recent_window_sec": repeated_window_sec,
        "recent_same_pair_trade_count": recent_count,
        "repeated_pair_active": repeated_pair_active,
    }


def flatten(obj: Any, prefix: str = "") -> list[tuple[str, Any]]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            nk = f"{prefix}.{k}" if prefix else str(k)
            out.extend(flatten(v, nk))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(flatten(v, f"{prefix}[{i}]"))
    else:
        out.append((prefix, obj))
    return out


def collect_pair_records(obj: Any, pair: str) -> list[dict[str, Any]]:
    pair_u = pair.upper()
    symbol_u = pair_to_symbol(pair)
    found = []

    def walk(x: Any):
        if isinstance(x, dict):
            txt = json.dumps(x, default=str).upper()
            pair_hit = pair_u in txt or symbol_u in txt
            if pair_hit:
                found.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(obj)
    return found


def flow_degradation(runtime: Path, candidate: dict[str, Any], cvd_z_threshold: float, low_score_threshold: float) -> dict[str, Any]:
    pair = str(candidate.get("pair") or "")
    side = normalize_side(candidate.get("side"))
    files = [
        runtime / "F4X_FULL_CONFLUENCE_FINAL_FULL.json",
        runtime / "revo_flow_context_collector.json",
        runtime / "F3D_CURRENT_FLOW_SNAPSHOT_SCORER_FULL.json",
        runtime / "F3D_CURRENT_FLOW_SNAPSHOT_SCORER_COMPACT.txt",
    ]

    records: list[dict[str, Any]] = []
    file_states = []
    for p in files:
        file_states.append({"path": str(p), "exists": p.exists(), "age_sec": file_age_sec(p)})
        if p.suffix == ".json":
            obj = read_json(p, None)
            records.extend(collect_pair_records(obj, pair))
        elif p.exists():
            txt = p.read_text(encoding="utf-8", errors="replace")
            if pair in txt or pair_to_symbol(pair) in txt.upper():
                records.append({"_text_file": str(p), "text": txt[:5000]})

    cvd_hits = []
    low_conf_hits = []
    support_hits = []
    flat_points = []

    for rec_i, rec in enumerate(records[:30]):
        for k, v in flatten(rec):
            kl = k.lower()
            vf = safe_float(v)
            vs = str(v).upper()

            if "cvd" in kl and vf is not None:
                if side == "LONG" and (("delta" in kl and vf < 0) or ("z" in kl and vf <= -abs(cvd_z_threshold))):
                    cvd_hits.append(f"rec{rec_i}.{k}={v}")
                if side == "SHORT" and (("delta" in kl and vf > 0) or ("z" in kl and vf >= abs(cvd_z_threshold))):
                    cvd_hits.append(f"rec{rec_i}.{k}={v}")

            if ("reason" in kl or "label" in kl) and "LOW_CONFLUENCE" in vs:
                low_conf_hits.append(f"rec{rec_i}.{k}={v}")

            if kl.endswith("score") or ".score" in kl:
                if vf is not None and vf <= low_score_threshold:
                    low_conf_hits.append(f"rec{rec_i}.{k}={v}")

            if any(x in kl for x in ("oi_delta", "open_interest_delta", "price_15m", "price_5m")) and vf is not None:
                if side == "LONG" and vf > 0:
                    support_hits.append(f"rec{rec_i}.{k}={v}")
                if side == "SHORT" and vf < 0:
                    support_hits.append(f"rec{rec_i}.{k}={v}")

            if any(x in kl for x in ("cvd", "score", "reason", "oi_delta", "open_interest_delta")):
                flat_points.append(f"rec{rec_i}.{k}={v}")

    return {
        "records_found": len(records),
        "file_states": file_states,
        "cvd_degradation": len(cvd_hits) > 0,
        "low_confluence": len(low_conf_hits) > 0,
        "cvd_hits": cvd_hits[:25],
        "low_confluence_hits": low_conf_hits[:25],
        "support_hits": support_hits[:25],
        "context_points": flat_points[:50],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--max-input-age-sec", type=int, default=900)
    ap.add_argument("--pair-cooldown-sec", type=int, default=21600)
    ap.add_argument("--repeated-pair-window-sec", type=int, default=86400)
    ap.add_argument("--cvd-z-threshold", type=float, default=1.5)
    ap.add_argument("--low-confluence-score", type=float, default=35.0)
    args = ap.parse_args()

    repo = Path(args.repo_dir)
    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    failures = []
    warnings = []
    blockers = []

    rest = rest_runtime(runtime)
    candidate = load_refreshed_candidate(runtime)
    strict_ok, strict_reasons = strict_candidate_ok(candidate)

    source_age = candidate.get("candidate_source_age_sec")
    if source_age is None:
        blockers.append("INPUT_SOURCE_MISSING")
    elif source_age > args.max_input_age_sec:
        blockers.append(f"INPUT_STALE_GT_{args.max_input_age_sec}_SEC")

    runtime_ok = rest["ping_ok"] and rest["status_ok"] and rest["show_config_ok"] and rest["dry_run"] is True and rest["force_entry_enable"] is True
    if not runtime_ok:
        blockers.append("REST_RUNTIME_HEALTH_FAIL")

    if rest["open_count"] > 0:
        blockers.append("REST_OPEN_TRADE_ACTIVE")

    pair = str(candidate.get("pair") or "")
    side = normalize_side(candidate.get("side"))

    pair_in_whitelist = False
    if pair and rest.get("whitelist_pairs"):
        pair_in_whitelist = pair in rest["whitelist_pairs"]
    elif pair and rest["whitelist_count"] == 0:
        warnings.append("REST_WHITELIST_EMPTY_OR_UNAVAILABLE_CANNOT_VERIFY_PAIR")
    if pair and rest.get("whitelist_pairs") and not pair_in_whitelist:
        blockers.append("PAIR_NOT_IN_REST_ACTIVE_WHITELIST")

    if not strict_ok:
        blockers.extend(strict_reasons)

    cooldown = recent_pair_cooldown(repo, pair, args.pair_cooldown_sec, args.repeated_pair_window_sec) if pair else {}
    if cooldown.get("cooldown_active"):
        blockers.append("RECENT_PAIR_COOLDOWN_ACTIVE")
    if cooldown.get("repeated_pair_active"):
        blockers.append("REPEATED_PAIR_SELECTION_ACTIVE")

    flow = flow_degradation(runtime, candidate, args.cvd_z_threshold, args.low_confluence_score) if candidate else {}
    if flow.get("cvd_degradation"):
        blockers.append("CVD_DEGRADATION_ACTIVE")
    if flow.get("low_confluence"):
        blockers.append("LOW_CONFLUENCE_ACTIVE")

    blockers_unique = list(dict.fromkeys(blockers))

    if any(x.startswith("INPUT_") for x in blockers_unique):
        final_decision = HOLD_INPUT_STALE
        next_action = "Refresh AP/AJ/flow inputs before AQ review. No K/L."
    elif "REST_RUNTIME_HEALTH_FAIL" in blockers_unique:
        final_decision = HOLD_RUNTIME_HEALTH
        next_action = "Patch runtime/REST health only. No K/L."
    elif "REST_OPEN_TRADE_ACTIVE" in blockers_unique:
        final_decision = HOLD_OPEN_TRADE
        next_action = "Wait for active open trade close. No K/L."
    elif not strict_ok:
        final_decision = HOLD_NO_STRICT
        next_action = "No strict candidate. Let scanner continue and audit blocker reasons."
    elif "PAIR_NOT_IN_REST_ACTIVE_WHITELIST" in blockers_unique:
        final_decision = HOLD_NO_STRICT
        next_action = "Candidate not in active whitelist. Let scanner continue."
    elif "RECENT_PAIR_COOLDOWN_ACTIVE" in blockers_unique or "REPEATED_PAIR_SELECTION_ACTIVE" in blockers_unique:
        final_decision = HOLD_RECENT_PAIR
        next_action = "Hold same-pair reuse. Let scanner refresh or choose another strict candidate."
    elif "CVD_DEGRADATION_ACTIVE" in blockers_unique:
        final_decision = HOLD_CVD
        next_action = "Hold AQ. CVD degradation detected. Shadow only."
    elif "LOW_CONFLUENCE_ACTIVE" in blockers_unique:
        final_decision = HOLD_LOW_CONF
        next_action = "Hold AQ. Low confluence detected. Shadow only."
    else:
        final_decision = READY
        next_action = "Candidate passed AS4 shadow checks. AQ may be considered only after explicit approval."

    result = {
        "generated_at": now_utc(),
        "mode": MODE,
        "paper_order_allowed": False,
        "k_write_allowed": False,
        "l_execute_allowed": False,
        "forceexit_allowed": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "final_decision": final_decision,
        "next_action": next_action,
        "failures": failures,
        "warnings": warnings,
        "blockers": blockers_unique,
        "rest_runtime": rest,
        "selected_candidate_shadow": candidate,
        "strict_check": {"strict_ok": strict_ok, "reject_reasons": strict_reasons},
        "pair_in_rest_active_whitelist": pair_in_whitelist,
        "recent_pair_cooldown": cooldown,
        "flow_degradation": flow,
        "decision_policy": [
            "AS4 is shadow-only.",
            "AS4 does not write K.",
            "AS4 does not execute L.",
            "AS4 does not forceenter.",
            "AS4 does not create paper order.",
            "AS4 does not enable live, risk-up, or gate-loosen.",
            "AS4 blocks recent same-pair reuse, CVD degradation, and low-confluence before AQ review.",
        ],
        "output_files": {
            "full_json": str(runtime / f"{OUT_PREFIX}_FULL.json"),
            "compact": str(runtime / f"{OUT_PREFIX}_COMPACT.txt"),
            "active": str(runtime / f"{OUT_PREFIX}_ACTIVE.json"),
        },
    }

    lines = []
    lines.append("F4X_AS4_REFRESHED_CANDIDATE_RECHECK_AND_RECENT_PAIR_COOLDOWN_SHADOW_ONLY_COMPACT")
    lines.append(f"generated_at={result['generated_at']}")
    lines.append(f"mode={MODE}")
    lines.append("paper_order=HOLD")
    lines.append("k_write=HOLD")
    lines.append("l_execute=HOLD")
    lines.append("forceexit=HOLD")
    lines.append("live=HOLD")
    lines.append("risk_up=HOLD")
    lines.append("gate_loosen=HOLD")
    lines.append("FINAL_DECISION")
    lines.append(f"final_decision={final_decision}")
    lines.append(f"next_action={next_action}")

    lines.append("FAILURES")
    lines.extend(failures if failures else ["NONE"])

    lines.append("WARNINGS")
    lines.extend(warnings if warnings else ["NONE"])

    lines.append("BLOCKERS")
    lines.extend(blockers_unique if blockers_unique else ["NONE"])

    lines.append("REST_RUNTIME")
    lines.append(
        f"ping_ok={rest['ping_ok']}|login_ok={rest['login_ok']}|show_config_ok={rest['show_config_ok']}|"
        f"status_ok={rest['status_ok']}|whitelist_ok={rest['whitelist_ok']}|dry_run={rest['dry_run']}|"
        f"force_entry_enable={rest['force_entry_enable']}|open_count={rest['open_count']}|"
        f"open_pairs={','.join(rest['open_pairs']) if rest['open_pairs'] else 'NONE'}|"
        f"whitelist_count={rest['whitelist_count']}"
    )

    lines.append("SELECTED_CANDIDATE_SHADOW")
    if candidate:
        lines.append(
            f"{pair}|side={side}|score={candidate.get('score')}|cvdoi={candidate.get('cvdoi')}|"
            f"trigger={candidate.get('trigger')}|smc={candidate.get('smc')}|latest={candidate.get('latest')}|"
            f"source_file={candidate.get('candidate_source_file')}|source_age_sec={candidate.get('candidate_source_age_sec')}"
        )
    else:
        lines.append("NONE")

    lines.append("STRICT_CHECK")
    lines.append(f"strict_ok={strict_ok}|reject_reasons={','.join(strict_reasons) if strict_reasons else 'NONE'}|pair_in_rest_active_whitelist={pair_in_whitelist}")

    lines.append("RECENT_PAIR_COOLDOWN")
    for k, v in cooldown.items():
        lines.append(f"{k}={v}")

    lines.append("FLOW_DEGRADATION")
    lines.append(
        f"records_found={flow.get('records_found')}|cvd_degradation={flow.get('cvd_degradation')}|"
        f"low_confluence={flow.get('low_confluence')}"
    )

    lines.append("CVD_HITS")
    lines.extend(flow.get("cvd_hits") or ["NONE"])

    lines.append("LOW_CONFLUENCE_HITS")
    lines.extend(flow.get("low_confluence_hits") or ["NONE"])

    lines.append("SUPPORT_HITS")
    lines.extend(flow.get("support_hits") or ["NONE"])

    lines.append("DECISION_POLICY")
    lines.extend(result["decision_policy"])

    lines.append("OUTPUT_FILES")
    for k, v in result["output_files"].items():
        lines.append(f"{k}={v}")

    compact = "\n".join(lines) + "\n"

    write_json(runtime / f"{OUT_PREFIX}_FULL.json", result)
    write_json(runtime / f"{OUT_PREFIX}_ACTIVE.json", result)
    (runtime / f"{OUT_PREFIX}_COMPACT.txt").write_text(compact, encoding="utf-8")
    print(compact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
