"""Order-placement delay ablation for Fusion Quantum H4+M15.

Hypothesis: live PF (0.55) collapses vs backtest PF (2.45) because live places the
limit ~18 min AFTER the M15 candle closes, while the backtest places it at
`conf.i + 1`. Fast fills (backtest winners) are gone by then, leaving adverse
selection.

Entry / SL / TP / RR formulas are byte-identical to backtest_quantum.simulate().
ONLY two things vary:
  delay_bars  -- how many M15 bars after confirmation the limit becomes active
  expiry_bars -- fill window measured from the confirmation bar (live anchors
                 expires_at to confirmed_at, so delay EATS the window)
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fusion_quantum.backtest_quantum import (  # noqa: E402
    CONFIRM_WINDOW, RR, TIERS, ZoneLifecycle, _atr, _imbalances, _valid_obs,
    fetch_klines, manage_quantum_exit, metrics, mss_confirm,
)

MODE = "baseline_2r"          # runtime = full TP 2R


def simulate_delay(sym: str, delay_bars: int, expiry_bars: int,
                   window_follows_delay: bool) -> dict:
    ztf, ltf_tf = TIERS["H4_M15"]
    z, l = fetch_klines(sym, ztf, 180), fetch_klines(sym, ltf_tf, 180)
    if len(z) < 50 or len(l) < 300:
        return {"symbol": sym, "short": True}
    atr = _atr(l)
    lows, highs, closes = l.low.to_numpy(), l.high.to_numpy(), l.close.to_numpy()
    trades: list[dict] = []
    lifecycle = ZoneLifecycle()

    for side in ("BULL", "BEAR"):
        obs = _valid_obs(z, side)
        for im in _imbalances(l, side):
            ce = int(im["ce"])
            prior = [ob for ob in obs
                     if ob["t"] < im["t"] and im["leg_low"] <= ob["zhigh"] and im["leg_high"] >= ob["zlow"]]
            if not prior:
                continue
            zone = prior[-1]
            lifecycle.found(side, zone)
            entry0 = ((im["leg_low"] + 0.618 * (im["leg_high"] - im["leg_low"])) if side == "BULL"
                      else (im["leg_high"] - 0.618 * (im["leg_high"] - im["leg_low"])))
            start = None
            for touch in range(ce + 1, min(ce + CONFIRM_WINDOW + 1, len(l))):
                if (side == "BULL" and lows[touch] <= entry0) or (side == "BEAR" and highs[touch] >= entry0):
                    start = touch
                    break
            if start is None:
                continue
            if not lifecycle.start_retest(side, zone):
                continue
            conf = mss_confirm(l, zone, side, start, min(start + CONFIRM_WINDOW, len(l)))
            if not conf:
                continue
            lifecycle.mark("mss_confirmed")
            lo, hi = (conf["sweep"], conf["disp"]) if side == "BULL" else (conf["disp"], conf["sweep"])
            entry = (lo + 0.559 * (hi - lo)) if side == "BULL" else (hi - 0.559 * (hi - lo))
            risk = ((entry - (lo - 0.5 * atr[conf["i"]])) if side == "BULL"
                    else ((hi + 0.5 * atr[conf["i"]]) - entry))
            if not np.isfinite(risk) or risk <= 0:
                continue
            sl = entry - risk if side == "BULL" else entry + risk
            lifecycle.mark("pending_orders")

            # ---- the ONLY varied logic: when the limit is live, and until when
            first = conf["i"] + 1 + delay_bars
            last = conf["i"] + expiry_bars + (delay_bars if window_follows_delay else 0)
            fill = None
            for f in range(first, min(last + 1, len(l))):
                if (side == "BULL" and lows[f] <= entry) or (side == "BEAR" and highs[f] >= entry):
                    fill = f
                    break
            if fill is None:
                lifecycle.mark("expired_orders")
                continue
            lifecycle.mark("fills")
            pu, reason = manage_quantum_exit(side, entry, sl, risk, lows, highs, closes, atr, fill, MODE)
            trades.append({"symbol": sym, "side": side,
                           "entry_time": str(l.open_time.iloc[fill]),
                           "fill_offset": fill - conf["i"],
                           "pnl_unit": float(pu), "reason": reason})
    return {"symbol": sym, "trades": trades, "funnel": lifecycle.funnel}


def split(rows: list[dict]) -> dict:
    rows = sorted(rows, key=lambda x: x["entry_time"])
    if not rows:
        return {"all": metrics([]), "is": metrics([]), "oos": metrics([])}
    cutoff = rows[int(len(rows) * 0.6)]["entry_time"]
    return {
        "all": metrics(rows),
        "is": metrics([x for x in rows if x["entry_time"] < cutoff]),
        "oos": metrics([x for x in rows if x["entry_time"] >= cutoff]),
        "long": metrics([x for x in rows if x["side"] == "BULL"]),
        "short": metrics([x for x in rows if x["side"] == "BEAR"]),
    }


# (label, delay_bars, expiry_bars, window_follows_delay)
SCENARIOS = [
    ("bt_parity_d0_e12",      0, 12, False),   # published backtest baseline
    ("bt_d1_e12",             1, 12, False),
    ("bt_d2_e12",             2, 12, False),
    ("bt_d4_e12",             4, 12, False),
    ("runtime_d0_e6",         0,  6, False),   # runtime expiry, zero delay
    ("runtime_d1_e6",         1,  6, False),
    ("runtime_d2_e6",         2,  6, False),   # ~= live: ~18-30 min late
    ("runtime_d4_e6",         4,  6, False),
    ("d2_e6_window_kept",     2,  6, True),    # isolate delay from lost window
]


def main() -> None:
    syms = [x.strip() for x in open(ROOT / "runtime/revo/canonical_universe.txt") if x.strip()]
    fetch_klines("BTCUSDT", "1h", 180)      # warm cache before threads
    out = {"exit_mode": MODE, "symbols": len(syms), "scenarios": {}}

    for label, delay, expiry, follows in SCENARIOS:
        t0 = time.time()
        rows, errors = [], []
        with ThreadPoolExecutor(max_workers=10) as ex:
            fs = {ex.submit(simulate_delay, s, delay, expiry, follows): s for s in syms}
            for f in as_completed(fs):
                try:
                    rows.append(f.result())
                except Exception as exc:                       # noqa: BLE001
                    errors.append({"symbol": fs[f], "error": repr(exc)})
        trades = [t for r in rows for t in r.get("trades", [])]
        funnel = {k: sum(r.get("funnel", {}).get(k, 0) for r in rows) for k in ZoneLifecycle().funnel}
        res = split(trades)
        res |= {
            "delay_bars": delay, "expiry_bars": expiry, "window_follows_delay": follows,
            "delay_minutes": delay * 15, "elapsed_sec": round(time.time() - t0, 1),
            "errors": errors, "funnel": funnel,
            "fill_rate_pct": (round(100 * funnel["fills"] / funnel["pending_orders"], 1)
                              if funnel["pending_orders"] else None),
        }
        out["scenarios"][label] = res
        a = res["all"]
        print(f"{label:22} n={a['n']:4} wr={a['wr']:5} pf={a['pf']:6} net={a['net_unit']:9} "
              f"oos_pf={res['oos']['pf']:6} fill%={res['fill_rate_pct']} err={len(errors)}", flush=True)

    dest = ROOT / "fusion_quantum/results/h4_m15_delay_ablation.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2))
    print("written", dest, flush=True)


if __name__ == "__main__":
    main()
