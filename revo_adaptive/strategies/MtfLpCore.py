# =============================================================================
# MtfLpCore — FEASIBILITY build dari spec "MTF Liquidity Pullback Strategy v1.3"
# Tujuan: cek cepat apakah INTI strategi punya edge di crypto (93 pair bybit),
# SEBELUM investasi build penuh (daily limit, session, sizing, OCO, dll).
#
# INTI yang dimekanisasi (mode Balanced, params default spec):
#   - Trend H1 : EMA50/200 + normalized slope + EMA separation.
#   - Zona M15 : Order Block dari candle berlawanan terakhir sebelum impulse.
#   - Trigger M5: liquidity sweep ATAU rejection candle di dalam zona.
#   - Konfirmasi: dalam <=4 candle M5 (body/range filter).
#   - SL struktural: trigger low/high +/- 0.10*ATR (max 3*ATR).
#   - TP: 2R.  Satu trade per zona.
#
# DISIPLIN ANTI-LOOKAHEAD (sama seperti SnRScalpM5, sudah diverifikasi):
#   - H1 & M15 diturunkan via resample dari 5m, di-shift 1 bar => hanya candle
#     yang SUDAH CLOSE yang terlihat.
#   - Pivot strength 2 => nilai pivot baru "diketahui" 2 bar setelahnya (shift 2).
#
# OMITTED di feasibility (peredam risiko, bukan edge): daily limits, session,
# position sizing, spread min-stop, structural-room, retest counting penuh,
# H1/M15 BOS & FVG requirement (default spec = Off).
# =============================================================================
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import IStrategy, stoploss_from_absolute
from freqtrade.persistence import Trade


def resample_htf(df: DataFrame, minutes: int) -> DataFrame:
    d = df.copy().set_index("date")
    agg = {"open": "first", "high": "max", "low": "min",
           "close": "last", "volume": "sum"}
    return d.resample(f"{minutes}min", label="right",
                      closed="right").agg(agg).dropna()


def build_obs(m15: DataFrame, min_impulse_atr: float, search: int,
              require_bos: bool = False, require_fvg: bool = False) -> DataFrame:
    """Bangun Order Block aktif per-bar M15 (loop kecil, ~2880 bar/pair).
    Bull OB = candle bearish terakhir sebelum bullish impulse; sebaliknya bear.
    Strict opsional: impulse harus BOS (close lewat pivot M15) dan/atau bikin FVG."""
    n = len(m15)
    o = m15["open"].values; c = m15["close"].values
    h = m15["high"].values; l = m15["low"].values
    atr = m15["atr"].values
    lph = m15["last_ph"].values if "last_ph" in m15 else np.full(n, np.nan)
    lpl = m15["last_pl"].values if "last_pl" in m15 else np.full(n, np.nan)
    bt = np.full(n, np.nan); bb = np.full(n, np.nan)   # bull OB top/bottom
    st = np.full(n, np.nan); sb = np.full(n, np.nan)   # bear OB top/bottom
    cbt = cbb = cst = csb = np.nan
    for k in range(n):
        vb = vs = False
        if not np.isnan(atr[k]) and abs(c[k] - o[k]) >= min_impulse_atr * atr[k]:
            if c[k] > o[k]:                                    # bullish impulse
                vb = True
                if require_bos and not (not np.isnan(lph[k]) and c[k] > lph[k]):
                    vb = False
                if require_fvg and not (k >= 2 and l[k] > h[k - 2]):
                    vb = False
            elif c[k] < o[k]:                                  # bearish impulse
                vs = True
                if require_bos and not (not np.isnan(lpl[k]) and c[k] < lpl[k]):
                    vs = False
                if require_fvg and not (k >= 2 and h[k] < l[k - 2]):
                    vs = False
        if vb:
            for j in range(k - 1, max(-1, k - 1 - search), -1):
                if c[j] < o[j]:                            # OB = bearish src
                    cbt = max(o[j], c[j]); cbb = l[j]; break
        if vs:
            for j in range(k - 1, max(-1, k - 1 - search), -1):
                if c[j] > o[j]:                            # OB = bullish src
                    cst = h[j]; csb = min(o[j], c[j]); break
        bt[k] = cbt; bb[k] = cbb; st[k] = cst; sb[k] = csb
    m15 = m15.copy()
    m15["bull_ob_top"] = bt; m15["bull_ob_bot"] = bb
    m15["bear_ob_top"] = st; m15["bear_ob_bot"] = sb
    return m15


