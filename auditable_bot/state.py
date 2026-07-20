from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import Position


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> tuple[dict[str, Position], float]:
        if not self.path.exists():
            return {}, 0.0
        data = json.loads(self.path.read_text(encoding="utf-8"))
        positions = {symbol: Position(**row) for symbol, row in data.get("positions", {}).items()}
        return positions, float(data.get("realized_pnl_usdt", 0.0))

    def save(self, positions: dict[str, Position], realized_pnl_usdt: float) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {
            "realized_pnl_usdt": realized_pnl_usdt,
            "positions": {symbol: asdict(pos) for symbol, pos in positions.items()},
        }
        tmp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        tmp.replace(self.path)
