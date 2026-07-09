from __future__ import annotations

from collections import Counter
from typing import Any, Iterable


def summarize_blockers(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    blockers: Counter[str] = Counter()
    entry_ready = watch = no_trade = 0
    for row in rows:
        perm = str(row.get('permission', '')).upper()
        if perm == 'ENTRY_READY':
            entry_ready += 1
        elif perm == 'WATCH':
            watch += 1
        elif perm == 'NO_TRADE':
            no_trade += 1
        for b in row.get('blockers', []) or []:
            blockers[str(b)] += 1
    return {
        'total': len(rows),
        'entry_ready': entry_ready,
        'watch': watch,
        'no_trade': no_trade,
        'blockers': dict(blockers.most_common()),
    }


def format_blocker_matrix(summary: dict[str, Any]) -> str:
    lines = [
        f"total={summary.get('total', 0)} entry_ready={summary.get('entry_ready', 0)} watch={summary.get('watch', 0)} no_trade={summary.get('no_trade', 0)}",
        'blockers:',
    ]
    for k, v in (summary.get('blockers') or {}).items():
        lines.append(f"- {k}: {v}")
    return '\n'.join(lines)
