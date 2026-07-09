#!/usr/bin/env python3
"""Cross-sectional momentum PAPER rebalancer (SHADOW — no real orders).

Logic (matches validated backtest): rank top-N pairs by 24h turnover, compute
LOOKBACK-day return, long top-K / short bottom-K, rebalance every REBALANCE_DAYS.
Per-rebalance equity update = spread/2 - round-trip cost. Marks-to-market each run.

Each invocation = one cycle (mark-to-market; rebalance if due). Cron it, or use --loop.
State persisted to runtime dir. SAFE: simulation only, Freqtrade remains sole executor.
"""
import urllib.request, urllib.parse, json, time, os, sys
from datetime import datetime, timezone

RUNTIME = "/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"
STATE = f"{RUNTIME}/revo_xsec_paper_state.json"
EQLOG = f"{RUNTIME}/revo_xsec_paper_equity.jsonl"
STATUS = f"{RUNTIME}/XSEC_PAPER_STATUS.txt"
BASE = "https://api.bybit.com/v5/market"

START_EQUITY = 1000.0
UNIVERSE_TOPN = 50
LOOKBACK_DAYS = 30
REBALANCE_DAYS = 7
K = 10
TAKER = 0.00055
RT_COST = 2 * TAKER          # full-turnover round trip per rebalance
STOP_LEVEL = 0.40            # catastrophe stop: cap a leg moving 40% against us (squeeze guard)
CONFIG_PATH = "/home/fusion_omega/revo_adaptive/user_data/config.bybit.dynamic-universe.paper.json"
NOTIFY_HOUR = 0              # send a daily MTM summary at this UTC hour (rebalances always notify)


def _telegram_creds():
    try:
        tg = json.load(open(CONFIG_PATH)).get("telegram", {})
        if tg.get("enabled") and tg.get("token") and tg.get("chat_id"):
            return str(tg["token"]), str(tg["chat_id"])
    except Exception:
        pass
    return None, None


def tg_send(text):
    token, chat = _telegram_creds()
    if not token:
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text, "parse_mode": "Markdown"}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data,
                                     headers={"User-Agent": "xsec/1.0"})
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        print("telegram err:", e)
        return False


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "xsec/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=25).read())


def tickers():
    rows = _get(f"{BASE}/tickers?category=linear")["result"]["list"]
    out = {}
    for r in rows:
        if r["symbol"].endswith("USDT") and float(r.get("lastPrice", 0)) > 0:
            out[r["symbol"]] = (float(r["lastPrice"]), float(r.get("turnover24h", 0)))
    return out


def past_close(sym, days):
    d = _get(f"{BASE}/kline?category=linear&symbol={sym}&interval=D&limit={days + 2}")
    rows = d.get("result", {}).get("list", [])
    if len(rows) < days + 1:
        return None
    rows.sort(key=lambda r: int(r[0]))      # ascending
    return float(rows[-(days + 1)][4])       # close `days` bars ago


def load():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"equity": START_EQUITY, "last_rebalance": None, "rebalance_count": 0,
            "longs": {}, "shorts": {}}


def save(st):
    json.dump(st, open(STATE, "w"), indent=2)


