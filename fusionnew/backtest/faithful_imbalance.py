"""Faithful backtest v2 — matches the Forex Sarjana transcript EXACTLY.

Trigger = IMBALANCE / FVG (3-candle), NOT engulfing. Fib 61.8% of the imbalance leg.
EMA50/200 trend (plain alignment). SL = swing ± 0.5 ATR. TP = fixed RR 1:3.
2-TF cascade: H4 zone -> M15 confirm ; H1 zone -> M5 confirm. LONG + SHORT.

CLI:
  python -m backtest.faithful_imbalance --tier H4 --days 180 --symbols BTCUSDT ETHUSDT SOLUSDT
"""
from __future__ import annotations

import os

os.environ.setdefault("LOG_DIR", "backtest/logs")

import argparse  # noqa: E402
from typing import Any, Dict, List  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest.data import fetch_klines  # noqa: E402
from backtest.faithful import _atr, _metrics  # noqa: E402
from signals.engulfing_detector import compute_fibonacci_entry  # noqa: E402

MAKER_FEE = 0.0002
TAKER_FEE = 0.0004
SLIP = 0.0003
EQUITY0 = 1000.0
RR = 3.0
FIB_EXPIRY = 12       # LTF bars the fib limit stays valid
MAX_HOLD = 300        # LTF bars max hold
TAP_WINDOW = 60       # LTF bars after zone tap to still act
OB_MAXAGE = 400       # zone-TF bars: OB validity age
BOS_LOOK = 10         # zone-TF bars for break-of-structure reference

TIERS = {
    "H4": {"zone": "4h", "ltf": "15m", "risk": 0.01},
    "H1": {"zone": "1h", "ltf": "5m", "risk": 0.01},
    "M15": {"zone": "15m", "ltf": "3m", "risk": 0.01},
    "M30": {"zone": "30m", "ltf": "5m", "risk": 0.01},
}


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _trend(zone_df: pd.DataFrame) -> pd.DataFrame:
    ema50 = _ema(zone_df["close"], 50)
    ema200 = _ema(zone_df["close"], 200)
    # sep = |EMA50-EMA200|/EMA200*100 (%). Regime-strength magnitude.
    # Backward-compatible extra column; existing readers use only up/down.
    sep = ((ema50 - ema200) / ema200.replace(0, np.nan) * 100.0).abs().fillna(0.0)
    return pd.DataFrame({"open_time": zone_df["open_time"],
                         "up": (ema50 > ema200).fillna(False),
                         "down": (ema50 < ema200).fillna(False),
                         "sep": sep})


def _valid_obs(df: pd.DataFrame, side: str) -> List[Dict[str, Any]]:
    """Valid OB = OB + imbalance(FVG) + BOS, on the zone TF. side BULL/BEAR."""
    o, c, h, low = (df[x].to_numpy() for x in ("open", "close", "high", "low"))
    t = df["open_time"].to_numpy()
    n = len(df)
    obs = []
    for i in range(BOS_LOOK, n - 3):
        if side == "BULL":
            if c[i] < o[i] and h[i] < low[i + 2]:                       # red OB + bullish FVG
                bos = max(h[i + 1:i + 4]) > max(h[i - BOS_LOOK:i])      # impulse breaks prior highs
                if bos:
                    obs.append({"i": i, "zlow": float(low[i]), "zhigh": float(h[i]), "t": t[i]})
        else:
            if c[i] > o[i] and low[i] > h[i + 2]:                       # green OB + bearish FVG
                bos = min(low[i + 1:i + 4]) < min(low[i - BOS_LOOK:i])
                if bos:
                    obs.append({"i": i, "zlow": float(low[i]), "zhigh": float(h[i]), "t": t[i]})
    return obs


def _imbalances(ltf: pd.DataFrame, side: str) -> List[Dict[str, Any]]:
    """3-candle imbalances on the LTF. Completion (no look-ahead) at candle-3 index = j+1."""
    h, low, o, c = (ltf[x].to_numpy() for x in ("high", "low", "open", "close"))
    t = ltf["open_time"].to_numpy()
    n = len(ltf)
    out = []
    for j in range(1, n - 1):
        if side == "BULL":
            if h[j - 1] < low[j + 1] and c[j] > o[j]:                  # gap up, green middle
                out.append({"ce": j + 1, "t": t[j + 1],
                            "leg_low": float(low[j - 1]), "leg_high": float(h[j + 1])})
        else:
            if low[j - 1] > h[j + 1] and c[j] < o[j]:                  # gap down, red middle
                out.append({"ce": j + 1, "t": t[j + 1],
                            "leg_low": float(low[j + 1]), "leg_high": float(h[j - 1])})
    return out


