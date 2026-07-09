#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT_PREFIX = "F4X_AP_AUTONOMOUS_SCANNER_DRIVEN_NEXT_CANDIDATE_LOOP_SHADOW"
MODE = "AUTONOMOUS_SCANNER_DRIVEN_NEXT_CANDIDATE_LOOP_SHADOW_ONLY"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def norm_pair(v: Any) -> str:
    return str(v or "").strip()


def norm_side(v: Any) -> str:
    s = str(v or "").strip().upper()
    if s in {"LONG", "BUY", "LONG_ONLY"}:
        return "LONG"
    if s in {"SHORT", "SELL", "SHORT_ONLY"}:
        return "SHORT"
    return ""


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


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
        "whitelist_pairs": [],
        "open_trades": [],
        "open_pairs": [],
        "dry_run": None,
        "force_entry_enable": None,
        "max_open_trades": None,
        "stake_amount": None,
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
        if isinstance(body, dict):
            out["dry_run"] = body.get("dry_run")
            out["force_entry_enable"] = body.get("force_entry_enable")
            out["max_open_trades"] = body.get("max_open_trades")
            out["stake_amount"] = body.get("stake_amount")
            out["show_config"] = body

        ok, status, body = request_json(rest_url + "/api/v1/status", headers=headers)
        out["status_ok"] = bool(ok and status == 200)
        out["status_status"] = status
        out["status_body"] = body

        open_trades: list[dict[str, Any]] = []
        if isinstance(body, list):
            open_trades = [x for x in body if isinstance(x, dict)]
        elif isinstance(body, dict):
            raw = body.get("trades") or body.get("open_trades") or body.get("data") or []
            if isinstance(raw, list):
                open_trades = [x for x in raw if isinstance(x, dict)]
        out["open_trades"] = open_trades
        out["open_pairs"] = sorted(set(norm_pair(x.get("pair")) for x in open_trades if norm_pair(x.get("pair"))))

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