def open_return(longs, shorts, prices):
    """f-weighted unrealized return of OPEN positions (each leg weight = 0.5/K)."""
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
    st = load()
    st.setdefault("locked_return", 0.0)      # PnL locked by catastrophe-stopped legs
    now = datetime.now(timezone.utc)
    tk = tickers()
    prices = {s: v[0] for s, v in tk.items()}
    f = 0.5 / K

    # --- Catastrophe stop: cap a leg that moves STOP_LEVEL against us (squeeze guard) ---
    stopped = []
    for p, e in list(st["shorts"].items()):
        if p in prices and prices[p] >= e * (1 + STOP_LEVEL):
            st["locked_return"] += f * (-STOP_LEVEL)
            del st["shorts"][p]
            stopped.append(f"SHORT {p.replace('USDT','')} +{int(STOP_LEVEL*100)}%")
    for p, e in list(st["longs"].items()):
        if p in prices and prices[p] <= e * (1 - STOP_LEVEL):
            st["locked_return"] += f * (-STOP_LEVEL)
            del st["longs"][p]
            stopped.append(f"LONG {p.replace('USDT','')} -{int(STOP_LEVEL*100)}%")

    due = (st["last_rebalance"] is None or
           (now - datetime.fromisoformat(st["last_rebalance"])).total_seconds() >= REBALANCE_DAYS * 86400)

    mtm = st["locked_return"] + open_return(st["longs"], st["shorts"], prices)   # unrealized since last rebalance
    live_equity = round(st["equity"] * (1 + mtm), 2)

    event = "MTM"
    if stopped:
        event = "CATASTROPHE_STOP"
        tg_send("🛑 *XSEC catastrophe-stop* (PAPER)\n" + "\n".join(stopped) +
                f"\nlive eq: {live_equity:.2f} USDT")
    if due:
        # realize previous book PnL (open legs + locked stops)
        if st["longs"] or st["shorts"] or st["locked_return"]:
            st["equity"] = round(st["equity"] * (1 + mtm - RT_COST), 2)
        st["locked_return"] = 0.0
        # rank fresh universe by turnover, then by lookback return
        universe = sorted(tk.items(), key=lambda kv: kv[1][1], reverse=True)[:UNIVERSE_TOPN]
        scored = []
        for sym, (px, _) in universe:
            pc = past_close(sym, LOOKBACK_DAYS)
            if pc and pc > 0:
                scored.append((sym, px / pc - 1, px))
            time.sleep(0.05)
        scored.sort(key=lambda x: x[1])
        if len(scored) >= 2 * K:
            shorts = scored[:K]
            longs = scored[-K:]
            st["longs"] = {s: px for s, _, px in longs}
            st["shorts"] = {s: px for s, _, px in shorts}
            st["last_rebalance"] = now.isoformat()
            st["rebalance_count"] += 1
            event = f"REBALANCE#{st['rebalance_count']}"
            live_equity = st["equity"]

    save(st)
    rec = {"ts": now.isoformat(), "event": event, "equity": st["equity"],
           "live_equity": live_equity, "unrealized_pct": round(mtm * 100, 3),
           "n_long": len(st["longs"]), "n_short": len(st["shorts"])}
    with open(EQLOG, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    write_status(st, rec)
    # Telegram: always on rebalance; once/day MTM summary at NOTIFY_HOUR
    if event.startswith("REBALANCE") or now.hour == NOTIFY_HOUR:
        tg_send(_msg(st, rec))
    return rec


def _msg(st, rec):
    pnl = (st["equity"] / START_EQUITY - 1) * 100
    head = "🔄 *XSEC Rebalance*" if rec["event"].startswith("REBALANCE") else "📊 *XSEC Daily*"
    body = [
        f"{head} (PAPER/shadow)",
        f"event: `{rec['event']}`",
        f"equity: *{st['equity']:.2f}* USDT ({pnl:+.2f}%)",
        f"live MTM: {rec['live_equity']:.2f} ({rec['unrealized_pct']:+.3f}%)",
        f"rebalances: {st['rebalance_count']}",
    ]
    if rec["event"].startswith("REBALANCE"):
        body.append("📈 LONG: " + ", ".join(s.replace("USDT", "") for s in sorted(st["longs"])))
        body.append("📉 SHORT: " + ", ".join(s.replace("USDT", "") for s in sorted(st["shorts"])))
    return "\n".join(body)


def write_status(st, rec):
    pnl = (st["equity"] / START_EQUITY - 1) * 100
    lines = [
        "CROSS-SECTIONAL MOMENTUM — PAPER REBALANCER (SHADOW, no real orders)",
        f"updated      : {rec['ts']}",
        f"event        : {rec['event']}",
        f"realized eq  : {st['equity']:.2f} USDT  ({pnl:+.2f}% from {START_EQUITY:.0f})",
        f"live eq (MTM): {rec['live_equity']:.2f} USDT  (unrealized {rec['unrealized_pct']:+.3f}%)",
        f"rebalances   : {st['rebalance_count']}   next in <= {REBALANCE_DAYS}d from last",
        f"config       : topN={UNIVERSE_TOPN} L={LOOKBACK_DAYS}d H={REBALANCE_DAYS}d K={K} cost={RT_COST*1e4:.0f}bps stop={int(STOP_LEVEL*100)}%",
        "",
        "LONGS : " + ", ".join(sorted(st["longs"])),
        "SHORTS: " + ", ".join(sorted(st["shorts"])),
    ]
    open(STATUS, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    if "--test-telegram" in sys.argv:
        ok = tg_send("✅ *XSEC paper rebalancer* terhubung ke Telegram.\nNotifikasi akan dikirim saat rebalance (mingguan) + ringkasan harian.")
        print("telegram test:", "SENT" if ok else "FAILED")
    elif "--loop" in sys.argv:
        while True:
            try:
                cycle()
            except Exception as e:
                print("cycle err:", e)
            time.sleep(3600)
    else:
        cycle()
