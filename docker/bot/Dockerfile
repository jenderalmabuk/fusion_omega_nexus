# Nexus Bot — multi-stage, non-root
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY bots/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim

RUN groupadd -r nexus && useradd -r -g nexus -d /app -s /usr/sbin/nologin nexus

WORKDIR /app

COPY --from=builder /install /usr/local
# Copy fusionnew engine code
COPY --chown=nexus:nexus fusionnew/ ./fusionnew/
# Copy nexus bot files
COPY --chown=nexus:nexus bots/ ./bots/

ENV PYTHONPATH=/app:/app/fusionnew
ENV NEXUS_API_URL=http://fastapi:8000

USER nexus

CMD ["python", "-u", "bots/run_bot.py"]
