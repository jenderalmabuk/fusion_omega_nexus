#!/usr/bin/env python3
"""Revo Signal Advisor — turns the Revo pipeline's market context into concrete, human-reviewable
trade RECOMMENDATIONS (NOT auto-execution). Decision-support for discretionary confluence trading.

For each FLOW_ELIGIBLE candidate the Revo scanner already surfaced, this:
  1. reads DIRECTION + CONFLUENCE from the pipeline (flow, OI, CVD, funding, regime, liquidity, strength)
  2. fetches recent Bybit klines and computes PRICE GEOMETRY (ATR, range, value zone, EQ)
  3. emits a recommendation:
       - "ENTRY NOW" with entry/SL/TP/R:R if price is already in a good value location, OR
       - "WAIT — limit @ <price>" with the better entry level + projected SL/TP if location isn't ideal yet
  4. sends a Telegram card; per-pair cooldown avoids spam. SAFE: no orders, Freqtrade stays dry-run.

Cron (e.g. every 5 min):  python3 scripts/revo_signal_advisor.py
"""
import urllib.request, urllib.parse, json, os, time
from datetime import datetime, timezone

RT = "/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"
EXEC_CTX = f"{RT}/revo_execution_context.json"
HEARTBEAT = f"{RT}/revo_gate_heartbeat_events.jsonl"
COOLDOWN_FILE = f"{RT}/revo_advisor_cooldown.json"
CONFIG_PATH = "/home/fusion_omega/revo_adaptive/user_data/config.bybit.dynamic-universe.paper.json"
BYBIT = "https://api.bybit.com/v5/market"

LOOKBACK = 96          # bars for range
INTERVAL = "15"        # 15m candles
ATR_N = 14
RR = 2.0               # reward:risk target
SL_ATR = 1.5           # stop = 1.5 ATR beyond entry
MAX_RISK_PCT = 0.06    # tightened: skip if ATR stop wider than this (degen low-caps out)
VALUE_BAND = 0.40      # long wants location <=0.40 (discount); short wants >=0.60 (premium)
LOC_CAP = 0.80         # reject LONG if loc>0.80 / SHORT if loc<0.20 (don't chase extremes)
MIN_CONFLUENCE = 4     # only alert B-setup and better
COOLDOWN_SEC = 3 * 3600
ATR_TP_CAP = True      # cap TP at range edge if nearer than R-multiple
BTC_ALIGN = True       # only LONG when BTC uptrend, SHORT when BTC downtrend (validated: lifts PF 0.94->0.99)
# tokenized stocks / commodities on Bybit perps — different dynamics, exclude from the crypto radar
EXCLUDE_BASES = {"ORCL","MRVL","INTC","MU","SNDK","SKHYNIX","NVDA","AAPL","TSLA","META","AMZN","GOOGL",
                 "MSFT","COIN","HOOD","CRCL","BABA","EWY","BILL","CBRS","AVGO","AMD","PLTR","SMCI","NFLX",
                 "XAU","XAUT","PAXG","XAG","CL","UAI","UB","ZBT","SPX"}


def btc_bullish():
    """BTC regime: EMA50>EMA200 on 15m klines. Returns True/False (default True if fetch fails)."""
    try:
        d = _get(f"{BYBIT}/kline?category=linear&symbol=BTCUSDT&interval=15&limit=220")
        rows = sorted(d.get("result", {}).get("list", []), key=lambda r: int(r[0]))
        closes = [float(r[4]) for r in rows]
        if len(closes) < 205:
            return True
        def ema(vals, n):
            k = 2/(n+1); e = vals[0]
            for v in vals[1:]:
                e = v*k + e*(1-k)
            return e
        return ema(closes, 50) > ema(closes, 200)
    except Exception:
        return True


def _tg():
    try:
        tg = json.load(open(CONFIG_PATH)).get("telegram", {})
        if tg.get("enabled"):
            return str(tg["token"]), str(tg["chat_id"])
    except Exception:
        pass
    return None, None


