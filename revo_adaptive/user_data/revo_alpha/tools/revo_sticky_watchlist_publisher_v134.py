#!/usr/bin/env python3
"""Control Tower v1.3.7 - Sticky Watchlist Publisher + Canonical Execution Context Contract.

Purpose
- Keep broad Top100 scan running, but stabilize the Freqtrade execution watchlist.
- Current actionable flow pairs are published immediately.
- Pairs that were actionable remain on watchlist for a minimum sticky TTL so Freqtrade
  has time to observe location/timing/geometry across multiple 5m candles.
- Retained pairs are still governed by the live flow context and gate. If their flow
  turns NO_TRADE, they stay visible for observation but cannot enter unless flow returns.

This does NOT open trades. It writes pair_universe_remote.json plus revo_execution_context.json.
The execution context is the canonical 1:1 contract read by the gate. UNKNOWN must not exist in normal flow.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ACTIONABLE_DIRECTIONS = {"LONG_ONLY", "SHORT_ONLY", "BOTH_ALLOWED"}
DEFAULT_STICKY_TTL_SEC = 1800
DEFAULT_REFRESH_PERIOD = 300
# PATCH: No cap — publish ALL flow-ready pairs, not just 80
DEFAULT_MAX_PUBLISHED = 9999

# CONTROL_TOWER_WATCH_EARLY_PRE_ACTIONABLE_TIER
# Pre-warm pairs whose OI is "baru mulai naik" + harga belum panas + volume mendukung,
# even before flow_direction confirms actionable. These are published with
# entry_permission=WATCH so Freqtrade keeps them warm (history+indicators ready) and the
# gate tests them every candle. The instant flow turns actionable, entry fires with no
# cold-start. This is the explicit early-OI thesis tier.
WATCH_EARLY_ENABLED = str(os.environ.get("REVO_WATCH_EARLY_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"}
WATCH_OI15_MIN = float(os.environ.get("REVO_WATCH_OI15_MIN", "0.3"))          # OI delta 15m (%) starting to rise
WATCH_PRICE15_ABS_MAX = float(os.environ.get("REVO_WATCH_PRICE15_ABS_MAX", "2.0"))  # |price delta 15m| (%) not overheated
WATCH_VOLZ_MIN = float(os.environ.get("REVO_WATCH_VOLZ_MIN", "0.0"))          # volume zscore support


def is_watch_early(row: Dict[str, Any], flow_ready: bool, actionable: bool) -> bool:
    """Pre-actionable early-OI candidate (the 'OI baru mulai naik' thesis tier)."""
    if not WATCH_EARLY_ENABLED or actionable or not flow_ready:
        return False
    risk = str(row.get("flow_risk", "")).upper()
    if "TRAP" in risk:
        return False
    oi15 = safe_float(row.get("oi_delta_pct_15m"), 0.0)
    oi1h = safe_float(row.get("oi_delta_pct_1h"), 0.0)
    price15 = abs(safe_float(row.get("price_delta_pct_15m"), 0.0))
    volz = safe_float(row.get("volume_zscore_15m"), 0.0)
    return (oi15 >= WATCH_OI15_MIN) and (oi1h >= 0.0) and (price15 <= WATCH_PRICE15_ABS_MAX) and (volz >= WATCH_VOLZ_MIN)


class DataCompletenessChecker:
    def __init__(self, db_path: Path, max_age_sec: int = 300):
        self.db_path = Path(db_path)
        self.max_age_sec = int(max_age_sec)
        self.enabled = self.db_path.exists()

    def _row_for_pair(self, pair: str) -> Optional[sqlite3.Row]:
        if not self.enabled:
            return None
        try:
            con = sqlite3.connect(str(self.db_path), timeout=5.0)
            con.row_factory = sqlite3.Row
            try:
                return con.execute(
                    """
                    SELECT pair, ts, oi_1h_delta_pct, funding_rate
                    FROM latest_flow
                    WHERE pair = ?
                    """,
                    (str(pair),),
                ).fetchone()
            finally:
                con.close()
        except Exception:
            self.enabled = False
            return None

    def is_complete(self, pair: str) -> bool:
        if not self.enabled:
            return True
        row = self._row_for_pair(pair)
        if row is None:
            return False
        if row["oi_1h_delta_pct"] is None or row["funding_rate"] is None:
            return False
        age = self.get_data_age(pair)
        return age is not None and age < float(self.max_age_sec)

    def get_data_age(self, pair: str) -> Optional[float]:
        if not self.enabled:
            return None
        row = self._row_for_pair(pair)
        if row is None:
            return None
        dt = parse_ts(row["ts"])
        if dt is None:
            return None
        return max((utc_now() - dt).total_seconds(), 0.0)

    def get_complete_pairs(self, candidates: List[str]) -> List[str]:
        return [str(pair) for pair in candidates if self.is_complete(str(pair))]

    def get_stale_pairs(self, candidates: List[str]) -> List[str]:
        if not self.enabled:
            return []
        return [str(pair) for pair in candidates if not self.is_complete(str(pair))]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_ts(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def age_sec(ts: Any, now: datetime) -> Optional[float]:
    dt = parse_ts(ts)
    if not dt:
        return None
    return max((now - dt).total_seconds(), 0.0)


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = dict(payload)
    out.setdefault("ts", utc_now_iso())
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False, default=str) + "\n")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "None"):
            return default
        return float(value)
    except Exception:
        return default


def flow_rank(row: Dict[str, Any]) -> float:
    direction = str(row.get("flow_direction", "NO_TRADE")).upper()
    strength = str(row.get("flow_strength", "NO_FLOW")).upper()
    quadrant = str(row.get("flow_quadrant", "NO_FLOW")).upper()
    score = 0.0
    if direction in ACTIONABLE_DIRECTIONS:
        score += 100.0
    if "STRONG" in strength:
        score += 30.0
    elif "FRESH" in strength:
        score += 20.0
    elif "STALE" in strength:
        score -= 20.0
    if "BULLISH_CONTINUATION" in quadrant or "BEARISH_CONTINUATION" in quadrant:
        score += 10.0
    # Use flow evidence as tie-breakers only.
    score += min(abs(safe_float(row.get("price_delta_pct_15m"), 0.0)), 8.0)
    score += min(abs(safe_float(row.get("oi_delta_pct_15m"), 0.0)), 8.0)
    score += min(abs(safe_float(row.get("cvd_zscore_15m"), 0.0)), 5.0)
    score += min(safe_float(row.get("quote_volume_24h"), 0.0) / 100_000_000.0, 10.0)
    return score


def build_execution_row(pair: str, state_row: Dict[str, Any], flow_row: Dict[str, Any], cycle_id: str) -> Dict[str, Any]:
    status = str(state_row.get("status", "EXPIRED")).upper()
    current_direction = str(state_row.get("current_direction", flow_row.get("flow_direction", "NO_TRADE"))).upper()
    last_direction = str(state_row.get("last_direction", current_direction)).upper()
    current_quadrant = str(state_row.get("current_quadrant", flow_row.get("flow_quadrant", "NO_FLOW"))).upper()
    last_quadrant = str(state_row.get("last_quadrant", current_quadrant)).upper()
    current_strength = str(state_row.get("current_strength", flow_row.get("flow_strength", "NO_FLOW"))).upper()
    last_strength = str(state_row.get("last_strength", current_strength)).upper()
    flow_ready = bool(flow_row.get("flow_ready", flow_row.get("data_ready", False)))
    data_quality = str(flow_row.get("data_quality", state_row.get("data_quality", "NO_FLOW")))
    published = bool(state_row.get("published", False))

    if status == "ACTIVE_ACTIONABLE" and current_direction in ACTIONABLE_DIRECTIONS and flow_ready:
        entry_permission = "FLOW_ELIGIBLE"
        deny_reason = "NONE"
        gate_flow_direction = current_direction
        gate_flow_ready = True
    elif status == "STICKY_RETAINED":
        entry_permission = "NO_TRADE"
        deny_reason = "DENY_STICKY_RETAINED_CURRENT_FLOW_NOT_ACTIONABLE"
        gate_flow_direction = "NO_TRADE"
        gate_flow_ready = bool(flow_ready)
    elif status == "WATCH_EARLY":
        entry_permission = "WATCH"
        deny_reason = "WATCH_EARLY_OI_RISING_DIRECTION_PENDING"
        gate_flow_direction = "NO_TRADE"
        gate_flow_ready = bool(flow_ready)
    elif not flow_ready:
        entry_permission = "NO_TRADE"
        deny_reason = "DENY_CONTEXT_FLOW_NOT_READY"
        gate_flow_direction = "NO_TRADE"
        gate_flow_ready = False
    else:
        entry_permission = "NO_TRADE"
        deny_reason = "DENY_CURRENT_FLOW_NOT_ACTIONABLE"
        gate_flow_direction = "NO_TRADE"
        gate_flow_ready = True

    out = dict(flow_row) if isinstance(flow_row, dict) else {}
    out.update({
        "pair": pair,
        "cycle_id": cycle_id,
        "published": published,
        "publish_reason": status,
        "entry_permission": entry_permission,
        "deny_reason": deny_reason,
        "flow_direction": gate_flow_direction,
        "flow_quadrant": current_quadrant if current_quadrant else "NO_FLOW",
        "flow_strength": current_strength if current_strength else "NO_FLOW",
        "current_direction": current_direction or "NO_TRADE",
        "last_direction": last_direction or "NO_TRADE",
        "current_quadrant": current_quadrant or "NO_FLOW",
        "last_quadrant": last_quadrant or "NO_FLOW",
        "current_strength": current_strength or "NO_FLOW",
        "last_strength": last_strength or "NO_FLOW",
        "flow_ready": bool(gate_flow_ready),
        "data_ready": bool(gate_flow_ready),
        "data_quality": data_quality if data_quality else "NO_FLOW",
        "data_complete": bool(state_row.get("data_complete", True)),
        "data_stale": bool(state_row.get("data_stale", False)),
        "sticky_status": status,
        "sticky_age_sec": safe_float(state_row.get("sticky_age_sec"), 0.0),
        "sticky_expires_in_sec": safe_float(state_row.get("sticky_expires_in_sec"), 0.0),
        "flow_lookup_source": "EXECUTION_CONTEXT",
        "context_contract_status": "OK",
    })
    # No normal UNKNOWN values. If any upstream field is missing, make it explicit NO_TRADE/NO_FLOW.
    for k, default in {
        "flow_direction": "NO_TRADE",
        "flow_quadrant": "NO_FLOW",
        "flow_strength": "NO_FLOW",
        "scanner_mode": "UNKNOWN_SCANNER",
        "btc_mode": "BTC_UNKNOWN",
        "coupling_status": "UNKNOWN_COUPLING",
    }.items():
        if out.get(k) in (None, "", "UNKNOWN"):
            out[k] = default
    return out


def normalize_pairs(top: Any, flow: Dict[str, Any]) -> List[str]:
    if isinstance(top, dict) and isinstance(top.get("pairs"), list) and top.get("pairs"):
        return [str(p) for p in top.get("pairs", [])]
    return [str(k) for k in flow.keys()]


def build_state(
    runtime: Path,
    sticky_ttl_sec: int,
    max_published: int,
    refresh_period: int,
    fallback_btc: bool,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    now = utc_now()
    now_iso = now.isoformat()
    completeness = DataCompletenessChecker(
        runtime / "f3a_market_wide_flow_cache.sqlite",
        max_age_sec=int(float(os.environ.get("REVO_DATA_MAX_AGE_SEC", "300"))),
    )
    top = load_json(runtime / "pair_universe_top100.json", {"pairs": []})
    flow = load_json(runtime / "revo_flow_context.json", {})
    prior_state = load_json(runtime / "pair_universe_sticky_state.json", {"pairs": {}})
    prior_pairs = prior_state.get("pairs", {}) if isinstance(prior_state, dict) else {}
    if not isinstance(prior_pairs, dict):
        prior_pairs = {}
    if not isinstance(flow, dict):
        flow = {}

    top_pairs = normalize_pairs(top, flow)
    top_set = set(top_pairs)

    current_actionable: List[str] = []
    current_ready: List[str] = []
    missing_flow: List[str] = []
    direction_counts: Counter[str] = Counter()
    quadrant_counts: Counter[str] = Counter()
    quality_counts: Counter[str] = Counter()

    # Start with prior state, then update current Top100 observations.
    new_state: Dict[str, Any] = {"generated_at": now_iso, "sticky_ttl_sec": int(sticky_ttl_sec), "pairs": {}}

    for pair in sorted(set(list(prior_pairs.keys()) + top_pairs)):
        old = prior_pairs.get(pair, {}) if isinstance(prior_pairs.get(pair), dict) else {}
        row = flow.get(pair) if pair in flow else None
        in_top100 = pair in top_set
        if not isinstance(row, dict):
            row = {}
            if in_top100:
                missing_flow.append(pair)

        flow_ready = bool(row.get("flow_ready", row.get("data_ready", False)))
        direction = str(row.get("flow_direction", "NO_TRADE")).upper()
        quadrant = str(row.get("flow_quadrant", "NO_FLOW")).upper()
        strength = str(row.get("flow_strength", "NO_FLOW")).upper()
        quality = str(row.get("data_quality", "UNKNOWN"))
        actionable = bool(flow_ready and direction in ACTIONABLE_DIRECTIONS)

        if in_top100:
            direction_counts[direction] += 1
            quadrant_counts[quadrant] += 1
            quality_counts[quality] += 1
            if flow_ready:
                current_ready.append(pair)
            if actionable:
                current_actionable.append(pair)

        first_seen = old.get("first_seen") or now_iso
        last_seen_top100 = now_iso if in_top100 else old.get("last_seen_top100")
        last_actionable = now_iso if actionable else old.get("last_actionable")
        last_direction = direction if direction != "NO_TRADE" else old.get("last_direction", direction)
        last_quadrant = quadrant if quadrant != "NO_FLOW" else old.get("last_quadrant", quadrant)
        last_strength = strength if strength != "NO_FLOW" else old.get("last_strength", strength)
        actionable_age = age_sec(last_actionable, now)
        sticky_active = actionable_age is not None and actionable_age <= float(sticky_ttl_sec)
        watch_early = is_watch_early(row, flow_ready, actionable)

        if actionable:
            status = "ACTIVE_ACTIONABLE"
        elif sticky_active:
            status = "STICKY_RETAINED"
        elif watch_early:
            status = "WATCH_EARLY"
        else:
            status = "EXPIRED"

        # Keep active/retained only in state unless it still belongs to top100 for audit freshness.
        if status != "EXPIRED" or in_top100:
            new_state["pairs"][pair] = {
                "pair": pair,
                "status": status,
                "in_top100": bool(in_top100),
                "flow_ready": bool(flow_ready),
                "current_direction": direction,
                "current_quadrant": quadrant,
                "current_strength": strength,
                "last_direction": last_direction,
                "last_quadrant": last_quadrant,
                "last_strength": last_strength,
                "first_seen": first_seen,
                "last_seen_top100": last_seen_top100,
                "last_actionable": last_actionable,
                "sticky_age_sec": actionable_age,
                "sticky_expires_in_sec": None if actionable_age is None else max(float(sticky_ttl_sec) - actionable_age, 0.0),
                "rank_score": flow_rank(row),
                "data_quality": quality,
                "updated_at": now_iso,
            }

    active = []
    retained = []
    watch_early_list = []
    expired_removed = []
    data_complete_pairs = set()
    data_stale_pairs = set()
    for pair, row in new_state["pairs"].items():
        complete = completeness.is_complete(pair)
        row["data_complete"] = bool(complete)
        row["data_stale"] = bool(completeness.enabled and not complete)
        if complete:
            data_complete_pairs.add(pair)
        elif completeness.enabled:
            data_stale_pairs.add(pair)

        if row.get("status") == "ACTIVE_ACTIONABLE":
            if complete:
                active.append(pair)
        elif row.get("status") == "STICKY_RETAINED":
            retained.append(pair)
        elif row.get("status") == "WATCH_EARLY":
            if complete:
                watch_early_list.append(pair)
        elif row.get("status") == "EXPIRED" and pair not in top_set:
            expired_removed.append(pair)

    def sort_key(pair: str) -> Tuple[int, float, str]:
        row = new_state["pairs"].get(pair, {})
        status = row.get("status")
        group = 0 if status == "ACTIVE_ACTIONABLE" else 1 if status == "STICKY_RETAINED" else 2
        return (group, -safe_float(row.get("rank_score"), 0.0), pair)

    published = sorted(active + retained + watch_early_list, key=sort_key)
    if max_published > 0:
        published = published[: int(max_published)]
    if not published and fallback_btc:
        published = ["BTC/USDT:USDT"]

    # Mark only actually published rows to make audit readable.
    published_set = set(published)
    for pair, row in new_state["pairs"].items():
        row["published"] = pair in published_set

    cycle_id = now.strftime("%Y%m%dT%H%M%SZ")
    # Load previous execution context for flow_direction_age tracking
    _prev_ctx = {}
    try:
        _prev_file = runtime / "revo_execution_context.json"
        if _prev_file.exists():
            _prev_data = json.loads(_prev_file.read_text())
            _prev_ctx = _prev_data.get("pairs", {})
    except Exception:
        pass
    execution_pairs: Dict[str, Any] = {}
    contract_broken: List[str] = []
    for pair in published:
        state_row = new_state["pairs"].get(pair, {})
        flow_row = flow.get(pair, {}) if isinstance(flow.get(pair), dict) else {}
        if not state_row:
            state_row = {"pair": pair, "status": "CONTRACT_BROKEN", "published": True}
            contract_broken.append(pair)
        execution_pairs[pair] = build_execution_row(pair, state_row, flow_row, cycle_id)

    # Flow direction age: count consecutive cycles with same direction
    for pair, row in execution_pairs.items():
        cur_dir = str(row.get("flow_direction", "NO_TRADE")).upper()
        prev_row = _prev_ctx.get(pair, {})
        prev_dir = str(prev_row.get("flow_direction", "NO_TRADE")).upper()
        prev_age = int(prev_row.get("flow_direction_age", 0))
        if cur_dir == prev_dir and cur_dir != "NO_TRADE":
            row["flow_direction_age"] = prev_age + 1
        else:
            row["flow_direction_age"] = 0

    remote_payload = {
        "pairs": published,
        "refresh_period": int(refresh_period),
        "generated_at": now_iso,
        "source": "CONTROL_TOWER_V137_CANONICAL_EXECUTION_CONTEXT",
        "cycle_id": cycle_id,
        "sticky_ttl_sec": int(sticky_ttl_sec),
        "max_published": int(max_published),
        "current_actionable_count": len(current_actionable),
        "sticky_retained_count": len([p for p in retained if p in published_set]),
        "watch_early_count": len([p for p in watch_early_list if p in published_set]),
        "data_complete_count": len([p for p in published if p in data_complete_pairs]),
        "data_stale_count": len([p for p in published if p in data_stale_pairs]),
    }

    execution_context = {
        "source": "CONTROL_TOWER_V137_CANONICAL_EXECUTION_CONTEXT",
        "cycle_id": cycle_id,
        "generated_at": now_iso,
        "contract_status": "OK" if not contract_broken and set(published) == set(execution_pairs.keys()) else "BROKEN",
        "remote_pair_count": len(published),
        "execution_pair_count": len(execution_pairs),
        "contract_broken_pairs": contract_broken,
        "pairs": execution_pairs,
    }

    heartbeat = {
        "event": "WATCHLIST_HEARTBEAT",
        "generated_at": now_iso,
        "top100_count": len(top_pairs),
        "flow_rows": len(flow),
        "flow_ready_count": len(current_ready),
        "current_actionable_count": len(current_actionable),
        "data_complete_count": len([p for p in published if p in data_complete_pairs]),
        "data_stale_count": len([p for p in published if p in data_stale_pairs]),
        "data_completeness_enabled": bool(completeness.enabled),
        "sticky_retained_count": len([p for p in retained if p in published_set]),
        "watch_early_count": len([p for p in watch_early_list if p in published_set]),
        "published_count": len(published),
        "execution_context_count": len(execution_pairs),
        "contract_status": execution_context.get("contract_status"),
        "expired_removed_count": len(expired_removed),
        "max_published": int(max_published),
        "sticky_ttl_sec": int(sticky_ttl_sec),
        "direction_counts": dict(direction_counts),
        "quadrant_counts": dict(quadrant_counts),
        "data_quality_counts": dict(quality_counts),
        "current_actionable_pairs": current_actionable[:100],
        "sticky_retained_pairs": [p for p in retained if p in published_set][:100],
        "watch_early_pairs": [p for p in watch_early_list if p in published_set][:100],
        "published_pairs": published[:100],
        "missing_flow_count": len(missing_flow),
        "missing_flow_sample": missing_flow[:30],
    }

    return remote_payload, {"state": new_state, "heartbeat": heartbeat, "execution_context": execution_context}


def write_compact(runtime: Path, heartbeat: Dict[str, Any]) -> None:
    lines = [
        "CONTROL TOWER v1.3.4 - STICKY WATCHLIST HEARTBEAT",
        f"generated_at={heartbeat.get('generated_at')}",
        f"top100_count={heartbeat.get('top100_count')}",
        f"flow_rows={heartbeat.get('flow_rows')}",
        f"flow_ready_count={heartbeat.get('flow_ready_count')}",
        f"current_actionable_count={heartbeat.get('current_actionable_count')}",
        f"data_complete_count={heartbeat.get('data_complete_count')}",
        f"data_stale_count={heartbeat.get('data_stale_count')}",
        f"data_completeness_enabled={heartbeat.get('data_completeness_enabled')}",
        f"sticky_retained_count={heartbeat.get('sticky_retained_count')}",
        f"published_count={heartbeat.get('published_count')}",
        f"expired_removed_count={heartbeat.get('expired_removed_count')}",
        f"sticky_ttl_sec={heartbeat.get('sticky_ttl_sec')}",
        f"max_published={heartbeat.get('max_published')}",
        "",
        "direction_counts:",
    ]
    for k, v in Counter(heartbeat.get("direction_counts", {})).most_common():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("quadrant_counts:")
    for k, v in Counter(heartbeat.get("quadrant_counts", {})).most_common():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("current_actionable_pairs:")
    for p in heartbeat.get("current_actionable_pairs", [])[:50]:
        lines.append(f"- {p}")
    lines.append("")
    lines.append("sticky_retained_pairs:")
    for p in heartbeat.get("sticky_retained_pairs", [])[:50]:
        lines.append(f"- {p}")
    lines.append("")
    lines.append("published_pairs:")
    for p in heartbeat.get("published_pairs", [])[:80]:
        lines.append(f"- {p}")
    (runtime / "WATCHLIST_HEARTBEAT_COMPACT.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Control Tower v1.3.7 Sticky Watchlist + Execution Context Publisher")
    p.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime")
    p.add_argument("--refresh-period", type=int, default=DEFAULT_REFRESH_PERIOD)
    p.add_argument("--sticky-ttl-sec", type=int, default=DEFAULT_STICKY_TTL_SEC)
    p.add_argument("--max-published", type=int, default=DEFAULT_MAX_PUBLISHED)
    p.add_argument("--fallback-btc", action="store_true", default=True)
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    runtime = Path(args.runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)
    remote_payload, extra = build_state(
        runtime=runtime,
        sticky_ttl_sec=int(args.sticky_ttl_sec),
        max_published=int(args.max_published),
        refresh_period=int(args.refresh_period),
        fallback_btc=bool(args.fallback_btc),
    )
    write_json(runtime / "pair_universe_remote.json", remote_payload)
    write_json(runtime / "pair_universe_freqtrade.json", remote_payload)
    write_json(runtime / "revo_execution_context.json", extra["execution_context"])
    write_json(runtime / "pair_universe_sticky_state.json", extra["state"])
    write_json(runtime / "watchlist_heartbeat_latest.json", extra["heartbeat"])
    append_jsonl(runtime / "watchlist_heartbeat.jsonl", extra["heartbeat"])
    write_compact(runtime, extra["heartbeat"])
    print(
        "CANONICAL_STICKY_WATCHLIST_PASS "
        f"published={len(remote_payload.get('pairs', []))} "
        f"watchlist_size={len(remote_payload.get('pairs', []))} "
        f"flow_ready={extra['heartbeat'].get('flow_ready_count')} "
        f"current_actionable={remote_payload.get('current_actionable_count')} "
        f"sticky_retained={remote_payload.get('sticky_retained_count')} "
        f"data_complete={remote_payload.get('data_complete_count')} "
        f"data_stale={remote_payload.get('data_stale_count')} "
        f"ttl={int(args.sticky_ttl_sec)} runtime={runtime}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