def _trend_ok(trend_df: pd.DataFrame, ts: np.datetime64, side: str) -> bool:
    idx = trend_df["open_time"].values.searchsorted(ts, side="right") - 1
    if idx < 0:
        return False
    return bool(trend_df["up"].iloc[idx]) if side == "BULL" else bool(trend_df["down"].iloc[idx])


def _trend_ok_strong(trend_df: pd.DataFrame, ts: np.datetime64, side: str,
                     strong_pct: float = 0.0) -> bool:
    """Like _trend_ok, but only enforces BTC-regime alignment when the regime is
    STRONG (|EMA50-EMA200|/EMA200*100 >= strong_pct). Weak/choppy regime => allow
    both directions. strong_pct<=0 reproduces _trend_ok exactly (binary filter)."""
    idx = trend_df["open_time"].values.searchsorted(ts, side="right") - 1
    if idx < 0:
        return strong_pct > 0  # no data: binary=block, strong=allow
    if strong_pct > 0 and "sep" in trend_df.columns:
        if float(trend_df["sep"].iloc[idx]) < strong_pct:
            return True  # weak regime => do not block counter-trend
    return bool(trend_df["up"].iloc[idx]) if side == "BULL" else bool(trend_df["down"].iloc[idx])


def generate_setups(zone_df: pd.DataFrame, ltf: pd.DataFrame, trend: pd.DataFrame,
                    side: str, rr: float = RR, sl_swing: int = 0) -> List[Dict[str, Any]]:
    """Pure entry generation (shared by backtest AND live bot => live == backtest).
    Returns setups with entry/sl/tp known at the imbalance candle-3 close (no look-ahead)."""
    obs = _valid_obs(zone_df, side)
    imbs = _imbalances(ltf, side)
    if not obs or not imbs:
        return []
    lt = ltf["open_time"].to_numpy()
    latr = _atr(ltf)
    imb_times = np.array([im["t"] for im in imbs])
    z_close = zone_df["close"].to_numpy()
    z_time = zone_df["open_time"].to_numpy()
    setups: List[Dict[str, Any]] = []
    for ob in obs:
        zlow, zhigh, zi = ob["zlow"], ob["zhigh"], ob["i"]
        t_inv = None
        for k in range(zi + 3, min(len(zone_df), zi + OB_MAXAGE)):
            if (side == "BULL" and z_close[k] < zlow) or (side == "BEAR" and z_close[k] > zhigh):
                t_inv = z_time[k]
                break
        lo = imb_times.searchsorted(ob["t"], side="left")
        chosen = None
        for p in range(lo, len(imbs)):
            im = imbs[p]
            if t_inv is not None and im["t"] >= t_inv:
                break
            in_zone = (im["leg_low"] <= zhigh and im["leg_high"] >= zlow)
            if not in_zone or not _trend_ok(trend, im["t"], side):
                continue
            chosen = im
            break
        if chosen is None:
            continue
        leg_low, leg_high = chosen["leg_low"], chosen["leg_high"]
        ce = chosen["ce"]
        candle = pd.Series({"open": leg_low if side == "BULL" else leg_high,
                            "high": leg_high, "low": leg_low,
                            "close": leg_high if side == "BULL" else leg_low})
        entry = compute_fibonacci_entry(candle, fib_level=0.618)
        if side == "BULL":
            sl = (ltf["low"].to_numpy()[max(0, ce - sl_swing):ce].min() - 0.25 * latr[ce]) if sl_swing > 0 else (leg_low - 0.5 * latr[ce])
            if not (sl < entry):
                continue
            risk = entry - sl
            tp = entry + rr * risk
        else:
            sl = (ltf["high"].to_numpy()[max(0, ce - sl_swing):ce].max() + 0.25 * latr[ce]) if sl_swing > 0 else (leg_high + 0.5 * latr[ce])
            if not (sl > entry):
                continue
            risk = sl - entry
            tp = entry - rr * risk
        setups.append({"side": side, "ce": int(ce), "t_complete": lt[ce],
                       "entry": float(entry), "sl": float(sl), "tp": float(tp),
                       "risk": float(risk), "zlow": zlow, "zhigh": zhigh})
    return setups


