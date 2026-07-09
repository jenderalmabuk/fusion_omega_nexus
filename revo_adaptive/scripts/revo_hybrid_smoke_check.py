#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    ROOT / "user_data/strategies/RevoAlphaStrategy.py",
    ROOT / "user_data/revo_alpha/bridge.py",
    ROOT / "configs/revo_futures_dryrun.example.json",
]

def main() -> int:
    missing = [str(p) for p in REQUIRED if not p.exists()]
    if missing:
        print("[FAIL] missing files:")
        for p in missing:
            print(" -", p)
        return 1
    cfg = json.loads((ROOT / "configs/revo_futures_dryrun.example.json").read_text())
    assert cfg["dry_run"] is True
    assert cfg["trading_mode"] == "futures"
    assert cfg["margin_mode"] == "isolated"
    print("[OK] config dry_run futures isolated")
    print("[OK] strategy adapter present")
    print("[OK] default mode is safe: Freqtrade executes; Revo is alpha/shadow layer")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
