#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT_PREFIX = "F4X_AS5_NEXT_NON_COOLDOWN_STRICT_CANDIDATE_SELECTOR_SHADOW_ONLY"
MODE = "NEXT_NON_COOLDOWN_STRICT_CANDIDATE_SELECTOR_SHADOW_ONLY"

READY = "F4X_AS5_READY_FOR_AQ_REVIEW_ONLY"
HOLD_NO_CLEAN = "F4X_AS5_HOLD_NO_CLEAN_NON_COOLDOWN_STRICT_CANDIDATE"
HOLD_RUNTIME = "F4X_AS5_HOLD_RUNTIME_HEALTH_FAIL"
HOLD_INPUT = "F4X_AS5_HOLD_INPUT_STALE_OR_MISSING"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_dt(v: Any):
    if not v:
        return None
    s = str(v).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            continue
    return None


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
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return default


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")


def load_env_file(path: Path) -> dict[str, str]:
    env = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def rest_request(base_url: str, path: str, token: str | None = None, basic: tuple[str, str] | None = None, method: str = "GET", timeout: int = 8):
    req = urllib.request.Request(base_url.rstrip("/") + path, method=method)
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
        return False, None, f"{type(e).__name__}:{e}"


def rest_runtime(runtime: Path, repo: Path) -> dict[str, Any]:
    env = dict(os.environ)
    env.update(load_env_file(runtime / "F4X_AE2_REST_API_ENV.sh"))

    url = env.get("F4X_L_REST_URL") or env.get("FREQTRADE_REST_URL") or "http://127.0.0.1:8080"
    user = env.get("F4X_L_REST_USER") or env.get("FREQTRADE_USERNAME") or ""
    pwd = env.get("F4X_L_REST_PASS") or env.get("FREQTRADE_PASSWORD") or ""

    if not (user and pwd):
        cfg_path = repo / "user_data" / "config.bybit.dynamic-universe.paper.json"
        cfg = read_json(cfg_path, {})
        api = cfg.get("api_server") if isinstance(cfg, dict) else {}
        if isinstance(api, dict):
            user = user or str(api.get("username") or "")
            pwd = pwd or str(api.get("password") or "")
            ip = str(api.get("listen_ip_address") or "127.0.0.1")
            if ip in {"0.0.0.0", "::"}:
                ip = "127.0.0.1"
            port = api.get("listen_port") or 8080
            url = f"http://{ip}:{port}"

    ping_ok, _, ping_err = rest_request(url, "/api/v1/ping")
    token = None
    login_ok = False
    login_err = ""
    if user and pwd:
        login_ok, login_body, login_err = rest_request(url, "/api/v1/token/login", basic=(user, pwd), method="POST")
        if isinstance(login_body, dict):
            token = login_body.get("access_token") or login_body.get("access")

    show_ok, show, show_err = rest_request(url, "/api/v1/show_config", token=token)
    status_ok, status, status_err = rest_request(url, "/api/v1/status", token=token)
    wl_ok, wl, wl_err = rest_request(url, "/api/v1/whitelist", token=token)

    open_pairs = []
    if isinstance(status, list):
        open_pairs = [str(x.get("pair")) for x in status if isinstance(x, dict) and x.get("pair")]

    whitelist_pairs = []
    if isinstance(wl, dict):
        raw = wl.get("whitelist") or wl.get("pairs") or wl.get("data") or []
        if isinstance(raw, list):
            whitelist_pairs = [str(x) for x in raw]

    return {
        "ping_ok": ping_ok,
        "login_ok": login_ok,
        "show_config_ok": show_ok,
        "status_ok": status_ok,
        "whitelist_ok": wl_ok,
        "dry_run": show.get("dry_run") if isinstance(show, dict) else None,
        "force_entry_enable": show.get("force_entry_enable") if isinstance(show, dict) else None,
        "open_count": len(open_pairs),
        "open_pairs": open_pairs,
        "whitelist_count": len(whitelist_pairs),
        "whitelist_pairs": whitelist_pairs,
        "errors": {"ping": ping_err, "login": login_err, "show": show_err, "status": status_err, "whitelist": wl_err},
    }


def normalize_side(v: Any) -> str:
    s = str(v or "").strip().upper()
    if s in {"BUY", "LONG", "LONG_ONLY"}:
        return "LONG"
    if s in {"SELL", "SHORT", "SHORT_ONLY"}:
        return "SHORT"
    return s


def candidate_from_dict(d: dict[str, Any], source_file: str) -> dict[str, Any] | None:
    pair = d.get("pair") or d.get("order_pair") or d.get("symbol")
    side = d.get("side") or d.get("order_side") or d.get("direction")
    if not pair or not side:
        return None
    return {
        "pair": str(pair),
        "side": normalize_side(side),
        "score": safe_float(d.get("score") or d.get("rank") or d.get("max_score")),
        "cvdoi": d.get("cvdoi") or d.get("cvdoi_label") or d.get("flow"),
        "trigger": d.get("trigger") or d.get("trigger_state"),
        "smc": d.get("smc") or d.get("smc_state") or d.get("location"),
        "latest": d.get("latest") or d.get("latest_before") or d.get("reason"),
        "source_file": source_file,
        "raw": d,
    }


def collect_candidates(obj: Any, source_file: str) -> list[dict[str, Any]]:
    out = []

    def walk(x: Any):
        if isinstance(x, dict):
            c = candidate_from_dict(x, source_file)
            if c:
                out.append(c)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(obj)
    unique = {}
    for c in out:
        key = (c["pair"], c["side"], c.get("score"), str(c.get("trigger")), str(c.get("smc")))
        unique[key] = c
    return list(unique.values())


