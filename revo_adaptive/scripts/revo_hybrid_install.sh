#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p user_data/strategies user_data/revo_alpha/runtime user_data/revo_alpha/audit user_data/data
python -S -m py_compile \
  user_data/revo_alpha/schema.py \
  user_data/revo_alpha/bridge.py \
  user_data/revo_alpha/risk.py \
  user_data/revo_alpha/shadow.py \
  user_data/strategies/RevoAlphaStrategy.py \
  scripts/revo_hybrid_smoke_check.py \
  scripts/revo_signal_bus_example_writer.py
printf '[OK] Revo x Freqtrade hybrid overlay installed and syntax-checked.\n'
printf '[NEXT] python -S scripts/revo_signal_bus_example_writer.py\n'
printf '[NEXT] freqtrade trade --config configs/revo_futures_dryrun.example.json --strategy RevoAlphaStrategy --strategy-path user_data/strategies\n'
