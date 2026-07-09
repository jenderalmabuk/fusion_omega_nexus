#!/usr/bin/env python3
from pathlib import Path

required_files = [
    "user_data/revo_alpha/flow_context.py",
    "user_data/revo_alpha/flow_ict_fusion.py",
    "user_data/revo_alpha/fusion_config.py",
    "user_data/strategies/RevoAlphaStrategy.py",
]
for f in required_files:
    if not Path(f).exists():
        raise SystemExit(f"[ERROR] missing: {f}")

s = Path("user_data/strategies/RevoAlphaStrategy.py").read_text(encoding="utf-8")
tokens = [
    "add_revo_flow_context_features",
    "add_revo_flow_ict_fusion_features",
]
for t in tokens:
    if t not in s:
        raise SystemExit(f"[ERROR] strategy token missing: {t}")

f = Path("user_data/revo_alpha/flow_ict_fusion.py").read_text(encoding="utf-8")
for t in [
    "revo_fusion_long_permission_shadow",
    "revo_fusion_short_permission_shadow",
    "revo_fusion_ranging_long_permission_shadow",
    "revo_fusion_ranging_short_permission_shadow",
]:
    if t not in f:
        raise SystemExit(f"[ERROR] fusion token missing: {t}")

print("[OK] Revo Flow x ICT Fusion Layer v1 smoke check PASS")
