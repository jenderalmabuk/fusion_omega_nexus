# Nexus — Install on New Server

Install repo commit `f58a795` plus private backup archive. Restore code, DB, runtime state, journal, `.env`, and Telegram session.

## 1. New server prerequisites

Ubuntu/Debian example:

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates
```

Install Docker Engine and Compose v2 using Docker's official guide:

<https://docs.docker.com/engine/install/>

Verify:

```bash
docker --version
docker compose version
git --version
```

User must run Docker without `sudo`, or prefix Docker commands with `sudo` consistently:

```bash
sudo usermod -aG docker "$USER"
# Log out and back in, then verify:
docker ps
```

Recommended disk: **at least 30 GB free**. Current database backup is about 699 MB compressed and restores to about 7.3 GB.

## 2. Clone exact branch

```bash
git clone https://github.com/jenderalmabuk/fusion_omega_nexus.git
cd fusion_omega_nexus
git checkout feat/signalcopy-isolated-gateway-regime-entry
git pull --ff-only origin feat/signalcopy-isolated-gateway-regime-entry
```

Expected latest gateway commit:

```text
f58a795 feat(gateway): containerize execution gateway
```

Do not commit `.env`, Telegram session files, runtime state, journal, or backup archive.

## 3. Upload private backup

Upload backup archive from local machine to repo directory on new server:

```bash
scp nexus_backup_20260724T150242Z.tar.gz user@NEW_SERVER:~/fusion_omega_nexus/
```

Or use rsync:

```bash
rsync -avP nexus_backup_20260724T150242Z.tar.gz user@NEW_SERVER:~/fusion_omega_nexus/
```

On new server, verify archive exists:

```bash
cd ~/fusion_omega_nexus
gzip -t nexus_backup_20260724T150242Z.tar.gz
ls -lh nexus_backup_20260724T150242Z.tar.gz
```

Archive contains secrets and Telegram auth. Never publish it, commit it, or upload it to GitHub.

## 4. Restore and start full stack

Run from repo root:

```bash
./scripts/deploy_new_server.sh ./nexus_backup_20260724T150242Z.tar.gz
```

Script performs:

1. Extract backup into temporary staging directory.
2. Restore `.env`, `*.session`, `runtime/`, and `journal/`.
3. Start TimescaleDB and wait for readiness.
4. Restore compressed PostgreSQL/TimescaleDB dump.
5. Build `nexus-bot:latest`.
6. Start Compose stack with `bots` profile.
7. Print container status.

Gateway now runs as Compose service `nexus_gateway`; no Hermes venv and no systemd unit needed.

## 5. Verify gateway

Load token from `.env` without printing it:

```bash
GATEWAY_TOKEN_VALUE="$(grep '^GATEWAY_TOKEN=' .env | cut -d= -f2-)"
curl -fsS \
  -H "Authorization: Bearer $GATEWAY_TOKEN_VALUE" \
  http://127.0.0.1:8787/gateway/health
```

Expected:

```json
{"status":"ok","service":"execution-gateway"}
```

Verify portfolio:

```bash
curl -fsS \
  -H "Authorization: Bearer $GATEWAY_TOKEN_VALUE" \
  http://127.0.0.1:8787/gateway/portfolio
```

Expected initial state after clean restore depends on archived state. Check `equity`, `open_position_count`, and `open_positions`; do not assume positions are empty.

Verify gateway container:

```bash
docker ps --filter name=nexus_gateway
docker logs --tail 50 nexus_gateway
```

Expected log includes:

```text
single execution point ready on /gateway
Application startup complete.
```

## 6. Verify signal-copy and bot stack

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}' | sort
```

Expected services include:

- `nexus_gateway`
- `nexus_signal_copy`
- `nexus_fastapi`
- `nexus_timescaledb`
- `nexus_binance_collector`
- `nexus_bybit_collector`
- `nexus_fusionnew_h1`
- `nexus_fusionnew_m30`
- `nexus_h1_imbalance`
- `nexus_m30_imbalance`
- `revo_adaptive_signal_bybit_paper`
- `revo_auditable_core_bybit_paper`
- `nexus_oi_rollup`
- `nexus_scanner`

Check signal-copy gateway route:

