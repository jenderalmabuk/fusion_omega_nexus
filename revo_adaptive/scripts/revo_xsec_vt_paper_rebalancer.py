#!/usr/bin/env python3
"""XSEC-VT — cross-sectional momentum with INVERSE-VOL weights + PORTFOLIO VOL-TARGETING (SHADOW).

Same edge + universe + accounting as revo_xsec_paper_rebalancer (top-N turnover, L=30 / H=7 / K=10,
dollar-neutral L/S, catastrophe-stop), but two validated risk upgrades layered on:
  1. INVERSE-VOL weights — each leg weighted by 1/realized_vol (volatile coins don't dominate risk).
  2. PORTFOLIO VOL-TARGETING — gross exposure scaled to a fixed annual vol target x fractional-Kelly,
     so the book de-risks automatically when its own volatility rises (Springer/Artemis: halves maxDD).
Runs in PARALLEL with the existing XSEC/CVD/BLEND bots — changes nothing already running.
Cron hourly or --loop. SAFE: simulation only.
"""
import urllib.request, urllib.parse, json, time, os, sys
from datetime import datetime, timezone

RUNTIME = "/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"
STATE = f"{RUNTIME}/revo_xsec_vt_paper_state.json"
EQLOG = f"{RUNTIME}/revo_xsec_vt_paper_equity.jsonl"
STATUS = f"{RUNTIME}/XSEC_VT_PAPER_STATUS.txt"
CONFIG_PATH = "/home/fusion_omega/revo_adaptive/user_data/config.bybit.dynamic-universe.paper.json"
BASE = "https://api.bybit.com/v5/market"

START_EQUITY = 1000.0
UNIVERSE_TOPN = 50
LOOKBACK_DAYS = 30
VOL_DAYS = 14
REBALANCE_DAYS = 7
K = 10
TAKER = 0.00055
RT_COST = 2 * TAKER
STOP_LEVEL = 0.40
NOTIFY_HOUR = 0
# vol-targeting
TARGET_VOL_ANN = 0.30
KELLY = 0.5
MAX_LEV = 1.5
BOOTSTRAP_VOL = 0.40
REB_PER_YEAR = 365 / REBALANCE_DAYS


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


def _get(url):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "xsecvt/1.0"}), timeout=25).read())


def tickers():
    rows = _get(f"{BASE}/tickers?category=linear")["result"]["list"]
    return {r["symbol"]: (float(r["lastPrice"]), float(r.get("turnover24h", 0)))
            for r in rows if r["symbol"].endswith("USDT") and float(r.get("lastPrice", 0)) > 0}


def daily(sym, n):
    d = _get(f"{BASE}/kline?category=linear&symbol={sym}&interval=D&limit={n + 2}")
    rows = d.get("result", {}).get("list", [])
    if len(rows) < n + 1:
        return None
    rows.sort(key=lambda r: int(r[0]))
    return [float(r[4]) for r in rows]


def mom_and_vol(sym):
    c = daily(sym, LOOKBACK_DAYS + 1)
    if not c:
        return None
    mom = c[-1] / c[-(LOOKBACK_DAYS + 1)] - 1
    rets = [c[i] / c[i - 1] - 1 for i in range(len(c) - VOL_DAYS, len(c))]
    m = sum(rets) / len(rets)
    vol = (sum((x - m) ** 2 for x in rets) / len(rets)) ** 0.5
    return mom, max(vol, 1e-4)


def load():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"equity": START_EQUITY, "last_rebalance": None, "rebalance_count": 0,
            "weights": {}, "entries": {}, "locked_return": 0.0, "leverage": 0.0, "reb_returns": []}


def realized_vol(reb_returns):
    if len(reb_returns) < 4:
        return BOOTSTRAP_VOL
    m = sum(reb_returns) / len(reb_returns)
    sd = (sum((x - m) ** 2 for x in reb_returns) / len(reb_returns)) ** 0.5
    ann = sd * (REB_PER_YEAR ** 0.5)
    return ann if ann > 1e-6 else BOOTSTRAP_VOL


def open_return(st, prices):
    """Signed-weight MTM: sum w_i * (price/entry - 1)."""
    tot = 0.0
    for sym, w in st["weights"].items():
        e = st["entries"].get(sym)
        if e and sym in prices:
            tot += w * (prices[sym] / e - 1)
    return tot


