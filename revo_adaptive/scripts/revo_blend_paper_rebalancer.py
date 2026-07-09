#!/usr/bin/env python3
"""Price+CVD BLEND PAPER rebalancer (SHADOW — no real orders).

Combines the two validated cross-sectional factors into one composite, the way the backtest
"Price + CVD" did (PF ~2.50, Sharpe ~2.38 — higher Sharpe than either factor alone is not the
goal; the blend is more ROBUST because the two factors are only ~0.32 correlated).

Composite score per coin (cross-sectional, single venue = Binance daily klines):
  mom = close_now / close_30d_ago - 1                      (price momentum)
  cvd = sum(flow)/sum(|flow|) over 30d, flow=2*takerBuyBase-volume   (taker accumulation)
  score = zscore(mom) + zscore(cvd)        # equal-weight blend of the two independent factors
LONG top-K by score / SHORT bottom-K. Mirrors revo_xsec/cvd accounting for apples-to-apples.
Cron hourly or --loop. SAFE: simulation only; Freqtrade remains sole executor.
"""
import urllib.request, urllib.parse, json, time, os, sys
from datetime import datetime, timezone

RUNTIME = "/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"
STATE = f"{RUNTIME}/revo_blend_paper_state.json"
EQLOG = f"{RUNTIME}/revo_blend_paper_equity.jsonl"
STATUS = f"{RUNTIME}/BLEND_PAPER_STATUS.txt"
CONFIG_PATH = "/home/fusion_omega/revo_adaptive/user_data/config.bybit.dynamic-universe.paper.json"
FAPI = "https://fapi.binance.com/fapi/v1"

START_EQUITY = 1000.0
UNIVERSE_TOPN = 50
LOOKBACK_DAYS = 30
REBALANCE_DAYS = 7
K = 10
TAKER = 0.00055
RT_COST = 2 * TAKER
STOP_LEVEL = 0.40
NOTIFY_HOUR = 0


def _get(u):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "blend/1.0"}), timeout=20).read())


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
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "Markdown"}).encode()
        urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data), timeout=15)
    except Exception as e:
        print("tg err", e)


def tickers():
    out = {}
    for r in _get(f"{FAPI}/ticker/24hr"):
        s = r["symbol"]
        if s.endswith("USDT") and float(r.get("lastPrice", 0)) > 0:
            out[s] = (float(r["lastPrice"]), float(r.get("quoteVolume", 0)))
    return out


def factors(sym):
    """Return (momentum, cvd) over LOOKBACK_DAYS from one Binance daily-kline call, else None."""
    try:
        k = _get(f"{FAPI}/klines?symbol={sym}&interval=1d&limit={LOOKBACK_DAYS + 1}")
    except Exception:
        return None
    if len(k) < LOOKBACK_DAYS + 1:
        return None
    c0 = float(k[0][4]); c1 = float(k[-1][4])
    if c0 <= 0:
        return None
    mom = c1 / c0 - 1
    flow = [2 * float(r[9]) - float(r[5]) for r in k[-LOOKBACK_DAYS:]]
    den = sum(abs(f) for f in flow) + 1e-9
    cvd = sum(flow) / den
    return mom, cvd


def _z(vals):
    n = len(vals)
    if n < 2:
        return [0.0] * n
    m = sum(vals) / n
    sd = (sum((v - m) ** 2 for v in vals) / n) ** 0.5 or 1.0
    return [(v - m) / sd for v in vals]


def load():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"equity": START_EQUITY, "last_rebalance": None, "rebalance_count": 0, "longs": {}, "shorts": {}, "locked_return": 0.0}


def open_return(longs, shorts, prices):
    f = 0.5 / K
    tot = 0.0
    for p, e in longs.items():
        if p in prices:
            tot += f * (prices[p] / e - 1)
    for p, e in shorts.items():
        if p in prices:
            tot += f * (1 - prices[p] / e)
    return tot


