#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MODE = "SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_CONVEYOR_SHADOW_ONLY"
OUT_PREFIX = "F4X_AJ_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_CONVEYOR_SHADOW"

EXCLUDE_CANDIDATE_FILE_PREFIXES = (
    "F4X_AA_",
    "F4X_AB_",
    "F4X_AC_",
    "F4X_AD_",
    OUT_PREFIX,
)

HARD_LATEST_TOKENS = (
    "FLOW_DIRECTION_BLOCK",
    "AVOID_TRAP",
    "TRAP_WARNING",
    "HARD_REJECT",
    "WAIT_LOCATION",
    "WAIT_TRIGGER",
    "OBSERVE_OK_NOT_ALLOWED",
    "DENY_HARD",
)

ENTRY_READY_TOKENS = (
    "ENTRY_READY",
    "ALLOW_PAPER_ENTRY",
    "WOULD_ORDER",
    "READY",
)

GOOD_SMC_LONG = {
    "SMC_GOOD_LOCATION_LONG",
}

GOOD_SMC_SHORT = {
    "SMC_GOOD_LOCATION_SHORT",
}

STRONG_LONG_FLOW = {
    "BULLISH_CONTINUATION_STRONG",
    "LONG_FLOW",
    "STRONG_LONG_FLOW",
    "BULLISH_CONTINUATION",
}

STRONG_SHORT_FLOW = {
    "BEARISH_CONTINUATION_STRONG",
    "SHORT_FLOW",
    "STRONG_SHORT_FLOW",
    "BEARISH_CONTINUATION",
    "LONG_UNWIND",
}


def utc_now() -> str:
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


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        if isinstance(v, bool):
            return default
        return float(v)
    except Exception:
        return default


def as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y", "ok"}
    return False


def norm_side(v: Any) -> str:
    s = str(v or "").strip().upper()
    if s in {"BUY", "LONG_ONLY", "LONG"}:
        return "LONG"
    if s in {"SELL", "SHORT_ONLY", "SHORT"}:
        return "SHORT"
    return ""


def norm_pair(v: Any) -> str:
    return str(v or "").strip()


def get_first(d: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def listify(v: Any) -> list[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, tuple):
        return list(v)
    if isinstance(v, str):
        if "," in v:
            return [x.strip() for x in v.split(",") if x.strip()]
        return [v]
    return [v]


def token_has(text: Any, tokens: tuple[str, ...] | set[str]) -> bool:
    t = str(text or "").upper()
    return any(tok in t for tok in tokens)


def is_trigger_confirmed(v: Any) -> bool:
    return str(v or "").upper() == "TRIGGER_CONFIRMED"


def is_smc_good(side: str, smc: Any) -> bool:
    s = str(smc or "").upper()
    if side == "LONG":
        return s in GOOD_SMC_LONG
    if side == "SHORT":
        return s in GOOD_SMC_SHORT
    return False


def is_strong_flow_for_side(side: str, cvdoi: Any) -> bool:
    c = str(cvdoi or "").upper()
    if side == "LONG":
        return c in STRONG_LONG_FLOW
    if side == "SHORT":
        return c in STRONG_SHORT_FLOW
    return False


def is_side_supported(side: str, row: dict[str, Any]) -> bool:
    if as_bool(row.get("side_aligned_strong")):
        return True
    align = str(row.get("align") or row.get("alignment") or "").upper()
    if align == "SUPPORTS_SIDE_STRONG":
        return True
    return is_strong_flow_for_side(side, row.get("cvdoi"))


def is_latest_hard_block(latest: Any) -> bool:
    return token_has(latest, HARD_LATEST_TOKENS)


def is_latest_ready(latest: Any) -> bool:
    return token_has(latest, ENTRY_READY_TOKENS)


def is_paper_allow(row: dict[str, Any]) -> bool:
    if as_bool(row.get("allow_paper_entry")):
        return True
    pa = str(row.get("paper_action") or row.get("paper_action_before") or row.get("intent_state") or "").upper()
    return pa == "ALLOW_PAPER_ENTRY"


def source_allowed(path: Path) -> bool:
    name = path.name
    if not name.endswith(".json"):
        return False
    if not (name.startswith("F4X_") or name.startswith("revo_")):
        return False
    for p in EXCLUDE_CANDIDATE_FILE_PREFIXES:
        if name.startswith(p):
            return False
    if name.endswith("_COMPACT.json"):
        return False
    return True


def read_env_file(runtime: Path) -> dict[str, str]:
    env_path = runtime / "F4X_AE2_REST_API_ENV.sh"
    out: dict[str, str] = {}
    if not env_path.exists():
        return out
    txt = env_path.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r'export\s+([A-Za-z0-9_]+)="([^"]*)"', txt):
        out[m.group(1)] = m.group(2)
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