def cycle():
    st = load(); st.setdefault("locked_return", 0.0)
    now = datetime.now(timezone.utc)
    tk = tickers(); prices = {s: v[0] for s, v in tk.items()}

    # catastrophe stop per leg (weight zeroed, lock -STOP*|w|)
    stopped = []
    for sym, w in list(st["weights"].items()):
        e = st["entries"].get(sym)
        if not e or sym not in prices:
            continue
        move = prices[sym] / e - 1
        if (w > 0 and move <= -STOP_LEVEL) or (w < 0 and move >= STOP_LEVEL):
            st["locked_return"] += w * (-STOP_LEVEL if w > 0 else STOP_LEVEL)
            del st["weights"][sym]; stopped.append(f"{'LONG' if w>0 else 'SHORT'} {sym}")

    due = (st["last_rebalance"] is None or
           (now - datetime.fromisoformat(st["last_rebalance"])).total_seconds() >= REBALANCE_DAYS * 86400)
    mtm = st["locked_return"] + open_return(st, prices)
    live = round(st["equity"] * (1 + mtm), 2)
    event = "MTM"
    if stopped:
        event = "CATASTROPHE_STOP"
        tg_send("🛑 *XSEC-VT catastrophe-stop* (PAPER)\n" + "\n".join(stopped) + f"\nlive eq: {live:.2f}")

    if due:
        if st["weights"] or st["locked_return"]:
            st["equity"] = round(st["equity"] * (1 + mtm - st.get("leverage", 1.0) * RT_COST), 2)
            st["reb_returns"] = (st.get("reb_returns", []) + [mtm])[-26:]
        st["locked_return"] = 0.0
        # rank universe by momentum; compute inverse-vol weights
        universe = sorted(tk.items(), key=lambda kv: kv[1][1], reverse=True)[:UNIVERSE_TOPN]
        scored = []
        for sym, (px, _) in universe:
            mv = mom_and_vol(sym)
            if mv:
                scored.append((sym, mv[0], mv[1], px))
            time.sleep(0.04)
        if len(scored) >= 2 * K:
            scored.sort(key=lambda x: x[1])
            shorts = scored[:K]; longs = scored[-K:]
            lev = max(0.0, min(MAX_LEV, TARGET_VOL_ANN / realized_vol(st.get("reb_returns", [])) * KELLY))
            # inverse-vol weights per side, each side gross = 0.5*lev (net 0)
            def side_weights(group, sign):
                inv = {s: 1.0 / v for s, _, v, _ in group}
                tot = sum(inv.values())
                return {s: sign * 0.5 * lev * inv[s] / tot for s in inv}
            w = {}
            w.update(side_weights(longs, +1)); w.update(side_weights(shorts, -1))
            st["weights"] = w
            st["entries"] = {s: px for s, _, _, px in longs + shorts}
            st["leverage"] = round(lev, 3)
            st["last_rebalance"] = now.isoformat(); st["rebalance_count"] += 1
            event = f"REBALANCE#{st['rebalance_count']}"; live = st["equity"]

    json.dump(st, open(STATE, "w"), indent=2)
    pnl = (st["equity"] / START_EQUITY - 1) * 100
    nl = sum(1 for x in st["weights"].values() if x > 0); ns = sum(1 for x in st["weights"].values() if x < 0)
    gross = sum(abs(x) for x in st["weights"].values())
    rec = {"ts": now.isoformat(), "event": event, "equity": st["equity"], "live_equity": live,
           "unrealized_pct": round(mtm * 100, 3), "n_long": nl, "n_short": ns,
           "leverage": st.get("leverage", 0.0), "gross": round(gross, 3)}
    open(EQLOG, "a").write(json.dumps(rec) + "\n")
    lines = [
        "XSEC-VT (inverse-vol + vol-targeting) — PAPER (SHADOW, no real orders)",
        f"updated      : {rec['ts']}", f"event        : {event}",
        f"realized eq  : {st['equity']:.2f} USDT  ({pnl:+.2f}%)",
        f"live eq (MTM): {live:.2f} USDT  (unrealized {rec['unrealized_pct']:+.3f}%)",
        f"rebalances   : {st['rebalance_count']}  | leverage={st.get('leverage',0):.2f}x  gross={gross:.2f}",
        f"config       : topN={UNIVERSE_TOPN} L={LOOKBACK_DAYS}d H={REBALANCE_DAYS}d K={K} targetVol={TARGET_VOL_ANN} kelly={KELLY}",
    ]
    open(STATUS, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    if event.startswith("REBALANCE") or now.hour == NOTIFY_HOUR:
        tg_send(f"🟠 *XSEC-VT* ({event})\nequity: *{st['equity']:.2f}* ({pnl:+.2f}%)\nlive MTM: {live:.2f}\nlev={st.get('leverage',0):.2f}x gross={gross:.2f}")
    return rec


if __name__ == "__main__":
    if "--test-telegram" in sys.argv:
        tg_send("✅ *XSEC-VT paper bot* terhubung Telegram."); print("sent")
    elif "--loop" in sys.argv:
        while True:
            try: cycle()
            except Exception as e: print("err", e)
            time.sleep(3600)
    else:
        cycle()
