#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm(v: Any) -> str:
    if v is None:
        return "UNKNOWN"
    s = str(v).strip()
    return s if s else "UNKNOWN"


def as_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def parse_dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    text = str(v).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        text = text.replace(" UTC", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    try:
        dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def dt_delta_sec(a: Optional[datetime], b: Optional[datetime]) -> float:
    if a is None or b is None:
        return 999999999.0
    return abs((a - b).total_seconds())


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def connect_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"ERROR: DB missing: {db_path}")
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def get_meta(con: sqlite3.Connection) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        for r in con.execute("select key, value from meta"):
            out[str(r["key"])] = str(r["value"])
    except Exception:
        pass
    return out


def load_pairlist(runtime: Path) -> set[str]:
    data = load_json(runtime / "pair_universe_remote.json", {})
    pairs = data.get("pairs", []) if isinstance(data, dict) else []
    return set(str(x) for x in pairs)


def bybit_symbol(pair: str) -> str:
    if "/USDT" in pair:
        return pair.split("/")[0] + "USDT"
    return pair.replace(":USDT", "").replace("/", "")


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def latest_flow_map(con: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    rows = con.execute("""
        select *
        from latest_flow
    """).fetchall()
    return {str(r["pair"]): row_to_dict(r) for r in rows}


def distinct_cycle_times(con: sqlite3.Connection, pair: str) -> List[Tuple[str, str, datetime]]:
    rows = con.execute("""
        select distinct cycle_id, ts
        from flow_snapshots
        where pair = ?
        order by ts asc
    """, (pair,)).fetchall()

    out = []
    for r in rows:
        dt = parse_dt(r["ts"])
        if dt:
            out.append((str(r["cycle_id"]), str(r["ts"]), dt))
    return out


def nearest_cycle(con: sqlite3.Connection, pair: str, target_dt: Optional[datetime]) -> Tuple[str, str, float, str]:
    cycles = distinct_cycle_times(con, pair)
    if not cycles:
        return "", "", 999999999.0, "NO_SQLITE_CYCLE_FOR_PAIR"

    if target_dt is None:
        cycle_id, ts, _ = cycles[-1]
        return cycle_id, ts, 0.0, "LATEST_SQLITE_CACHE_NO_TARGET_TS"

    best = None
    best_delta = 999999999.0
    for cycle_id, ts, dt in cycles:
        d = dt_delta_sec(dt, target_dt)
        if d < best_delta:
            best = (cycle_id, ts, dt)
            best_delta = d

    if not best:
        return "", "", 999999999.0, "NO_SQLITE_CYCLE_FOR_PAIR"

    return best[0], best[1], best_delta, "NEAREST_SQLITE_CYCLE"


def snapshot_intervals(con: sqlite3.Connection, pair: str, cycle_id: str) -> Dict[str, Dict[str, Any]]:
    if not cycle_id:
        return {}
    rows = con.execute("""
        select *
        from flow_snapshots
        where pair = ? and cycle_id = ?
        order by interval_name
    """, (pair, cycle_id)).fetchall()
    return {str(r["interval_name"]): row_to_dict(r) for r in rows}


def f3b_map(runtime: Path) -> Dict[str, Dict[str, Any]]:
    state = load_json(runtime / "revo_f3b_regime_aware_oi_interpreter_state.json", {})
    interpreted = state.get("interpreted", []) if isinstance(state, dict) else []
    return {norm(x.get("pair")): x for x in interpreted if norm(x.get("pair")) != "UNKNOWN"}


def f2x_targets(runtime: Path) -> List[Dict[str, Any]]:
    state = load_json(runtime / "revo_f2x_shadow_outcome_state.json", {})
    rows = state.get("results", []) if isinstance(state, dict) else []
    out = []
    for r in rows:
        pair = norm(r.get("pair"))
        if pair == "UNKNOWN":
            continue
        target_ts = r.get("candidate_ts") or r.get("source_candle") or r.get("source_ts") or r.get("candle")
        out.append({
            "pair": pair,
            "side": norm(r.get("side")),
            "target_ts": target_ts,
            "target_kind": "F2X_OLD_TRIGGER_CANDIDATE",
            "target_source": "F2X",
        })
    return out


def build_targets(runtime: Path, top_n: int) -> List[Dict[str, Any]]:
    current_pairlist = load_pairlist(runtime)
    f3b_state = load_json(runtime / "revo_f3b_regime_aware_oi_interpreter_state.json", {})

    targets: List[Dict[str, Any]] = []

    for pair in sorted(current_pairlist):
        targets.append({
            "pair": pair,
            "side": "UNKNOWN",
            "target_ts": None,
            "target_kind": "CURRENT_PAIRLIST",
            "target_source": "PAIRLIST",
        })

    for key, kind in [
        ("long_candidates", "F3B_TOP_LONG_FLOW"),
        ("short_candidates", "F3B_TOP_SHORT_FLOW"),
        ("trap_candidates", "F3B_TOP_TRAP_WARNING"),
        ("fast_long", "F3B_FAST_LONG_ACCELERATION"),
        ("fast_short_or_trap", "F3B_FAST_SHORT_OR_TRAP"),
    ]:
        rows = f3b_state.get(key, []) if isinstance(f3b_state, dict) else []
        for r in rows[:top_n]:
            pair = norm(r.get("pair"))
            if pair == "UNKNOWN":
                continue
            targets.append({
                "pair": pair,
                "side": "UNKNOWN",
                "target_ts": None,
                "target_kind": kind,
                "target_source": "F3B",
            })

    targets.extend(f2x_targets(runtime))

    # Deduplicate by pair + target_kind + target_ts.
    seen = set()
    deduped = []
    for t in targets:
        k = (t["pair"], t["target_kind"], str(t.get("target_ts")))
        if k in seen:
            continue
        seen.add(k)
        deduped.append(t)

    return deduped


def interval_value(intervals: Dict[str, Dict[str, Any]], interval: str, key: str) -> Optional[float]:
    row = intervals.get(interval)
    if not row:
        return None
    return as_float(row.get(key))


def classify_snapshot(
    pair: str,
    target_kind: str,
    alignment_status: str,
    alignment_delta_sec: float,
    max_align_sec: int,
    latest: Dict[str, Any],
    intervals: Dict[str, Dict[str, Any]],
    f3b: Dict[str, Any],
    in_pairlist: bool,
) -> Dict[str, Any]:
    primary_bias = norm(f3b.get("primary_bias"))
    trend = norm(f3b.get("trending_interpretation"))
    trap = norm(f3b.get("trap_interpretation"))
    health = norm(f3b.get("health_class"))

    event_alignment_class = alignment_status
    if alignment_status == "NEAREST_SQLITE_CYCLE":
        event_alignment_class = "EVENT_SQLITE_ALIGNED" if alignment_delta_sec <= max_align_sec else "EVENT_SQLITE_OUTSIDE_WINDOW"

    oi1h = interval_value(intervals, "1h", "oi_delta_pct")
    oi15 = interval_value(intervals, "15m", "oi_delta_pct")
    oi5 = interval_value(intervals, "5m", "oi_delta_pct")
    oi1m = interval_value(intervals, "1m", "oi_delta_pct")

    px1h = interval_value(intervals, "1h", "price_delta_pct")
    px15 = interval_value(intervals, "15m", "price_delta_pct")
    px5 = interval_value(intervals, "5m", "price_delta_pct")
    px1m = interval_value(intervals, "1m", "price_delta_pct")

    status = "SNAPSHOT_READY"
    reasons = []

    if not intervals:
        status = "SNAPSHOT_MISSING"
        reasons.append("NO_FLOW_SNAPSHOT_INTERVALS")
    else:
        if oi1h is None or oi15 is None:
            status = "SNAPSHOT_CORE_PARTIAL"
            reasons.append("CORE_1H_15M_MISSING")
        if primary_bias == "TRAP_WARNING":
            reasons.append("TRAP_WARNING_FROM_F3B")
        if event_alignment_class == "EVENT_SQLITE_OUTSIDE_WINDOW":
            reasons.append("EVENT_OUTSIDE_SQLITE_WINDOW")
        if target_kind.startswith("F2X") and event_alignment_class != "EVENT_SQLITE_ALIGNED":
            reasons.append("OLD_TRIGGER_NOT_SAFE_TO_REPLAY_FROM_CURRENT_SQLITE")

    if not reasons:
        reasons.append("OK")

    watch_action = "AUDIT_ONLY"
    if primary_bias == "LONG_FLOW" and in_pairlist and status == "SNAPSHOT_READY":
        watch_action = "WATCH_LONG_FLOW_READY_AUDIT"
    elif primary_bias == "SHORT_FLOW" and in_pairlist and status == "SNAPSHOT_READY":
        watch_action = "WATCH_SHORT_FLOW_READY_AUDIT"
    elif primary_bias == "TRAP_WARNING":
        watch_action = "WATCH_TRAP_AVOID_AUDIT"
    elif primary_bias in {"WEAK_LONG_FLOW", "WEAK_SHORT_FLOW"}:
        watch_action = "WATCH_WEAK_FLOW_AUDIT"
    elif target_kind.startswith("F2X"):
        watch_action = "OLD_CANDIDATE_ALIGNMENT_AUDIT"

    return {
        "pair": pair,
        "symbol": bybit_symbol(pair),
        "target_kind": target_kind,
        "in_current_pairlist": int(in_pairlist),
        "snapshot_status": status,
        "watch_action": watch_action,
        "event_alignment_class": event_alignment_class,
        "alignment_delta_sec": round(alignment_delta_sec, 1),
        "primary_bias": primary_bias,
        "trending_interpretation": trend,
        "trap_interpretation": trap,
        "health_class": health,
        "oi_1h_delta_pct": oi1h,
        "oi_15m_delta_pct": oi15,
        "oi_5m_delta_pct": oi5,
        "oi_1m_delta_pct": oi1m,
        "price_1h_delta_pct": px1h,
        "price_15m_delta_pct": px15,
        "price_5m_delta_pct": px5,
        "price_1m_delta_pct": px1m,
        "latest_cycle_id": latest.get("cycle_id"),
        "latest_ts": latest.get("ts"),
        "reasons": reasons,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--db-path", default="")
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--max-align-sec", type=int, default=900)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    db_path = Path(args.db_path) if args.db_path else runtime / "f3a_market_wide_flow_cache.sqlite"

    con = connect_db(db_path)
    meta = get_meta(con)
    last_cycle = meta.get("last_cycle_id", "")

    latest_map = latest_flow_map(con)
    interp_map = f3b_map(runtime)
    current_pairlist = load_pairlist(runtime)
    targets = build_targets(runtime, args.top_n)

    snapshots = []
    status_counts = Counter()
    action_counts = Counter()
    align_counts = Counter()
    bias_counts = Counter()
    target_counts = Counter()
    reason_counts = Counter()

    for t in targets:
        pair = t["pair"]
        target_dt = parse_dt(t.get("target_ts"))
        cycle_id, cycle_ts, delta_sec, align_status = nearest_cycle(con, pair, target_dt)
        intervals = snapshot_intervals(con, pair, cycle_id)
        latest = latest_map.get(pair, {})
        f3b = interp_map.get(pair, {})
        in_pairlist = pair in current_pairlist

        snap = classify_snapshot(
            pair=pair,
            target_kind=t["target_kind"],
            alignment_status=align_status,
            alignment_delta_sec=delta_sec,
            max_align_sec=args.max_align_sec,
            latest=latest,
            intervals=intervals,
            f3b=f3b,
            in_pairlist=in_pairlist,
        )
        snap["target_source"] = t["target_source"]
        snap["target_ts"] = t.get("target_ts")
        snap["sqlite_cycle_id"] = cycle_id
        snap["sqlite_cycle_ts"] = cycle_ts

        snapshots.append(snap)

        status_counts[snap["snapshot_status"]] += 1
        action_counts[snap["watch_action"]] += 1
        align_counts[snap["event_alignment_class"]] += 1
        bias_counts[snap["primary_bias"]] += 1
        target_counts[snap["target_kind"]] += 1
        for r in snap["reasons"]:
            reason_counts[r] += 1

    current_pairlist_snaps = [x for x in snapshots if x["target_kind"] == "CURRENT_PAIRLIST"]
    long_ready = [x for x in snapshots if x["watch_action"] == "WATCH_LONG_FLOW_READY_AUDIT"]
    short_ready = [x for x in snapshots if x["watch_action"] == "WATCH_SHORT_FLOW_READY_AUDIT"]
    trap_avoid = [x for x in snapshots if x["watch_action"] == "WATCH_TRAP_AVOID_AUDIT"]
    old_alignment = [x for x in snapshots if x["target_kind"].startswith("F2X")]

    if not snapshots:
        decision = "F3C_NO_SNAPSHOTS"
    elif status_counts.get("SNAPSHOT_READY", 0) >= max(1, len(current_pairlist_snaps)):
        decision = "F3C_SQLITE_FLOW_SNAPSHOT_READY_FOR_F3D_SCORER_AUDIT"
    else:
        decision = "F3C_SNAPSHOT_PARTIAL_FIX_SQLITE_COLLECTION"

    payload = {
        "event": "F3C_EVENT_ALIGNED_FLOW_SNAPSHOT_FROM_SQLITE_CACHE",
        "generated_at": utc_now(),
        "runtime_dir": str(runtime),
        "db_path": str(db_path),
        "last_cycle_id": last_cycle,
        "max_align_sec": args.max_align_sec,
        "snapshot_count": len(snapshots),
        "status_counts": status_counts.most_common(),
        "action_counts": action_counts.most_common(),
        "align_counts": align_counts.most_common(),
        "bias_counts": bias_counts.most_common(),
        "target_counts": target_counts.most_common(),
        "reason_counts": reason_counts.most_common(),
        "current_pairlist_snapshots": current_pairlist_snaps,
        "long_ready_snapshots": long_ready,
        "short_ready_snapshots": short_ready,
        "trap_avoid_snapshots": trap_avoid,
        "old_candidate_alignment": old_alignment,
        "snapshots": snapshots,
        "decision": decision,
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
        "db_write_change": "NONE",
    }

    out_state = runtime / "revo_f3c_event_aligned_flow_snapshot_state.json"
    out_jsonl = runtime / "revo_f3c_event_aligned_flow_snapshots.jsonl"
    out_compact_runtime = runtime / "F3C_EVENT_ALIGNED_FLOW_SNAPSHOT_COMPACT.txt"
    out_compact_root = Path("F3C_EVENT_ALIGNED_FLOW_SNAPSHOT_COMPACT.txt")

    out_state.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    out_jsonl.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in snapshots) + "\n", encoding="utf-8")

    lines = []
    lines.append("F3C_EVENT_ALIGNED_FLOW_SNAPSHOT_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append(f"runtime_dir={runtime}")
    lines.append(f"db_path={db_path}")
    lines.append(f"last_cycle_id={last_cycle}")
    lines.append(f"max_align_sec={args.max_align_sec}")
    lines.append("storage=SQLITE_PRIMARY_READ_ONLY_AUDIT")
    lines.append("behavior_change=NONE")
    lines.append("entry_gate_change=NONE")
    lines.append("risk_change=NONE")
    lines.append("db_write_change=NONE")
    lines.append("")
    lines.append("COUNTS")
    lines.append(f"snapshot_count={len(snapshots)}")
    lines.append(f"current_pairlist_count={len(current_pairlist_snaps)}")
    lines.append(f"long_ready_count={len(long_ready)}")
    lines.append(f"short_ready_count={len(short_ready)}")
    lines.append(f"trap_avoid_count={len(trap_avoid)}")
    lines.append(f"old_candidate_alignment_count={len(old_alignment)}")
    lines.append("")
    lines.append("SNAPSHOT_STATUS_COUNTS")
    for k, v in status_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("WATCH_ACTION_COUNTS")
    for k, v in action_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("ALIGNMENT_COUNTS")
    for k, v in align_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("PRIMARY_BIAS_COUNTS")
    for k, v in bias_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("REASON_COUNTS")
    for k, v in reason_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")
    lines.append("CURRENT_PAIRLIST_SNAPSHOTS")
    for x in current_pairlist_snaps:
        lines.append(
            f"{x['pair']}|action={x['watch_action']}|status={x['snapshot_status']}|bias={x['primary_bias']}|"
            f"trend={x['trending_interpretation']}|trap={x['trap_interpretation']}|"
            f"oi1h={x['oi_1h_delta_pct']}|oi15={x['oi_15m_delta_pct']}|oi5={x['oi_5m_delta_pct']}|"
            f"px15={x['price_15m_delta_pct']}|px5={x['price_5m_delta_pct']}|reasons={','.join(x['reasons'])}"
        )
    lines.append("")
    lines.append("LONG_READY_SNAPSHOTS_AUDIT")
    for x in long_ready[:30]:
        lines.append(
            f"{x['pair']}|target={x['target_kind']}|status={x['snapshot_status']}|"
            f"oi1h={x['oi_1h_delta_pct']}|oi15={x['oi_15m_delta_pct']}|oi5={x['oi_5m_delta_pct']}|"
            f"px15={x['price_15m_delta_pct']}|px5={x['price_5m_delta_pct']}"
        )
    lines.append("")
    lines.append("SHORT_READY_SNAPSHOTS_AUDIT")
    for x in short_ready[:30]:
        lines.append(
            f"{x['pair']}|target={x['target_kind']}|status={x['snapshot_status']}|"
            f"oi1h={x['oi_1h_delta_pct']}|oi15={x['oi_15m_delta_pct']}|oi5={x['oi_5m_delta_pct']}|"
            f"px15={x['price_15m_delta_pct']}|px5={x['price_5m_delta_pct']}"
        )
    lines.append("")
    lines.append("TRAP_AVOID_SNAPSHOTS_AUDIT")
    for x in trap_avoid[:40]:
        lines.append(
            f"{x['pair']}|target={x['target_kind']}|status={x['snapshot_status']}|"
            f"oi15={x['oi_15m_delta_pct']}|px15={x['price_15m_delta_pct']}|"
            f"oi5={x['oi_5m_delta_pct']}|px5={x['price_5m_delta_pct']}|reasons={','.join(x['reasons'])}"
        )
    lines.append("")
    lines.append("OLD_CANDIDATE_ALIGNMENT_AUDIT")
    for x in old_alignment:
        lines.append(
            f"{x['pair']}|target_ts={x['target_ts']}|align={x['event_alignment_class']}|"
            f"delta_sec={x['alignment_delta_sec']}|status={x['snapshot_status']}|"
            f"reasons={','.join(x['reasons'])}"
        )
    lines.append("")
    lines.append("DECISION")
    lines.append(decision)
    lines.append("DO_NOT_CONNECT_TO_ENTRY_GATE_YET")
    lines.append("NEXT_F3D_CAN_SCORE_CURRENT_SQLITE_SNAPSHOTS_AUDIT_ONLY")
    lines.append("OLD_F2X_REPLAY_ONLY_VALID_IF_EVENT_SQLITE_ALIGNED")
    lines.append("")
    lines.append("OUTPUT_FILES")
    lines.append(f"state={out_state}")
    lines.append(f"jsonl={out_jsonl}")
    lines.append(f"compact_runtime={out_compact_runtime}")
    lines.append(f"compact_root={out_compact_root}")

    text = "\n".join(lines) + "\n"
    out_compact_runtime.write_text(text, encoding="utf-8")
    out_compact_root.write_text(text, encoding="utf-8")
    print(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