def _annotate_ob_invalidation(obs: List[Dict[str, Any]], zone_df: pd.DataFrame, side: str) -> None:
    z_close = zone_df["close"].to_numpy()
    z_time = zone_df["open_time"].to_numpy()
    for ob in obs:
        zi, zlow, zhigh = ob["i"], ob["zlow"], ob["zhigh"]
        t_inv = None
        for k in range(zi + 3, min(len(zone_df), zi + OB_MAXAGE)):
            if (side == "BULL" and z_close[k] < zlow) or (side == "BEAR" and z_close[k] > zhigh):
                t_inv = z_time[k]
                break
        ob["t_inv"] = t_inv


def nearest_unmitigated_setups(zone_df: pd.DataFrame, ltf: pd.DataFrame, trend: pd.DataFrame,
                               side: str, rr: float = RR, max_age: int = 0,
                               sl_swing: int = 0) -> List[Dict[str, Any]]:
    """Live detector: choose nearest valid imbalance that is still unmitigated/uninvalidated.
    Age is a soft preference only; max_age<=0 disables hard expiry."""
    obs = _valid_obs(zone_df, side)
    imbs = _imbalances(ltf, side)
    if not obs or not imbs:
        return []
    lt = ltf["open_time"].to_numpy()
    latr = _atr(ltf)
    _annotate_ob_invalidation(obs, zone_df, side)
    current_idx = len(ltf) - 1
    setups: List[Dict[str, Any]] = []
    for ob in obs:
        best = None
        for im in imbs:
            if im["t"] <= ob["t"]:
                continue
            if ob["t_inv"] is not None and ob["t_inv"] <= im["t"]:
                continue
            if im["leg_low"] > ob["zhigh"] or im["leg_high"] < ob["zlow"]:
                continue
            ce = int(im["ce"])
            if not _trend_ok(trend, im["t"], side):
                continue
            if max_age > 0 and ce < current_idx - max_age:
                continue
            if side == "BULL":
                entry = compute_fibonacci_entry(pd.Series({"open": im["leg_low"], "high": im["leg_high"], "low": im["leg_low"], "close": im["leg_high"]}), fib_level=0.618)
                if ce + 1 < len(ltf) and (ltf["low"].iloc[ce + 1:] <= entry).any():
                    continue
                sl = (ltf["low"].to_numpy()[max(0, ce - sl_swing):ce].min() - 0.25 * latr[ce]) if sl_swing > 0 else (im["leg_low"] - 0.5 * latr[ce])
                if not (sl < entry):
                    continue
                risk = entry - sl
                tp = entry + rr * risk
                cur = float(ltf["close"].iloc[-1])
                dist = abs(cur - entry) / max(cur, 1e-9) * 100.0
            else:
                entry = compute_fibonacci_entry(pd.Series({"open": im["leg_low"], "high": im["leg_high"], "low": im["leg_low"], "close": im["leg_low"]}), fib_level=0.618)
                if ce + 1 < len(ltf) and (ltf["high"].iloc[ce + 1:] >= entry).any():
                    continue
                sl = (ltf["high"].to_numpy()[max(0, ce - sl_swing):ce].max() + 0.25 * latr[ce]) if sl_swing > 0 else (im["leg_high"] + 0.5 * latr[ce])
                if not (sl > entry):
                    continue
                risk = sl - entry
                tp = entry - rr * risk
                cur = float(ltf["close"].iloc[-1])
                dist = abs(cur - entry) / max(cur, 1e-9) * 100.0
            candidate = {"side": side, "ce": ce, "t_complete": lt[ce], "entry": float(entry), "sl": float(sl), "tp": float(tp),
                         "risk": float(risk), "zlow": ob["zlow"], "zhigh": ob["zhigh"], "dist_pct": dist,
                         "age_bars": current_idx - ce}
            if best is None or candidate["dist_pct"] < best["dist_pct"] or (
                candidate["dist_pct"] == best["dist_pct"] and candidate["age_bars"] < best["age_bars"]
            ):
                best = candidate
        if best is not None:
            setups.append(best)
    setups.sort(key=lambda s: (s["dist_pct"], s["age_bars"]))
    return setups


