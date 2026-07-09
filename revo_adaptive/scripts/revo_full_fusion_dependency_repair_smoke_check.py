#!/usr/bin/env python3
from pathlib import Path

paths = [
    "user_data/revo_alpha/indicators.py",
    "user_data/revo_alpha/institutional_concepts.py",
    "user_data/revo_alpha/flow_context.py",
    "user_data/revo_alpha/flow_ict_fusion.py",
    "user_data/revo_alpha/fusion_config.py",
    "user_data/strategies/RevoAlphaStrategy.py",
]

for p in paths:
    if not Path(p).exists():
        raise SystemExit(f"[ERROR] missing: {p}")

s = Path("user_data/strategies/RevoAlphaStrategy.py").read_text(encoding="utf-8")

ordered = [
    "add_revo_indicator_features(dataframe, metadata=metadata)",
    "add_revo_institutional_features(dataframe, metadata=metadata)",
    "add_revo_flow_context_features(dataframe, pair=pair, metadata=metadata)",
    "self.bridge.enrich_dataframe(dataframe, pair)",
    "add_revo_flow_ict_fusion_features(dataframe, metadata=metadata)",
]

positions = []
for token in ordered:
    idx = s.find(token)
    if idx == -1:
        raise SystemExit(f"[ERROR] pipeline token missing: {token}")
    positions.append(idx)

if positions != sorted(positions):
    raise SystemExit("[ERROR] pipeline order is wrong")

print("[OK] Full Fusion dependency repair smoke check PASS")