def get_rest_context(repo: Path, runtime: Path, config: dict[str, Any]) -> dict[str, Any]:
    env = read_env_file(runtime)
    api = config.get("api_server") if isinstance(config.get("api_server"), dict) else {}

    rest_url = env.get("F4X_L_REST_URL") or f"http://127.0.0.1:{api.get('listen_port', 8080)}"
    user = env.get("F4X_L_REST_USER") or str(api.get("username") or "")
    pw = env.get("F4X_L_REST_PASS") or str(api.get("password") or "")

    ctx: dict[str, Any] = {
        "rest_url": rest_url,
        "token_login_ok": False,
        "ping_ok": False,
        "show_config_ok": False,
        "status_ok": False,
        "whitelist_ok": False,
        "dry_run_verified": False,
        "force_entry_enable_verified": False,
        "open_trades": [],
        "rest_whitelist": [],
        "errors": [],
    }

    ok, status, body = request_json(rest_url.rstrip("/") + "/api/v1/ping")
    ctx["ping_ok"] = bool(ok and status == 200)
    ctx["ping_status"] = status
    ctx["ping_body"] = body

    token = ""
    if user and pw:
        basic = base64.b64encode(f"{user}:{pw}".encode("utf-8")).decode("ascii")
        ok, status, body = request_json(
            rest_url.rstrip("/") + "/api/v1/token/login",
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
        ok, status, body = request_json(rest_url.rstrip("/") + "/api/v1/show_config", headers=headers)
        ctx["show_config_ok"] = bool(ok and status == 200 and isinstance(body, dict))
        ctx["show_config_status"] = status
        ctx["show_config"] = body if isinstance(body, dict) else {}
        if isinstance(body, dict):
            ctx["dry_run_verified"] = body.get("dry_run") is True
            ctx["force_entry_enable_verified"] = body.get("force_entry_enable") is True

        ok, status, body = request_json(rest_url.rstrip("/") + "/api/v1/status", headers=headers)
        ctx["status_ok"] = bool(ok and status == 200)
        ctx["status_status"] = status
        if isinstance(body, list):
            ctx["open_trades"] = body
        elif isinstance(body, dict):
            ctx["open_trades"] = body.get("trades") or body.get("data") or []

        ok, status, body = request_json(rest_url.rstrip("/") + "/api/v1/whitelist", headers=headers)
        ctx["whitelist_ok"] = bool(ok and status == 200)
        ctx["whitelist_status"] = status
        if isinstance(body, dict):
            wl = body.get("whitelist") or body.get("pairs") or body.get("data") or []
            if isinstance(wl, list):
                ctx["rest_whitelist"] = [norm_pair(x) for x in wl if norm_pair(x)]
        elif isinstance(body, list):
            ctx["rest_whitelist"] = [norm_pair(x) for x in body if norm_pair(x)]

    return ctx


def extract_candidates_from_obj(
    obj: Any,
    source_file: str,
    source_path: str,
    out: list[dict[str, Any]],
    key_path: str = "",
) -> None:
    if isinstance(obj, dict):
        pair = get_first(obj, ["pair", "order_pair", "symbol_pair"])
        side = get_first(obj, ["side", "order_side", "direction", "trade_side"])

        has_candidate_signal = any(
            k in obj
            for k in (
                "score",
                "cvdoi",
                "trigger",
                "smc",
                "mapped_smc",
                "latest",
                "mapped_latest",
                "paper_action",
                "shadow_lane",
                "replay_lane",
                "intent_state",
                "allow_paper_entry",
            )
        )

        if pair and side and has_candidate_signal:
            row: dict[str, Any] = {
                "pair": norm_pair(pair),
                "side": norm_side(side),
                "score": safe_float(get_first(obj, ["score", "quality_score", "shadow_confluence_score"], 0.0)),
                "cvdoi": get_first(obj, ["cvdoi", "flow_quadrant", "primary_bias"], "UNKNOWN"),
                "trigger": get_first(obj, ["trigger", "mapped_trigger"], "UNKNOWN"),
                "smc": get_first(obj, ["smc", "mapped_smc"], "UNKNOWN"),
                "latest": get_first(obj, ["latest", "mapped_latest", "latest_before"], "UNKNOWN"),
                "paper_action": get_first(obj, ["paper_action", "paper_action_before", "intent_state"], "UNKNOWN"),
                "align": get_first(obj, ["align", "alignment", "alignment_class"], "UNKNOWN"),
                "source_file": source_file,
                "source_path": source_path,
                "json_path": key_path,
                "final_reason_before": get_first(obj, ["final_reason_before", "final_reason_side", "final_reason"], "UNKNOWN"),
                "shadow_lane": get_first(obj, ["shadow_lane"], "UNKNOWN"),
                "shadow_reason": get_first(obj, ["shadow_reason"], "UNKNOWN"),
                "replay_lane": get_first(obj, ["replay_lane"], "UNKNOWN"),
                "replay_decision": get_first(obj, ["replay_decision"], "UNKNOWN"),
                "missing_after_shadow": listify(get_first(obj, ["missing_after_shadow", "missing"], [])),
                "stale_flow_removed_shadow": as_bool(get_first(obj, ["stale_flow_removed_shadow"], False)),
                "side_aligned_strong": as_bool(get_first(obj, ["side_aligned_strong"], False)),
                "side_aligned_watch": as_bool(get_first(obj, ["side_aligned_watch"], False)),
                "smc_good_flag": as_bool(get_first(obj, ["smc_good"], False)),
                "trigger_confirmed_flag": as_bool(get_first(obj, ["trigger_confirmed"], False)),
                "no_side_hard_after_shadow": as_bool(get_first(obj, ["no_side_hard_after_shadow"], False)),
                "allow_paper_entry": as_bool(get_first(obj, ["allow_paper_entry"], False)),
                "would_order": as_bool(get_first(obj, ["would_order"], False)),
            }
            out.append(row)

        for k, v in obj.items():
            extract_candidates_from_obj(v, source_file, source_path, out, key_path + "." + str(k) if key_path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            extract_candidates_from_obj(v, source_file, source_path, out, f"{key_path}[{i}]")


def collect_candidates(runtime: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    paths: list[Path] = []
    for pattern in ("F4X_*_ACTIVE.json", "F4X_*_FULL.json"):  # F4X_BA5E_EXCLUDE_ACTIVE_K_FROM_AJ_CANDIDATE_SCAN: active K excluded; active K is control/output state, not candidate source.
        paths.extend(runtime.glob(pattern))

    unique_paths = []
    seen = set()
    for p in sorted(paths, key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        if p in seen:
            continue
        seen.add(p)
        if p.name == "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json":
            # F4X_BA5E_EXCLUDE_ACTIVE_K_FROM_AJ_CANDIDATE_SCAN: never use active K control state as scanner candidate input.
            continue
        if source_allowed(p):
            unique_paths.append(p)

    for p in unique_paths:
        data = read_json(p)
        if data is None:
            continue
        before = len(candidates)
        extract_candidates_from_obj(data, p.name, str(p), candidates)
        for row in candidates[before:]:
            row["source_mtime"] = p.stat().st_mtime if p.exists() else 0

    return candidates


def dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for c in candidates:
        pair = c.get("pair")
        side = c.get("side")
        source = c.get("source_file")
        lane = str(c.get("shadow_lane") or c.get("replay_lane") or c.get("paper_action") or "")
        key = (str(pair), str(side), str(source), lane)
        old = best.get(key)
        if old is None:
            best[key] = c
            continue
        old_rank = safe_float(old.get("score")) + safe_float(old.get("source_mtime")) / 1_000_000_000
        new_rank = safe_float(c.get("score")) + safe_float(c.get("source_mtime")) / 1_000_000_000
        if new_rank > old_rank:
            best[key] = c
    return list(best.values())


def stale_shadow_ok(row: dict[str, Any]) -> bool:
    shadow_lane = str(row.get("shadow_lane") or "").upper()
    replay_lane = str(row.get("replay_lane") or "").upper()
    missing = {str(x) for x in listify(row.get("missing_after_shadow"))}
    expected_missing = missing.issubset({"latest_entry_ready", "paper_action_allow"}) and bool(missing)
    return (
        (
            shadow_lane == "ENTRY_READY_REVIEW_SHADOW"
            or replay_lane == "NEAR_ENTRY_AFTER_STALE_STICKY_DOWNGRADE"
        )
        and as_bool(row.get("stale_flow_removed_shadow"))
        and as_bool(row.get("no_side_hard_after_shadow"))
        and expected_missing
    )


def evaluate_candidate(
    row: dict[str, Any],
    whitelist: set[str],
    open_pairs: set[str],
    rest_ready: bool,
    min_score: float,
) -> tuple[bool, list[str], float]:
    reasons: list[str] = []
    pair = norm_pair(row.get("pair"))
    side = norm_side(row.get("side"))
    score = safe_float(row.get("score"))

    if not rest_ready:
        reasons.append("REST_DRYRUN_OR_FORCE_ENTRY_NOT_VERIFIED")
    if not whitelist:
        reasons.append("ACTIVE_WHITELIST_EMPTY")
    elif pair not in whitelist:
        reasons.append("PAIR_NOT_IN_ACTIVE_WHITELIST")
    if pair in open_pairs:
        reasons.append("OPEN_SAME_PAIR_EXISTS")
    if side not in {"LONG", "SHORT"}:
        reasons.append("SIDE_INVALID")
    if score < min_score:
        reasons.append("SCORE_BELOW_MIN")
    if not (is_trigger_confirmed(row.get("trigger")) or as_bool(row.get("trigger_confirmed_flag"))):
        reasons.append("TRIGGER_NOT_CONFIRMED")
    if not (is_smc_good(side, row.get("smc")) or as_bool(row.get("smc_good_flag"))):
        reasons.append("SMC_NOT_GOOD")
    if not is_side_supported(side, row):
        reasons.append("SIDE_FLOW_NOT_STRONG")

    st_shadow = stale_shadow_ok(row)
    paper_allow = is_paper_allow(row)
    latest_ready = is_latest_ready(row.get("latest"))
    latest_hard = is_latest_hard_block(row.get("latest"))

    if st_shadow:
        pass
    else:
        if latest_hard:
            reasons.append("LATEST_HARD_BLOCK")
        if not (paper_allow or latest_ready):
            reasons.append("NOT_ENTRY_READY_OR_NOT_PAPER_ALLOW")

    ok = not reasons

    rank = score
    if st_shadow:
        rank += 60
    if paper_allow:
        rank += 40
    if latest_ready:
        rank += 25
    if is_strong_flow_for_side(side, row.get("cvdoi")):
        rank += 15
    if is_trigger_confirmed(row.get("trigger")):
        rank += 10
    if is_smc_good(side, row.get("smc")):
        rank += 10
    rank += safe_float(row.get("source_mtime")) / 1_000_000_000

    return ok, reasons, rank


def load_config(repo: Path) -> tuple[Path | None, dict[str, Any]]:
    candidates = [
        repo / "user_data/config.bybit.dynamic-universe.paper.json",
        repo / "user_data/config-revo-alpha-gate-dynamic-universe.json",
        repo / "user_data/config.json",
    ]
    for p in candidates:
        d = read_json(p)
        if isinstance(d, dict):
            return p, d
    return None, {}


def get_file_whitelist(config: dict[str, Any]) -> list[str]:
    ex = config.get("exchange") if isinstance(config.get("exchange"), dict) else {}
    wl = ex.get("pair_whitelist") or []
    if isinstance(wl, list):
        return [norm_pair(x) for x in wl if norm_pair(x)]
    return []


def open_pairs_from_status(open_trades: Any) -> set[str]:
    out: set[str] = set()
    if not isinstance(open_trades, list):
        return out
    for t in open_trades:
        if not isinstance(t, dict):
            continue
        pair = norm_pair(t.get("pair"))
        if not pair:
            continue
        is_open = t.get("is_open")
        if is_open is None:
            is_open = t.get("open")
        if is_open is None or as_bool(is_open):
            out.add(pair)
    return out


def compact_lines(result: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    lines.append("F4X_AJ_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_CONVEYOR_SHADOW_COMPACT")
    lines.append(f"generated_at={result['generated_at']}")
    lines.append(f"mode={result['mode']}")
    lines.append("paper_order=HOLD")
    lines.append("live=HOLD")
    lines.append("risk_up=HOLD")
    lines.append("gate_loosen=HOLD")
    lines.append("entry_from_watch_recheck_deny=HOLD")
    lines.append("FINAL_DECISION")
    lines.append(f"final_decision={result['final_decision']}")
    lines.append(f"next_action={result['next_action']}")
    lines.append("RUNTIME_CHECKS")
    rc = result["runtime_checks"]
    for k in (
        "rest_ping_ok",
        "rest_token_login_ok",
        "rest_show_config_ok",
        "dry_run_verified",
        "force_entry_enable_verified",
        "active_whitelist_count",
        "open_trade_count",
    ):
        lines.append(f"{k}={rc.get(k)}")
    lines.append("COUNTS")
    for k in (
        "scanner_candidate_count",
        "deduped_candidate_count",
        "whitelist_valid_candidate_count",
        "strict_candidate_count",
        "selected_candidate_count",
    ):
        lines.append(f"{k}={result['counts'].get(k)}")
    lines.append("BLOCKED_REASON_COUNTS")
    if result["blocked_reason_counts"]:
        for k, v in result["blocked_reason_counts"].items():
            lines.append(f"{k}={v}")
    else:
        lines.append("NONE")
    lines.append("SELECTED_STRICT_CANDIDATE")
    selected = result.get("selected_candidate")
    if selected:
        lines.append(
            f"{selected['pair']}|side={selected['side']}|score={selected['score']}|rank={selected['rank']}|"
            f"source={selected['source_file']}|cvdoi={selected['cvdoi']}|trigger={selected['trigger']}|"
            f"smc={selected['smc']}|latest={selected['latest']}|shadow_lane={selected.get('shadow_lane')}|"
            f"reason={selected['selection_reason']}"
        )
    else:
        lines.append("NONE")
    lines.append("TOP_BLOCKED_SAMPLE")
    for b in result.get("blocked_sample", [])[:15]:
        lines.append(
            f"{b['pair']}|side={b['side']}|score={b['score']}|source={b['source_file']}|"
            f"reason={','.join(b['blocked_reasons'])}"
        )
    lines.append("DECISION_POLICY")
    lines.append("AJ is shadow-only.")
    lines.append("AJ does not write real F4X-K active signal.")
    lines.append("AJ does not create paper order.")
    lines.append("AJ selects at most one whitelist-valid strict scanner candidate.")
    lines.append("Next step is AK only if ready_for_AK_real_K_paper_intent=True.")
    lines.append("No live. No risk-up. No gate-loosen.")
    lines.append("OUTPUT_FILES")
    for k, v in result["output_files"].items():
        lines.append(f"{k}={v}")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default=os.environ.get("REVO_RUNTIME_DIR", "/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"))
    ap.add_argument("--min-score", type=float, default=float(os.environ.get("F4X_AJ_MIN_SCORE", "55")))
    ap.add_argument("--max-selected", type=int, default=1)
    args = ap.parse_args()

    repo = Path(args.repo_dir)
    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)

    config_path, config = load_config(repo)
    file_whitelist = get_file_whitelist(config)
    rest = get_rest_context(repo, runtime, config)

    rest_whitelist = rest.get("rest_whitelist") or []
    active_whitelist_list = rest_whitelist if rest_whitelist else file_whitelist
    active_whitelist = {norm_pair(x) for x in active_whitelist_list if norm_pair(x)}

    open_pairs = open_pairs_from_status(rest.get("open_trades"))
    rest_ready = bool(
        rest.get("ping_ok")
        and rest.get("token_login_ok")
        and rest.get("show_config_ok")
        and rest.get("dry_run_verified")
        and rest.get("force_entry_enable_verified")
    )

    raw_candidates = collect_candidates(runtime)
    candidates = dedupe_candidates(raw_candidates)

    evaluated: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    blocked_counter: Counter[str] = Counter()
    whitelist_valid_count = 0

    for row in candidates:
        pair = norm_pair(row.get("pair"))
        side = norm_side(row.get("side"))
        if not pair or side not in {"LONG", "SHORT"}:
            continue
        if pair in active_whitelist:
            whitelist_valid_count += 1

        ok, reasons, rank = evaluate_candidate(
            row=row,
            whitelist=active_whitelist,
            open_pairs=open_pairs,
            rest_ready=rest_ready,
            min_score=args.min_score,
        )
        row2 = dict(row)
        row2["rank"] = round(rank, 6)
        row2["blocked_reasons"] = reasons
        row2["strict_ok"] = ok

        if ok:
            row2["selection_reason"] = (
                "SCANNER_CANDIDATE_STRICT_AND_WHITELIST_VALID_SHADOW_ONLY"
                if not stale_shadow_ok(row2)
                else "STALE_STICKY_DOWNGRADE_SCANNER_CANDIDATE_STRICT_AND_WHITELIST_VALID_SHADOW_ONLY"
            )
            evaluated.append(row2)
        else:
            for r in reasons:
                blocked_counter[r] += 1
            blocked.append(row2)

    evaluated.sort(key=lambda x: safe_float(x.get("rank")), reverse=True)
    selected_rows = evaluated[: max(0, args.max_selected)]
    selected = selected_rows[0] if selected_rows else None

    final_decision = (
        "F4X_AJ_SCANNER_STRICT_SHADOW_CANDIDATE_READY_FOR_AK"
        if selected
        else "F4X_AJ_NO_WHITELIST_VALID_STRICT_SCANNER_CANDIDATE"
    )
    next_action = (
        "APPROVE_F4X_AK_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_DRYRUN_PATCH"
        if selected
        else "HOLD_AUDIT_BLOCKERS_OR_WAIT_NEXT_SCANNER_CYCLE"
    )

    selected_payload = None
    if selected:
        selected_payload = {
            "pair": selected["pair"],
            "side": selected["side"],
            "score": selected["score"],
            "rank": selected["rank"],
            "source_file": selected["source_file"],
            "source_path": selected["source_path"],
            "json_path": selected["json_path"],
            "cvdoi": selected["cvdoi"],
            "trigger": selected["trigger"],
            "smc": selected["smc"],
            "latest": selected["latest"],
            "paper_action_before": selected.get("paper_action"),
            "shadow_lane": selected.get("shadow_lane"),
            "replay_lane": selected.get("replay_lane"),
            "selection_reason": selected["selection_reason"],
            "ready_for_AK_real_K_paper_intent": True,
            "paper_order_allowed": False,
            "would_order": False,
            "live_allowed": False,
            "risk_up_allowed": False,
            "gate_loosen_allowed": False,
            "entry_from_watch_recheck_deny_allowed": False,
            "k_compat_fields": {
                "paper_order_mode": "STRICT_ALLOW_ONLY",
                "intent_source": "F4X_AJ_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_CONVEYOR_SHADOW_ONLY",
                "intent_state": "STRICT_SCANNER_SHADOW_INTENT_READY",
                "order_pair": selected["pair"],
                "order_side": selected["side"],
                "allow_paper_entry": False,
                "would_order": False,
                "blocked_reason": "SHADOW_ONLY_AK_REQUIRED_BEFORE_REAL_K_WRITE",
            },
        }

    blocked_sorted = sorted(blocked, key=lambda x: safe_float(x.get("score")), reverse=True)
    blocked_sample = []
    for b in blocked_sorted[:30]:
        blocked_sample.append(
            {
                "pair": b.get("pair"),
                "side": b.get("side"),
                "score": b.get("score"),
                "source_file": b.get("source_file"),
                "cvdoi": b.get("cvdoi"),
                "trigger": b.get("trigger"),
                "smc": b.get("smc"),
                "latest": b.get("latest"),
                "blocked_reasons": b.get("blocked_reasons", []),
            }
        )

    result = {
        "generated_at": utc_now(),
        "mode": MODE,
        "active": True,
        "paper_order_allowed": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "entry_from_watch_recheck_deny_allowed": False,
        "final_decision": final_decision,
        "next_action": next_action,
        "ready_for_AK_real_K_paper_intent": bool(selected),
        "runtime_checks": {
            "config_path": str(config_path) if config_path else None,
            "config_dry_run": config.get("dry_run"),
            "config_force_entry_enable": config.get("force_entry_enable"),
            "rest_url": rest.get("rest_url"),
            "rest_ping_ok": rest.get("ping_ok"),
            "rest_token_login_ok": rest.get("token_login_ok"),
            "rest_show_config_ok": rest.get("show_config_ok"),
            "dry_run_verified": rest.get("dry_run_verified"),
            "force_entry_enable_verified": rest.get("force_entry_enable_verified"),
            "active_whitelist_count": len(active_whitelist),
            "active_whitelist_source": "REST_WHITELIST" if rest_whitelist else "CONFIG_FILE",
            "active_whitelist_sample": sorted(active_whitelist)[:30],
            "open_trade_count": len(rest.get("open_trades") or []),
            "open_pairs": sorted(open_pairs),
        },
        "counts": {
            "scanner_candidate_count": len(raw_candidates),
            "deduped_candidate_count": len(candidates),
            "whitelist_valid_candidate_count": whitelist_valid_count,
            "strict_candidate_count": len(evaluated),
            "selected_candidate_count": len(selected_rows),
        },
        "selected_candidate": selected_payload,
        "strict_candidates_shadow_only": [
            {
                "pair": x.get("pair"),
                "side": x.get("side"),
                "score": x.get("score"),
                "rank": x.get("rank"),
                "source_file": x.get("source_file"),
                "cvdoi": x.get("cvdoi"),
                "trigger": x.get("trigger"),
                "smc": x.get("smc"),
                "latest": x.get("latest"),
                "selection_reason": x.get("selection_reason"),
            }
            for x in selected_rows
        ],
        "blocked_reason_counts": dict(blocked_counter.most_common()),
        "blocked_sample": blocked_sample,
        "decision_policy": {
            "shadow_only": True,
            "write_real_k": False,
            "create_paper_order": False,
            "max_selected": args.max_selected,
            "next_if_ready": "F4X_AK_SCANNER_DRIVEN_STRICT_K_PAPER_INTENT_DRYRUN_PATCH",
        },
        "source_notes": {
            "excluded_manual_chain_prefixes": list(EXCLUDE_CANDIDATE_FILE_PREFIXES),
            "not_hardcoded_pair": True,
            "whitelist_required": True,
        },
        "output_files": {
            "full_json": str(runtime / f"{OUT_PREFIX}_FULL.json"),
            "compact": str(runtime / f"{OUT_PREFIX}_COMPACT.txt"),
            "active": str(runtime / f"{OUT_PREFIX}_ACTIVE.json"),
        },
    }

    full_path = runtime / f"{OUT_PREFIX}_FULL.json"
    compact_path = runtime / f"{OUT_PREFIX}_COMPACT.txt"
    active_path = runtime / f"{OUT_PREFIX}_ACTIVE.json"

    write_json(full_path, result)
    write_json(active_path, {
        "generated_at": result["generated_at"],
        "mode": result["mode"],
        "active": True,
        "paper_order_allowed": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "entry_from_watch_recheck_deny_allowed": False,
        "final_decision": final_decision,
        "next_action": next_action,
        "ready_for_AK_real_K_paper_intent": bool(selected),
        "selected_candidate": selected_payload,
        "counts": result["counts"],
        "runtime_checks": result["runtime_checks"],
        "blocked_reason_counts": result["blocked_reason_counts"],
    })
    compact_path.write_text("\n".join(compact_lines(result)) + "\n", encoding="utf-8")

    print(compact_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