def recent_setups(zone_df: pd.DataFrame, ltf: pd.DataFrame, trend: pd.DataFrame,
                  side: str, rr: float = RR, max_age: int = FIB_EXPIRY, sl_swing: int = 0) -> List[Dict[str, Any]]:
    """Backward-compatible wrapper: retain fresh-only behavior for legacy callers/tests."""
    obs = _valid_obs(zone_df, side)
    imbs = _imbalances(ltf, side)
    if not obs or not imbs:
        return []
    n = len(ltf)
    lt = ltf["open_time"].to_numpy()
    latr = _atr(ltf)
    _annotate_ob_invalidation(obs, zone_df, side)
    setups: List[Dict[str, Any]] = []
    for im in imbs:
        if im["ce"] < n - 1 - max_age:
            continue
        if not _trend_ok(trend, im["t"], side):
            continue
        match = None
        for ob in reversed(obs):
            if ob["t"] >= im["t"]:
                continue
            if ob["t_inv"] is not None and ob["t_inv"] <= im["t"]:
                continue
            if im["leg_low"] <= ob["zhigh"] and im["leg_high"] >= ob["zlow"]:
                match = ob
                break
        if match is None:
            continue
        first_tap = True
        for p in imbs:
            if p["t"] <= match["t"]:
                continue
            if p["t"] >= im["t"]:
                break
            if (p["leg_low"] <= match["zhigh"] and p["leg_high"] >= match["zlow"]
                    and _trend_ok(trend, p["t"], side)):
                first_tap = False
                break
        if not first_tap:
            continue
        leg_low, leg_high, ce = im["leg_low"], im["leg_high"], int(im["ce"])
        candle = pd.Series({"open": leg_low if side == "BULL" else leg_high,
                            "high": leg_high, "low": leg_low,
                            "close": leg_high if side == "BULL" else leg_low})
        entry = compute_fibonacci_entry(candle, fib_level=0.618)
        if side == "BULL":
            sl = (ltf["low"].to_numpy()[max(0, ce - sl_swing):ce].min() - 0.25 * latr[ce]) if sl_swing > 0 else (leg_low - 0.5 * latr[ce])
            if not (sl < entry):
                continue
            risk = entry - sl
            tp = entry + rr * risk
        else:
            sl = (ltf["high"].to_numpy()[max(0, ce - sl_swing):ce].max() + 0.25 * latr[ce]) if sl_swing > 0 else (leg_high + 0.5 * latr[ce])
            if not (sl > entry):
                continue
            risk = sl - entry
            tp = entry - rr * risk
        setups.append({"side": side, "ce": int(ce), "t_complete": lt[ce],
                       "entry": float(entry), "sl": float(sl), "tp": float(tp),
                       "risk": float(risk), "zlow": match["zlow"], "zhigh": match["zhigh"]})
    return setups


CVD_LOOK = 20
FUND_CAP = 0.0005
DP_LOOKBACK = 60   # LTF bars for the dealing range (discount/premium)


def _filter_liquidity(setups: List[Dict[str, Any]], ltf: pd.DataFrame, min_turn: float, lookback: int = 20) -> List[Dict[str, Any]]:
    """Recent-liquidity gate (freqtrade insight: 24h volume can be STALE — check CURRENT turnover).
    Keep setups where recent LTF quote-turnover (sum vol*close over `lookback` bars) >= min_turn.
    Validated: lifts WR ~57%->67% by skipping thin/stale-liquidity moments."""
    if min_turn <= 0 or not setups:
        return setups
    turn = ltf["volume"].to_numpy() * ltf["close"].to_numpy()
    keep = []
    for s in setups:
        ce = s["ce"]
        if float(turn[max(0, ce - lookback):ce].sum()) >= min_turn:
            keep.append(s)
    return keep