def walk_candidate_dicts(obj: Any, source_path: str, key_path: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    if isinstance(obj, dict):
        has_pair = any(k in obj for k in ["pair", "order_pair"])
        has_side = any(k in obj for k in ["side", "order_side", "direction"])
        has_score = "score" in obj

        if has_pair and has_side and has_score:
            row = dict(obj)
            row["_source_path"] = source_path
            row["_key_path"] = key_path
            rows.append(row)

        for k, v in obj.items():
            rows.extend(walk_candidate_dicts(v, source_path, f"{key_path}.{k}" if key_path else str(k)))

    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            rows.extend(walk_candidate_dicts(v, source_path, f"{key_path}[{i}]"))

    return rows


def collect_scanner_candidates(runtime: Path) -> list[dict[str, Any]]:
    paths = [
        runtime / "F4X_AJ_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_CONVEYOR_SHADOW_ACTIVE.json",
        runtime / "F4X_AJ_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_CONVEYOR_SHADOW_FULL.json",
    ]

    rows: list[dict[str, Any]] = []
    for p in paths:
        data = read_json(p, {})
        if isinstance(data, dict):
            rows.extend(walk_candidate_dicts(data, str(p)))

    dedup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        pair = norm_pair(row.get("pair") or row.get("order_pair"))
        side = norm_side(row.get("side") or row.get("order_side") or row.get("direction"))
        source = str(row.get("_source_path") or "")
        key = (pair, side, source)
        if pair and side:
            old = dedup.get(key)
            if old is None or safe_float(row.get("score")) > safe_float(old.get("score")):
                dedup[key] = row

    return list(dedup.values())


def load_recent_closed_trades(repo: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}

    db_paths = sorted((repo / "user_data").glob("*.sqlite"))
    for db in db_paths:
        try:
            con = sqlite3.connect(str(db))
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                select pair, is_open, open_date, close_date, close_rate, close_profit,
                       close_profit_abs, enter_tag, exit_reason
                from trades
                where is_open = 0 and close_date is not null
                order by close_date desc
                limit 200
                """
            ).fetchall()
            con.close()
        except Exception:
            continue

        for r in rows:
            pair = norm_pair(r["pair"])
            if not pair or pair in latest:
                continue
            latest[pair] = dict(r)
            latest[pair]["_db"] = str(db)

    return latest


def parse_dt_utc(v: Any) -> datetime | None:
    if not v:
        return None
    s = str(v).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        try:
            dt = datetime.fromisoformat(s.replace(" ", "T") + "+00:00")
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def strict_candidate_check(
    row: dict[str, Any],
    rest_whitelist: set[str],
    open_pairs: set[str],
    latest_closed: dict[str, dict[str, Any]],
    close_cooldown_sec: int,
    min_score: float,
) -> tuple[bool, list[str], dict[str, Any]]:
    reasons: list[str] = []

    pair = norm_pair(row.get("pair") or row.get("order_pair"))
    side = norm_side(row.get("side") or row.get("order_side") or row.get("direction"))
    score = safe_float(row.get("score"))

    cvdoi = str(row.get("cvdoi") or "")
    trigger = str(row.get("trigger") or "")
    smc = str(row.get("smc") or "")
    latest = str(row.get("latest") or row.get("latest_before") or row.get("latest_block") or "")
    paper_action = str(row.get("paper_action") or row.get("paper_action_before") or "")
    reason_text = str(row.get("reason") or row.get("shadow_reason") or row.get("replay_decision") or "")
    shadow_lane = str(row.get("shadow_lane") or row.get("replay_lane") or "")
    source_path = str(row.get("_source_path") or "")
    key_path = str(row.get("_key_path") or "")

    if not pair:
        reasons.append("PAIR_MISSING")
    if side not in {"LONG", "SHORT"}:
        reasons.append("SIDE_INVALID")
    if score < min_score:
        reasons.append("SCORE_BELOW_MIN")
    if trigger != "TRIGGER_CONFIRMED":
        reasons.append("TRIGGER_NOT_CONFIRMED")
    if not smc.startswith("SMC_GOOD_LOCATION"):
        reasons.append("SMC_NOT_GOOD")

    side_flow_ok = False
    if side == "LONG":
        side_flow_ok = (
            "BULLISH_CONTINUATION_STRONG" in cvdoi
            or "SHORT_SQUEEZE" in cvdoi
            or "LONG_STRONG_FLOW" in cvdoi
        )
    elif side == "SHORT":
        side_flow_ok = (
            "BEARISH_CONTINUATION_STRONG" in cvdoi
            or "LONG_UNWIND" in cvdoi
            or "SHORT_STRONG_FLOW" in cvdoi
        )
    if not side_flow_ok:
        reasons.append("SIDE_FLOW_NOT_STRONG")

    stale_shadow_exception = (
        "STALE_STICKY" in reason_text
        or "STALE_STICKY" in shadow_lane
        or "ENTRY_READY_REVIEW_SHADOW" in shadow_lane
        or "F4X_AJ" in source_path
    )
    if latest in {"FLOW_DIRECTION_BLOCK", "DENY_HARD"} and not stale_shadow_exception:
        reasons.append("LATEST_HARD_BLOCK_WITHOUT_STALE_STICKY_EXCEPTION")

    if rest_whitelist and pair not in rest_whitelist:
        reasons.append("PAIR_NOT_IN_REST_ACTIVE_WHITELIST")

    if pair in open_pairs:
        reasons.append("PAIR_ALREADY_OPEN")

    closed = latest_closed.get(pair)
    close_age_sec = None
    if closed:
        close_dt = parse_dt_utc(closed.get("close_date"))
        if close_dt:
            close_age_sec = (datetime.now(timezone.utc) - close_dt).total_seconds()
            if close_age_sec < close_cooldown_sec:
                reasons.append("RECENTLY_CLOSED_COOLDOWN_ACTIVE")

    normalized = {
        "pair": pair,
        "side": side,
        "score": score,
        "cvdoi": cvdoi,
        "trigger": trigger,
        "smc": smc,
        "latest": latest,
        "paper_action": paper_action,
        "reason": reason_text,
        "shadow_lane": shadow_lane,
        "source_path": source_path,
        "key_path": key_path,
        "close_age_sec": close_age_sec,
    }

    return len(reasons) == 0, reasons, normalized


def rank_candidate(row: dict[str, Any]) -> float:
    score = safe_float(row.get("score"))
    source = str(row.get("_source_path") or "")
    key_path = str(row.get("_key_path") or "")
    bonus = 0.0
    if "ACTIVE" in source:
        bonus += 1000.0
    if "selected" in key_path.lower():
        bonus += 500.0
    if "strict" in key_path.lower():
        bonus += 200.0
    if "F4X_AJ" in source:
        bonus += 100.0
    return score + bonus


def compact_text(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("F4X_AP_AUTONOMOUS_SCANNER_DRIVEN_NEXT_CANDIDATE_LOOP_SHADOW_COMPACT")
    lines.append(f"generated_at={result['generated_at']}")
    lines.append(f"mode={result['mode']}")
    lines.append("paper_order=HOLD")
    lines.append("k_write=HOLD")
    lines.append("live=HOLD")
    lines.append("risk_up=HOLD")
    lines.append("gate_loosen=HOLD")
    lines.append("entry_from_watch_recheck_deny=HOLD")
    lines.append("FINAL_DECISION")
    lines.append(f"final_decision={result['final_decision']}")
    lines.append(f"next_action={result['next_action']}")

    lines.append("RUNTIME_CHECKS")
    rc = result["runtime_checks"]
    lines.append(
        "rest_ping_ok={}|rest_login_ok={}|rest_show_config_ok={}|rest_status_ok={}|rest_whitelist_ok={}".format(
            rc["rest_ping_ok"],
            rc["rest_login_ok"],
            rc["rest_show_config_ok"],
            rc["rest_status_ok"],
            rc["rest_whitelist_ok"],
        )
    )
    lines.append(
        "dry_run={}|force_entry_enable={}|active_whitelist_count={}|open_trade_count={}|open_pairs={}".format(
            rc["dry_run"],
            rc["force_entry_enable"],
            rc["active_whitelist_count"],
            rc["open_trade_count"],
            ",".join(rc["open_pairs"][:20]) if rc["open_pairs"] else "NONE",
        )
    )

    lines.append("COUNTS")
    for k, v in result["counts"].items():
        lines.append(f"{k}={v}")

    lines.append("SELECTED_SHADOW_CANDIDATE")
    sc = result.get("selected_candidate")
    if sc:
        lines.append(
            "{}|side={}|score={}|rank={}|source={}|cvdoi={}|trigger={}|smc={}|latest={}|reason={}".format(
                sc.get("pair"),
                sc.get("side"),
                sc.get("score"),
                sc.get("rank"),
                Path(str(sc.get("source_path") or "")).name,
                sc.get("cvdoi"),
                sc.get("trigger"),
                sc.get("smc"),
                sc.get("latest"),
                sc.get("selection_reason"),
            )
        )
    else:
        lines.append("NONE")

    lines.append("REJECT_REASON_COUNTS")
    rrc = result.get("reject_reason_counts") or {}
    if rrc:
        for k, v in rrc.items():
            lines.append(f"{k}={v}")
    else:
        lines.append("NONE")

    lines.append("TOP_REJECTED_SAMPLE")
    sample = result.get("top_rejected_sample") or []
    if sample:
        for x in sample[:20]:
            lines.append(
                "{}|side={}|score={}|reason={}|source={}".format(
                    x.get("pair"),
                    x.get("side"),
                    x.get("score"),
                    ",".join(x.get("reasons", [])),
                    Path(str(x.get("source_path") or "")).name,
                )
            )
    else:
        lines.append("NONE")

    lines.append("DECISION_POLICY")
    lines.append("AP is shadow-only.")
    lines.append("AP does not write real F4X-K active signal.")
    lines.append("AP does not create paper order.")
    lines.append("AP selects at most one REST-active-whitelist strict scanner candidate.")
    lines.append("AP blocks same-pair open trade and recent close cooldown.")
    lines.append("No live. No risk-up. No gate-loosen.")
    lines.append("OUTPUT_FILES")
    for k, v in result["output_files"].items():
        lines.append(f"{k}={v}")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default=os.environ.get("REVO_RUNTIME_DIR", "/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"))
    ap.add_argument("--min-score", type=float, default=55.0)
    ap.add_argument("--close-cooldown-sec", type=int, default=1800)
    args = ap.parse_args()

    repo = Path(args.repo_dir)
    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    rest = rest_probe(runtime)
    rest_whitelist = set(rest.get("whitelist_pairs") or [])
    open_pairs = set(rest.get("open_pairs") or [])
    latest_closed = load_recent_closed_trades(repo)

    failures: list[str] = []
    warnings: list[str] = []

    if not rest.get("ping_ok"):
        failures.append("REST_PING_NOT_OK")
    if not rest.get("login_ok"):
        failures.append("REST_LOGIN_NOT_OK")
    if not rest.get("show_config_ok"):
        failures.append("REST_SHOW_CONFIG_NOT_OK")
    if rest.get("dry_run") is not True:
        failures.append("REST_DRY_RUN_NOT_TRUE")
    if rest.get("force_entry_enable") is not True:
        warnings.append("FORCE_ENTRY_ENABLE_NOT_TRUE_FOR_FUTURE_AQ")
    if not rest.get("whitelist_ok") or not rest_whitelist:
        failures.append("REST_ACTIVE_WHITELIST_UNAVAILABLE")

    raw_candidates = collect_scanner_candidates(runtime)

    valid: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    reject_counter: Counter[str] = Counter()

    for row in raw_candidates:
        ok, reasons, n = strict_candidate_check(
            row=row,
            rest_whitelist=rest_whitelist,
            open_pairs=open_pairs,
            latest_closed=latest_closed,
            close_cooldown_sec=args.close_cooldown_sec,
            min_score=args.min_score,
        )
        if ok:
            n["rank"] = rank_candidate(row)
            n["selection_reason"] = "STRICT_SCANNER_CANDIDATE_REST_ACTIVE_WHITELIST_VALID_SHADOW_ONLY"
            valid.append(n)
        else:
            for r in reasons:
                reject_counter[r] += 1
            n["reasons"] = reasons
            rejected.append(n)

    valid.sort(key=lambda x: safe_float(x.get("rank")), reverse=True)
    selected = valid[0] if valid else None

    if failures:
        final_decision = "F4X_AP_AUTONOMOUS_LOOP_HEALTH_FAIL_HOLD"
        next_action = "PATCH_FAILED_RUNTIME_SOURCE_BEFORE_AQ"
    elif selected:
        final_decision = "F4X_AP_NEXT_STRICT_SCANNER_CANDIDATE_READY_FOR_AQ_SHADOW_ONLY"
        next_action = "APPROVE_F4X_AQ_AUTONOMOUS_SCANNER_STRICT_K_INTENT_DRYRUN_PATCH"
    else:
        final_decision = "F4X_AP_NO_STRICT_CANDIDATE_HOLD_SCANNER_CONTINUE"
        next_action = "WAIT_NEXT_SCANNER_CYCLE_OR_PATCH_SELECTION_SOURCE"

    result = {
        "generated_at": now_utc(),
        "mode": MODE,
        "active": True,
        "paper_order_allowed": False,
        "k_write_allowed": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "entry_from_watch_recheck_deny_allowed": False,
        "final_decision": final_decision,
        "next_action": next_action,
        "failures": failures,
        "warnings": warnings,
        "runtime_checks": {
            "rest_ping_ok": rest.get("ping_ok"),
            "rest_login_ok": rest.get("login_ok"),
            "rest_show_config_ok": rest.get("show_config_ok"),
            "rest_status_ok": rest.get("status_ok"),
            "rest_whitelist_ok": rest.get("whitelist_ok"),
            "dry_run": rest.get("dry_run"),
            "force_entry_enable": rest.get("force_entry_enable"),
            "active_whitelist_count": len(rest_whitelist),
            "open_trade_count": len(rest.get("open_trades") or []),
            "open_pairs": sorted(open_pairs),
            "max_open_trades": rest.get("max_open_trades"),
            "stake_amount": rest.get("stake_amount"),
        },
        "counts": {
            "raw_scanner_candidate_count": len(raw_candidates),
            "valid_strict_candidate_count": len(valid),
            "rejected_candidate_count": len(rejected),
            "selected_candidate_count": 1 if selected else 0,
        },
        "selected_candidate": selected,
        "reject_reason_counts": dict(reject_counter.most_common()),
        "top_rejected_sample": rejected[:30],
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
