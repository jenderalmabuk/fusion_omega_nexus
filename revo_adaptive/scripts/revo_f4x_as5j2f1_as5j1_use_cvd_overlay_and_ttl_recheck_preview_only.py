#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT_PREFIX = "F4X_AS5J2F1_AS5J1_USE_CVD_OVERLAY_AND_TTL_RECHECK_PREVIEW_ONLY"
MODE = "AS5J1_USE_CVD_OVERLAY_AND_TTL_RECHECK_PREVIEW_ONLY"

PAIR_RE = re.compile(r"^[A-Z0-9]{1,60}/[A-Z0-9]{2,20}(:[A-Z0-9]{2,20})?$")
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,60}(USDT|USDC|USD|PERP)$")

RUNTIME_FILES = {
    "f3a_b": "revo_f3a_b_flow_cache_health_classifier_state.json",
    "f3b": "revo_f3b_regime_aware_oi_interpreter_state.json",
    "f3c": "revo_f3c_event_aligned_flow_snapshot_state.json",
    "f3d": "revo_f3d_current_flow_snapshot_scorer_state.json",
    "sticky": "F4X_X_STICKY_SOURCE_CYCLE_GUARD_SHADOW_FULL.json",
    "full": "F4X_FULL_CONFLUENCE_FINAL_FULL.json",
    "paper": "F4X_PAPER_DECISION_SIGNALS.json",
    "j": "F4X_J_SIDE_AWARE_MAPPING_SHADOW_CLASSIFIER_FULL.json",
    "p": "F4X_P_CORE_ALIGNED_ENTRY_BLOCKER_CONVEYOR_FULL.json",
    "q": "F4X_Q_LATEST_ENTRY_READY_AND_HARD_BLOCK_SOURCE_ATTRIBUTION_FULL.json",
    "pair_universe": "pair_universe_remote.json",
    "feeder_raw": "F4X_LEGACY_FEEDER_RAW_UNIVERSE_REPORT_ONLY.json",
    "feeder_lanes": "F4X_LEGACY_FEEDER_HOT_WARM_COLD_REPORT_ONLY.json",
    "flow_context": "revo_flow_context_collector.json",
    "cvd_overlay": "F4X_CVD_TAKER_FLOW_OVERLAY_SCHEMA_BRIDGE_PREVIEW_REPORT_ONLY.json",
    "cvd_targets": "F4X_CVD_TAKER_FLOW_TARGET_UNIVERSE_PREVIEW_REPORT_ONLY.json",
    "k": "F4X_K_PAPER_BRIDGE_ACTIVE_SIGNAL.json",
    "l": "F4X_L_PAPER_BRIDGE_ACTIVE_EXECUTION.json",
}

VOLUME_KEYS = ["quote_volume", "quoteVolume", "quote_volume_usd", "volume_usd", "turnover24h", "quoteVolume24h", "volume24h", "vol_usd", "volume", "volume_24h"]
PRICE_KEYS = ["price", "last_price", "lastPrice", "mark_price", "markPrice", "close", "last_close"]
OI_KEYS = ["oi_5m_delta_pct", "oi_15m_delta_pct", "oi_1h_delta_pct", "oi_change_15m_pct", "oi_change_1h_pct", "open_interest", "openInterest"]
CVD_KEYS = ["cvd_ratio", "cvd", "cvd_label", "cvdoi_label", "cvdoi_direction", "taker_buy_ratio", "taker_flow", "buy_sell_delta", "aggtrade_cvd", "cvd_delta_15m", "cvd_zscore_15m", "cvd_delta", "cvd_z", "cvd_z_15m", "trade_delta", "buy_volume", "sell_volume"]
FUNDING_KEYS = ["funding_rate", "funding_rate_pct", "funding", "lastFundingRate"]
KLINE_KEYS = ["kline", "klines", "candles", "tf_1m", "tf_5m", "tf_15m", "kline_1m", "kline_5m", "kline_15m", "last_close", "prev_close"]

# Old stale sources are useful as candidate-universe hints, but should not poison TTL
# when fresh metric-bearing sources exist.
UNIVERSE_ONLY_STALE_DO_NOT_POISON = {"feeder_raw", "feeder_lanes", "pair_universe", "cvd_targets"}
CVD_SOURCE_PRIORITY = {"cvd_overlay", "flow_context", "full", "paper"}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")


