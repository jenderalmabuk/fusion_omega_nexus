#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Gateway runs in Docker now; retain systemd fallback for older installs.
token=""
if command -v docker >/dev/null 2>&1 && docker inspect nexus_gateway >/dev/null 2>&1; then
    status=$(docker inspect -f '{{.State.Status}}' nexus_gateway 2>/dev/null || true)
    [[ "$status" == "running" ]] || { echo "nexus_gateway not running (status: ${status:-missing})"; exit 1; }
    token=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' nexus_gateway \
        | sed -n 's/^GATEWAY_TOKEN=//p' | head -n 1)
else
    pid=$(systemctl show -p MainPID --value fusion-gateway.service 2>/dev/null || true)
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] || { echo "nexus_gateway and fusion-gateway.service not running"; exit 1; }
    token=$(tr '\0' '\n' <"/proc/$pid/environ" | sed -n 's/^GATEWAY_TOKEN=//p')
fi
[[ -n "$token" ]] || { echo "GATEWAY_TOKEN not found in gateway container/process"; exit 1; }
export GATEWAY_TOKEN="$token" GATEWAY_URL="http://127.0.0.1:8787/gateway"
exec python3 tools/signal_copy_monitor.py --watch "${1:-5}"
