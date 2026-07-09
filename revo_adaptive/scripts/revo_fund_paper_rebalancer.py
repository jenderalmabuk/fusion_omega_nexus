#!/usr/bin/env python3
"""FUND — funding-contrarian cross-sectional PAPER rebalancer (SHADOW, no real orders).

The 'whale/crowd positioning' factor (validated in backtest/whale_factors.py: Sharpe 1.70, lowest
maxDD -8.7%, ONLY factor that held up out-of-sample +1.26, uncorrelated with momentum & CVD).
Logic: rank top-N by turnover, then by FUNDING RATE. LONG the lowest/most-negative funding
(crowded shorts → squeeze setup), SHORT the highest funding (crowded longs → flush risk).
Mirrors revo_xsec/cvd accounting (L=N/A here — uses current funding snapshot, H=7, K=10, catastrophe-stop).
Cron hourly or --loop. SAFE: simulation only; Freqtrade remains sole executor.
"""
import urllib.request, urllib.parse, json, time, os, sys
from datetime import datetime, timezone

RUNTIME = "/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"
STATE = f"{RUNTIME}/revo_fund_paper_state.json"
EQLOG = f"{RUNTIME}/revo_fund_paper_equity.jsonl"
STATUS = f"{RUNTIME}/FUND_PAPER_STATUS.txt"
CONFIG_PATH = "/home/fusion_omega/revo_adaptive/user_data/config.bybit.dynamic-universe.paper.json"
BASE = "https://api.bybit.com/v5/market"

START_EQUITY = 1000.0
UNIVERSE_TOPN = 50
REBALANCE_DAYS = 7
K = 10
TAKER = 0.00055
RT_COST = 2 * TAKER
STOP_LEVEL = 0.40
NOTIFY_HOUR = 0


def _get(u):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "fund/1.0"}), timeout=25).read())


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
    """Return {symbol: (lastPrice, turnover24h, fundingRate)} for USDT linear perps."""
    rows = _get(f"{BASE}/tickers?category=linear")["result"]["list"]
    out = {}
    for r in rows:
        s = r["symbol"]
        if s.endswith("USDT") and float(r.get("lastPrice", 0)) > 0:
            try:
                fr = float(r.get("fundingRate", 0))
            except Exception:
                fr = 0.0
            out[s] = (float(r["lastPrice"]), float(r.get("turnover24h", 0)), fr)
    return out


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
            st["locked_return"] += f * (-STOP_LEVEL); del st["shorts"][p]; stopped.append(f"SHORT {p}")
    for p, e in list(st["longs"].items()):
        if p in prices and prices[p] <= e * (1 - STOP_LEVEL):
            st["locked_return"] += f * (-STOP_LEVEL); del st["longs"][p]; stopped.append(f"LONG {p}")

    due = (st["last_rebalance"] is None or
           (now - datetime.fromisoformat(st["last_rebalance"])).total_seconds() >= REBALANCE_DAYS * 86400)
    mtm = st["locked_return"] + open_return(st["longs"], st["shorts"], prices)
    live = round(st["equity"] * (1 + mtm), 2)
    event = "MTM"
    if stopped:
        event = "CATASTROPHE_STOP"
        tg_send("🛑 *FUND catastrophe-stop* (PAPER)\n" + "\n".join(stopped) + f"\nlive eq: {live:.2f}")
    if due:
        if st["longs"] or st["shorts"] or st["locked_return"]:
            st["equity"] = round(st["equity"] * (1 + mtm - RT_COST), 2)
        st["locked_return"] = 0.0
        # rank top-N by turnover, then by FUNDING (ascending). long lowest funding / short highest.
        universe = sorted(tk.items(), key=lambda kv: kv[1][1], reverse=True)[:UNIVERSE_TOPN]
        scored = [(sym, fr, px) for sym, (px, _, fr) in universe]
        scored.sort(key=lambda x: x[1])           # ascending funding: most-negative first
        if len(scored) >= 2 * K:
            st["longs"] = {s: px for s, _, px in scored[:K]}        # lowest/negative funding -> LONG
            st["shorts"] = {s: px for s, _, px in scored[-K:]}      # highest funding -> SHORT
            st["last_rebalance"] = now.isoformat(); st["rebalance_count"] += 1
            event = f"REBALANCE#{st['rebalance_count']}"; live = st["equity"]

    json.dump(st, open(STATE, "w"), indent=2)
    rec = {"ts": now.isoformat(), "event": event, "equity": st["equity"], "live_equity": live,
           "unrealized_pct": round(mtm * 100, 3), "n_long": len(st["longs"]), "n_short": len(st["shorts"])}
    open(EQLOG, "a").write(json.dumps(rec) + "\n")
    pnl = (st["equity"] / START_EQUITY - 1) * 100
    lines = [
        "FUND-CONTRARIAN (funding positioning) — PAPER (SHADOW, no real orders)",
        f"updated      : {rec['ts']}", f"event        : {event}",
        f"realized eq  : {st['equity']:.2f} USDT  ({pnl:+.2f}%)",
        f"live eq (MTM): {live:.2f} USDT  (unrealized {rec['unrealized_pct']:+.3f}%)",
        f"rebalances   : {st['rebalance_count']}",
        f"config       : topN={UNIVERSE_TOPN} H={REBALANCE_DAYS}d K={K} factor=funding(long low / short high)",
        "", "LONG (low/neg funding) : " + ", ".join(sorted(st["longs"])),
        "SHORT (high funding)   : " + ", ".join(sorted(st["shorts"])),
    ]
    open(STATUS, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    if event.startswith("REBALANCE") or now.hour == NOTIFY_HOUR:
        msg = [f"🟤 *FUND-Contrarian* ({event})", f"equity: *{st['equity']:.2f}* ({pnl:+.2f}%)", f"live MTM: {live:.2f}"]
        if event.startswith("REBALANCE"):
            msg.append("📈 LONG: " + ", ".join(s.replace("USDT", "") for s in sorted(st["longs"])))
            msg.append("📉 SHORT: " + ", ".join(s.replace("USDT", "") for s in sorted(st["shorts"])))
        tg_send("\n".join(msg))
    return rec


if __name__ == "__main__":
    if "--test-telegram" in sys.argv:
        tg_send("✅ *FUND-contrarian paper bot* terhubung Telegram."); print("sent")
    elif "--loop" in sys.argv:
        while True:
            try: cycle()
            except Exception as e: print("err", e)
            time.sleep(3600)
    else:
        cycle()
