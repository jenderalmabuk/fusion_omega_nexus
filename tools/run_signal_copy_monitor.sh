#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
pid=$(systemctl show -p MainPID --value fusion-gateway.service)
[[ "$pid" =~ ^[1-9][0-9]*$ ]] || { echo "fusion-gateway.service not running"; exit 1; }
token=$(tr '\0' '\n' <"/proc/$pid/environ" | sed -n 's/^GATEWAY_TOKEN=//p')
[[ -n "$token" ]] || { echo "GATEWAY_TOKEN not found in gateway process"; exit 1; }
export GATEWAY_TOKEN="$token" GATEWAY_URL="http://127.0.0.1:8787/gateway"
exec python3 tools/signal_copy_monitor.py --watch "${1:-5}"