def tg_send(text):
    tok, chat = _tg()
    if not tok:
        print(text); return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "Markdown",
                                       "disable_web_page_preview": "true"}).encode()
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data,
            headers={"User-Agent": "advisor/1.0"}), timeout=15)
    except Exception as e:
        print("tg err", e)


def _get(url):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "advisor/1.0"}), timeout=20).read())


def klines(sym):
    d = _get(f"{BYBIT}/kline?category=linear&symbol={sym}&interval={INTERVAL}&limit={LOOKBACK+ATR_N+5}")
    rows = d.get("result", {}).get("list", [])
    rows.sort(key=lambda r: int(r[0]))      # ascending
    return [(float(r[2]), float(r[3]), float(r[4])) for r in rows]   # (high, low, close)


def geometry(kl):
    highs = [h for h, _, _ in kl]; lows = [l for _, l, _ in kl]; closes = [c for _, _, c in kl]
    price = closes[-1]
    win_h = highs[-LOOKBACK:]; win_l = lows[-LOOKBACK:]
    hi = max(win_h); lo = min(win_l); eq = (hi + lo) / 2
    loc = (price - lo) / (hi - lo) if hi > lo else 0.5
    # ATR
    trs = []
    for i in range(1, len(kl)):
        h, l, _ = kl[i]; pc = kl[i-1][2]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[-ATR_N:]) / ATR_N if len(trs) >= ATR_N else (hi - lo) / LOOKBACK
    swing_lo = min(win_l[-20:]); swing_hi = max(win_h[-20:])
    return dict(price=price, hi=hi, lo=lo, eq=eq, loc=loc, atr=atr, swing_lo=swing_lo, swing_hi=swing_hi)


def build_reco(direction, g, ctx, hb):
    price, atr, hi, lo, eq, loc = g["price"], g["atr"], g["hi"], g["lo"], g["eq"], g["loc"]
    long = direction == "LONG_ONLY"
    # reject extreme locations (don't chase the top/bottom — value entry would be meaningless there)
    if (long and loc > LOC_CAP) or ((not long) and loc < 1 - LOC_CAP):
        return None
    # confluence factors (from Revo pipeline)
    conf = []
    if str(ctx.get("flow_strength")) == "STRONG_FLOW": conf.append("flow STRONG")
    elif str(ctx.get("flow_strength")) == "MODERATE_FLOW": conf.append("flow moderate")
    oi15 = ctx.get("oi_delta_15m_pct", 0) or 0
    if (long and oi15 > 0) or (not long and oi15 < 0): conf.append(f"OI {oi15:+.1f}%✅")
    cvds = str(ctx.get("cvd_structure", ""))
    if (long and "BUY" in cvds) or (not long and "SELL" in cvds): conf.append("CVD aligned")
    if str(ctx.get("funding_context")) == "FUNDING_SAFE": conf.append("funding safe")
    if str(ctx.get("flow_risk")) == "NORMAL": conf.append("no trap")
    reg = str(hb.get("regime_router", "?"))
    if reg == "TRENDING": conf.append("TRENDING")
    if hb.get("liq_floor_ok") == 1 or (ctx.get("liq_qvol_med_5m", 0) or 0) >= 100000: conf.append("liq OK")
    loc_ok = (long and loc <= VALUE_BAND) or (not long and loc >= 1 - VALUE_BAND)
    if loc_ok: conf.append(f"location {loc:.2f}✅")

    # price geometry → entry / sl / tp (ATR-based stop, bounded; skip if too wide/volatile)
    if long:
        value_price = lo + VALUE_BAND * (hi - lo)
        if loc <= VALUE_BAND:
            action = "ENTRY NOW LONG"; entry = price
        else:
            action = "WAIT ⏳ — limit BUY"; entry = round(value_price, 8)
        sl = entry - SL_ATR * atr
        risk = entry - sl
        if risk / entry > MAX_RISK_PCT:
            return None
        tp = entry + RR * risk
        if ATR_TP_CAP and hi > entry and (hi - entry) >= risk:
            tp = min(tp, hi)
    else:
        value_price = lo + (1 - VALUE_BAND) * (hi - lo)
        if loc >= 1 - VALUE_BAND:
            action = "ENTRY NOW SHORT"; entry = price
        else:
            action = "WAIT ⏳ — limit SELL"; entry = round(value_price, 8)
        sl = entry + SL_ATR * atr
        risk = sl - entry
        if risk / entry > MAX_RISK_PCT:
            return None
        tp = entry - RR * risk
        if ATR_TP_CAP and entry > lo and (entry - lo) >= risk:
            tp = max(tp, lo)

    rr = abs(tp - entry) / abs(entry - sl) if entry != sl else 0
    return dict(action=action, entry=entry, sl=sl, tp=tp, rr=rr, conf=conf, loc=loc,
                sl_pct=(sl/entry - 1)*100, tp_pct=(tp/entry - 1)*100, n_conf=len(conf))


