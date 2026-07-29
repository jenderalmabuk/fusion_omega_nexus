"""BTC-regime directional ablation for Fusion Quantum H4+M15.

Entry/SL/TP/RR/expiry formulas are NOT modified. Variants only grant or deny
direction permission at entry time, using the last CLOSED BTC 4h candle.
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fusion_quantum.backtest_quantum import (  # noqa: E402
    TIERS, ZoneLifecycle, fetch_klines, metrics, simulate,
)

RUNTIME_EXIT_MODE = "baseline_2r"   # runtime = full TP 2R
H4 = pd.Timedelta(hours=4)


def btc_features() -> pd.DataFrame:
    b = fetch_klines("BTCUSDT", "4h", 180).copy()
    b["open_time"] = pd.to_datetime(b["open_time"])
    b = b.sort_values("open_time").reset_index(drop=True)
    b["ret_pct"] = (b["close"] / b["open"] - 1.0) * 100.0
    b["ema20"] = b["close"].ewm(span=20, adjust=False).mean()
    b["ema20_slope"] = b["ema20"].diff()
    b["closed_at"] = b["open_time"] + H4
    return b


def attach_btc(trades: list[dict], btc: pd.DataFrame) -> list[dict]:
    """Attach the last BTC 4h candle already CLOSED at each entry_time."""
    if not trades:
        return []
    tr = pd.DataFrame(trades)
    tr["ts"] = pd.to_datetime(tr["entry_time"])
    tr = tr.sort_values("ts").reset_index(drop=True)
    joined = pd.merge_asof(
        tr, btc[["closed_at", "ret_pct", "close", "ema20", "ema20_slope"]],
        left_on="ts", right_on="closed_at", direction="backward",
    )
    return joined.dropna(subset=["closed_at"]).to_dict("records")


VARIANTS = {
    "baseline": lambda t: True,
    "A_block_short_btc_green": lambda t: not (t["side"] == "BEAR" and t["ret_pct"] > 0),
    "B_block_short_btc_above_ema20_rising": lambda t: not (
        t["side"] == "BEAR" and t["close"] > t["ema20"] and t["ema20_slope"] > 0
    ),
    "C_block_both_momentum_025": lambda t: not (
        (t["side"] == "BEAR" and t["ret_pct"] > 0.25)
        or (t["side"] == "BULL" and t["ret_pct"] < -0.25)
    ),
    "D_long_only": lambda t: t["side"] == "BULL",
}


def split(rows: list[dict]) -> dict:
    rows = sorted(rows, key=lambda x: x["entry_time"])
    if not rows:
        return {"all": metrics([]), "is": metrics([]), "oos": metrics([])}
    cutoff = rows[int(len(rows) * 0.6)]["entry_time"]
    return {
        "cutoff": cutoff,
        "all": metrics(rows),
        "is": metrics([x for x in rows if x["entry_time"] < cutoff]),
        "oos": metrics([x for x in rows if x["entry_time"] >= cutoff]),
        "long": metrics([x for x in rows if x["side"] == "BULL"]),
        "short": metrics([x for x in rows if x["side"] == "BEAR"]),
    }


def main() -> None:
    universe = ROOT / "runtime/revo/canonical_universe.txt"
    syms = [x.strip() for x in open(universe) if x.strip()]
    fetch_klines("BTCUSDT", "1h", 180)          # preload cache before threads
    btc = btc_features()
    print(f"btc 4h candles={len(btc)} range={btc.open_time.iloc[0]} -> {btc.open_time.iloc[-1]}", flush=True)

    t0 = time.time()
    rows, errors = [], []
    with ThreadPoolExecutor(max_workers=10) as ex:
        fs = {ex.submit(simulate, s, "H4_M15", 180, RUNTIME_EXIT_MODE): s for s in syms}
        for n, f in enumerate(as_completed(fs), 1):
            try:
                rows.append(f.result())
            except Exception as exc:                       # noqa: BLE001
                errors.append({"symbol": fs[f], "error": repr(exc)})
            if n % 100 == 0:
                print(f"{n}/{len(syms)}", flush=True)

    trades = [t for r in rows for t in r.get("trades", [])]
    tagged = attach_btc(trades, btc)
    funnel = {k: sum(r.get("funnel", {}).get(k, 0) for r in rows) for k in ZoneLifecycle().funnel}

    out = {
        "exit_mode": RUNTIME_EXIT_MODE,
        "tier": list(TIERS),
        "elapsed_sec": round(time.time() - t0, 1),
        "symbols": len(syms),
        "short_history": sum(r.get("short", False) for r in rows),
        "errors": errors,
        "funnel": funnel,
        "trades_total": len(trades),
        "trades_tagged": len(tagged),
        "date_range": {
            "start": min((t["entry_time"] for t in tagged), default=None),
            "end": max((t["entry_time"] for t in tagged), default=None),
        },
        "variants": {},
    }
    for name, rule in VARIANTS.items():
        kept = [t for t in tagged if rule(t)]
        blocked = [t for t in tagged if not rule(t)]
        res = split(kept)
        res["blocked"] = {
            "n": len(blocked),
            "blocked_net_r": round(sum(t["pnl_unit"] for t in blocked), 3),
            "blocked_winners": sum(1 for t in blocked if t["pnl_unit"] > 0),
            "blocked_losers": sum(1 for t in blocked if t["pnl_unit"] <= 0),
        }
        out["variants"][name] = res
        print(name, json.dumps({k: res[k] for k in ("all", "is", "oos", "blocked")}), flush=True)

    dest = ROOT / "fusion_quantum/results/h4_m15_btc_regime_ablation.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2))
    print("written", dest, flush=True)


if __name__ == "__main__":
    main()