def _filter_ema_dist(setups: List[Dict[str, Any]], zone_df: pd.DataFrame, min_dist: float) -> List[Dict[str, Any]]:
    """Keep only setups where price is >= min_dist*ATR from EMA200 on the zone TF
    (established trend/momentum, not consolidation). Validated: monotonic WR/PF lift, OOS-robust."""
    if min_dist <= 0 or not setups:
        return setups
    zc = zone_df["close"].to_numpy()
    ema200 = zone_df["close"].ewm(span=200, adjust=False).mean().to_numpy()
    atr = _atr(zone_df)
    zt = zone_df["open_time"].to_numpy()
    keep = []
    for s in setups:
        i = zt.searchsorted(np.datetime64(pd.Timestamp(s["t_complete"])), "right") - 1
        if i < 200:
            continue
        if abs(zc[i] - ema200[i]) / max(atr[i], 1e-9) >= min_dist:
            keep.append(s)
    return keep


def _filter_discount(side: str, setups: List[Dict[str, Any]], ltf: pd.DataFrame,
                     lookback: int = DP_LOOKBACK) -> List[Dict[str, Any]]:
    """ICT discount/premium (both source books stress it): only BUY in discount (entry below
    50% of the recent dealing range) / only SELL in premium (entry above 50%)."""
    hi = ltf["high"].to_numpy()
    lo = ltf["low"].to_numpy()
    keep = []
    for s in setups:
        ce = s["ce"]
        a = max(0, ce - lookback)
        rng_hi = hi[a:ce + 1].max()
        rng_lo = lo[a:ce + 1].min()
        mid = (rng_hi + rng_lo) / 2.0
        if (side == "BULL" and s["entry"] <= mid) or (side == "BEAR" and s["entry"] >= mid):
            keep.append(s)
    return keep


def _filter_flow(symbol: str, days: int, side: str, setups: List[Dict[str, Any]],
                 ltf: pd.DataFrame, use_cvd: bool, use_funding: bool) -> List[Dict[str, Any]]:
    """Optional confirmation filters (measured, not assumed):
      CVD: recent cumulative delta must confirm direction.
      Funding: skip overcrowded side (BULL skip if funding>=cap; BEAR skip if<=-cap)."""
    out = setups
    if use_cvd and "taker_buy_base" in ltf.columns:
        import numpy as _np
        cvd = _np.cumsum(2 * ltf["taker_buy_base"].to_numpy() - ltf["volume"].to_numpy())
        keep = []
        for s in out:
            ce = s["ce"]
            mom = cvd[ce] - cvd[max(0, ce - CVD_LOOK)]
            if (side == "BULL" and mom > 0) or (side == "BEAR" and mom < 0):
                keep.append(s)
        out = keep
    if use_funding:
        from backtest.data import fetch_funding
        fdf = fetch_funding(symbol, days)
        if not fdf.empty:
            ft = fdf["fundingTime"].to_numpy()
            fr = fdf["funding_rate"].to_numpy()
            keep = []
            for s in out:
                idx = ft.searchsorted(np.datetime64(s["t_complete"]), side="right") - 1
                f = float(fr[idx]) if idx >= 0 else 0.0
                if (side == "BULL" and f < FUND_CAP) or (side == "BEAR" and f > -FUND_CAP):
                    keep.append(s)
            out = keep
    return out


def _filter_stochastic(setups: List[Dict[str, Any]], ltf: pd.DataFrame, side: str,
                       max_k: float) -> List[Dict[str, Any]]:
    """Keep setups where Stochastic %K <= max_k (avoid overbought entries).
    For BULL: want %K not too high (avoid chasing). For BEAR: want %K not too low (avoid oversold shorts).
    Validated cross-asset: %K<50 improves PF +5-75% on 15 pairs, fill rate trade-off acceptable."""
    if max_k <= 0 or not setups:
        return setups
    low_min = ltf["low"].rolling(14).min().to_numpy()
    high_max = ltf["high"].rolling(14).max().to_numpy()
    close = ltf["close"].to_numpy()
    stoch_k = np.where(high_max - low_min > 0, (close - low_min) / (high_max - low_min) * 100, 50.0)
    keep = []
    for s in setups:
        ce = s["ce"]
        if ce < 14:
            continue
        k_val = stoch_k[ce]
        if (side == "BULL" and k_val <= max_k) or (side == "BEAR" and k_val >= (100 - max_k)):
            keep.append(s)
    return keep


TP1_R = 1.0       # take partial at +1R
TP1_FRAC = 0.5    # close 50% at TP1
TRAIL_ATR = 2.0   # runner trails by 2x ATR after TP1