def file_age_sec(path: Path) -> float | None:
    try:
        if not path.exists():
            return None
        return max(0.0, datetime.now(timezone.utc).timestamp() - path.stat().st_mtime)
    except Exception:
        return None


def as_num(v: Any) -> float | None:
    try:
        if v in (None, "", "None", "null"):
            return None
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    except Exception:
        return None


def norm_pair(v: Any) -> str | None:
    if not isinstance(v, str):
        return None
    s = v.strip().upper()
    if PAIR_RE.match(s):
        if ":" not in s and s.endswith("/USDT"):
            return s + ":USDT"
        if ":" not in s and s.endswith("/USDC"):
            return s + ":USDC"
        return s
    if SYMBOL_RE.match(s):
        if s.endswith("USDT"):
            return s[:-4] + "/USDT:USDT"
        if s.endswith("USDC"):
            return s[:-4] + "/USDC:USDC"
        if s.endswith("USD"):
            return s[:-3] + "/USD:USD"
    return None


def canonical_pair(pair: str) -> str:
    p = norm_pair(pair) or str(pair).upper()
    return p


def pick_nested(d: Any, key: str) -> Any:
    if not isinstance(d, dict):
        return None
    if key in d:
        return d.get(key)
    for subkey in ("candidate", "raw", "data", "metric", "metrics", "flow", "trigger", "smc", "cvdoi", "cvd_overlay"):
        sub = d.get(subkey)
        if isinstance(sub, dict):
            v = pick_nested(sub, key)
            if v is not None:
                return v
    return None


def pick_any(d: Any, keys: list[str]) -> Any:
    for k in keys:
        v = pick_nested(d, k)
        if v is not None:
            return v
    return None


def extract_pair(d: Any) -> str | None:
    if isinstance(d, str):
        return norm_pair(d)
    if not isinstance(d, dict):
        return None
    for key in ("pair", "symbol", "market", "asset", "order_pair"):
        p = norm_pair(d.get(key))
        if p:
            return p
    for subkey in ("candidate", "raw", "data", "metric", "metrics", "flow", "trigger", "smc", "cvdoi"):
        sub = d.get(subkey)
        if isinstance(sub, dict):
            p = extract_pair(sub)
            if p:
                return p
    for v in d.values():
        p = norm_pair(v)
        if p:
            return p
    return None


