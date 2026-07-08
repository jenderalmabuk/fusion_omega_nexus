"""Faithful backtest of the user's REAL method (see backtest/STRATEGY_SPEC.md).

Nested OB cascade + mandatory SMA50/200 HTF trend (anti-chop) + 5m/3m engulfing GATE
(reuses check_engulfing_at_ob) + fib-0.618 LIMIT entry with expiry (reuses
compute_fibonacci_entry) + OB mitigation + resistance TP.  Walk-forward 60/40.  LONG-only.

CLI:
  python -m backtest.faithful --tier A --days 180 --symbols BTCUSDT ETHUSDT SOLUSDT
  python -m backtest.faithful --tier B --days 180 --symbols BTCUSDT ETHUSDT SOLUSDT
"""
from __future__ import annotations

import os

os.environ.setdefault("LOG_DIR", "backtest/logs")

import argparse  # noqa: E402
from typing import Any, Dict, List  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest.data import fetch_klines  # noqa: E402
from signals.engulfing_detector import check_engulfing_at_ob, compute_fibonacci_entry  # noqa: E402

MAKER_FEE = 0.0002   # limit entry
TAKER_FEE = 0.0004   # market-ish exit
SLIP = 0.0003        # exit slippage
EQUITY0 = 1000.0

# Tier configs: (htf, entry_tf, trig_primary, trig_fallback, risk_pct)
TIERS = {
    "A": {"htf": "4h", "entry_tf": "15m", "trig": "5m", "trig_fb": "3m", "risk": 0.01},
    "B": {"htf": "1h", "entry_tf": "5m", "trig": "3m", "trig_fb": "3m", "risk": 0.005},
}

SEP_MIN = 0.003       # anti-chop: min (SMA50-SMA200)/price
TAP_WINDOW = 24       # entry-TF bars after first tap to still act on the OB
FIB_EXPIRY = 8        # trigger bars the fib limit stays valid
MAX_HOLD = 240        # trigger bars max hold before scratch exit
RES_LOOKBACK = 40     # entry-TF bars to find resistance (swing high)
SLICE = 14            # bars to pass into check_engulfing_at_ob (needs >=10 for vol avg)


