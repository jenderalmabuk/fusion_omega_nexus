from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


def _rows(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def summarize_journal(root: Path) -> dict:
    root = Path(root)
    reasons: Counter[str] = Counter()
    gate_count = allows = entries = exits = 0
    pnl = 0.0
    for row in _rows(root / "gate_decision.jsonl") or []:
        gate_count += 1
        allows += bool(row.get("allow"))
        reasons.update(row.get("reasons", []))
    for row in _rows(root / "paper_trades.jsonl") or []:
        if row.get("event") == "entry":
            entries += 1
        if row.get("event") == "exit":
            exits += 1
            pnl += float(row.get("pnl_usdt", 0.0))
    return {
        "gate_decisions": gate_count,
        "allows": allows,
        "rejects": gate_count - allows,
        "entries": entries,
        "exits": exits,
        "realized_pnl_usdt": round(pnl, 6),
        "reasons": dict(reasons),
    }
