#!/usr/bin/env python3
"""Control Tower v1.3.2 - Flow-Ready Publisher.

Reads Top100 and revo_flow_context.json, then publishes only flow-ready pairs to
pair_universe_remote.json for Freqtrade RemotePairList.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Publish flow-ready top100 pairs to RemotePairList JSON")
    p.add_argument("--runtime-dir", default="user_data/revo_alpha/runtime")
    p.add_argument("--refresh-period", type=int, default=300)
    p.add_argument("--include-watch-only", action="store_true", help="Publish all flow_ready pairs, including NO_TRADE/watch states. Default publishes directional ready pairs only.")
    p.add_argument("--fallback-btc", action="store_true", default=True)
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    rt = Path(args.runtime_dir)
    top = load_json(rt / "pair_universe_top100.json", {"pairs": []})
    flow = load_json(rt / "revo_flow_context.json", {})
    top_pairs = top.get("pairs", []) if isinstance(top, dict) else []
    if not top_pairs and isinstance(flow, dict):
        top_pairs = list(flow.keys())

    published: List[str] = []
    ready_pairs: List[str] = []
    missing_flow: List[str] = []
    direction_counts: Counter[str] = Counter()
    quadrant_counts: Counter[str] = Counter()
    quality_counts: Counter[str] = Counter()

    for pair in top_pairs:
        row = flow.get(pair) if isinstance(flow, dict) else None
        if not isinstance(row, dict):
            missing_flow.append(pair)
            continue
        flow_ready = bool(row.get("flow_ready", row.get("data_ready", False)))
        direction = str(row.get("flow_direction", "NO_TRADE")).upper()
        quadrant = str(row.get("flow_quadrant", "NO_FLOW")).upper()
        quality = str(row.get("data_quality", "UNKNOWN"))
        direction_counts[direction] += 1
        quadrant_counts[quadrant] += 1
        quality_counts[quality] += 1
        if flow_ready:
            ready_pairs.append(pair)
            if args.include_watch_only or direction in {"LONG_ONLY", "SHORT_ONLY", "BOTH_ALLOWED"}:
                published.append(pair)

    if not published and bool(args.fallback_btc):
        # Avoid invalid RemotePairList file. This is a fail-safe only; gate still denies if BTC has no flow.
        published = ["BTC/USDT:USDT"]

    payload = {
        "pairs": published,
        "refresh_period": int(args.refresh_period),
        "generated_at": utc_now_iso(),
        "source": "CONTROL_TOWER_V132_FLOW_READY_PUBLISHER",
    }
    write_json(rt / "pair_universe_remote.json", payload)

    audit = {
        "generated_at": utc_now_iso(),
        "top100_count": len(top_pairs),
        "flow_ready_count": len(ready_pairs),
        "published_count": len(published),
        "missing_flow_count": len(missing_flow),
        "published_pairs": published,
        "ready_pairs": ready_pairs,
        "missing_flow_sample": missing_flow[:50],
        "direction_counts": dict(direction_counts),
        "quadrant_counts": dict(quadrant_counts),
        "data_quality_counts": dict(quality_counts),
    }
    write_json(rt / "revo_pair_funnel_audit.json", audit)

    lines = [
        "CONTROL TOWER v1.3.2 - FLOW FUNNEL AUDIT",
        f"generated_at={audit['generated_at']}",
        f"top100_count={audit['top100_count']}",
        f"flow_ready_count={audit['flow_ready_count']}",
        f"published_count={audit['published_count']}",
        f"missing_flow_count={audit['missing_flow_count']}",
        "",
        "direction_counts:",
    ]
    for k, v in direction_counts.most_common():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("quadrant_counts:")
    for k, v in quadrant_counts.most_common():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("data_quality_counts:")
    for k, v in quality_counts.most_common():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("published_pairs:")
    for p in published[:100]:
        lines.append(f"- {p}")
    (rt / "FLOW_FUNNEL_AUDIT_COMPACT.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"FLOW_READY_PUBLISH_PASS top100={len(top_pairs)} flow_ready={len(ready_pairs)} published={len(published)} runtime={rt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
