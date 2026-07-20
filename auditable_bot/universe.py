from __future__ import annotations

import json
from pathlib import Path


def load_pair_whitelist(config_path: Path) -> list[str]:
    data = json.loads(Path(config_path).read_text(encoding="utf-8"))
    pairs = data.get("pairs", data.get("exchange", {}).get("pair_whitelist", []))
    if not isinstance(pairs, list):
        raise ValueError("pair list must be a list")
    return [str(p) for p in pairs]