def strict_check(c: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    cvdoi = str(c.get("cvdoi") or "").upper()
    trigger = str(c.get("trigger") or "").upper()
    smc = str(c.get("smc") or "").upper()
    side = normalize_side(c.get("side"))

    if side not in {"LONG", "SHORT"}:
        reasons.append("SIDE_INVALID")
    if "STRONG" not in cvdoi and "BULLISH_CONTINUATION" not in cvdoi and "BEARISH_CONTINUATION" not in cvdoi:
        reasons.append("SIDE_FLOW_NOT_STRONG")
    if "TRIGGER_CONFIRMED" not in trigger:
        reasons.append("TRIGGER_NOT_CONFIRMED")
    if "GOOD" not in smc and "SMC_A" not in smc and "SMC_B" not in smc:
        reasons.append("SMC_NOT_GOOD")
    return len(reasons) == 0, reasons


# F4X_AS5J2K1_AS5_SOURCE_BRIDGE_PATCH_PREVIEW
# Purpose: widen AS5 shadow candidate intake with AS5J1 fuel-ready pairs.
# Safety: candidate intake/reporting only; strict_check, flow_state, cooldown, K/L/order/live/risk/gate remain unchanged.

def _f4x_as5j2k1_as_num(v, default=None):
    try:
        if v is None or v == "":
            return default
        x = float(v)
        if x != x:
            return default
        return x
    except Exception:
        return default


def _f4x_as5j2k1_context_candidates(runtime: Path) -> dict[str, dict[str, Any]]:
    ctx: dict[str, dict[str, Any]] = {}
    for p in [
        runtime / "F4X_FULL_CONFLUENCE_FINAL_FULL.json",
        runtime / "F4X_PAPER_DECISION_SIGNALS.json",
    ]:
        obj = read_json(p, None)
        if obj is None:
            continue
        for c in collect_candidates(obj, str(p)):
            pair = str(c.get("pair") or "")
            old = ctx.get(pair)
            if old is None or (_f4x_as5j2k1_as_num(c.get("score"), -999999) or -999999) > (_f4x_as5j2k1_as_num(old.get("score"), -999999) or -999999):
                ctx[pair] = c
    return ctx


def _f4x_as5j2k1_infer_side_from_flow(flow: dict[str, Any]) -> str:
    p15 = _f4x_as5j2k1_as_num(flow.get("price_delta_15m_pct") or flow.get("price_change_15m_pct") or flow.get("p15"), 0.0) or 0.0
    cvd = _f4x_as5j2k1_as_num(flow.get("cvd_delta_15m") or flow.get("cvd_delta"), 0.0) or 0.0
    cvdz = _f4x_as5j2k1_as_num(flow.get("cvd_zscore_15m") or flow.get("cvd_zscore") or flow.get("cvd_z_15m") or flow.get("cvd_z"), 0.0) or 0.0
    oi15 = _f4x_as5j2k1_as_num(flow.get("oi_delta_15m_pct") or flow.get("open_interest_delta_15m_pct") or flow.get("oi15"), 0.0) or 0.0

    long_score = 0
    short_score = 0

    if p15 > 0:
        long_score += 1
    elif p15 < 0:
        short_score += 1

    if cvd > 0 or cvdz >= 1.0:
        long_score += 1
    elif cvd < 0 or cvdz <= -1.0:
        short_score += 1

    if oi15 > 0 and p15 > 0:
        long_score += 1
    elif oi15 > 0 and p15 < 0:
        short_score += 1

    if long_score > short_score:
        return "LONG"
    if short_score > long_score:
        return "SHORT"
    return ""


def _f4x_as5j2k1_cvdoi_from_flow(side: str, flow: dict[str, Any]) -> str:
    cvd = _f4x_as5j2k1_as_num(flow.get("cvd_delta_15m") or flow.get("cvd_delta"), 0.0) or 0.0
    cvdz = _f4x_as5j2k1_as_num(flow.get("cvd_zscore_15m") or flow.get("cvd_zscore") or flow.get("cvd_z_15m") or flow.get("cvd_z"), 0.0) or 0.0
    if side == "LONG" and (cvd > 0 or cvdz >= 1.0):
        return "BULLISH_CONTINUATION_AS5J1_BRIDGE_CVD_OBSERVED"
    if side == "SHORT" and (cvd < 0 or cvdz <= -1.0):
        return "BEARISH_CONTINUATION_AS5J1_BRIDGE_CVD_OBSERVED"
    return "AS5J1_BRIDGE_FLOW_OBSERVED_NOT_STRONG"


def _f4x_as5j2k1_bridge_candidates(runtime: Path, max_age_sec: int) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    out: list[dict[str, Any]] = []

    as5j1_path = runtime / "F4X_AS5J1_FEEDER_METRIC_COVERAGE_AND_SOURCE_FRESHNESS_REPAIR_PREVIEW_AUDIT_FULL.json"
    flow_path = runtime / "revo_flow_context_collector.json"

    as5j1_age = file_age_sec(as5j1_path)
    if not as5j1_path.exists():
        warnings.append("MISSING_INPUT:F4X_AS5J1_FUEL_READY_BRIDGE_SOURCE")
        return out, warnings
    if as5j1_age is not None and as5j1_age > max_age_sec:
        warnings.append(f"STALE_INPUT:F4X_AS5J1_FUEL_READY_BRIDGE_SOURCE:{int(as5j1_age)}s")

    as5j1 = read_json(as5j1_path, {}) or {}
    rows = as5j1.get("rows") if isinstance(as5j1, dict) else []
    if not isinstance(rows, list):
        warnings.append("AS5J1_BRIDGE_ROWS_INVALID")
        return out, warnings

    flow_context = read_json(flow_path, {}) or {}
    if not isinstance(flow_context, dict):
        flow_context = {}

    context_by_pair = _f4x_as5j2k1_context_candidates(runtime)

    fuel_seen = 0
    bridge_added = 0
    bridge_no_side = 0
    bridge_context_enriched = 0

    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("state") != "FUEL_READY_WITH_OVERLAY_PREVIEW":
            continue
        if row.get("missing"):
            continue

        pair = str(row.get("pair") or "").strip()
        if not pair:
            continue
        fuel_seen += 1

        ctx = context_by_pair.get(pair) or {}
        flow = flow_context.get(pair) if isinstance(flow_context.get(pair), dict) else {}

        side = normalize_side(ctx.get("side")) if ctx else ""
        if side not in {"LONG", "SHORT"}:
            side = _f4x_as5j2k1_infer_side_from_flow(flow)

        if side not in {"LONG", "SHORT"}:
            bridge_no_side += 1
            continue

        cvdoi = ctx.get("cvdoi") if ctx else None
        if not cvdoi:
            cvdoi = _f4x_as5j2k1_cvdoi_from_flow(side, flow)

        trigger = ctx.get("trigger") if ctx else None
        if not trigger:
            trigger = "AS5J1_BRIDGE_TRIGGER_PENDING"

        smc = ctx.get("smc") if ctx else None
        if not smc:
            smc = "AS5J1_BRIDGE_SMC_PENDING"

        score = _f4x_as5j2k1_as_num(ctx.get("score") if ctx else None, None)
        if score is None:
            vol = _f4x_as5j2k1_as_num(row.get("volume_usd"), 0.0) or 0.0
            score = min(34.0, max(1.0, vol / 10000000.0))

        raw = {
            "source": "AS5J2K1_AS5J1_FUEL_READY_SOURCE_BRIDGE",
            "as5j1_row": row,
            "flow_context": flow,
            "context_candidate": ctx,
            "bridge_note": "Intake only. Strict AS5 gates remain unchanged; pending trigger/SMC remains reject until real upstream context confirms.",
        }

        out.append({
            "pair": pair,
            "side": side,
            "score": score,
            "cvdoi": cvdoi,
            "trigger": trigger,
            "smc": smc,
            "latest": "AS5J1_FUEL_READY_BRIDGE",
            "source_file": "F4X_AS5J2K1_AS5J1_FUEL_READY_SOURCE_BRIDGE",
            "source_age_sec": as5j1_age,
            "raw": raw,
        })
        bridge_added += 1
        if ctx:
            bridge_context_enriched += 1

    warnings.append(f"F4X_AS5J2K1_BRIDGE_FUEL_READY_SEEN:{fuel_seen}")
    warnings.append(f"F4X_AS5J2K1_BRIDGE_ADDED:{bridge_added}")
    warnings.append(f"F4X_AS5J2K1_BRIDGE_NO_SIDE:{bridge_no_side}")
    warnings.append(f"F4X_AS5J2K1_BRIDGE_CONTEXT_ENRICHED:{bridge_context_enriched}")
    return out, warnings


def load_all_candidates(runtime: Path, max_age_sec: int) -> tuple[list[dict[str, Any]], list[str]]:
    # F4X_AS5J2K1_PRESERVE_ORIGINAL_INPUTS
    # F4X_AS5J2K1_ADD_AS5J1_FUEL_READY_BRIDGE
    # F4X_AS5J2K1_NO_STRICT_GATE_LOOSEN
    files = [
        runtime / "F4X_AP_AUTONOMOUS_SCANNER_DRIVEN_NEXT_CANDIDATE_LOOP_SHADOW_FULL.json",
        runtime / "F4X_AP_AUTONOMOUS_SCANNER_DRIVEN_NEXT_CANDIDATE_LOOP_SHADOW_ACTIVE.json",
        runtime / "F4X_AJ_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_CONVEYOR_SHADOW_FULL.json",
        runtime / "F4X_AJ_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_CONVEYOR_SHADOW_ACTIVE.json",
        runtime / "F4X_FULL_CONFLUENCE_FINAL_FULL.json",
        runtime / "F4X_PAPER_DECISION_SIGNALS.json",
    ]
    warnings = []
    candidates = []
    for p in files:
        age = file_age_sec(p)
        if not p.exists():
            warnings.append(f"MISSING_INPUT:{p.name}")
            continue
        if age is not None and age > max_age_sec:
            warnings.append(f"STALE_INPUT:{p.name}:{int(age)}s")
        obj = read_json(p, None)
        for c in collect_candidates(obj, str(p)):
            c["source_age_sec"] = age
            candidates.append(c)

    bridge_candidates, bridge_warnings = _f4x_as5j2k1_bridge_candidates(runtime, max_age_sec)
    candidates.extend(bridge_candidates)
    warnings.extend(bridge_warnings)

    dedup = {}
    for c in candidates:
        key = (c["pair"], c["side"])
        old = dedup.get(key)
        if old is None or (safe_float(c.get("score"), -999999) or -999999) > (safe_float(old.get("score"), -999999) or -999999):
            dedup[key] = c

    ranked = sorted(dedup.values(), key=lambda x: safe_float(x.get("score"), -999999) or -999999, reverse=True)
    return ranked, warnings
def sqlite_pair_trades(repo: Path, pair: str) -> list[dict[str, Any]]:
    rows = []
    for db in sorted((repo / "user_data").glob("tradesv3*.sqlite")):
        try:
            con = sqlite3.connect(str(db))
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if "trades" not in {str(x[0]) for x in cur.fetchall()}:
                con.close()
                continue
            cur.execute("PRAGMA table_info(trades)")
            cols = [str(r[1]) for r in cur.fetchall()]
            wanted = ["id", "pair", "is_open", "open_date", "close_date", "stake_amount", "amount", "open_rate", "close_rate", "close_profit", "close_profit_abs", "enter_tag", "exit_reason", "leverage"]
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


def cooldown_state(repo: Path, pair: str, cooldown_sec: int, repeated_window_sec: int) -> dict[str, Any]:
    rows = sqlite_pair_trades(repo, pair)
    now = datetime.now(timezone.utc)
    latest_close = None
    recent_count = 0
    for r in rows:
        odt = parse_dt(r.get("open_date"))
        cdt = parse_dt(r.get("close_date"))
        if cdt and latest_close is None:
            latest_close = cdt
        if odt and (now - odt).total_seconds() <= repeated_window_sec:
            recent_count += 1
    age = (now - latest_close).total_seconds() if latest_close else None
    return {
        "trade_count": len(rows),
        "latest_trade": rows[0] if rows else None,
        "latest_close_utc": latest_close.isoformat() if latest_close else None,
        "latest_close_age_sec": age,
        "cooldown_sec": cooldown_sec,
        "cooldown_active": age is not None and age < cooldown_sec,
        "recent_same_pair_trade_count": recent_count,
        "repeated_pair_active": recent_count >= 3,
    }


def flatten(obj: Any, prefix: str = ""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            nk = f"{prefix}.{k}" if prefix else str(k)
            yield from flatten(v, nk)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from flatten(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj


def pair_symbol(pair: str) -> str:
    return str(pair).upper().replace("/", "").replace(":USDT", "")


def pair_records(runtime: Path, pair: str) -> list[dict[str, Any]]:
    records = []
    for p in [runtime / "F4X_FULL_CONFLUENCE_FINAL_FULL.json", runtime / "revo_flow_context_collector.json"]:
        obj = read_json(p, None)
        if obj is None:
            continue

        def walk(x: Any):
            if isinstance(x, dict):
                txt = json.dumps(x, default=str).upper()
                if pair.upper() in txt or pair_symbol(pair) in txt:
                    records.append(x)
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)

        walk(obj)
    return records[:30]


def flow_state(runtime: Path, c: dict[str, Any], low_score_threshold: float) -> dict[str, Any]:
    side = normalize_side(c.get("side"))
    records = pair_records(runtime, c["pair"])
    cvd_hits = []
    low_hits = []
    support_hits = []

    for i, rec in enumerate(records):
        for k, v in flatten(rec):
            kl = k.lower()
            vf = safe_float(v)
            vs = str(v).upper()

            if "cvd" in kl and vf is not None:
                if side == "LONG" and (("delta" in kl and vf < 0) or ("z" in kl and vf <= -1.5) or ("oi_15m" in kl and vf < 0) or ("oi_5m" in kl and vf < 0)):
                    cvd_hits.append(f"rec{i}.{k}={v}")
                if side == "SHORT" and (("delta" in kl and vf > 0) or ("z" in kl and vf >= 1.5) or ("oi_15m" in kl and vf > 0) or ("oi_5m" in kl and vf > 0)):
                    cvd_hits.append(f"rec{i}.{k}={v}")

            if "LOW_CONFLUENCE" in vs:
                low_hits.append(f"rec{i}.{k}={v}")
            if (kl.endswith("score") or ".score" in kl) and vf is not None and vf <= low_score_threshold:
                low_hits.append(f"rec{i}.{k}={v}")

            if vf is not None and any(x in kl for x in ["price_15m_delta_pct", "price_5m_delta_pct", "oi_15m_delta_pct", "oi_5m_delta_pct"]):
                if side == "LONG" and vf > 0:
                    support_hits.append(f"rec{i}.{k}={v}")
                if side == "SHORT" and vf < 0:
                    support_hits.append(f"rec{i}.{k}={v}")

    return {
        "records_found": len(records),
        "cvd_degradation": bool(cvd_hits),
        "low_confluence": bool(low_hits),
        "cvd_hits": cvd_hits[:10],
        "low_confluence_hits": low_hits[:10],
        "support_hits": support_hits[:10],
    }


# F4X_AS5J2M_TRUTH_ATTRIBUTION_PATCH_PREVIEW
# F4X_AS5J2M_NO_GATE_LOOSEN
# Report-only attribution helpers. These functions do not remove or soften any AS5 reject reason.
# They only classify whether CVD/low-confluence looks pair-specific or shared/global, and expose REST whitelist gaps.

def _f4x_as5j2m_as_list(v):
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, tuple):
        return [str(x) for x in v]
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return []


def _f4x_as5j2m_candidate_from_item(item):
    if isinstance(item, dict):
        c = item.get("candidate") or item.get("c") or item.get("raw") or item
        return c if isinstance(c, dict) else item
    return {}


def _f4x_as5j2m_pair_from_item(item):
    c = _f4x_as5j2m_candidate_from_item(item)
    for obj in (item, c):
        if isinstance(obj, dict):
            for key in ("pair", "symbol", "market", "asset", "order_pair"):
                v = obj.get(key)
                if isinstance(v, str) and v:
                    return v
    return ""


def _f4x_as5j2m_side_from_item(item):
    c = _f4x_as5j2m_candidate_from_item(item)
    for obj in (item, c):
        if isinstance(obj, dict):
            v = obj.get("side")
            if isinstance(v, str) and v:
                return v
    return ""


def _f4x_as5j2m_score_from_item(item):
    c = _f4x_as5j2m_candidate_from_item(item)
    for obj in (item, c):
        if isinstance(obj, dict):
            v = obj.get("score")
            if v is not None:
                return v
    return None


def _f4x_as5j2m_reasons_from_item(item):
    if not isinstance(item, dict):
        return []
    for key in ("reasons", "reason", "reject_reasons", "blocked_reasons"):
        if key in item:
            return _f4x_as5j2m_as_list(item.get(key))
    return []


def _f4x_as5j2m_flow_from_item(item):
    if isinstance(item, dict):
        fl = item.get("flow") or item.get("flow_state") or item.get("flow_context") or {}
        return fl if isinstance(fl, dict) else {}
    return {}


def _f4x_as5j2m_truth_report(evaluated, rest):
    # F4X_AS5J2M_CVD_LOWCONF_SHARED_SATURATION_REPORT
    # F4X_AS5J2M_REST_WHITELIST_GAP_REPORT
    rows = evaluated if isinstance(evaluated, list) else []
    n = len(rows)

    reason_counts = {}
    pair_rows = []
    whitelist_raw = []
    if isinstance(rest, dict):
        whitelist_raw = rest.get("whitelist_pairs") or []
    whitelist = set(str(x) for x in whitelist_raw)

    cvd_pairs = []
    low_pairs = []
    whitelist_gap_pairs = []
    pair_specific_cvd = []
    pair_specific_low = []
    shared_cvd_only = []
    shared_low_only = []

    for item in rows:
        if not isinstance(item, dict):
            continue

        pair = _f4x_as5j2m_pair_from_item(item)
        side = _f4x_as5j2m_side_from_item(item)
        score = _f4x_as5j2m_score_from_item(item)
        reasons = _f4x_as5j2m_reasons_from_item(item)
        flow = _f4x_as5j2m_flow_from_item(item)

        for r in reasons:
            reason_counts[r] = int(reason_counts.get(r, 0)) + 1

        has_cvd = "CVD_DEGRADATION_ACTIVE" in reasons
        has_low = "LOW_CONFLUENCE_ACTIVE" in reasons

        cvd_hits = _f4x_as5j2m_as_list(flow.get("cvd_hits"))
        low_hits = _f4x_as5j2m_as_list(flow.get("low_confluence_hits"))

        if has_cvd:
            cvd_pairs.append(pair)
            if cvd_hits:
                pair_specific_cvd.append(pair)
            else:
                shared_cvd_only.append(pair)

        if has_low:
            low_pairs.append(pair)
            if low_hits:
                pair_specific_low.append(pair)
            else:
                shared_low_only.append(pair)

        in_whitelist = pair in whitelist if pair else False
        if "PAIR_NOT_IN_REST_ACTIVE_WHITELIST" in reasons or (whitelist and pair and not in_whitelist):
            whitelist_gap_pairs.append(pair)

        pair_rows.append({
            "pair": pair,
            "side": side,
            "score": score,
            "reasons": reasons,
            "in_rest_whitelist": in_whitelist,
            "cvd_degradation_active": has_cvd,
            "low_confluence_active": has_low,
            "cvd_pair_specific_hits": cvd_hits[:5],
            "low_confluence_pair_specific_hits": low_hits[:5],
            "cvd_truth_class": (
                "PAIR_SPECIFIC_CVD_DEGRADATION" if has_cvd and cvd_hits
                else "SHARED_OR_GLOBAL_CVD_CAUTION" if has_cvd
                else "NO_CVD_DEGRADATION_REASON"
            ),
            "low_conf_truth_class": (
                "PAIR_SPECIFIC_LOW_CONFLUENCE" if has_low and low_hits
                else "SHARED_OR_GLOBAL_LOW_CONFLUENCE_CAUTION" if has_low
                else "NO_LOW_CONFLUENCE_REASON"
            ),
            "rest_whitelist_class": (
                "PAIR_NOT_IN_REST_ACTIVE_WHITELIST" if pair in whitelist_gap_pairs
                else "PAIR_IN_REST_ACTIVE_WHITELIST"
            ),
        })

    cvd_count = int(reason_counts.get("CVD_DEGRADATION_ACTIVE", 0))
    low_count = int(reason_counts.get("LOW_CONFLUENCE_ACTIVE", 0))
    whitelist_gap_count = int(reason_counts.get("PAIR_NOT_IN_REST_ACTIVE_WHITELIST", 0))

    cvd_ratio = cvd_count / max(1, n)
    low_ratio = low_count / max(1, n)
    whitelist_gap_ratio = max(whitelist_gap_count, len(set(whitelist_gap_pairs))) / max(1, n)

    report = {
        "candidate_count": n,
        "reason_counts": reason_counts,
        "cvd_degradation_count": cvd_count,
        "low_confluence_active_count": low_count,
        "cvd_ratio": cvd_ratio,
        "low_ratio": low_ratio,
        "cvd_shared_saturation": bool(n and cvd_ratio >= 0.95),
        "low_conf_shared_saturation": bool(n and low_ratio >= 0.95),
        "cvd_pair_specific_count": len(set(pair_specific_cvd)),
        "low_conf_pair_specific_count": len(set(pair_specific_low)),
        "cvd_shared_or_global_only_count": len(set(shared_cvd_only)),
        "low_conf_shared_or_global_only_count": len(set(shared_low_only)),
        "rest_whitelist_count": len(whitelist),
        "rest_whitelist_gap_count": len(set(whitelist_gap_pairs)),
        "rest_whitelist_gap_ratio": whitelist_gap_ratio,
        "rest_whitelist_gap_major": bool(n and whitelist_gap_ratio >= 0.40),
        "sample_pair_attribution": pair_rows[:80],
        "rest_whitelist_gap_sample": sorted(set(whitelist_gap_pairs))[:80],
    }
    return report


def _f4x_as5j2m_append_truth_attribution_section(lines, evaluated, rest):
    # Report-only output section. Does not alter selected candidate, reject reasons, K/L, or order path.
    rep = _f4x_as5j2m_truth_report(evaluated, rest)
    lines.append("AS5J2M_TRUTH_ATTRIBUTION_REPORT")
    lines.append(f"candidate_count={rep.get('candidate_count')}")
    lines.append(f"cvd_degradation_count={rep.get('cvd_degradation_count')}|ratio={rep.get('cvd_ratio')}")
    lines.append(f"low_confluence_active_count={rep.get('low_confluence_active_count')}|ratio={rep.get('low_ratio')}")
    lines.append(f"cvd_shared_saturation={rep.get('cvd_shared_saturation')}")
    lines.append(f"low_conf_shared_saturation={rep.get('low_conf_shared_saturation')}")
    lines.append(f"cvd_pair_specific_count={rep.get('cvd_pair_specific_count')}")
    lines.append(f"low_conf_pair_specific_count={rep.get('low_conf_pair_specific_count')}")
    lines.append(f"cvd_shared_or_global_only_count={rep.get('cvd_shared_or_global_only_count')}")
    lines.append(f"low_conf_shared_or_global_only_count={rep.get('low_conf_shared_or_global_only_count')}")
    lines.append("AS5J2M_REST_WHITELIST_GAP_REPORT")
    lines.append(f"rest_whitelist_count={rep.get('rest_whitelist_count')}")
    lines.append(f"rest_whitelist_gap_count={rep.get('rest_whitelist_gap_count')}|ratio={rep.get('rest_whitelist_gap_ratio')}")
    lines.append(f"rest_whitelist_gap_major={rep.get('rest_whitelist_gap_major')}")
    lines.append(f"rest_whitelist_gap_sample={rep.get('rest_whitelist_gap_sample')}")
    lines.append("AS5J2M_PAIR_ATTRIBUTION_SAMPLE")
    for row in rep.get("sample_pair_attribution", [])[:40]:
        try:
            lines.append(json.dumps(row, default=str))
        except Exception:
            lines.append(str(row))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--max-input-age-sec", type=int, default=900)
    ap.add_argument("--pair-cooldown-sec", type=int, default=21600)
    ap.add_argument("--repeated-pair-window-sec", type=int, default=86400)
    ap.add_argument("--low-confluence-score", type=float, default=35)
    args = ap.parse_args()

    repo = Path(args.repo_dir)
    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    rest = rest_runtime(runtime, repo)
    runtime_ok = rest["ping_ok"] and rest["login_ok"] and rest["show_config_ok"] and rest["status_ok"] and rest["whitelist_ok"] and rest["dry_run"] is True and rest["force_entry_enable"] is True

    candidates, input_warnings = load_all_candidates(runtime, args.max_input_age_sec)
    evaluated = []
    selected = None
    reject_counts: dict[str, int] = {}

    whitelist = set(rest.get("whitelist_pairs") or [])
    open_pairs = set(rest.get("open_pairs") or [])

    for c in candidates:
        reasons = []
        strict_ok, strict_reasons = strict_check(c)
        if not strict_ok:
            reasons.extend(strict_reasons)
        if c["pair"] not in whitelist:
            reasons.append("PAIR_NOT_IN_REST_ACTIVE_WHITELIST")
        if c["pair"] in open_pairs:
            reasons.append("PAIR_ALREADY_OPEN")
        cd = cooldown_state(repo, c["pair"], args.pair_cooldown_sec, args.repeated_pair_window_sec)
        if cd.get("cooldown_active"):
            reasons.append("RECENT_PAIR_COOLDOWN_ACTIVE")
        if cd.get("repeated_pair_active"):
            reasons.append("REPEATED_PAIR_SELECTION_ACTIVE")
        fl = flow_state(runtime, c, args.low_confluence_score)
        if fl.get("cvd_degradation"):
            reasons.append("CVD_DEGRADATION_ACTIVE")
        if fl.get("low_confluence"):
            reasons.append("LOW_CONFLUENCE_ACTIVE")

        item = {
            "candidate": c,
            "reject_reasons": list(dict.fromkeys(reasons)),
            "cooldown": cd,
            "flow": fl,
        }
        evaluated.append(item)
        for r in item["reject_reasons"]:
            reject_counts[r] = reject_counts.get(r, 0) + 1

        if not item["reject_reasons"] and selected is None:
            selected = item

    failures = []
    warnings = input_warnings
    if not runtime_ok:
        failures.append("REST_RUNTIME_HEALTH_FAIL")
    if not candidates:
        failures.append("NO_CANDIDATES_FOUND")

    if failures and "REST_RUNTIME_HEALTH_FAIL" in failures:
        final_decision = HOLD_RUNTIME
        next_action = "Patch runtime REST health only. No AQ/K/L."
    elif failures:
        final_decision = HOLD_INPUT
        next_action = "Refresh scanner/AP/AJ inputs. No AQ/K/L."
    elif selected:
        final_decision = READY
        next_action = "One clean non-cooldown strict candidate found. AQ may be considered only after explicit approval."
    else:
        final_decision = HOLD_NO_CLEAN
        next_action = "No clean non-cooldown strict candidate. Let scanner continue and audit top blockers."

    result = {
        "generated_at": now_utc(),
        "mode": MODE,
        "paper_order_allowed": False,
        "k_write_allowed": False,
        "l_execute_allowed": False,
        "forceenter_allowed": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "final_decision": final_decision,
        "next_action": next_action,
        "failures": failures,
        "warnings": warnings,
        "rest_runtime": rest,
        "candidate_count": len(candidates),
        "evaluated_count": len(evaluated),
        "selected": selected,
        "reject_reason_counts": dict(sorted(reject_counts.items(), key=lambda kv: kv[1], reverse=True)),
        "top_evaluated": evaluated[:20],
        "decision_policy": [
            "AS5 is shadow-only.",
            "AS5 does not write K.",
            "AS5 does not execute L.",
            "AS5 does not forceenter.",
            "AS5 does not create paper order.",
            "AS5 does not enable live, risk-up, or gate-loosen.",
            "AS5 selects max one clean non-cooldown strict candidate for AQ review only.",
        ],
    }

    full = runtime / f"{OUT_PREFIX}_FULL.json"
    active = runtime / f"{OUT_PREFIX}_ACTIVE.json"
    compact_path = runtime / f"{OUT_PREFIX}_COMPACT.txt"
    write_json(full, result)
    write_json(active, result)

    lines = [
        "F4X_AS5_NEXT_NON_COOLDOWN_STRICT_CANDIDATE_SELECTOR_SHADOW_ONLY_COMPACT",
        f"generated_at={result['generated_at']}",
        f"mode={MODE}",
        "paper_order=HOLD",
        "k_write=HOLD",
        "l_execute=HOLD",
        "forceenter=HOLD",
        "live=HOLD",
        "risk_up=HOLD",
        "gate_loosen=HOLD",
        "FINAL_DECISION",
        f"final_decision={final_decision}",
        f"next_action={next_action}",
        "FAILURES",
        *(failures if failures else ["NONE"]),
        "WARNINGS",
        *(warnings if warnings else ["NONE"]),
        "REST_RUNTIME",
        f"ping_ok={rest['ping_ok']}|login_ok={rest['login_ok']}|show_config_ok={rest['show_config_ok']}|status_ok={rest['status_ok']}|whitelist_ok={rest['whitelist_ok']}|dry_run={rest['dry_run']}|force_entry_enable={rest['force_entry_enable']}|open_count={rest['open_count']}|whitelist_count={rest['whitelist_count']}",
        "COUNTS",
        f"candidate_count={len(candidates)}|evaluated_count={len(evaluated)}|clean_selected={bool(selected)}",
        "SELECTED_CANDIDATE",
    ]

    if selected:
        c = selected["candidate"]
        lines.append(f"{c['pair']}|side={c['side']}|score={c.get('score')}|cvdoi={c.get('cvdoi')}|trigger={c.get('trigger')}|smc={c.get('smc')}|source={c.get('source_file')}")
    else:
        lines.append("NONE")

    lines.append("REJECT_REASON_COUNTS")
    if reject_counts:
        for k, v in sorted(reject_counts.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"{k}={v}")
    else:
        lines.append("NONE")

    _f4x_as5j2m_append_truth_attribution_section(lines, locals().get("evaluated", []), locals().get("rest", {}))
    lines.append("TOP_EVALUATED_SAMPLE")
    for item in evaluated[:15]:
        c = item["candidate"]
        lines.append(
            f"{c['pair']}|side={c['side']}|score={c.get('score')}|reason={','.join(item['reject_reasons']) if item['reject_reasons'] else 'CLEAN'}|"
            f"cooldown={item['cooldown'].get('cooldown_active')}|cvd={item['flow'].get('cvd_degradation')}|low_conf={item['flow'].get('low_confluence')}"
        )

    lines.append("DECISION_POLICY")
    lines.extend(result["decision_policy"])
    lines.append("OUTPUT_FILES")
    lines.append(f"full_json={full}")
    lines.append(f"compact={compact_path}")
    lines.append(f"active={active}")

    compact = "\n".join(lines) + "\n"
    compact_path.write_text(compact, encoding="utf-8")
    print(compact)
    return 0




# CONTROL_TOWER_F4X_AS5D_AUTO_SHARED_METRIC_CAUTION_REPORT_INTEGRATION_ONLY_START
def _f4x_as5d_read_json(path, default=None):
    try:
        from pathlib import Path as _Path
        import json as _json
        p = _Path(path)
        if p.exists():
            return _json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return default

def _f4x_as5d_write_json(path, data):
    try:
        import json as _json
        from pathlib import Path as _Path
        p = _Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps(data, indent=2, default=str), encoding="utf-8")
        return True
    except Exception:
        return False

