#!/usr/bin/env python3
from pathlib import Path
import py_compile

p = Path("scripts/binance_flow_live_collector.py")
if not p.exists():
    raise SystemExit("[ERROR] missing scripts/binance_flow_live_collector.py")
py_compile.compile(str(p), doraise=True)
s = p.read_text(encoding="utf-8")
for token in ["openInterestHist", "takerlongshortRatio", "premiumIndex", "revo_flow_context.json"]:
    if token not in s:
        raise SystemExit(f"[ERROR] token missing: {token}")
print("[OK] Binance Flow Live Collector v1 smoke check PASS")