def cycle():
    st = load(); st.setdefault("locked_return", 0.0)
    now = datetime.now(timezone.utc)
    tk = tickers()
    prices = {s: v[0] for s, v in tk.items()}
    f = 0.5 / K

    stopped = []
    for p, e in list(st["shorts"].items()):
        if p in prices and prices[p] >= e * (1 + STOP_LEVEL):
            st["locked_return"] += f * (-STOP_LEVEL); del st["shorts"][p]; stopped.append(f"SHORT {p} +{int(STOP_LEVEL*100)}%")
    for p, e in list(st["longs"].items()):
        if p in prices and prices[p] <= e * (1 - STOP_LEVEL):
            st["locked_return"] += f * (-STOP_LEVEL); del st["longs"][p]; stopped.append(f"LONG {p} -{int(STOP_LEVEL*100)}%")

    due = (st["last_rebalance"] is None or
           (now - datetime.fromisoformat(st["last_rebalance"])).total_seconds() >= REBALANCE_DAYS * 86400)
    mtm = st["locked_return"] + open_return(st["longs"], st["shorts"], prices)
    live = round(st["equity"] * (1 + mtm), 2)
    event = "MTM"
    if stopped:
        event = "CATASTROPHE_STOP"
        tg_send("🛑 *BLEND catastrophe-stop* (PAPER)\n" + "\n".join(stopped) + f"\nlive eq: {live:.2f}")
    if due:
        if st["longs"] or st["shorts"] or st["locked_return"]:
            st["equity"] = round(st["equity"] * (1 + mtm - RT_COST), 2)
        st["locked_return"] = 0.0
        universe = sorted(tk.items(), key=lambda kv: kv[1][1], reverse=True)[:UNIVERSE_TOPN]
        raw = []
        for sym, (px, _) in universe:
            fac = factors(sym)
            if fac is not None:
                raw.append((sym, fac[0], fac[1], px))
            time.sleep(0.04)
        if len(raw) >= 2 * K:
            zmom = _z([r[1] for r in raw])
            zcvd = _z([r[2] for r in raw])
            scored = [(raw[i][0], zmom[i] + zcvd[i], raw[i][3]) for i in range(len(raw))]
            scored.sort(key=lambda x: x[1])       # ascending: weakest composite first
            st["shorts"] = {s: px for s, _, px in scored[:K]}
            st["longs"] = {s: px for s, _, px in scored[-K:]}
            st["last_rebalance"] = now.isoformat(); st["rebalance_count"] += 1
            event = f"REBALANCE#{st['rebalance_count']}"; live = st["equity"]

    json.dump(st, open(STATE, "w"), indent=2)
    rec = {"ts": now.isoformat(), "event": event, "equity": st["equity"], "live_equity": live,
           "unrealized_pct": round(mtm * 100, 3), "n_long": len(st["longs"]), "n_short": len(st["shorts"])}
    open(EQLOG, "a").write(json.dumps(rec) + "\n")
    pnl = (st["equity"] / START_EQUITY - 1) * 100
    lines = [
        "PRICE+CVD BLEND — PAPER REBALANCER (SHADOW, no real orders)",
        f"updated      : {rec['ts']}", f"event        : {event}",
        f"realized eq  : {st['equity']:.2f} USDT  ({pnl:+.2f}%)",
        f"live eq (MTM): {live:.2f} USDT  (unrealized {rec['unrealized_pct']:+.3f}%)",
        f"rebalances   : {st['rebalance_count']}",
        f"config       : topN={UNIVERSE_TOPN} L={LOOKBACK_DAYS}d H={REBALANCE_DAYS}d K={K} cost={RT_COST*1e4:.0f}bps stop={int(STOP_LEVEL*100)}% score=z(mom)+z(cvd)",
        "", "LONG  : " + ", ".join(sorted(st["longs"])),
        "SHORT : " + ", ".join(sorted(st["shorts"])),
    ]
    open(STATUS, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    if event.startswith("REBALANCE") or now.hour == NOTIFY_HOUR:
        msg = [f"🔵 *Price+CVD Blend* ({event})", f"equity: *{st['equity']:.2f}* ({pnl:+.2f}%)", f"live MTM: {live:.2f}"]
        if event.startswith("REBALANCE"):
            msg.append("📈 LONG: " + ", ".join(s.replace("USDT", "") for s in sorted(st["longs"])))
            msg.append("📉 SHORT: " + ", ".join(s.replace("USDT", "") for s in sorted(st["shorts"])))
        tg_send("\n".join(msg))
    return rec


if __name__ == "__main__":
    if "--test-telegram" in sys.argv:
        tg_send("✅ *Price+CVD blend paper bot* terhubung Telegram."); print("sent")
    elif "--loop" in sys.argv:
        while True:
            try: cycle()
            except Exception as e: print("err", e)
            time.sleep(3600)
    else:
        cycle()
