#!/usr/bin/env python3
from pathlib import Path

p = Path("user_data/audit_fusion_probe_repair_v12.py")
if not p.exists():
    raise SystemExit("[ERROR] missing user_data/audit_fusion_probe_repair_v12.py")
s = p.read_text(encoding="utf-8")
for token in [
    "run_exact_strategy_pipeline",
    "entry_probe_match_v12",
    "FEATURE MEANS BY EXIT REASON",
    "FUSION_PROBE_REPAIR_V12_COMPACT_FOR_CHAT.txt",
]:
    if token not in s:
        raise SystemExit(f"[ERROR] missing token: {token}")
print("[OK] Fusion Probe Repair v1.2 smoke check PASS")
