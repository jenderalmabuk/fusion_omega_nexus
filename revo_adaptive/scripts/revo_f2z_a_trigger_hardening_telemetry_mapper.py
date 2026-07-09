#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple


GROUP_PATTERNS = {
    "RSI": ["rsi", "relative_strength"],
    "OI": ["oi", "open_interest", "openinterest"],
    "CVD": ["cvd", "cum_delta", "cumulative_delta", "volume_delta", "delta_volume", "taker_delta", "buy_sell_delta"],
    "VOLUME": ["volume", "quote_volume", "base_volume", "vol_"],
    "FUNDING": ["funding", "funding_rate"],
    "STOCH": ["stoch", "stochastic", "k_", "d_"],
    "CANDLE": ["candle", "wick", "rejection", "engulf", "reclaim", "close", "open", "high", "low"],
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
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    rows = []
    for line in lines[-max_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def classify_key(path: str) -> List[str]:
    p = path.lower()
    groups = []
    for group, pats in GROUP_PATTERNS.items():
        if any(x in p for x in pats):
            groups.append(group)
    return groups


def scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def scan_obj(obj: Any, prefix: str = "", limit_depth: int = 12) -> List[Tuple[str, Any]]:
    found: List[Tuple[str, Any]] = []

    def walk(x: Any, p: str, depth: int) -> None:
        if depth > limit_depth:
            return

        if isinstance(x, dict):
            for k, v in x.items():
                key = f"{p}.{k}" if p else str(k)
                if scalar(v):
                    found.append((key, v))
                else:
                    walk(v, key, depth + 1)
        elif isinstance(x, list):
            for i, v in enumerate(x[:5]):
                key = f"{p}[{i}]"
                if scalar(v):
                    found.append((key, v))
                else:
                    walk(v, key, depth + 1)
        else:
            found.append((p, x))

    walk(obj, prefix, 0)
    return found


def pair_from_key_or_row(source: str, key_path: str, row: Any) -> str:
    if isinstance(row, dict):
        p = row.get("pair") or row.get("symbol")
        if p:
            return norm(p)

    first = key_path.split(".")[0]
    if "/USDT" in first or "USDT" in first:
        return first
    return "GLOBAL_OR_UNKNOWN"


def collect_candidates(runtime: Path) -> List[Dict[str, Any]]:
    f2y = load_json(runtime / "revo_f2y_trigger_failure_attribution_state.json", {})
    f2w = load_json(runtime / "revo_f2w_b_trigger_field_score_state.json", {})

    out = []

    for row in f2y.get("classified", []) if isinstance(f2y, dict) else []:
        pair = norm(row.get("pair"))
        side = norm(row.get("side")).upper()
        out.append({
            "pair": pair,
            "side": side,
            "source": "F2Y_CLASSIFIED",
            "status": norm(row.get("status")),
            "regime": norm(row.get("regime")),
            "zone": norm(row.get("zone")),
            "direction": norm(row.get("direction")),
            "mapped_groups": row.get("mapped_groups", []),
            "tags": row.get("tags", []),
        })

    if out:
        return out

    for row in f2w.get("rows", []) if isinstance(f2w, dict) else []:
        if row.get("f2w_b_trigger_status") == "TRIGGER_CONFIRMED_SHADOW":
            out.append({
                "pair": norm(row.get("pair")),
                "side": norm(row.get("side")).upper(),
                "source": "F2W_B",
                "status": norm(row.get("f2w_b_trigger_status")),
                "regime": norm(row.get("regime_router")),
                "zone": norm(row.get("pd_zone")),
                "direction": norm(row.get("direction_engine")),
                "mapped_groups": row.get("f2w_b_mapped_groups", []),
                "tags": [],
            })

    return out


def normalize_pair_symbol(pair: str) -> List[str]:
    p = norm(pair)
    base = p.split("/")[0] if "/" in p else p.replace("USDT", "")
    return [
        p,
        base,
        f"{base}USDT",
        f"{base}/USDT:USDT",
        f"{base}/USDT",
    ]


def row_matches_pair(row: Any, pair: str) -> bool:
    tokens = set(normalize_pair_symbol(pair))
    if isinstance(row, dict):
        values = [
            norm(row.get("pair")),
            norm(row.get("symbol")),
            norm(row.get("base")),
            norm(row.get("base_currency")),
        ]
        if any(v in tokens for v in values):
            return True

        # Avoid expensive full dumps. Check shallow scalar values only.
        for _, v in scan_obj(row, "", limit_depth=2):
            if isinstance(v, str) and v in tokens:
                return True

    return False


def source_payloads(runtime: Path, jsonl_tail_lines: int) -> List[Tuple[str, Path, Any]]:
    files = [
        ("FLOW_CONTEXT", runtime / "revo_flow_context.json", "json"),
        ("EXECUTION_CONTEXT", runtime / "revo_execution_context.json", "json"),
        ("FLOW_COLLECTOR", runtime / "revo_flow_context_collector.json", "json"),
        ("F2W_B_STATE", runtime / "revo_f2w_b_trigger_field_score_state.json", "json"),
        ("F2Y_STATE", runtime / "revo_f2y_trigger_failure_attribution_state.json", "json"),
        ("GATE_SHADOW_EVENTS", runtime / "revo_gate_shadow_events.jsonl", "jsonl"),
        ("GATE_HEARTBEAT_EVENTS", runtime / "revo_gate_heartbeat_events.jsonl", "jsonl"),
        ("F2U_SETUP_EVENTS", runtime / "revo_f2u_setup_state_events.jsonl", "jsonl"),
    ]

    payloads = []
    for name, path, kind in files:
        if kind == "json":
            payloads.append((name, path, load_json(path, {})))
        else:
            payloads.append((name, path, read_jsonl_tail(path, jsonl_tail_lines)))
    return payloads


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime/bybit")
    ap.add_argument("--jsonl-tail-lines", type=int, default=2500)
    args = ap.parse_args()

    runtime = Path(args.runtime_dir)
    candidates = collect_candidates(runtime)
    candidate_pairs = sorted(set(c["pair"] for c in candidates))

    field_counts = Counter()
    group_counts = Counter()
    source_group_counts = Counter()
    field_samples: Dict[str, Any] = {}
    pair_group_fields: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
    pair_source_hits: Dict[str, Counter] = defaultdict(Counter)

    source_stats = []

    for source_name, path, payload in source_payloads(runtime, args.jsonl_tail_lines):
        exists = path.exists()
        size = path.stat().st_size if exists else 0
        rows_scanned = 0
        fields_seen = 0

        if isinstance(payload, list):
            iterable = payload
        elif isinstance(payload, dict):
            iterable = [payload]
        else:
            iterable = []

        for row in iterable:
            rows_scanned += 1
            scanned = scan_obj(row, source_name)
            for key_path, value in scanned:
                fields_seen += 1
                groups = classify_key(key_path)
                if not groups:
                    continue

                field_counts[key_path] += 1
                if key_path not in field_samples:
                    field_samples[key_path] = value

                for g in groups:
                    group_counts[g] += 1
                    source_group_counts[f"{source_name}|{g}"] += 1

                for pair in candidate_pairs:
                    if row_matches_pair(row, pair) or any(tok in key_path for tok in normalize_pair_symbol(pair)):
                        pair_source_hits[pair][source_name] += 1
                        for g in groups:
                            if key_path not in pair_group_fields[pair][g]:
                                pair_group_fields[pair][g].append(key_path)

        source_stats.append({
            "source": source_name,
            "path": str(path),
            "exists": exists,
            "size": size,
            "rows_scanned": rows_scanned,
            "fields_seen": fields_seen,
        })

    required_groups = ["RSI", "OI", "CVD"]
    candidate_reports = []
    missing_group_counts = Counter()
    present_group_counts = Counter()

    for c in candidates:
        pair = c["pair"]
        present = sorted(pair_group_fields[pair].keys())
        missing = [g for g in required_groups if g not in pair_group_fields[pair]]

        for g in present:
            present_group_counts[g] += 1
        for g in missing:
            missing_group_counts[g] += 1

        candidate_reports.append({
            "pair": pair,
            "side": c["side"],
            "status": c.get("status"),
            "regime": c.get("regime"),
            "zone": c.get("zone"),
            "direction": c.get("direction"),
            "current_mapped_groups": c.get("mapped_groups", []),
            "telemetry_present_groups": present,
            "missing_required_groups": missing,
            "rsi_fields": pair_group_fields[pair].get("RSI", [])[:20],
            "oi_fields": pair_group_fields[pair].get("OI", [])[:20],
            "cvd_fields": pair_group_fields[pair].get("CVD", [])[:20],
            "flow_fields": pair_group_fields[pair].get("FLOW", [])[:20],
            "location_fields": pair_group_fields[pair].get("LOCATION", [])[:20],
            "source_hits": pair_source_hits[pair].most_common(),
        })

    # Rule proposal is audit-only.
    if missing_group_counts.get("RSI", 0) or missing_group_counts.get("OI", 0) or missing_group_counts.get("CVD", 0):
        mapping_decision = "HARDENING_MAPPING_INCOMPLETE"
    else:
        mapping_decision = "HARDENING_MAPPING_READY_FOR_SCORER_AUDIT"

    primary_rule_proposal = [
        "DO_NOT_PROMOTE_ENTRY_UNTIL_RSI_OI_CVD_ARE_MAPPED_OR_EXPLICITLY_DECLARED_UNAVAILABLE",
        "PROMOTION_REQUIRE_TRIGGER_CONFIRMED_SHADOW",
        "PROMOTION_REQUIRE_NO_1C_MAE_LT_NEG_050_IN_SHADOW_OUTCOME_BATCH",
        "PROMOTION_REQUIRE_NO_3C_MAE_DOMINATES_MFE",
        "RANGING_LONG_REQUIRE_3C_CLOSE_NONNEG_OR_3C_MFE_GE_030",
        "IF_6C_CLOSE_FADES_NEGATIVE_AFTER_MFE_MARK_FAST_INVALIDATION_REQUIRED",
    ]

    payload = {
        "event": "F2Z_A_TRIGGER_HARDENING_TELEMETRY_MAPPER",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_dir": str(runtime),
        "candidate_count": len(candidates),
        "candidate_pairs": candidate_pairs,
        "source_stats": source_stats,
        "group_counts": group_counts.most_common(),
        "source_group_counts": source_group_counts.most_common(),
        "field_counts": field_counts.most_common(300),
        "field_samples": {k: field_samples.get(k) for k, _ in field_counts.most_common(100)},
        "present_group_counts": present_group_counts.most_common(),
        "missing_required_group_counts": missing_group_counts.most_common(),
        "candidate_reports": candidate_reports,
        "mapping_decision": mapping_decision,
        "primary_rule_proposal": primary_rule_proposal,
        "behavior_change": "NONE",
        "entry_gate_change": "NONE",
        "risk_change": "NONE",
    }

    out_state = runtime / "revo_f2z_a_trigger_hardening_telemetry_mapper_state.json"
    out_compact_runtime = runtime / "F2Z_A_TRIGGER_HARDENING_TELEMETRY_MAPPER_COMPACT.txt"
    out_compact_root = Path("F2Z_A_TRIGGER_HARDENING_TELEMETRY_MAPPER_COMPACT.txt")

    out_state.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = []
    lines.append("F2Z_A_TRIGGER_HARDENING_TELEMETRY_MAPPER_COMPACT")
    lines.append(f"generated_at={payload['generated_at']}")
    lines.append(f"runtime_dir={runtime}")
    lines.append(f"candidate_count={len(candidates)}")
    lines.append(f"candidate_pairs={candidate_pairs}")
    lines.append(f"mapping_decision={mapping_decision}")
    lines.append("behavior_change=NONE")
    lines.append("entry_gate_change=NONE")
    lines.append("risk_change=NONE")
    lines.append("")

    lines.append("SOURCE_STATS")
    for s in source_stats:
        lines.append(
            f"{s['source']}|exists={s['exists']}|size={s['size']}|rows={s['rows_scanned']}|fields={s['fields_seen']}|path={s['path']}"
        )
    lines.append("")

    lines.append("GROUP_COUNTS")
    for k, v in group_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("PRESENT_GROUP_COUNTS_BY_CANDIDATE")
    for k, v in present_group_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("MISSING_REQUIRED_GROUP_COUNTS_BY_CANDIDATE")
    for k, v in missing_group_counts.most_common():
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("SOURCE_GROUP_COUNTS_TOP")
    for k, v in source_group_counts.most_common(60):
        lines.append(f"{k}={v}")
    lines.append("")

    lines.append("TOP_FIELD_CANDIDATES")
    for k, v in field_counts.most_common(80):
        sample = field_samples.get(k)
        sample_text = str(sample)
        if len(sample_text) > 90:
            sample_text = sample_text[:87] + "..."
        lines.append(f"{k}|count={v}|sample={sample_text}")
    lines.append("")

    lines.append("CANDIDATE_MAPPING_DETAIL")
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
                f"present={','.join(r.get('telemetry_present_groups') or [])}",
                f"missing_required={','.join(r.get('missing_required_groups') or [])}",
                f"rsi_fields={';'.join(r.get('rsi_fields') or [])}",
                f"oi_fields={';'.join(r.get('oi_fields') or [])}",
                f"cvd_fields={';'.join(r.get('cvd_fields') or [])}",
            ])
        )
    lines.append("")

    lines.append("PRIMARY_RULE_PROPOSAL_AUDIT_ONLY")
    for rule in primary_rule_proposal:
        lines.append(f"- {rule}")
    lines.append("")

    lines.append("DECISION")
    if mapping_decision == "HARDENING_MAPPING_INCOMPLETE":
        lines.append("NEXT_PATCH_SHOULD_MAP_OR_EXPORT_RSI_OI_CVD_TELEMETRY_BEFORE_TRIGGER_PROMOTION")
    else:
        lines.append("NEXT_PATCH_CAN_BUILD_F2Z_B_HARDENED_TRIGGER_SCORER_AUDIT")
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
