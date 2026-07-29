#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
tmux kill-session -t 9 2>/dev/null || true
tmux new-session -d -s 9 "cd '$PWD' && python3 fusion_quantum/monitor.py"
echo "Fusion Quantum dashboard running: tmux attach -t 9"