def gen_signals(df: DataFrame, p: dict):
    """State-machine loop M5 (Sections 13-15 spec): pending -> konfirmasi -> entry.
    Satu arah pending, satu trade per zona, konfirmasi dalam <=deadline candle."""
    n = len(df)
    el = np.zeros(n, dtype=int); es = np.zeros(n, dtype=int)
    slp = np.full(n, np.nan); tpp = np.full(n, np.nan)
    o = df["open"].values; c = df["close"].values
    h = df["high"].values; l = df["low"].values
    atr = df["atr"].values; body = df["body"].values; rng = df["rng"].values
    up = df["h1_bull"].values; dn = df["h1_bear"].values
    bt = df["bull_ob_top"].values; bb = df["bull_ob_bot"].values
    st = df["bear_ob_top"].values; sb = df["bear_ob_bot"].values
    tb = df["trig_bull"].values; ts = df["trig_bear"].values
    pend = None
    traded_bull = None; traded_bear = None
    for i in range(n):
        if np.isnan(atr[i]) or atr[i] <= 0:
            continue
        # 1) expire pending (deadline)
        if pend is not None:
            pend["age"] += 1
            if pend["age"] > p["deadline"]:
                pend = None
        # 2) invalidasi zona pending (close tembus batas)
        if pend is not None:
            if pend["side"] == "long" and c[i] < pend["zbot"]:
                pend = None
            elif pend["side"] == "short" and c[i] > pend["ztop"]:
                pend = None
        # 3) trigger baru (replace pending, satu arah)
        if up[i] and not np.isnan(bt[i]) and tb[i]:
            zid = (round(bt[i], 10), round(bb[i], 10))
            if zid != traded_bull:
                pend = {"side": "long", "tclose": c[i], "tlow": l[i], "thigh": h[i],
                        "age": 0, "ztop": bt[i], "zbot": bb[i], "zid": zid}
        elif dn[i] and not np.isnan(st[i]) and ts[i]:
            zid = (round(st[i], 10), round(sb[i], 10))
            if zid != traded_bear:
                pend = {"side": "short", "tclose": c[i], "thigh": h[i], "tlow": l[i],
                        "age": 0, "ztop": st[i], "zbot": sb[i], "zid": zid}
        # 4) konfirmasi (bar setelah trigger: age>=1)
        if pend is not None and pend["age"] >= 1:
            if pend["side"] == "long" and up[i]:
                ref = pend["thigh"] if p["strict_confirm"] else pend["tclose"]
                ok = (c[i] > ref and c[i] > o[i]
                      and body[i] >= p["min_body"] * atr[i]
                      and rng[i] <= p["max_range"] * atr[i])
                if ok:
                    base = min(pend["tlow"], pend["zbot"]) if p["wider_stop"] else pend["tlow"]
                    sl = base - p["sl_buf"] * atr[i]
                    risk = c[i] - sl
                    if 0 < risk <= p["max_stop"] * atr[i]:
                        el[i] = 1; slp[i] = sl; tpp[i] = c[i] + p["rr"] * risk
                        traded_bull = pend["zid"]; pend = None
            elif pend["side"] == "short" and dn[i]:
                ref = pend["tlow"] if p["strict_confirm"] else pend["tclose"]
                ok = (c[i] < ref and c[i] < o[i]
                      and body[i] >= p["min_body"] * atr[i]
                      and rng[i] <= p["max_range"] * atr[i])
                if ok:
                    base = max(pend["thigh"], pend["ztop"]) if p["wider_stop"] else pend["thigh"]
                    sl = base + p["sl_buf"] * atr[i]
                    risk = sl - c[i]
                    if 0 < risk <= p["max_stop"] * atr[i]:
                        es[i] = 1; slp[i] = sl; tpp[i] = c[i] - p["rr"] * risk
                        traded_bear = pend["zid"]; pend = None
    return el, es, slp, tpp