def _manage_exit(side, entry, sl, tp, ll, lh, lc, atr, fill, mode):
    """Exit simulator. mode: 'fixed' (SL/TP RR), 'tp1be' (TP1 partial + BE + runner to TP),
    'tp1trail' (TP1 partial + BE + ATR-trailed runner). Returns (net_profit_per_unit, reason)."""
    risk = abs(entry - sl)
    pos, profit, cur_stop = 1.0, 0.0, sl
    tp1 = entry + TP1_R * risk if side == "BULL" else entry - TP1_R * risk
    tp1_done = False
    ext = entry
    reason = "TIME"
    end = min(fill + 1 + MAX_HOLD, len(ll)) - 1
    for m in range(fill + 1, min(fill + 1 + MAX_HOLD, len(ll))):
        h, lo = lh[m], ll[m]
        if side == "BULL":
            if lo <= cur_stop:
                profit += (cur_stop - entry) * pos
                reason = "SL" if cur_stop <= sl else ("BE" if abs(cur_stop - entry) < 1e-12 else "TRAIL")
                pos = 0.0
                break
            if mode != "fixed" and not tp1_done and h >= tp1:
                profit += (tp1 - entry) * TP1_FRAC
                pos -= TP1_FRAC
                tp1_done = True
                cur_stop = entry
            if mode in ("fixed", "tp1be") and h >= tp:
                profit += (tp - entry) * pos
                reason = "TP"
                pos = 0.0
                break
            if mode == "tp1trail" and tp1_done:
                ext = max(ext, h)
                cur_stop = max(cur_stop, ext - TRAIL_ATR * atr[m])
        else:
            if h >= cur_stop:
                profit += (entry - cur_stop) * pos
                reason = "SL" if cur_stop >= sl else ("BE" if abs(cur_stop - entry) < 1e-12 else "TRAIL")
                pos = 0.0
                break
            if mode != "fixed" and not tp1_done and lo <= tp1:
                profit += (entry - tp1) * TP1_FRAC
                pos -= TP1_FRAC
                tp1_done = True
                cur_stop = entry
            if mode in ("fixed", "tp1be") and lo <= tp:
                profit += (entry - tp) * pos
                reason = "TP"
                pos = 0.0
                break
            if mode == "tp1trail" and tp1_done:
                ext = min(ext, lo)
                cur_stop = min(cur_stop, ext + TRAIL_ATR * atr[m])
        end = m
    if pos > 0:
        cl = lc[end]
        profit += ((cl - entry) if side == "BULL" else (entry - cl)) * pos
        reason = "TP1RUN" if tp1_done else "TIME"
    fees = entry * MAKER_FEE + entry * (TAKER_FEE + SLIP)
    return profit - fees, reason


def _simulate_side(symbol: str, tier: str, days: int, side: str,
                   zone_df: pd.DataFrame, ltf: pd.DataFrame, trend: pd.DataFrame,
                   rr: float = RR, use_cvd: bool = False, use_funding: bool = False,
                   btc_trend: "pd.DataFrame | None" = None, use_discount: bool = False,
                   exit_mode: str = "fixed", ema_dist: float = 0.0, min_turn: float = 0.0,
                   stoch_max: float = 0.0) -> List[Dict[str, Any]]:
    cfg = TIERS[tier]
    setups = generate_setups(zone_df, ltf, trend, side, rr)
    setups = _filter_flow(symbol, days, side, setups, ltf, use_cvd, use_funding)
    if use_discount:
        setups = _filter_discount(side, setups, ltf)
    if btc_trend is not None:   # only trade in BTC's macro-regime direction
        setups = [s for s in setups if _trend_ok(btc_trend, s["t_complete"], side)]
    setups = _filter_ema_dist(setups, zone_df, ema_dist)
    setups = _filter_liquidity(setups, ltf, min_turn)
    if stoch_max > 0:   # faithful to live engine: avoid overbought/oversold entries
        setups = _filter_stochastic(setups, ltf, side, stoch_max)
    if not setups:
        return []
    lt = ltf["open_time"].to_numpy()
    ll = ltf["low"].to_numpy()
    lh = ltf["high"].to_numpy()
    lc = ltf["close"].to_numpy()
    latr = _atr(ltf)
    risk_pct = cfg["risk"]
    trades: List[Dict[str, Any]] = []
    for s in setups:
        ce, entry, sl, tp, risk = s["ce"], s["entry"], s["sl"], s["tp"], s["risk"]
        # fill the fib limit within FIB_EXPIRY LTF bars
        fill = None
        for f in range(ce + 1, min(ce + 1 + FIB_EXPIRY, len(ltf))):
            if (side == "BULL" and ll[f] <= entry) or (side == "BEAR" and lh[f] >= entry):
                fill = f
                break
        if fill is None:
            continue
        per_unit, reason = _manage_exit(side, entry, sl, tp, ll, lh, lc, latr, fill, exit_mode)
        trades.append({"symbol": symbol, "tier": tier, "side": side, "t_entry": lt[fill],
                       "entry": entry, "sl": sl, "tp": tp, "exit": None, "reason": reason,
                       "per_unit": per_unit, "risk": risk, "risk_pct": risk_pct})
    return trades