def _atr(df: pd.DataFrame, n: int = 14) -> np.ndarray:
    h, low, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - low, (h - c.shift()).abs(), (low - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean().to_numpy()


def _htf_trend_ok(htf: pd.DataFrame) -> pd.DataFrame:
    sma50 = htf["close"].rolling(50).mean()
    sma200 = htf["close"].rolling(200).mean()
    aligned = (htf["close"] > sma50) & (sma50 > sma200)
    sep_ok = ((sma50 - sma200) / htf["close"]) >= SEP_MIN
    return pd.DataFrame({"open_time": htf["open_time"], "trend_ok": (aligned & sep_ok).fillna(False)})


def _precompute_bull_obs(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Bullish demand OBs: red candle + bullish FVG (high[i] < low[i+2]). Same as smc_engine."""
    o, c, h, low = (df[x].to_numpy() for x in ("open", "close", "high", "low"))
    t = df["open_time"].to_numpy()
    obs = []
    for i in range(len(df) - 2):
        if c[i] < o[i] and h[i] < low[i + 2]:
            obs.append({"i": i, "zlow": float(low[i]), "zhigh": float(h[i]), "t_form": t[i]})
    return obs


def _trend_ok_at(trend_df: pd.DataFrame, ts: np.datetime64) -> bool:
    idx = trend_df["open_time"].values.searchsorted(ts, side="right") - 1
    if idx < 0:
        return False
    return bool(trend_df["trend_ok"].iloc[idx])


def _resistance_above(entry_df: pd.DataFrame, ts: np.datetime64, entry: float) -> float:
    e = entry_df["open_time"].values.searchsorted(ts, side="right") - 1
    if e <= 0:
        return np.nan
    lo = max(0, e - RES_LOOKBACK)
    window_hi = entry_df["high"].to_numpy()[lo:e]
    if len(window_hi) == 0:
        return np.nan
    res = float(window_hi.max())
    return res if res > entry * 1.001 else np.nan


def _simulate_symbol(symbol: str, tier: str, days: int, sl_mode: str = "candle") -> List[Dict[str, Any]]:
    cfg = TIERS[tier]
    entry_df = fetch_klines(symbol, cfg["entry_tf"], days)
    trig = fetch_klines(symbol, cfg["trig"], days)
    trig_fb = fetch_klines(symbol, cfg["trig_fb"], days)
    htf = fetch_klines(symbol, cfg["htf"], days)
    if min(len(entry_df), len(trig), len(htf)) < 250:
        return []

    trend_df = _htf_trend_ok(htf)
    obs = _precompute_bull_obs(entry_df)
    e_low = entry_df["low"].to_numpy()
    e_close = entry_df["close"].to_numpy()
    e_time = entry_df["open_time"].to_numpy()
    trig_time = trig["open_time"].to_numpy()
    trig_low = trig["low"].to_numpy()
    trig_high = trig["high"].to_numpy()
    trig_atr = _atr(trig)
    risk_pct = cfg["risk"]

    trades: List[Dict[str, Any]] = []
    for ob in obs:
        i, zlow, zhigh = ob["i"], ob["zlow"], ob["zhigh"]
        n_e = len(entry_df)
        # first tap (entry-TF) + invalidation (close below zone low)
        ft = None
        for j in range(i + 2, n_e):
            if e_close[j] < zlow:           # invalidated before any tap
                break
            if e_low[j] <= zhigh:           # first tap into zone
                ft = j
                break
        if ft is None:
            continue
        # action window on entry-TF: [ft, ft+TAP_WINDOW] until invalidation
        end_e = ft
        for j in range(ft, min(n_e, ft + TAP_WINDOW)):
            if e_close[j] < zlow:
                break
            end_e = j
        t_start, t_end = e_time[ft], e_time[end_e]
        if not _trend_ok_at(trend_df, t_start):
            continue

        # scan primary trigger bars within the action window for a confirmed engulfing
        s0 = trig_time.searchsorted(t_start, side="left")
        s1 = trig_time.searchsorted(t_end, side="right")
        setup = None
        for s in range(max(s0, SLICE), min(s1, len(trig))):
            price = float(trig["close"].iloc[s])
            if not (zlow <= price <= zhigh * 1.0005):   # must be interacting with the OB
                continue
            tb = trig_time[s]
            fb_end = trig_fb["open_time"].values.searchsorted(tb, side="right")
            res = check_engulfing_at_ob(
                symbol=symbol,
                df_15m=entry_df.iloc[max(0, i - 2):ft + 1],
                df_5m=trig.iloc[s - SLICE + 1:s + 1],
                df_3m=trig_fb.iloc[max(0, fb_end - SLICE):fb_end],
                ob_15m_zone={"low": zlow, "high": zhigh},
                ob_4h_zone={},
                direction="LONG",
            )
            if res.get("engulfing_confirmed"):
                conf_tf = res.get("timeframe")
                cand = trig.iloc[s] if conf_tf == "5M" else trig_fb.iloc[fb_end - 1]
                setup = {"s": s, "tb": tb, "candle": cand, "tf": conf_tf}
                break
        if setup is None:
            continue

        # fib-0.618 LIMIT entry (reused) + SL + TP
        entry = compute_fibonacci_entry(setup["candle"], fib_level=0.618)
        engulf_low = float(setup["candle"]["low"])
        if sl_mode == "zone":
            sl = zlow - 0.5 * trig_atr[setup["s"]]        # below the OB demand zone
        else:
            sl = engulf_low - 0.5 * trig_atr[setup["s"]]  # below the confirmation candle
        if not (sl < entry):
            continue
        tp = _resistance_above(entry_df, setup["tb"], entry)
        if np.isnan(tp) or tp <= entry:
            continue
        risk = entry - sl

        # fill within FIB_EXPIRY trigger bars (limit), then SL-first management
        s = setup["s"]
        fill_idx = None
        for f in range(s + 1, min(s + 1 + FIB_EXPIRY, len(trig))):
            if trig_low[f] <= entry:
                fill_idx = f
                break
        if fill_idx is None:
            continue
        exit_price, exit_reason = None, None
        for m in range(fill_idx + 1, min(fill_idx + 1 + MAX_HOLD, len(trig))):
            if trig_low[m] <= sl:
                exit_price, exit_reason = sl, "SL"
                break
            if trig_high[m] >= tp:
                exit_price, exit_reason = tp, "TP"
                break
        if exit_price is None:
            exit_price = float(trig["close"].iloc[min(fill_idx + MAX_HOLD, len(trig) - 1)])
            exit_reason = "TIME"

        gross = exit_price - entry
        fees = entry * MAKER_FEE + exit_price * (TAKER_FEE + SLIP)
        per_unit = gross - fees
        trades.append({
            "symbol": symbol, "tier": tier, "t_entry": trig_time[fill_idx],
            "entry": entry, "sl": sl, "tp": tp, "exit": exit_price, "reason": exit_reason,
            "per_unit": per_unit, "risk": risk, "risk_pct": risk_pct,
        })
    return trades


def _metrics(trades: List[Dict[str, Any]], label: str) -> None:
    if not trades:
        print(f"[{label}] trades=0 (no setups)")
        return
    equity = EQUITY0
    pnls, eq_curve = [], []
    for t in trades:
        qty = (equity * t["risk_pct"]) / max(t["risk"], 1e-9)
        pnl = qty * t["per_unit"]
        equity += pnl
        pnls.append(pnl)
        eq_curve.append(equity)
    pnls = np.array(pnls)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    wr = len(wins) / len(pnls) * 100
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    net = pnls.sum()
    payoff = (wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else 0.0
    peak, maxdd = -1e18, 0.0
    for e in eq_curve:
        peak = max(peak, e)
        maxdd = max(maxdd, peak - e)
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    print(f"[{label}] trades={len(pnls)} winrate={wr:.1f}% PF={pf:.2f} net={net:.2f} "
          f"expectancy={net/len(pnls):.3f} payoff={payoff:.2f} maxDD={maxdd:.2f} exits={reasons}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["A", "B"], default="A")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    ap.add_argument("--oos", type=float, default=0.40)
    ap.add_argument("--sl-mode", choices=["candle", "zone"], default="candle")
    a = ap.parse_args()

    all_trades: List[Dict[str, Any]] = []
    for sym in a.symbols:
        try:
            tr = _simulate_symbol(sym, a.tier, a.days, a.sl_mode)
            all_trades.extend(tr)
            print(f"  {sym} tier {a.tier}: {len(tr)} setups")
        except Exception as exc:
            print(f"  {sym} ERROR: {exc}")
    all_trades.sort(key=lambda t: t["t_entry"])

    print("=" * 70)
    _metrics(all_trades, f"FAITHFUL TIER {a.tier} | ALL")
    if all_trades:
        split = int(len(all_trades) * (1 - a.oos))
        _metrics(all_trades[split:], f"FAITHFUL TIER {a.tier} | OUT-OF-SAMPLE {int(a.oos*100)}%")
    print("VERDICT: edge is real only if OUT-OF-SAMPLE PF > 1.2 with enough trades.")


if __name__ == "__main__":
    main()