class MtfLpCore(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "5m"
    can_short = True

    minimal_roi = {"0": 100.0}
    stoploss = -0.99
    use_custom_stoploss = True
    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False

    startup_candle_count: int = 2400   # EMA200 H1 = 200*12 bar 5m

    # ---- params default spec v1.3 (Balanced) ----
    ema_fast = 50
    ema_slow = 200
    slope_lookback = 5
    min_slope = 0.03
    min_ema_sep = 0.05
    min_impulse_atr = 0.50
    ob_search_bars = 8
    pivot_strength = 2
    conf_deadline = 4
    min_confirm_body = 0.20
    max_confirm_range = 2.50
    sl_atr_buffer = 0.10
    max_stop_atr = 3.00
    rr = 2.0

    # ---- flag STRICT (Section 33). Default Off => base = Balanced tak berubah. ----
    require_h1_bos = False      # AND-kan BOS H1 ke trend filter
    require_m15_bos = False     # impulse M15 harus tembus pivot (BOS)
    require_fvg = False         # impulse M15 harus bikin FVG
    strict_sweep = False        # trigger = liquidity sweep saja (buang rejection)
    strict_confirm = False      # konfirmasi close > high sweep (long) / < low (short)
    wider_stop = False          # SL = wider(trigger, batas zona) + buffer

    def leverage(self, pair, current_time, current_rate, proposed_leverage,
                 max_leverage, side, **kwargs) -> float:
        return 1.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe.copy()

        # ===== Trend H1 (resample dari 5m, shift 1 = closed) =====
        h1 = resample_htf(df, 60)
        h1["ema_f"] = ta.EMA(h1, timeperiod=self.ema_fast)
        h1["ema_s"] = ta.EMA(h1, timeperiod=self.ema_slow)
        h1["atr"] = ta.ATR(h1, timeperiod=14)
        h1["slope"] = (h1["ema_f"] - h1["ema_f"].shift(self.slope_lookback)) / h1["atr"]
        h1["sep"] = (h1["ema_f"] - h1["ema_s"]).abs() / h1["atr"]
        h1["bull"] = ((h1["ema_f"] > h1["ema_s"]) & (h1["close"] > h1["ema_s"])
                      & (h1["slope"] >= self.min_slope) & (h1["sep"] >= self.min_ema_sep))
        h1["bear"] = ((h1["ema_f"] < h1["ema_s"]) & (h1["close"] < h1["ema_s"])
                      & (h1["slope"] <= -self.min_slope) & (h1["sep"] >= self.min_ema_sep))
        if self.require_h1_bos:
            wz = 2 * self.pivot_strength + 1
            h1_ph = h1["high"] == h1["high"].rolling(wz, center=True).max()
            h1_pl = h1["low"] == h1["low"].rolling(wz, center=True).min()
            lph = h1["high"].where(h1_ph).shift(self.pivot_strength).ffill()
            lpl = h1["low"].where(h1_pl).shift(self.pivot_strength).ffill()
            bos = np.where(h1["close"] > lph, 1.0,
                           np.where(h1["close"] < lpl, -1.0, np.nan))
            bos = pd.Series(bos, index=h1.index).ffill()
            h1["bull"] = h1["bull"] & (bos == 1.0)
            h1["bear"] = h1["bear"] & (bos == -1.0)
        h1s = h1[["bull", "bear"]].shift(1)
        h1s.columns = ["h1_bull", "h1_bear"]
        df = pd.merge_asof(df.sort_values("date"),
                           h1s.reset_index().sort_values("date"),
                           on="date", direction="backward")
        df["h1_bull"] = df["h1_bull"].fillna(False).astype(bool)
        df["h1_bear"] = df["h1_bear"].fillna(False).astype(bool)

        # ===== Zona Order Block M15 (resample dari 5m, shift 1) =====
        m15 = resample_htf(df[["date", "open", "high", "low", "close", "volume"]], 15)
        m15["atr"] = ta.ATR(m15, timeperiod=14)
        if self.require_m15_bos:                 # pivot M15 causal utk cek BOS impulse
            wz = 2 * self.pivot_strength + 1
            m_ph = m15["high"] == m15["high"].rolling(wz, center=True).max()
            m_pl = m15["low"] == m15["low"].rolling(wz, center=True).min()
            m15["last_ph"] = m15["high"].where(m_ph).shift(self.pivot_strength).ffill()
            m15["last_pl"] = m15["low"].where(m_pl).shift(self.pivot_strength).ffill()
        m15 = build_obs(m15, self.min_impulse_atr, self.ob_search_bars,
                        require_bos=self.require_m15_bos, require_fvg=self.require_fvg)
        obc = ["bull_ob_top", "bull_ob_bot", "bear_ob_top", "bear_ob_bot"]
        m15s = m15[obc].shift(1)
        df = pd.merge_asof(df.sort_values("date"),
                           m15s.reset_index().sort_values("date"),
                           on="date", direction="backward")

        # ===== Indikator M5 =====
        df["atr"] = ta.ATR(df, timeperiod=14)
        df["body"] = (df["close"] - df["open"]).abs()
        df["rng"] = (df["high"] - df["low"])
        lw = df[["open", "close"]].min(axis=1) - df["low"]
        uw = df["high"] - df[["open", "close"]].max(axis=1)

        # swing strength 2, causal: nilai pivot dipakai 2 bar setelah (shift 2)
        w = 2 * self.pivot_strength + 1
        is_ph = df["high"] == df["high"].rolling(w, center=True).max()
        is_pl = df["low"] == df["low"].rolling(w, center=True).min()
        df["last_sh"] = df["high"].where(is_ph).shift(self.pivot_strength).ffill()
        df["last_sl"] = df["low"].where(is_pl).shift(self.pivot_strength).ffill()

        # touch zona (M5 vs OB M15 yang sudah closed)
        df["touch_bull"] = (df["low"] <= df["bull_ob_top"]) & (df["high"] >= df["bull_ob_bot"])
        df["touch_bear"] = (df["high"] >= df["bear_ob_bot"]) & (df["low"] <= df["bear_ob_top"])

        # trigger Balanced = sweep OR rejection
        sweep_b = df["touch_bull"] & (df["low"] < df["last_sl"]) & (df["close"] > df["last_sl"])
        sweep_s = df["touch_bear"] & (df["high"] > df["last_sh"]) & (df["close"] < df["last_sh"])
        rej_b = df["touch_bull"] & (df["close"] > df["open"]) & (lw >= 0.5 * df["body"])
        rej_s = df["touch_bear"] & (df["close"] < df["open"]) & (uw >= 0.5 * df["body"])
        if self.strict_sweep:                    # Strict = liquidity sweep saja
            df["trig_bull"] = sweep_b.fillna(False)
            df["trig_bear"] = sweep_s.fillna(False)
        else:                                    # Balanced = sweep OR rejection
            df["trig_bull"] = (sweep_b | rej_b).fillna(False)
            df["trig_bear"] = (sweep_s | rej_s).fillna(False)

        return df

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        df = dataframe
        p = {"deadline": self.conf_deadline, "min_body": self.min_confirm_body,
             "max_range": self.max_confirm_range, "sl_buf": self.sl_atr_buffer,
             "max_stop": self.max_stop_atr, "rr": self.rr,
             "strict_confirm": self.strict_confirm, "wider_stop": self.wider_stop}
        el, es, slp, tpp = gen_signals(df, p)
        df["enter_long"] = el
        df["enter_short"] = es
        df["_sl"] = slp
        df["_tp"] = tpp
        df.loc[df["enter_long"] == 1, "enter_tag"] = "mtf_long"
        df.loc[df["enter_short"] == 1, "enter_tag"] = "mtf_short"
        return df

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    # ---- SL & TP absolut dari harga sinyal (disimpan di trade custom data) ----
    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs):
        sl = trade.get_custom_data("sl")
        if sl is None:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            row = df.loc[df["date"] <= trade.open_date_utc].iloc[-1] if len(df) else None
            if row is not None and not pd.isna(row.get("_sl", np.nan)):
                sl = float(row["_sl"]); tp = float(row["_tp"])
                trade.set_custom_data("sl", sl); trade.set_custom_data("tp", tp)
        if sl is None:
            return None
        return stoploss_from_absolute(sl, current_rate, is_short=trade.is_short,
                                      leverage=trade.leverage)

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        tp = trade.get_custom_data("tp")
        if tp is None:
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            row = df.loc[df["date"] <= trade.open_date_utc].iloc[-1] if len(df) else None
            if row is not None and not pd.isna(row.get("_tp", np.nan)):
                tp = float(row["_tp"]); trade.set_custom_data("tp", tp)
                if trade.get_custom_data("sl") is None and not pd.isna(row.get("_sl", np.nan)):
                    trade.set_custom_data("sl", float(row["_sl"]))
        if tp is None:
            return None
        if not trade.is_short and current_rate >= tp:
            return "tp_2r"
        if trade.is_short and current_rate <= tp:
            return "tp_2r"
        return None