def fmt(p):
    return f"{p:.6g}"


def card(pair, direction, r):
    sym = pair.split("/")[0]
    long = direction == "LONG_ONLY"
    emoji = "🟢" if long else "🔴"
    tier = "🟢 A-SETUP" if r["n_conf"] >= 6 else ("🟡 B-SETUP" if r["n_conf"] >= 4 else "👀 WATCH")
    chart = f"https://www.tradingview.com/chart/?symbol=BYBIT:{sym}USDT.P&interval=15"
    lines = [
        f"{emoji} *{sym}* — {'LONG' if long else 'SHORT'}  ({tier} {r['n_conf']}/8)",
        f"*{r['action']}*",
        f"Entry: `{fmt(r['entry'])}`   (lokasi {r['loc']:.2f})",
        f"SL: `{fmt(r['sl'])}` ({r['sl_pct']:+.1f}%)   TP: `{fmt(r['tp'])}` ({r['tp_pct']:+.1f}%)   R:R {r['rr']:.1f}",
        f"Konfluensi: {', '.join(r['conf']) or '-'}",
        f"[chart]({chart})  _bukan auto-trade — keputusan & sizing di kamu_",
    ]
    return "\n".join(lines)


def load_cooldown():
    try: return json.load(open(COOLDOWN_FILE))
    except Exception: return {}


def main():
    try:
        ec = json.load(open(EXEC_CTX))
    except Exception as e:
        print("no exec ctx", e); return
    pairs = ec.get("pairs", ec)
    # latest heartbeat per pair
    hb_by_pair = {}
    try:
        for line in open(HEARTBEAT):
            line = line.strip()
            if not line: continue
            d = json.loads(line)
            hb_by_pair[d.get("pair")] = d
    except Exception:
        pass

    cooldown = load_cooldown(); now = time.time(); sent = 0
    btc_bull = btc_bullish() if BTC_ALIGN else None
    for pair, ctx in pairs.items():
        if ctx.get("entry_permission") != "FLOW_ELIGIBLE":
            continue
        direction = ctx.get("flow_direction") or ctx.get("current_direction")
        if direction not in ("LONG_ONLY", "SHORT_ONLY"):
            continue
        base = pair.split("/")[0]
        if base in EXCLUDE_BASES:          # skip tokenized stocks/commodities
            continue
        if BTC_ALIGN:                      # don't fight the market (validated overlay)
            if direction == "LONG_ONLY" and not btc_bull:
                continue
            if direction == "SHORT_ONLY" and btc_bull:
                continue
        sym = base + "USDT"
        try:
            kl = klines(sym)
            if len(kl) < LOOKBACK: continue
            g = geometry(kl)
        except Exception as e:
            print("klines err", sym, e); continue
        hb = hb_by_pair.get(pair, {})
        r = build_reco(direction, g, ctx, hb)
        if r is None or r["n_conf"] < MIN_CONFLUENCE or r["rr"] < 1.2:
            continue
        # cooldown: skip if alerted same pair+action recently
        key = f"{pair}:{r['action'][:9]}"
        if now - cooldown.get(key, 0) < COOLDOWN_SEC:
            continue
        tg_send(card(pair, direction, r))
        cooldown[key] = now; sent += 1
        time.sleep(0.5)
    json.dump(cooldown, open(COOLDOWN_FILE, "w"))
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} advisor: sent {sent} recommendation(s)")


if __name__ == "__main__":
    main()
