#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple, Optional


GROUP_PATTERNS = {
    "RSI": ["rsi", "relative_strength"],
    "OI": ["oi", "open_interest", "openinterest"],
    "CVD": ["cvd", "cum_delta", "cumulative_delta", "volume_delta", "delta_volume", "taker_delta", "buy_sell_delta"],
    "VOLUME": ["volume", "quote_volume", "base_volume", "vol_"],
    "FUNDING": ["funding", "funding_rate"],
    "STOCH": ["stoch", "stochastic", "k_", "d_"],
    "CANDLE": ["candle", "wick", "rejection", "engulf", "reclaim", "close", "open", "high", "low", "price"],
    "TIMING": ["timing", "trigger", "tap", "confirm", "signal"],
    "LOCATION": ["pd_zone", "pd_location", "premium", "discount", "support", "resistance", "supply", "demand"],
    "FLOW": ["flow", "quadrant", "authority", "direction", "strength"],
}


def norm(v: Any) -> str:
    if v is None:
        return "UNKNOWN"
    s = str(v).strip()
    return s if s else "UNKNOWN"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def read_jsonl_tail(path: Path, max_lines: int) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def pair_tokens(pair: str) -> List[str]:
    p = norm(pair)
    base = p.split("/")[0] if "/" in p else p.replace("USDT", "")
    return [
        p,
        f"{base}/USDT:USDT",
        f"{base}/USDT",
        f"{base}USDT",
        base,
    ]


def classify_key(key: str) -> List[str]:
    lk = key.lower()
    out = []
    for group, patterns in GROUP_PATTERNS.items():
        if any(p in lk for p in patterns):
            out.append(group)
    return out


def scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def scan_obj(obj: Any, prefix: str = "", max_depth: int = 10) -> List[Tuple[str, Any]]:
    out: List[Tuple[str, Any]] = []

    def walk(x: Any, p: str, depth: int) -> None:
        if depth > max_depth:
            return
        if isinstance(x, dict):
            for k, v in x.items():
                key = f"{p}.{k}" if p else str(k)
                if scalar(v):
                    out.append((key, v))
                else:
                    walk(v, key, depth + 1)
        elif isinstance(x, list):
            for i, v in enumerate(x[:20]):
                key = f"{p}[{i}]"
                if scalar(v):
                    out.append((key, v))
                else:
                    walk(v, key, depth + 1)
        else:
            out.append((p, x))

    walk(obj, prefix, 0)
    return out


def row_has_pair_identity(row: Any, pair: str) -> bool:
    tokens = set(pair_tokens(pair))
    if not isinstance(row, dict):
        return False

    identity_keys = [
        "pair", "symbol", "market", "instrument", "base", "base_currency",
        "pair_name", "pair_symbol",
    ]

    for k in identity_keys:
        v = row.get(k)
        if norm(v) in tokens:
            return True

    # only shallow identity check, not full-object search
    for k, v in row.items():
        if isinstance(v, str) and norm(v) in tokens and any(x in k.lower() for x in ["pair", "symbol", "market", "base"]):
            return True

    return False


def get_pair_object_from_dict(data: Any, pair: str) -> Optional[Any]:
    if not isinstance(data, dict):
        return None

    tokens = set(pair_tokens(pair))

    for token in tokens:
        if token in data:
            return data[token]

    # common nested pairs container
    pairs = data.get("pairs")
    if isinstance(pairs, dict):
        for token in tokens:
            if token in pairs:
                return pairs[token]

    if isinstance(pairs, list):
        for row in pairs:
            if row_has_pair_identity(row, pair):
                return row

    return None


def get_pair_object_from_list(rows: Any, pair: str) -> List[Any]:
    if not isinstance(rows, list):
        return []
    return [r for r in rows if row_has_pair_identity(r, pair)]


def collect_candidates(runtime: Path) -> List[Dict[str, Any]]:
    f2y = load_json(runtime / "revo_f2y_trigger_failure_attribution_state.json", {})
    out = []

    for row in f2y.get("classified", []) if isinstance(f2y, dict) else []:
        out.append({
            "pair": norm(row.get("pair")),
            "side": norm(row.get("side")).upper(),
            "status": norm(row.get("status")),
            "regime": norm(row.get("regime")),
            "zone": norm(row.get("zone")),
            "direction": norm(row.get("direction")),
            "mapped_groups": row.get("mapped_groups", []),
            "tags": row.get("tags", []),
        })

    return out


