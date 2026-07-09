# ---- Build stage ----
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

COPY bots/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Runtime stage ----
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 && \
    rm -rf /var/lib/apt/lists/* && \
    useradd --uid 1000 --create-home appuser

COPY --from=builder /install /usr/local

# Copy fusionnew engine code
COPY fusionnew/ ./fusionnew/
# Copy nexus bot files
COPY bots/ ./bots/

ENV PYTHONPATH=/app:/app/fusionnew
ENV NEXUS_API_URL=http://fastapi:8000

USER appuser

CMD ["python", "-u", "bots/run_bot.py"]
