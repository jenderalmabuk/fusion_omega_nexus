# Fusion Quantum H4 + 15m robustness

Dataset: 2026-07-10 through 2026-07-24, 465-symbol universe, 382 symbols with enough H4 history. PnL normalized to initial risk (R). All walk-forward boundaries keep identical timestamps on one side.

## Expiry ablation

| Expiry | Trades | All PF | IS PF | OOS PF | Net R | Max DD R | Test fold PF |
|---|---:|---:|---:|---:|---:|---:|---|
| 6 bars | 102 | 2.52 | 2.12 | 3.35 | 65.61 | 6.01 | 2.47 / 3.09 / 3.29 / 4.02 |
| 12 bars | 121 | 2.24 | 1.86 | 2.98 | 67.74 | 7.03 | 2.47 / 3.69 / 2.13 / 3.13 |
| 24 bars | 131 | 2.25 | 1.97 | 2.78 | 73.48 | 7.03 | 2.05 / 5.07 / 2.68 / 2.58 |

Candidate: 6 bars (90 minutes), due to highest PF and lowest drawdown. Treat as provisional until long-history test.

## Cost stress (12-bar expiry)

| Cost multiplier | All PF | IS PF | OOS PF | Net R | Max DD R | Test fold PF |
|---|---:|---:|---:|---:|---:|---|
| 1x | 2.24 | 1.86 | 2.98 | 67.74 | 7.03 | 2.47 / 3.69 / 2.13 / 3.13 |
| 2x | 2.00 | 1.66 | 2.69 | 58.40 | 8.06 | 2.21 / 3.26 / 1.91 / 2.83 |
| 3x | 1.79 | 1.48 | 2.42 | 49.06 | 9.10 | 1.98 / 2.89 / 1.71 / 2.55 |
| 5x | 1.43 | 1.19 | 1.90 | 30.39 | 11.16 | 1.59 / 2.30 / 1.32 / 2.08 |

All four folds remain profitable at 5x costs. Cost break-even was not reached.

## Limitation

Only about 15 days of common history and 49 trades in the 40% OOS segment. This is robustness evidence, not production approval. Require 60–90 days, multi-regime walk-forward, then isolated paper-forward.