def walk_records(obj: Any, path: str = "") -> list[dict[str, Any]]:
    rows = []
    if isinstance(obj, dict):
        # Special overlay schema: { rows: [{pair:..., cvd...}, ...] } works through normal recursion.
        pair = extract_pair(obj)
        if pair:
            rows.append({"path": path or "$", "pair": canonical_pair(pair), "record": obj})
        for k, v in obj.items():
            rows.extend(walk_records(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            rows.extend(walk_records(v, f"{path}[{i}]"))
    elif isinstance(obj, str):
        pair = extract_pair(obj)
        if pair:
            rows.append({"path": path or "$", "pair": canonical_pair(pair), "record": {"pair": canonical_pair(pair)}})
    return rows


def index_records(obj: Any) -> dict[str, list[dict[str, Any]]]:
    idx: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in walk_records(obj):
        idx[r["pair"]].append(r)
    return dict(idx)


def has_value(rec: dict[str, Any], keys: list[str]) -> bool:
    v = pick_any(rec, keys)
    if v is None:
        return False
    if isinstance(v, str) and v.strip().lower() in {"", "none", "null", "nan", "cvd_missing"}:
        return False
    return True


def best_num(records: list[dict[str, Any]], keys: list[str]) -> float | None:
    for item in records:
        x = as_num(pick_any(item.get("record") or {}, keys))
        if x is not None:
            return abs(x)
    return None


def build_source_data(runtime: Path) -> tuple[dict[str, Any], dict[str, dict[str, list[dict[str, Any]]]], dict[str, float | None]]:
    data = {stage: read_json(runtime / fname, {}) for stage, fname in RUNTIME_FILES.items()}
    idx_by_stage = {stage: index_records(obj) for stage, obj in data.items() if stage not in {"k", "l"}}
    ages = {stage: file_age_sec(runtime / fname) for stage, fname in RUNTIME_FILES.items() if stage not in {"k", "l"}}
    return data, idx_by_stage, ages


def metric_presence(pair: str, idx_by_stage: dict[str, dict[str, list[dict[str, Any]]]], ages: dict[str, float | None], keys: list[str], max_age_sec: int, source_filter: set[str] | None = None) -> dict[str, Any]:
    supporting = []
    fresh_supporting = []
    for stage, idx in idx_by_stage.items():
        if source_filter and stage not in source_filter:
            continue
        rows = idx.get(pair) or []
        for row in rows:
            rec = row.get("record") or {}
            if has_value(rec, keys):
                age = ages.get(stage)
                item = {"stage": stage, "age_sec": age, "path": row.get("path")}
                supporting.append(item)
                if age is None or age <= max_age_sec:
                    fresh_supporting.append(item)
                break
    return {
        "present": bool(supporting),
        "fresh": bool(fresh_supporting),
        "supporting": supporting,
        "fresh_supporting": fresh_supporting,
        "best_stage": (fresh_supporting or supporting or [{}])[0].get("stage"),
        "best_age_sec": (fresh_supporting or supporting or [{}])[0].get("age_sec"),
    }


def kline_presence(pair: str, idx_by_stage: dict[str, dict[str, list[dict[str, Any]]]], ages: dict[str, float | None], max_age_sec: int) -> dict[str, Any]:
    # Direct kline-like keys, or event/full stages that carry kline context by design.
    direct = metric_presence(pair, idx_by_stage, ages, KLINE_KEYS, max_age_sec)
    structural = []
    fresh_structural = []
    for stage in ("f3c", "f3d", "full", "paper"):
        if pair in (idx_by_stage.get(stage) or {}):
            age = ages.get(stage)
            item = {"stage": stage, "age_sec": age}
            structural.append(item)
            if age is None or age <= max_age_sec:
                fresh_structural.append(item)
    supporting = direct["supporting"] + structural
    fresh_supporting = direct["fresh_supporting"] + fresh_structural
    return {
        "present": bool(supporting),
        "fresh": bool(fresh_supporting),
        "supporting": supporting,
        "fresh_supporting": fresh_supporting,
        "best_stage": (fresh_supporting or supporting or [{}])[0].get("stage"),
        "best_age_sec": (fresh_supporting or supporting or [{}])[0].get("age_sec"),
    }


def old_like_pair_status(pair: str, idx_by_stage: dict[str, dict[str, list[dict[str, Any]]]], ages: dict[str, float | None], min_volume_usd: float, max_age_sec: int) -> dict[str, Any]:
    rec_rows = []
    stage_presence = {}
    stage_ages = {}
    for stage, idx in idx_by_stage.items():
        present = pair in idx
        stage_presence[stage] = present
        if present:
            rec_rows.extend(idx[pair])
            stage_ages[stage] = ages.get(stage)
    records = [x.get("record") or {} for x in rec_rows]
    def has(keys): return any(has_value(r, keys) for r in records)
    volume = best_num(rec_rows, VOLUME_KEYS)
    price = has(PRICE_KEYS)
    oi = has(OI_KEYS)
    cvd = has(CVD_KEYS)
    funding = has(FUNDING_KEYS)
    kline = has(KLINE_KEYS) or stage_presence.get("f3c") or stage_presence.get("f3d") or stage_presence.get("full")
    valid_ages = [x for x in stage_ages.values() if isinstance(x, (int, float))]
    max_age = max(valid_ages) if valid_ages else None
    stale = max_age is not None and max_age > max_age_sec
    volume_ok = True if volume is None else volume >= min_volume_usd
    missing = []
    if not volume_ok: missing.append("VOLUME_BELOW_MIN")
    if not price: missing.append("PRICE_MISSING")
    if not oi: missing.append("OI_MISSING")
    if not cvd: missing.append("CVD_MISSING")
    if not funding: missing.append("FUNDING_MISSING")
    if not kline: missing.append("KLINE_CONTEXT_MISSING")
    if stale: missing.append("STALE_SOURCE")
    state = "FUEL_READY" if volume_ok and cvd and price and oi and funding and kline and not stale else ("VOLUME_OK_NEEDS_FUEL" if volume_ok else "REJECT_VOLUME_OR_DATA")
    return {"state": state, "missing": missing, "volume_usd": volume, "stage_presence": stage_presence, "stage_ages": stage_ages}


def new_pair_status(pair: str, idx_by_stage: dict[str, dict[str, list[dict[str, Any]]]], ages: dict[str, float | None], min_volume_usd: float, max_age_sec: int) -> dict[str, Any]:
    # Volume may be less fresh than market microstructure, but if a fresh f3 source has volume, use it.
    volume_metric = metric_presence(pair, idx_by_stage, ages, VOLUME_KEYS, max_age_sec)
    price_metric = metric_presence(pair, idx_by_stage, ages, PRICE_KEYS, max_age_sec)
    oi_metric = metric_presence(pair, idx_by_stage, ages, OI_KEYS, max_age_sec)
    funding_metric = metric_presence(pair, idx_by_stage, ages, FUNDING_KEYS, max_age_sec)
    cvd_metric = metric_presence(pair, idx_by_stage, ages, CVD_KEYS, max_age_sec, CVD_SOURCE_PRIORITY)
    if not cvd_metric["present"]:
        cvd_metric = metric_presence(pair, idx_by_stage, ages, CVD_KEYS, max_age_sec)
    kline_metric = kline_presence(pair, idx_by_stage, ages, max_age_sec)

    all_rows = []
    for idx in idx_by_stage.values():
        all_rows.extend(idx.get(pair) or [])
    volume = best_num(all_rows, VOLUME_KEYS)
    volume_ok = True if volume is None else volume >= min_volume_usd

    # stale metric is only true if a required present metric has no fresh supporting source.
    required = {
        "volume": volume_metric,
        "price": price_metric,
        "oi": oi_metric,
        "cvd": cvd_metric,
        "funding": funding_metric,
        "kline": kline_metric,
    }
    stale_reasons = []
    for name, m in required.items():
        if m["present"] and not m["fresh"]:
            stale_reasons.append(f"{name.upper()}_STALE")

    missing = []
    if not volume_ok: missing.append("VOLUME_BELOW_MIN")
    if not price_metric["present"]: missing.append("PRICE_MISSING")
    if not oi_metric["present"]: missing.append("OI_MISSING")
    if not cvd_metric["present"]: missing.append("CVD_MISSING")
    if not funding_metric["present"]: missing.append("FUNDING_MISSING")
    if not kline_metric["present"]: missing.append("KLINE_CONTEXT_MISSING")
    if stale_reasons: missing.append("STALE_REQUIRED_METRIC")

    # Fresh readiness requires fresh support for required market metrics. Volume can be stale only if no fresh volume exists.
    fresh_ready = all([
        volume_ok,
        price_metric["fresh"], oi_metric["fresh"], cvd_metric["fresh"], funding_metric["fresh"], kline_metric["fresh"],
    ])
    state = "FUEL_READY_WITH_OVERLAY_PREVIEW" if fresh_ready else ("VOLUME_OK_NEEDS_FUEL" if volume_ok else "REJECT_VOLUME_OR_DATA")
    return {
        "state": state,
        "missing": missing,
        "volume_usd": volume,
        "volume_ok": volume_ok,
        "price_ok": price_metric["present"], "price_fresh": price_metric["fresh"],
        "oi_ok": oi_metric["present"], "oi_fresh": oi_metric["fresh"],
        "cvd_ok": cvd_metric["present"], "cvd_fresh": cvd_metric["fresh"],
        "funding_ok": funding_metric["present"], "funding_fresh": funding_metric["fresh"],
        "kline_ok": kline_metric["present"], "kline_fresh": kline_metric["fresh"],
        "metric_sources": {k: {"best_stage": v["best_stage"], "best_age_sec": v["best_age_sec"]} for k, v in required.items()},
        "stale_reasons": stale_reasons,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-dir", default="/home/fusion_omega/revo_adaptive")
    ap.add_argument("--runtime-dir", default="/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--min-volume-usd", type=float, default=4000000.0)
    ap.add_argument("--max-age-sec", type=int, default=1800)
    ap.add_argument("--top-n", type=int, default=80)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    data, idx_by_stage, ages = build_source_data(runtime)

    all_pairs = set()
    for stage, idx in idx_by_stage.items():
        all_pairs |= set(idx.keys())

    old_rows = []
    new_rows = []
    old_idx_by_stage = {k: v for k, v in idx_by_stage.items() if k not in {"flow_context", "cvd_overlay", "cvd_targets"}}
    old_ages = {k: v for k, v in ages.items() if k not in {"flow_context", "cvd_overlay", "cvd_targets"}}

    for pair in sorted(all_pairs):
        old = old_like_pair_status(pair, old_idx_by_stage, old_ages, args.min_volume_usd, args.max_age_sec)
        new = new_pair_status(pair, idx_by_stage, ages, args.min_volume_usd, args.max_age_sec)
        old_rows.append({"pair": pair, **old})
        new_rows.append({"pair": pair, **new})

    old_missing = Counter(m for r in old_rows for m in r["missing"])
    new_missing = Counter(m for r in new_rows for m in r["missing"])
    old_states = Counter(r["state"] for r in old_rows)
    new_states = Counter(r["state"] for r in new_rows)

    major_pairs = ["BTC/USDT:USDT", "ETH/USDT:USDT", "XRP/USDT:USDT", "DOGE/USDT:USDT", "SUI/USDT:USDT", "ADA/USDT:USDT", "LINK/USDT:USDT", "TON/USDT:USDT"]
    new_by_pair = {r["pair"]: r for r in new_rows}
    old_by_pair = {r["pair"]: r for r in old_rows}
    major_status = {p: {"old": old_by_pair.get(p), "new": new_by_pair.get(p)} for p in major_pairs}

    fuel_ready = sorted([r for r in new_rows if r["state"] == "FUEL_READY_WITH_OVERLAY_PREVIEW"], key=lambda r: r.get("volume_usd") or 0, reverse=True)
    cvd_fixed = sorted([r for r in new_rows if r.get("cvd_ok") and r.get("cvd_fresh")], key=lambda r: r.get("volume_usd") or 0, reverse=True)
    still_cvd_missing = sorted([r for r in new_rows if "CVD_MISSING" in r["missing"] and r.get("volume_ok")], key=lambda r: r.get("volume_usd") or 0, reverse=True)
    still_kline_missing = sorted([r for r in new_rows if "KLINE_CONTEXT_MISSING" in r["missing"] and r.get("volume_ok")], key=lambda r: r.get("volume_usd") or 0, reverse=True)

    # Conservative decision: this is preview-only and should feed the next patch to AS5J1.
    failures = []
    warnings = []
    if new_missing.get("CVD_MISSING", 0) >= old_missing.get("CVD_MISSING", 0):
        warnings.append("CVD_MISSING_DID_NOT_IMPROVE_REVIEW_OVERLAY_INDEXING")
    if new_states.get("FUEL_READY_WITH_OVERLAY_PREVIEW", 0) == 0:
        warnings.append("NO_FUEL_READY_AFTER_OVERLAY_TTL_RECHECK")

    if failures:
        final_decision = "F4X_AS5J2F1_HOLD_INPUT_MISSING_REVIEW_REQUIRED"
        next_action = "Do not patch AS5J1. Review missing runtime input."
    elif new_missing.get("CVD_MISSING", 0) < old_missing.get("CVD_MISSING", 0):
        final_decision = "F4X_AS5J2F1_OVERLAY_TTL_RECHECK_PREVIEW_IMPROVES_CVD_READY_FOR_AS5J1_PATCH_PREVIEW"
        next_action = "Generate AS5J1 source patch preview to add cvd_overlay/flow_context and metric-level TTL. No K/L/order."
    else:
        final_decision = "F4X_AS5J2F1_OVERLAY_TTL_RECHECK_PREVIEW_REVIEW_REQUIRED"
        next_action = "Overlay did not improve CVD. Inspect pair normalization and overlay schema."

    result = {
        "event": OUT_PREFIX,
        "generated_at": now_utc(),
        "mode": MODE,
        "min_volume_usd": args.min_volume_usd,
        "max_age_sec": args.max_age_sec,
        "paper_order_allowed": False,
        "k_write_allowed": False,
        "l_execute_allowed": False,
        "forceenter_allowed": False,
        "live_allowed": False,
        "risk_up_allowed": False,
        "gate_loosen_allowed": False,
        "source_overwrite_allowed": False,
        "final_decision": final_decision,
        "next_action": next_action,
        "failures": failures,
        "warnings": warnings,
        "age_by_stage": ages,
        "old_summary": {"pair_count": len(old_rows), "state_counts": dict(old_states), "missing_counts": old_missing.most_common()},
        "new_summary": {"pair_count": len(new_rows), "state_counts": dict(new_states), "missing_counts": new_missing.most_common()},
        "improvement": {
            "cvd_missing_delta": old_missing.get("CVD_MISSING", 0) - new_missing.get("CVD_MISSING", 0),
            "stale_old_delta": old_missing.get("STALE_SOURCE", 0) - new_missing.get("STALE_REQUIRED_METRIC", 0),
            "fuel_ready_old": old_states.get("FUEL_READY", 0),
            "fuel_ready_new": new_states.get("FUEL_READY_WITH_OVERLAY_PREVIEW", 0),
        },
        "major_status": major_status,
        "fuel_ready_sample": fuel_ready[:args.top_n],
        "cvd_fixed_sample": cvd_fixed[:args.top_n],
        "still_cvd_missing_sample": still_cvd_missing[:args.top_n],
        "still_kline_missing_sample": still_kline_missing[:args.top_n],
        "rows": new_rows,
        "decision_policy": [
            "AS5J2F1 is preview/report-only.",
            "AS5J2F1 does not write K.",
            "AS5J2F1 does not execute L.",
            "AS5J2F1 does not forceenter.",
            "AS5J2F1 does not create paper orders.",
            "AS5J2F1 does not enable live, risk-up, or gate-loosen.",
            "AS5J2F1 does not overwrite source files.",
        ],
    }

    runtime.mkdir(parents=True, exist_ok=True)
    full = runtime / f"{OUT_PREFIX}_FULL.json"
    active = runtime / f"{OUT_PREFIX}_ACTIVE.json"
    compact = runtime / f"{OUT_PREFIX}_COMPACT.txt"
    write_json(full, result)
    write_json(active, result)

    lines = [
        "F4X_AS5J2F1_AS5J1_USE_CVD_OVERLAY_AND_TTL_RECHECK_PREVIEW_ONLY_COMPACT",
        f"generated_at={result['generated_at']}",
        f"mode={MODE}",
        f"min_volume_usd={args.min_volume_usd}",
        f"max_age_sec={args.max_age_sec}",
        "paper_order=HOLD",
        "k_write=HOLD",
        "l_execute=HOLD",
        "forceenter=HOLD",
        "live=HOLD",
        "risk_up=HOLD",
        "gate_loosen=HOLD",
        "source_overwrite=HOLD",
        "FINAL_DECISION",
        f"final_decision={final_decision}",
        f"next_action={next_action}",
        "FAILURES",
        *(failures if failures else ["NONE"]),
        "WARNINGS",
        *(warnings if warnings else ["NONE"]),
        "OLD_SUMMARY",
        str(result["old_summary"]),
        "NEW_SUMMARY",
        str(result["new_summary"]),
        "IMPROVEMENT",
        str(result["improvement"]),
        "MAJOR_STATUS",
    ]
    for p in major_pairs:
        n = major_status.get(p, {}).get("new") or {}
        lines.append(f"{p}|new_state={n.get('state')}|missing={n.get('missing')}|cvd={n.get('cvd_ok')}/{n.get('cvd_fresh')}|kline={n.get('kline_ok')}/{n.get('kline_fresh')}|sources={n.get('metric_sources')}")
    lines.append("FUEL_READY_SAMPLE")
    for r in fuel_ready[:args.top_n]:
        lines.append(f"{r['pair']}|vol={r.get('volume_usd')}|missing={r.get('missing')}|sources={r.get('metric_sources')}")
    lines.append("CVD_FIXED_SAMPLE")
    for r in cvd_fixed[:args.top_n]:
        lines.append(f"{r['pair']}|vol={r.get('volume_usd')}|state={r.get('state')}|missing={r.get('missing')}|cvd_source={r.get('metric_sources',{}).get('cvd')}")
    lines.append("STILL_CVD_MISSING_SAMPLE")
    for r in still_cvd_missing[:args.top_n]:
        lines.append(f"{r['pair']}|vol={r.get('volume_usd')}|missing={r.get('missing')}|sources={r.get('metric_sources')}")
    lines.append("STILL_KLINE_MISSING_SAMPLE")
    for r in still_kline_missing[:args.top_n]:
        lines.append(f"{r['pair']}|vol={r.get('volume_usd')}|missing={r.get('missing')}|sources={r.get('metric_sources')}")
    lines.extend([
        "OUTPUT_FILES",
        f"full_json={full}",
        f"active_json={active}",
        f"compact={compact}",
        "DECISION_POLICY",
        *result["decision_policy"],
    ])
    compact.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(compact.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