```bash
docker exec nexus_signal_copy sh -c \
  'python3 -c "import os,urllib.request,json; u=os.environ[\"GATEWAY_URL\"]+\"/portfolio\"; r=urllib.request.Request(u,headers={\"Authorization\":\"Bearer \"+os.environ[\"GATEWAY_TOKEN\"]}); d=json.load(urllib.request.urlopen(r,timeout=8)); print(os.environ[\"GATEWAY_URL\"], d.get(\"equity\"), d.get(\"open_position_count\"))"'
```

Expected route:

```text
http://gateway:8787/gateway
```

Check signal-copy logs:

```bash
docker logs --tail 100 nexus_signal_copy
```

Expected:

```text
[SIGNAL COPY] LIVE mode: Execution Gateway active
```

## 7. Verify DB restore

```bash
docker exec nexus_timescaledb psql -U nexus -d nexus -tc \
  "SELECT count(*) FROM klines;"
```

Check database size:

```bash
docker exec nexus_timescaledb psql -U nexus -d nexus -tc \
  "SELECT pg_size_pretty(pg_database_size('nexus'));"
```

Check TimescaleDB health:

```bash
docker inspect --format '{{.State.Health.Status}}' nexus_timescaledb
```

Expected: `healthy`.

## 8. LLM / 9router setup

Gateway containerization does not install or migrate 9router. Install and configure 9router separately, as planned.

Signal-copy expects the 9router endpoint from `.env`, currently shaped like:

```text
NINE_ROUTER_BASE=http://host.docker.internal:20128/v1
```

On new server, 9router must listen on host port `20128`. Verify before enabling LLM-gated signal processing:

```bash
curl -fsS http://127.0.0.1:20128/v1/models >/dev/null && echo "9router reachable"
```

Restore your 9router model combos and DB manually. Do not put 9router DB or API keys in git.

## 9. Delete private archive after verified restore

After DB, gateway, signal-copy, and all engines pass checks:

```bash
rm -f ~/fusion_omega_nexus/nexus_backup_*.tar.gz
```

Confirm:

```bash
ls nexus_backup_*.tar.gz 2>/dev/null || echo "backup archive deleted"
```

Keep a second encrypted copy offline if rollback matters.

## Rollback notes

- Gateway state is runtime data; restoring an archive restores its captured journal/runtime state.
- Gateway container uses `journal/` bind mount and runs as uid `1000:1000` so PnL writes survive container recreation.
- `docker compose ... down` stops services but does not delete named volumes unless `-v` is used.
- Never run `docker compose down -v` unless intentional DB destruction is confirmed.
- Do not expose port `8787` publicly; Compose binds it to `127.0.0.1`.

## Common failures

### `archive not found`

Pass absolute or correct relative archive path:

```bash
./scripts/deploy_new_server.sh "$HOME/fusion_omega_nexus/nexus_backup_20260724T150242Z.tar.gz"
```

### `NEXUS_DB_PASSWORD is required`

`.env` was not restored or Compose cannot read it. Recheck archive contents and rerun deploy.

### Gateway `401`

`.env` has a token mismatch. Check only hashes, never print token:

```bash
sha256sum <(grep '^GATEWAY_TOKEN=' .env | cut -d= -f2-)
docker exec nexus_signal_copy sh -c 'printf "%s" "$GATEWAY_TOKEN"' | sha256sum
```

### Signal-copy gets connection refused

Check gateway and route:

```bash
docker ps --filter name=nexus_gateway
docker exec nexus_signal_copy sh -c 'printf "%s\n" "$GATEWAY_URL"'
docker logs --tail 100 nexus_gateway
```

Route must be `http://gateway:8787/gateway` when both services use this Compose file.

### 9router errors

Gateway can remain healthy while LLM calls fail. Check 9router separately on host port `20128`; this migration does not install it.

## Security

- Backup archive contains `.env` credentials and Telegram session auth.
- Use `scp`/`rsync` over SSH only.
- Do not paste archive contents into chat or tickets.
- Delete archive from VPS after verified restore.
- Never commit `.env`, `*.session`, runtime, journal, or backup archives.
- Do not expose gateway, database, or 9router ports to the public internet.

ponytail: this guide assumes same CPU architecture and Docker Compose v2; add platform-specific image builds and health-gated deployment when deploying across mixed architectures.
