#!/bin/bash
# Disk health monitoring for Nexus pipeline
# Run via cron or manually to check disk usage

THRESHOLD=85
ALERT_CHAT="${TELEGRAM_CHAT_ID:-}"
ALERT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"

echo "=== Disk Health Check @ $(date) ==="

# Current usage
USAGE=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')
echo "Disk usage: ${USAGE}%"

# Nexus data size
DATA_SIZE=$(du -sh /home/fusion_omega/fusion_omega_nexus/data 2>/dev/null | cut -f1)
echo "Nexus data: ${DATA_SIZE}"

# Parquet file counts
for tf in 1m 5m 30m 1h; do
  count=$(find /home/fusion_omega/fusion_omega_nexus/data/$tf -name '*.parquet' 2>/dev/null | wc -l)
  echo "  $tf: $count files"
done

# Docker usage
echo "Docker volumes: $(docker system df --format '{{.Volumes}} {{.Size}}' 2>/dev/null | tail -1)"

# Git repo size
GIT_SIZE=$(du -sh /home/fusion_omega/fusion_omega_nexus/.git 2>/dev/null | cut -f1)
echo "Git objects: ${GIT_SIZE}"

# Alert if threshold exceeded
if [ "$USAGE" -gt "$THRESHOLD" ]; then
  MSG="⚠️ DISK WARNING: ${USAGE}% full (threshold: ${THRESHOLD}%)"
  echo "$MSG"
  
  if [ -n "$ALERT_TOKEN" ] && [ -n "$ALERT_CHAT" ]; then
    curl -s -X POST "https://api.telegram.org/bot${ALERT_TOKEN}/sendMessage" \
      -d "chat_id=${ALERT_CHAT}" \
      -d "text=${MSG}" > /dev/null
  fi
fi

echo "=== Check complete ==="