def add_fields(
    result: Dict[str, Any],
    source_name: str,
    pair: str,
    obj: Any,
) -> None:
    if obj is None:
        return

    for key, value in scan_obj(obj, f"{source_name}.{pair}"):
        groups = classify_key(key)
        if not groups:
            continue

        for g in groups:
            if key not in result["fields_by_group"][g]:
                result["fields_by_group"][g].append(key)
            result["group_counts"][g] += 1

        if key not in result["samples"]:
            result["samples"][key] = value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--jsonl-tail-lines", type=int, default=5000)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    candidates = collect_candidates(runtime)

    flow_context = load_json(runtime / "revo_flow_context.json", {})
    execution_context = load_json(runtime / "revo_execution_context.json", {})
    collector = load_json(runtime / "revo_flow_context_collector.json", {})
    f2w_b = load_json(runtime / "revo_f2w_b_trigger_field_score_state.json", {})
    f2y = load_json(runtime / "revo_f2y_trigger_failure_attribution_state.json", {})

    shadow_rows = read_jsonl_tail(runtime / "revo_gate_shadow_events.jsonl", args.jsonl_tail_lines)
    heartbeat_rows = read_jsonl_tail(runtime / "revo_gate_heartbeat_events.jsonl", args.jsonl_tail_lines)
    f2u_rows = read_jsonl_tail(runtime / "revo_f2u_setup_state_events.jsonl", args.jsonl_tail_lines)

    candidate_reports = []
    missing_required_counts = Counter()
    present_required_counts = Counter()
    source_hit_counts = Counter()

    for c in candidates:
        pair = c["pair"]
        side = c["side"]

        report = {
            "pair": pair,
            "side": side,
            "status": c.get("status"),
            "regime": c.get("regime"),
            "zone": c.get("zone"),
            "direction": c.get("direction"),
            "current_mapped_groups": c.get("mapped_groups", []),
            "fields_by_group": defaultdict(list),
            "group_counts": Counter(),
            "samples": {},
            "source_hits": Counter(),
        }

        sources = [
            ("FLOW_CONTEXT", get_pair_object_from_dict(flow_context, pair)),
            ("EXECUTION_CONTEXT", get_pair_object_from_dict(execution_context, pair)),
            ("FLOW_COLLECTOR", get_pair_object_from_dict(collector, pair)),
        ]

        # State files are not pair-indexed; use row identity only.
        f2w_rows = f2w_b.get("rows", []) if isinstance(f2w_b, dict) else []
        f2y_rows = f2y.get("classified", []) if isinstance(f2y, dict) else []

        state_sources = [
            ("F2W_B_STATE", get_pair_object_from_list(f2w_rows, pair)),
            ("F2Y_STATE", get_pair_object_from_list(f2y_rows, pair)),
            ("GATE_SHADOW_EVENTS", get_pair_object_from_list(shadow_rows, pair)),
            ("GATE_HEARTBEAT_EVENTS", get_pair_object_from_list(heartbeat_rows, pair)),
            ("F2U_SETUP_EVENTS", get_pair_object_from_list(f2u_rows, pair)),
        ]

        for source_name, obj in sources:
            if obj is not None:
                report["source_hits"][source_name] += 1
                source_hit_counts[source_name] += 1
                add_fields(report, source_name, pair, obj)

        for source_name, rows in state_sources:
            if rows:
                report["source_hits"][source_name] += len(rows)
                source_hit_counts[source_name] += len(rows)
                for row in rows[-50:]:
                    add_fields(report, source_name, pair, row)

        present_groups = sorted(report["fields_by_group"].keys())
        required = ["RSI", "OI", "CVD"]
        missing_required = [g for g in required if g not in report["fields_by_group"]]

        for g in required:
            if g in report["fields_by_group"]:
                present_required_counts[g] += 1
            else:
                missing_required_counts[g] += 1

        candidate_reports.append({
            "pair": pair,
            "side": side,
            "status": report["status"],
            "regime": report["regime"],
            "zone": report["zone"],
            "direction": report["direction"],
            "current_mapped_groups": report["current_mapped_groups"],
            "present_groups": present_groups,
            "missing_required": missing_required,
            "source_hits": report["source_hits"].most_common(),
            "rsi_fields": report["fields_by_group"].get("RSI", [])[:30],
            "oi_fields": report["fields_by_group"].get("OI", [])[:30],
            "cvd_fields": report["fields_by_group"].get("CVD", [])[:30],
            "volume_fields": report["fields_by_group"].get("VOLUME", [])[:20],
            "funding_fields": report["fields_by_group"].get("FUNDING", [])[:20],
            "flow_fields": report["fields_by_group"].get("FLOW", [])[:20],
            "samples": {k: report["samples"].get(k) for k in list(report["samples"].keys())[:80]},
        })

    all_required_ok = all(not r["missing_required"] for r in candidate_reports)

    oi_cvd_pair_bound_ok = all(
        "OI" in r["present_groups"] and "CVD" in r["present_groups"]
        for r in candidate_reports
    )

    rsi_pair_bound_ok = all("RSI" in r["present_groups"] for r in candidate_reports)

    if all_required_ok:
        mapping_decision = "PAIR_BOUND_MAPPING_READY_FOR_HARDENED_SCORER_AUDIT"
    elif oi_cvd_pair_bound_ok and not rsi_pair_bound_ok:
        mapping_decision = "PAIR_BOUND_OI_CVD_READY_RSI_MISSING"
    else:
        mapping_decision = "PAIR_BOUND_MAPPING_INCOMPLETE"

    primary_rule_proposal = [
        "NO_ENTRY_PROMOTION_FROM_CURRENT_SAMPLE",
        "DO_NOT_USE_GLOBAL_OI_CVD_FIELDS_AS_PAIR_CONFIRMATION",
        "REQUIRE_PAIR_BOUND_OI_CVD_FOR_HARDENED_TRIGGER_SCORER",
        "RSI_MISSING_MUST_BE_EXPORTED_OR_REPLACED_BY_EXISTING_MOMENTUM_FIELD",
        "KEEP_TRIGGER_CONFIRMED_SHADOW_AS_WATCH_ONLY_UNTIL_PAIR_BOUND_SCORER_AND_OUTCOME_BATCH_PASS",
    ]

    payload = {
        "event": "F2Z_B_PAIR_BOUND_TELEMETRY_MAPPER",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_dir": str(runtime),
        "candidate_count": len(candidates),
        "mapping_decision": mapping_decision,
        "present_required_counts": present_required_counts.most_common(),
        "missing_required_counts": missing_required_counts.most_common(),
        "source_hit_counts": source_hit_counts.most_common(),
        "candidate_reports": candidate_reports,
        "primary_rule_proposal": primary_rule_proposal,
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
    }

    out_state = runtime / "revo_f2z_b_pair_bound_telemetry_mapper_state.json"
    out_compact_runtime = runtime / "F2Z_B_PAIR_BOUND_TELEMETRY_MAPPER_COMPACT.txt"
    out_compact_root = Path("F2Z_B_PAIR_BOUND_TELEMETRY_MAPPER_COMPACT.txt")

    out_state.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = []
    lines.append("F2Z_B_PAIR_BOUND_TELEMETRY_MAPPER_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append(f"runtime_dir={runtime}")
    lines.append(f"candidate_count={len(candidates)}")
    lines.append(f"mapping_decision={mapping_decision}")
    lines.append("behavior_change=NONE")
    lines.append("entry_gate_change=NONE")
    lines.append("risk_change=NONE")
    lines.append("")

    lines.append("PRESENT_REQUIRED_COUNTS")
    for k, v in present_required_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("MISSING_REQUIRED_COUNTS")
    for k, v in missing_required_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("SOURCE_HIT_COUNTS")
    for k, v in source_hit_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("CANDIDATE_PAIR_BOUND_DETAIL")
    for r in candidate_reports:
        lines.append(
            "|".join([
                str(r["pair"]),
                str(r["side"]),
                f"status={r['status']}",
                f"regime={r['regime']}",
                f"zone={r['zone']}",
                f"direction={r['direction']}",
                f"current_mapped={','.join(r.get('current_mapped_groups') or [])}",
                f"present={','.join(r.get('present_groups') or [])}",
                f"missing_required={','.join(r.get('missing_required') or [])}",
                f"source_hits={r.get('source_hits')}",
            ])
        )
        lines.append(f"  RSI_FIELDS={';'.join(r.get('rsi_fields') or [])}")
        lines.append(f"  OI_FIELDS={';'.join(r.get('oi_fields') or [])}")
        lines.append(f"  CVD_FIELDS={';'.join(r.get('cvd_fields') or [])}")
    lines.append("")

    lines.append("PRIMARY_RULE_PROPOSAL_AUDIT_ONLY")
    for rule in primary_rule_proposal:
        lines.append(f"- {rule}")
    lines.append("")

    lines.append("DECISION")
    if mapping_decision == "PAIR_BOUND_MAPPING_READY_FOR_HARDENED_SCORER_AUDIT":
        lines.append("NEXT_PATCH_CAN_BUILD_F2Z_C_PAIR_BOUND_HARDENED_TRIGGER_SCORER_AUDIT")
    elif mapping_decision == "PAIR_BOUND_OI_CVD_READY_RSI_MISSING":
        lines.append("NEXT_PATCH_SHOULD_MAP_OI_CVD_AND_HANDLE_RSI_MISSING_EXPLICITLY")
    else:
        lines.append("NEXT_PATCH_SHOULD_FIX_PAIR_BOUND_TELEMETRY_EXPORT_OR_MAPPING")
    lines.append("NO_ENTRY_GATE_RISK_CHANGE")
    lines.append("")

    lines.append("OUTPUT_FILES")
    lines.append(f"state={out_state}")
    lines.append(f"compact_runtime={out_compact_runtime}")
    lines.append(f"compact_root={out_compact_root}")

    text = "\n".join(lines) + "\n"
    out_compact_runtime.write_text(text, encoding="utf-8")
    out_compact_root.write_text(text, encoding="utf-8")
    print(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