def _f4x_as5d_detect_runtime_dir():
    try:
        import sys as _sys
        import os as _os
        from pathlib import Path as _Path

        argv = list(getattr(_sys, "argv", []) or [])
        for i, item in enumerate(argv):
            if item == "--runtime-dir" and i + 1 < len(argv):
                return _Path(argv[i + 1])
            if item.startswith("--runtime-dir="):
                return _Path(item.split("=", 1)[1])

        env_runtime = str(_os.getenv("REVO_RUNTIME_DIR", "")).strip()
        if env_runtime:
            return _Path(env_runtime)

        return _Path("/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    except Exception:
        from pathlib import Path as _Path
        return _Path("/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")

def _f4x_as5d_auto_shared_metric_caution_report(runtime=None):
    """
    AS5D report integration only.
    This function must never change AS5 final_decision, candidate selection,
    cooldown, CVD gate, low-confluence gate, K, L, forceenter, live, risk, or gate logic.
    """
    try:
        from pathlib import Path as _Path
        from datetime import datetime as _datetime, timezone as _timezone

        runtime = _Path(runtime) if runtime else _f4x_as5d_detect_runtime_dir()
        runtime.mkdir(parents=True, exist_ok=True)

        as5_active = _f4x_as5d_read_json(
            runtime / "F4X_AS5_NEXT_NON_COOLDOWN_STRICT_CANDIDATE_SELECTOR_SHADOW_ONLY_ACTIVE.json",
            {},
        ) or {}
        as5b_active = _f4x_as5d_read_json(
            runtime / "F4X_AS5B_FLOW_RECORD_LINEAGE_AND_SHARED_METRIC_DEDUP_AUDIT_ACTIVE.json",
            {},
        ) or {}
        as5c_active = _f4x_as5d_read_json(
            runtime / "F4X_AS5C_SHARED_METRIC_CAUTION_REPORT_ONLY_ACTIVE.json",
            {},
        ) or {}

        as5c_ok = None
        as5c_out = None

        try:
            if "_f4x_as5c_write_shared_metric_report" in globals():
                as5c_ok, as5c_out = globals()["_f4x_as5c_write_shared_metric_report"](runtime)
                as5c_active = _f4x_as5d_read_json(
                    runtime / "F4X_AS5C_SHARED_METRIC_CAUTION_REPORT_ONLY_ACTIVE.json",
                    {},
                ) or as5c_active
        except Exception as e:
            as5c_ok = False
            as5c_out = f"{type(e).__name__}:{e}"

        summary = as5b_active.get("summary") if isinstance(as5b_active, dict) else {}
        if not isinstance(summary, dict):
            summary = {}

        shared_caution_count = int(summary.get("shared_caution_count") or as5c_active.get("shared_caution_count") or 0)
        false_positive_count = int(summary.get("false_positive_count") or as5c_active.get("false_positive_count") or 0)
        shared_metric_group_count = int(summary.get("shared_metric_group_count") or as5c_active.get("shared_metric_group_count") or 0)
        shared_record_group_count = int(summary.get("shared_record_group_count") or as5c_active.get("shared_record_group_count") or 0)

        if false_positive_count > 0:
            final_decision = "F4X_AS5D_ATTRIBUTION_FALSE_POSITIVE_RISK_REPORT_ONLY"
            interpretation = "AS5D detected false-positive lineage risk from AS5B/AS5C. Patch report/lineage only. No AQ/K/L."
        elif shared_caution_count > 0:
            final_decision = "F4X_AS5D_AUTO_SHARED_METRIC_CAUTION_INTEGRATED"
            interpretation = "AS5 report integration active. AS5 blockers remain HOLD-valid with shared/global metric caution label."
        else:
            final_decision = "F4X_AS5D_AUTO_REPORT_NO_SHARED_CAUTION"
            interpretation = "AS5 report integration active. No shared metric caution currently detected."

        out = {
            "generated_at": _datetime.now(_timezone.utc).isoformat(),
            "mode": "AS5D_AUTO_SHARED_METRIC_CAUTION_REPORT_INTEGRATION_ONLY",
            "paper_order_allowed": False,
            "k_write_allowed": False,
            "l_execute_allowed": False,
            "forceenter_allowed": False,
            "live_allowed": False,
            "risk_up_allowed": False,
            "gate_loosen_allowed": False,
            "final_decision": final_decision,
            "interpretation": interpretation,
            "as5_final_decision_preserved": as5_active.get("final_decision"),
            "as5_next_action_preserved": as5_active.get("next_action"),
            "as5b_final_decision": as5b_active.get("final_decision"),
            "as5c_final_decision": as5c_active.get("final_decision"),
            "as5c_auto_report_ok": as5c_ok,
            "as5c_auto_report_out": as5c_out,
            "shared_caution_count": shared_caution_count,
            "false_positive_count": false_positive_count,
            "shared_metric_group_count": shared_metric_group_count,
            "shared_record_group_count": shared_record_group_count,
            "classification_counts": summary.get("classification_counts") or as5c_active.get("classification_counts"),
            "candidate_classification_sample": (
                as5b_active.get("candidate_classifications", [])[:12]
                if isinstance(as5b_active.get("candidate_classifications"), list)
                else as5c_active.get("candidate_classifications", [])[:12]
                if isinstance(as5c_active.get("candidate_classifications"), list)
                else []
            ),
            "decision_policy": [
                "AS5D is report-integration only.",
                "AS5D does not change AS5 final_decision.",
                "AS5D does not change AS5 candidate selection.",
                "AS5D does not change cooldown, CVD, low-confluence, SMC, trigger, or side-flow gates.",
                "AS5D does not write K.",
                "AS5D does not execute L.",
                "AS5D does not forceenter.",
                "AS5D does not create paper order.",
                "AS5D does not enable live, risk-up, or gate-loosen.",
            ],
        }

        full = runtime / "F4X_AS5D_AUTO_SHARED_METRIC_CAUTION_REPORT_INTEGRATION_ONLY_FULL.json"
        active = runtime / "F4X_AS5D_AUTO_SHARED_METRIC_CAUTION_REPORT_INTEGRATION_ONLY_ACTIVE.json"
        compact = runtime / "F4X_AS5D_AUTO_SHARED_METRIC_CAUTION_REPORT_INTEGRATION_ONLY_COMPACT.txt"

        _f4x_as5d_write_json(full, out)
        _f4x_as5d_write_json(active, out)

        lines = [
            "F4X_AS5D_AUTO_SHARED_METRIC_CAUTION_REPORT_INTEGRATION_ONLY_COMPACT",
            f"generated_at={out['generated_at']}",
            "mode=AS5D_AUTO_SHARED_METRIC_CAUTION_REPORT_INTEGRATION_ONLY",
            "paper_order=HOLD",
            "k_write=HOLD",
            "l_execute=HOLD",
            "forceenter=HOLD",
            "live=HOLD",
            "risk_up=HOLD",
            "gate_loosen=HOLD",
            "FINAL_DECISION",
            f"final_decision={final_decision}",
            f"interpretation={interpretation}",
            "SOURCE_CONTEXT",
            f"as5_final_decision_preserved={out['as5_final_decision_preserved']}|as5b_final_decision={out['as5b_final_decision']}|as5c_final_decision={out['as5c_final_decision']}|as5c_auto_report_ok={as5c_ok}",
            "SUMMARY",
            f"shared_caution_count={shared_caution_count}|false_positive_count={false_positive_count}|shared_metric_group_count={shared_metric_group_count}|shared_record_group_count={shared_record_group_count}|classification_counts={out['classification_counts']}",
            "CLASSIFICATION_SAMPLE",
        ]

        sample = out.get("candidate_classification_sample") or []
        if sample:
            for item in sample[:12]:
                lines.append(
                    f"{item.get('pair')}|side={item.get('side')}|score={item.get('score')}|classification={item.get('classification')}|shared_metrics={item.get('has_shared_metrics')}|pair_specific={item.get('has_pair_specific')}"
                )
        else:
            lines.append("NONE")

        lines.append("DECISION_POLICY")
        lines.extend(out["decision_policy"])
        lines.append("OUTPUT_FILES")
        lines.append(f"full_json={full}")
        lines.append(f"compact={compact}")
        lines.append(f"active={active}")

        compact.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True, str(compact)
    except Exception as e:
        try:
            runtime = _f4x_as5d_detect_runtime_dir()
            err = runtime / "F4X_AS5D_AUTO_SHARED_METRIC_CAUTION_REPORT_INTEGRATION_ONLY_ERROR.txt"
            err.write_text(f"{type(e).__name__}:{e}\n", encoding="utf-8")
        except Exception:
            pass
        return False, f"{type(e).__name__}:{e}"
# CONTROL_TOWER_F4X_AS5D_AUTO_SHARED_METRIC_CAUTION_REPORT_INTEGRATION_ONLY_END



# F4X_AS5D_BLOCKER_SCOPE_REPORTING_ONLY_PATCH START
# Reporting-only extension. This block must not alter AS5 final_decision,
# candidate selection, K, L, forceenter, live, risk, or gate logic.
def _f4x_as5d_now_utc():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _f4x_as5d_runtime_from_argv():
    from pathlib import Path
    import sys
    args = list(sys.argv)
    for i, x in enumerate(args):
        if x == "--runtime-dir" and i + 1 < len(args):
            return Path(args[i + 1])
    return Path("/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")


def _f4x_as5d_read_json(path, default=None):
    import json
    try:
        p = path
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return default


def _f4x_as5d_write_json(path, data):
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def _f4x_as5d_norm_reason(v):
    import re
    if v is None:
        return None
    s = str(v).strip().upper().replace(" ", "_").replace("-", "_")
    s = re.sub(r"[^A-Z0-9_:/]", "", s)
    return s or None


def _f4x_as5d_extract_pair(d):
    import re
    pair_re = re.compile(r"^[A-Z0-9]{2,20}/[A-Z0-9]{2,20}(:[A-Z0-9]{2,20})?$")
    if not isinstance(d, dict):
        return None
    for key in ("pair", "symbol", "market", "asset"):
        v = d.get(key)
        if isinstance(v, str) and pair_re.match(v.strip().upper()):
            return v.strip().upper()
    for v in d.values():
        if isinstance(v, str) and pair_re.match(v.strip().upper()):
            return v.strip().upper()
    raw = d.get("raw")
    if isinstance(raw, dict):
        return _f4x_as5d_extract_pair(raw)
    return None


def _f4x_as5d_extract_side(d):
    if not isinstance(d, dict):
        return None
    for key in ("side", "direction", "trade_side", "signal_side"):
        v = d.get(key)
        if isinstance(v, str) and v.strip().upper() in {"LONG", "SHORT", "BUY", "SELL"}:
            return v.strip().upper()
    raw = d.get("raw")
    if isinstance(raw, dict):
        return _f4x_as5d_extract_side(raw)
    return None


def _f4x_as5d_extract_score(d):
    if not isinstance(d, dict):
        return None
    for key in ("score", "final_score", "shadow_score", "max_score", "score_allow", "candidate_score"):
        if key in d:
            try:
                return float(d.get(key))
            except Exception:
                pass
    raw = d.get("raw")
    if isinstance(raw, dict):
        return _f4x_as5d_extract_score(raw)
    return None


def _f4x_as5d_extract_reasons(item):
    reasons = []
    if isinstance(item, dict):
        for key in (
            "reject_reasons", "reasons", "blockers", "fail_reasons",
            "deny_reasons", "hard_blockers", "soft_blockers", "reason",
        ):
            v = item.get(key)
            if isinstance(v, list):
                for x in v:
                    r = _f4x_as5d_norm_reason(x)
                    if r:
                        reasons.append(r)
            elif isinstance(v, dict):
                for kk, vv in v.items():
                    if vv is True or (isinstance(vv, (int, float)) and vv):
                        r = _f4x_as5d_norm_reason(kk)
                        if r:
                            reasons.append(r)
                    elif isinstance(vv, str):
                        r = _f4x_as5d_norm_reason(vv)
                        if r:
                            reasons.append(r)
            elif v is not None:
                r = _f4x_as5d_norm_reason(v)
                if r:
                    reasons.append(r)

        cand = item.get("candidate")
        if isinstance(cand, dict):
            for x in _f4x_as5d_extract_reasons(cand):
                reasons.append(x)

    out, seen = [], set()
    for r in reasons:
        if r and r not in seen:
            out.append(r)
            seen.add(r)
    return out


def _f4x_as5d_candidate_label(item):
    if not isinstance(item, dict):
        return {}
    cand = item.get("candidate") if isinstance(item.get("candidate"), dict) else item
    raw = cand.get("raw") if isinstance(cand, dict) and isinstance(cand.get("raw"), dict) else {}

    def pick(*names):
        for src in (cand, raw):
            if not isinstance(src, dict):
                continue
            for name in names:
                if name in src:
                    return src.get(name)
        return None

    return {
        "pair": _f4x_as5d_extract_pair(cand),
        "side": _f4x_as5d_extract_side(cand),
        "score": _f4x_as5d_extract_score(cand),
        "trigger": pick("trigger", "trigger_status"),
        "smc": pick("smc", "mapped_smc", "smc_status"),
        "flow": pick("flow", "side_flow", "latest"),
        "cvdoi": pick("cvdoi", "cvdoi_state", "cvdoi_context", "cvdoi_label"),
    }


def _f4x_as5d_scope_for_item(item, global_shared):
    shared_names = {"CVD_DEGRADATION_ACTIVE", "LOW_CONFLUENCE_ACTIVE"}
    pair_hard_names = {
        "SMC_NOT_GOOD",
        "SMC_REJECT",
        "SIDE_FLOW_NOT_STRONG",
        "TRIGGER_NOT_CONFIRMED",
        "TRIGGER_NOT_READY",
        "TRIGGER_WEAK",
        "RECENT_PAIR_COOLDOWN_ACTIVE",
        "REPEATED_PAIR_ACTIVE",
        "PAIR_NOT_IN_REST_ACTIVE_WHITELIST",
        "OPEN_TRADE_PRESENT",
        "REST_RUNTIME_HEALTH_FAIL",
    }

    reasons = _f4x_as5d_extract_reasons(item)
    global_shared_blockers = []
    local_shared_blockers = []
    pair_hard_blockers = []
    other_blockers = []

    for r in reasons:
        if r in shared_names:
            if global_shared.get(r, False):
                global_shared_blockers.append(r)
            else:
                local_shared_blockers.append(r)
        elif r in pair_hard_names:
            pair_hard_blockers.append(r)
        else:
            other_blockers.append(r)

    has_global = bool(global_shared_blockers)
    has_local_shared = bool(local_shared_blockers)
    has_pair_hard = bool(pair_hard_blockers)

    if has_pair_hard and (has_local_shared or has_global):
        blocker_scope = "MIXED_GLOBAL_AND_PAIR_LOCAL"
    elif has_pair_hard:
        blocker_scope = "PAIR_LOCAL_HARD_BLOCKER"
    elif has_local_shared:
        blocker_scope = "PAIR_LOCAL_SHARED_METRIC_HARD_BLOCKER"
    elif has_global:
        blocker_scope = "GLOBAL_SHARED_CAUTION"
    else:
        blocker_scope = "NO_LOCAL_HARD_BLOCKER_VISIBLE"

    label = _f4x_as5d_candidate_label(item)

    return {
        **label,
        "all_reasons": reasons,
        "global_cvd_caution": "CVD_DEGRADATION_ACTIVE" in global_shared_blockers,
        "global_low_confluence_caution": "LOW_CONFLUENCE_ACTIVE" in global_shared_blockers,
        "local_cvd_degradation": "CVD_DEGRADATION_ACTIVE" in local_shared_blockers,
        "local_low_confluence": "LOW_CONFLUENCE_ACTIVE" in local_shared_blockers,
        "global_shared_blockers": global_shared_blockers,
        "local_shared_blockers": local_shared_blockers,
        "pair_local_hard_blockers": pair_hard_blockers,
        "other_blockers": other_blockers,
        "blocker_scope": blocker_scope,
    }


def _f4x_as5d_write_blocker_scope_report():
    runtime = _f4x_as5d_runtime_from_argv()
    runtime.mkdir(parents=True, exist_ok=True)

    active_path = runtime / "F4X_AS5_NEXT_NON_COOLDOWN_STRICT_CANDIDATE_SELECTOR_SHADOW_ONLY_ACTIVE.json"
    full_path = runtime / "F4X_AS5_NEXT_NON_COOLDOWN_STRICT_CANDIDATE_SELECTOR_SHADOW_ONLY_FULL.json"

    active = _f4x_as5d_read_json(active_path, {}) or {}
    full = _f4x_as5d_read_json(full_path, {}) or {}

    candidate_count = active.get("candidate_count")
    if not isinstance(candidate_count, int):
        candidate_count = full.get("candidate_count")
    if not isinstance(candidate_count, int):
        candidate_count = 0

    reject_counts = active.get("reject_reason_counts")
    if not isinstance(reject_counts, dict):
        reject_counts = full.get("reject_reason_counts")
    if not isinstance(reject_counts, dict):
        reject_counts = {}

    global_shared = {}
    for reason in ("CVD_DEGRADATION_ACTIVE", "LOW_CONFLUENCE_ACTIVE"):
        try:
            cnt = int(reject_counts.get(reason, 0))
        except Exception:
            cnt = 0
        global_shared[reason] = bool(candidate_count and cnt >= candidate_count)

    evaluated = []
    for src_name, src in (("active", active), ("full", full)):
        top = src.get("top_evaluated") if isinstance(src, dict) else None
        if isinstance(top, list):
            for i, item in enumerate(top):
                if isinstance(item, dict):
                    evaluated.append({
                        "source": src_name,
                        "index": i,
                        **_f4x_as5d_scope_for_item(item, global_shared),
                    })

    scope_counts = {}
    for x in evaluated:
        key = x.get("blocker_scope") or "UNKNOWN"
        scope_counts[key] = scope_counts.get(key, 0) + 1

    report = {
        "event": "F4X_AS5D_BLOCKER_SCOPE_REPORTING_ONLY",
        "generated_at": _f4x_as5d_now_utc(),
        "mode": "AS5D_BLOCKER_SCOPE_REPORTING_ONLY",
        "reporting_only": True,
        "paper_order_allowed": False,
        "k_write_allowed": False,
        "l_execute_allowed": False,
        "forceenter_allowed": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "as5_final_decision": active.get("final_decision"),
        "candidate_count": candidate_count,
        "reject_reason_counts": reject_counts,
        "global_shared_caution": {
            "global_cvd_caution": bool(global_shared.get("CVD_DEGRADATION_ACTIVE")),
            "global_low_confluence_caution": bool(global_shared.get("LOW_CONFLUENCE_ACTIVE")),
            "reasoning": "A shared blocker is marked global when its reject count is >= AS5 candidate_count for this AS5 run.",
        },
        "scope_counts": scope_counts,
        "evaluated_scope": evaluated,
        "decision_policy": [
            "AS5D is reporting-only.",
            "AS5D does not change AS5 final_decision.",
            "AS5D does not change selected candidate.",
            "AS5D does not loosen CVD or low-confluence gates.",
            "AS5D does not write K.",
            "AS5D does not execute L.",
            "AS5D does not forceenter, create paper orders, enable live, risk-up, or gate-loosen.",
        ],
    }

    out_full = runtime / "F4X_AS5D_BLOCKER_SCOPE_REPORTING_ONLY_FULL.json"
    out_active = runtime / "F4X_AS5D_BLOCKER_SCOPE_REPORTING_ONLY_ACTIVE.json"
    out_compact = runtime / "F4X_AS5D_BLOCKER_SCOPE_REPORTING_ONLY_COMPACT.txt"

    _f4x_as5d_write_json(out_full, report)
    _f4x_as5d_write_json(out_active, report)

    lines = [
        "F4X_AS5D_BLOCKER_SCOPE_REPORTING_ONLY_COMPACT",
        f"generated_at={report['generated_at']}",
        f"mode={report['mode']}",
        "paper_order=HOLD",
        "k_write=HOLD",
        "l_execute=HOLD",
        "forceenter=HOLD",
        "live=HOLD",
        "risk_up=HOLD",
        "gate_loosen=HOLD",
        "FINAL_DECISION",
        f"as5_final_decision={report.get('as5_final_decision')}",
        "GLOBAL_SHARED_CAUTION",
        str(report["global_shared_caution"]),
        "SCOPE_COUNTS",
        str(scope_counts),
        "EVALUATED_SCOPE_TOP",
    ]

    for i, row in enumerate(evaluated[:20], 1):
        lines.append(
            f"{i}. pair={row.get('pair')}|side={row.get('side')}|score={row.get('score')}|scope={row.get('blocker_scope')}|global_cvd={row.get('global_cvd_caution')}|global_low={row.get('global_low_confluence_caution')}|local_cvd={row.get('local_cvd_degradation')}|local_low={row.get('local_low_confluence')}|pair_hard={row.get('pair_local_hard_blockers')}|other={row.get('other_blockers')}"
        )

    lines.extend([
        "DECISION_POLICY",
        *report["decision_policy"],
        "OUTPUT_FILES",
        f"full_json={out_full}",
        f"active={out_active}",
        f"compact={out_compact}",
    ])
    out_compact.write_text("\n".join(lines) + "\n", encoding="utf-8")
# F4X_AS5D_BLOCKER_SCOPE_REPORTING_ONLY_PATCH END



# F4X_AS5D1_ATEXIT_BLOCKER_SCOPE_REPORT_HOOK_PATCH START
# Reporting-only hook. Runs after AS5 writes normal ACTIVE/FULL output.
# Must not change AS5 final_decision, candidate selection, gates, K, L, forceenter, live, or risk.
try:
    import atexit as _f4x_as5d1_atexit
    if "_f4x_as5d_write_blocker_scope_report" in globals():
        _f4x_as5d1_atexit.register(globals()["_f4x_as5d_write_blocker_scope_report"])
except Exception:
    pass
# F4X_AS5D1_ATEXIT_BLOCKER_SCOPE_REPORT_HOOK_PATCH END


if __name__ == "__main__":
    _f4x_as5d_exit_code = main()
    try:
        if _f4x_as5d_exit_code is None:
            _f4x_as5d_exit_code = 0
        _f4x_as5d_runtime = _f4x_as5d_detect_runtime_dir()
        _f4x_as5d_ok, _f4x_as5d_out = _f4x_as5d_auto_shared_metric_caution_report(_f4x_as5d_runtime)
        try:
            print(f"AS5D_REPORT_OK={_f4x_as5d_ok}")
            print(f"AS5D_REPORT_OUT={_f4x_as5d_out}")
        except Exception:
            pass
    except Exception as _f4x_as5d_e:
        try:
            print(f"AS5D_REPORT_ERROR={type(_f4x_as5d_e).__name__}:{_f4x_as5d_e}")
        except Exception:
            pass
    raise SystemExit(_f4x_as5d_exit_code)


# CONTROL_TOWER_F4X_AS5C_SHARED_METRIC_CAUTION_REPORT_ONLY_START
def _f4x_as5c_load_json(path):
    try:
        from pathlib import Path as _Path
        p = _Path(path)
        if p.exists():
            import json as _json
            return _json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return {}

def _f4x_as5c_write_shared_metric_report(runtime):
    """
    AS5C report-only helper.
    It reads AS5B lineage/dedup output and emits a compact caution report.
    It must not affect candidate selection, K writing, L execution, forceenter,
    live, risk, or gate logic.
    """
    try:
        from pathlib import Path as _Path
        from datetime import datetime as _datetime, timezone as _timezone
        import json as _json

        runtime = _Path(runtime)
        as5b = _f4x_as5c_load_json(runtime / "F4X_AS5B_FLOW_RECORD_LINEAGE_AND_SHARED_METRIC_DEDUP_AUDIT_ACTIVE.json")
        as5 = _f4x_as5c_load_json(runtime / "F4X_AS5_NEXT_NON_COOLDOWN_STRICT_CANDIDATE_SELECTOR_SHADOW_ONLY_ACTIVE.json")

        summary = as5b.get("summary") if isinstance(as5b, dict) else {}
        classifications = as5b.get("candidate_classifications") if isinstance(as5b, dict) else []
        shared_metric_groups = as5b.get("shared_metric_groups") if isinstance(as5b, dict) else []
        shared_record_groups = as5b.get("shared_record_groups") if isinstance(as5b, dict) else []

        shared_caution_count = int((summary or {}).get("shared_caution_count") or 0)
        false_positive_count = int((summary or {}).get("false_positive_count") or 0)
        shared_metric_group_count = int((summary or {}).get("shared_metric_group_count") or len(shared_metric_groups or []))
        shared_record_group_count = int((summary or {}).get("shared_record_group_count") or len(shared_record_groups or []))

        if false_positive_count > 0:
            decision = "F4X_AS5C_REPORT_FALSE_POSITIVE_RISK_PATCH_AS5_LINEAGE_ONLY"
            interpretation = "AS5B found false-positive attribution risk. Patch AS5 lineage/report only. No AQ/K/L."
        elif shared_caution_count > 0:
            decision = "F4X_AS5C_SHARED_METRIC_CAUTION_REPORT_READY"
            interpretation = "AS5 blockers remain HOLD-valid, but report must label shared/global metric caution."
        else:
            decision = "F4X_AS5C_NO_SHARED_CAUTION_REPORT_READY"
            interpretation = "No shared metric caution detected. AS5 report can remain standard."

        out = {
            "generated_at": _datetime.now(_timezone.utc).isoformat(),
            "mode": "AS5C_SHARED_METRIC_CAUTION_REPORT_ONLY",
            "paper_order_allowed": False,
            "k_write_allowed": False,
            "l_execute_allowed": False,
            "forceenter_allowed": False,
            "live_allowed": False,
            "risk_up_allowed": False,
            "gate_loosen_allowed": False,
            "final_decision": decision,
            "interpretation": interpretation,
            "as5_final_decision": as5.get("final_decision") if isinstance(as5, dict) else None,
            "as5b_final_decision": as5b.get("final_decision") if isinstance(as5b, dict) else None,
            "shared_caution_count": shared_caution_count,
            "false_positive_count": false_positive_count,
            "shared_metric_group_count": shared_metric_group_count,
            "shared_record_group_count": shared_record_group_count,
            "classification_counts": (summary or {}).get("classification_counts"),
            "candidate_classifications": classifications[:20] if isinstance(classifications, list) else [],
            "top_shared_metric_groups": shared_metric_groups[:20] if isinstance(shared_metric_groups, list) else [],
            "top_shared_record_groups": shared_record_groups[:10] if isinstance(shared_record_groups, list) else [],
            "decision_policy": [
                "AS5C is report-only.",
                "AS5C does not change AS5 selection gates.",
                "AS5C does not write K.",
                "AS5C does not execute L.",
                "AS5C does not forceenter.",
                "AS5C does not create paper order.",
                "AS5C does not enable live, risk-up, or gate-loosen.",
                "Shared metrics must be labeled as caution, not independent pair-local proof.",
            ],
        }

        full = runtime / "F4X_AS5C_SHARED_METRIC_CAUTION_REPORT_ONLY_FULL.json"
        active = runtime / "F4X_AS5C_SHARED_METRIC_CAUTION_REPORT_ONLY_ACTIVE.json"
        compact = runtime / "F4X_AS5C_SHARED_METRIC_CAUTION_REPORT_ONLY_COMPACT.txt"

        full.write_text(_json.dumps(out, indent=2, default=str), encoding="utf-8")
        active.write_text(_json.dumps(out, indent=2, default=str), encoding="utf-8")

        lines = [
            "F4X_AS5C_SHARED_METRIC_CAUTION_REPORT_ONLY_COMPACT",
            f"generated_at={out['generated_at']}",
            "mode=AS5C_SHARED_METRIC_CAUTION_REPORT_ONLY",
            "paper_order=HOLD",
            "k_write=HOLD",
            "l_execute=HOLD",
            "forceenter=HOLD",
            "live=HOLD",
            "risk_up=HOLD",
            "gate_loosen=HOLD",
            "FINAL_DECISION",
            f"final_decision={decision}",
            f"interpretation={interpretation}",
            "SOURCE_CONTEXT",
            f"as5_final_decision={out['as5_final_decision']}|as5b_final_decision={out['as5b_final_decision']}",
            "SUMMARY",
            f"shared_caution_count={shared_caution_count}|false_positive_count={false_positive_count}|shared_metric_group_count={shared_metric_group_count}|shared_record_group_count={shared_record_group_count}|classification_counts={out['classification_counts']}",
            "CLASSIFICATION_SAMPLE",
        ]

        if isinstance(classifications, list) and classifications:
            for item in classifications[:12]:
                lines.append(
                    f"{item.get('pair')}|side={item.get('side')}|score={item.get('score')}|classification={item.get('classification')}|shared_metrics={item.get('has_shared_metrics')}|pair_specific={item.get('has_pair_specific')}"
                )
        else:
            lines.append("NONE")

        lines.append("TOP_SHARED_METRIC_GROUPS")
        if isinstance(shared_metric_groups, list) and shared_metric_groups:
            for g in shared_metric_groups[:10]:
                lines.append(
                    f"{g.get('family')}|{g.get('norm_key')}={g.get('value')}|pair_count={g.get('pair_count')}|pairs={str(g.get('pairs') or [])[:180]}"
                )
        else:
            lines.append("NONE")

        lines.append("DECISION_POLICY")
        lines.extend(out["decision_policy"])
        lines.append("OUTPUT_FILES")
        lines.append(f"full_json={full}")
        lines.append(f"compact={compact}")
        lines.append(f"active={active}")

        compact.write_text("\\n".join(lines) + "\\n", encoding="utf-8")
        return True, str(compact)
    except Exception as e:
        try:
            err = runtime / "F4X_AS5C_SHARED_METRIC_CAUTION_REPORT_ONLY_ERROR.txt"
            err.write_text(f"{type(e).__name__}:{e}\\n", encoding="utf-8")
        except Exception:
            pass
        return False, str(e)
# CONTROL_TOWER_F4X_AS5C_SHARED_METRIC_CAUTION_REPORT_ONLY_END

