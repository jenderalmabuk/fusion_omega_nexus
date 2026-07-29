"""Observer-only Fusion Quantum readiness audit. No orders, state writes, or Telegram."""
import json
import re
import subprocess
import time
from statistics import median

import httpx


def logs(container: str, since: str = "1h") -> str:
    return subprocess.run(
        ["docker", "logs", "--since", since, container],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    ).stdout


def db(sql: str) -> list[str]:
    out = subprocess.run(
        ["docker", "exec", "nexus_timescaledb", "psql", "-U", "nexus", "-d", "nexus", "-Atc", sql],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def main() -> None:
    b, y = logs("nexus_binance_collector"), logs("nexus_bybit_collector")
    t0 = time.time()
    r = httpx.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=20)
    binance_probe = {
        "status": r.status_code,
        "latency_ms": round((time.time() - t0) * 1000),
        "used_weight_1m": int(r.headers.get("x-mbx-used-weight-1m", 0)),
        "retry_after": r.headers.get("retry-after"),
    }
    cycle_b = [float(x) for x in re.findall(r"cycle done in ([0-9.]+)s", b)]
    cycle_y = [float(x) for x in re.findall(r"cycle done in ([0-9.]+)s", y)]
    lag = db("SELECT exchange,round(percentile_cont(.5) within group(order by extract(epoch from now()-max_close))::numeric,1),round(percentile_cont(.95) within group(order by extract(epoch from now()-max_close))::numeric,1) FROM (SELECT exchange,symbol,max(close_time) max_close FROM klines WHERE timeframe='15m' AND close_time > now()-interval '1 day' GROUP BY exchange,symbol) x GROUP BY exchange ORDER BY exchange;")
    report = {
        "observer_only": True,
        "window": "1h",
        "binance": {"http_429": b.count("429"), "http_400": b.count("400 Bad Request"), "errors": b.count(" ERR "), "cycle_median_sec": median(cycle_b) if cycle_b else None, **binance_probe},
        "bybit": {"http_429": y.count("429"), "errors": y.count(" ERR "), "cycle_median_sec": median(cycle_y) if cycle_y else None},
        "closed_15m_db_lag_sec": {row.split("|")[0]: {"p50": float(row.split("|")[1]), "p95": float(row.split("|")[2])} for row in lag},
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