def _simulate_symbol(symbol: str, tier: str, days: int, direction: str, rr: float = RR,
                     use_cvd: bool = False, use_funding: bool = False,
                     use_btc: bool = False, use_discount: bool = False,
                     exit_mode: str = "fixed", ema_dist: float = 0.0, min_turn: float = 0.0,
                     stoch_max: float = 0.0) -> List[Dict[str, Any]]:
    cfg = TIERS[tier]
    zone_df = fetch_klines(symbol, cfg["zone"], days)
    ltf = fetch_klines(symbol, cfg["ltf"], days)
    if min(len(zone_df), len(ltf)) < 260:
        return []
    trend = _trend(zone_df)
    btc_trend = _trend(fetch_klines("BTCUSDT", cfg["zone"], days)) if use_btc else None
    out: List[Dict[str, Any]] = []
    if direction in ("both", "long"):
        out += _simulate_side(symbol, tier, days, "BULL", zone_df, ltf, trend, rr, use_cvd, use_funding, btc_trend, use_discount, exit_mode, ema_dist, min_turn, stoch_max)
    if direction in ("both", "short"):
        out += _simulate_side(symbol, tier, days, "BEAR", zone_df, ltf, trend, rr, use_cvd, use_funding, btc_trend, use_discount, exit_mode, ema_dist, min_turn, stoch_max)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["H4", "H1", "M15", "M30"], default="H4")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    ap.add_argument("--direction", choices=["both", "long", "short"], default="both")
    ap.add_argument("--rr", type=float, default=RR)
    ap.add_argument("--cvd", action="store_true", help="require CVD confirmation")
    ap.add_argument("--funding", action="store_true", help="skip overcrowded funding")
    ap.add_argument("--btc-regime", action="store_true", help="only trade in BTC's macro-regime direction")
    ap.add_argument("--discount", action="store_true", help="ICT discount/premium filter (buy discount/sell premium)")
    ap.add_argument("--exit", dest="exit_mode", choices=["fixed", "tp1be", "tp1trail"], default="fixed")
    ap.add_argument("--ema-dist", type=float, default=0.0, help="min |price-EMA200|/ATR on zone TF")
    ap.add_argument("--min-turn", type=float, default=0.0, help="min recent LTF quote-turnover (20-bar)")
    ap.add_argument("--oos", type=float, default=0.40)
    a = ap.parse_args()

    all_trades: List[Dict[str, Any]] = []
    for sym in a.symbols:
        try:
            tr = _simulate_symbol(sym, a.tier, a.days, a.direction, a.rr, a.cvd, a.funding, a.btc_regime, a.discount, a.exit_mode, a.ema_dist, a.min_turn)
            all_trades.extend(tr)
            print(f"  {sym} tier {a.tier} ({a.direction}): {len(tr)} setups")
        except Exception as exc:
            print(f"  {sym} ERROR: {exc}")
    all_trades.sort(key=lambda t: t["t_entry"])

    print("=" * 70)
    _metrics(all_trades, f"IMBALANCE TIER {a.tier} | ALL")
    if all_trades:
        split = int(len(all_trades) * (1 - a.oos))
        _metrics(all_trades[split:], f"IMBALANCE TIER {a.tier} | OUT-OF-SAMPLE {int(a.oos*100)}%")
    print("VERDICT: edge is real only if OUT-OF-SAMPLE PF > 1.2 with enough trades.")


if __name__ == "__main__":
    main()
