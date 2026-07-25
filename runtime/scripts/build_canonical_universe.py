#!/usr/bin/env python3
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_JSON = ROOT / "runtime" / "revo" / "canonical_universe.json"
OUT_TXT = ROOT / "runtime" / "revo" / "canonical_universe.txt"
ROOT_TXT = ROOT / "universe.txt"
BOTS_TXT = ROOT / "bots" / "universe.txt"
# Manual hard exclusions: symbols with unusable execution/feed characteristics.
EXCLUDED_SYMBOLS = {"AERGOUSDT"}


def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "nexus-canonical-universe/1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def bybit_symbols() -> set[str]:
    out = set()
    cursor = ""
    while True:
        qs = {"category": "linear", "limit": "1000"}
        if cursor:
            qs["cursor"] = cursor
        data = get_json("https://api.bybit.com/v5/market/instruments-info?" + urllib.parse.urlencode(qs))
        result = data.get("result", {})
        out.update(
            r["symbol"].upper()
            for r in result.get("list", [])
            if r.get("status") == "Trading"
            and r.get("contractType") == "LinearPerpetual"
            and r.get("quoteCoin") == "USDT"
        )
        cursor = result.get("nextPageCursor") or ""
        if not cursor:
            return out


def binance_symbols() -> set[str]:
    data = get_json("https://fapi.binance.com/fapi/v1/exchangeInfo")
    return {
        r["symbol"].upper()
        for r in data.get("symbols", [])
        if r.get("contractType") == "PERPETUAL"
        and r.get("quoteAsset") == "USDT"
        and r.get("status") == "TRADING"
    }


def main() -> None:
    bybit = bybit_symbols()
    binance = binance_symbols()
    common = sorted((bybit & binance) - EXCLUDED_SYMBOLS)
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "ts": now,
        "kind": "canonical_bybit_binance_usdt_perp_intersection",
        "bybit_count": len(bybit),
        "binance_count": len(binance),
        "count": len(common),
        "pairs": common,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n")
    txt = "\n".join(common) + "\n"
    OUT_TXT.write_text(txt)
    ROOT_TXT.write_text(txt)
    BOTS_TXT.write_text(txt)
    print(json.dumps({k: payload[k] for k in ("bybit_count", "binance_count", "count")}, indent=2))
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "RIFUSDT"):
        print(sym, sym in common)


if __name__ == "__main__":
    main()
