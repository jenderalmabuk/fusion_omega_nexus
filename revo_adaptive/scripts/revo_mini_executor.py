#!/usr/bin/env python3
"""Mini market-neutral EXECUTOR — DRY_RUN learning tool for a $100 real account.

Purpose: learn the REAL execution mechanics (position sizing, qty rounding to exchange step/minimum,
min-notional exclusion, fees, funding) WITHOUT placing real orders. Mirrors how a live market-neutral
rebalance would build its order ticket on Bybit USDT perps.

Config (honest mini): $100 capital, leverage 1.0x, dollar-neutral, K long/short, EXCLUDE BTC/ETH
(min notional too large), weekly rebalance, momentum factor. Produces an ORDER TICKET = exactly what
would be sent to Bybit. State persisted; marks-to-market each run; realizes on weekly rebalance.

SAFETY: places NO real orders. Going live requires BYBIT_API_KEY/SECRET + an explicit --live flag,
which is intentionally NOT implemented here — this is a mechanics simulator only.

CLI: python3 scripts/revo_mini_executor.py            # one dry rebalance/MTM cycle
     python3 scripts/revo_mini_executor.py --ticket   # just show the order ticket
"""
import urllib.request, urllib.parse, json, time, os, sys, math
from datetime import datetime, timezone

RUNTIME = "/home/fusion_omega/revo_adaptive/user_data/revo_alpha/runtime/bybit"
STATE = f"{RUNTIME}/revo_mini_exec_state.json"
EQLOG = f"{RUNTIME}/revo_mini_exec_equity.jsonl"
CONFIG_PATH = "/home/fusion_omega/revo_adaptive/user_data/config.bybit.dynamic-universe.paper.json"
BASE = "https://api.bybit.com/v5/market"

CAPITAL = 100.0          # USD (paper)
LEVERAGE = 1.0           # gross = LEVERAGE * CAPITAL (dollar-neutral: half long, half short)
K = 5                    # positions per side -> 2K total
UNIVERSE_TOPN = 40       # rank pool by 24h turnover
LOOKBACK_DAYS = 30       # momentum lookback
REBALANCE_DAYS = 7       # weekly (daily turnover would bleed at $100)
TAKER = 0.00055
SLIP = 0.0005
STOP_LEVEL = 0.40
EXCLUDE = {"BTCUSDT", "ETHUSDT"}   # min notional too large for a $100 diversified book
DRY_RUN = True


def _get(u):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "miniexec/1.0"}), timeout=25).read())


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


def instruments():
    out = {}
    for r in _get(f"{BASE}/instruments-info?category=linear")["result"]["list"]:
        lot = r.get("lotSizeFilter", {})
        out[r["symbol"]] = dict(min_qty=float(lot.get("minOrderQty", 0)), step=float(lot.get("qtyStep", 0) or lot.get("minOrderQty", 0)))
    return out


def tickers():
    return {r["symbol"]: (float(r["lastPrice"]), float(r.get("turnover24h", 0)))
            for r in _get(f"{BASE}/tickers?category=linear")["result"]["list"]
            if r["symbol"].endswith("USDT") and float(r.get("lastPrice", 0)) > 0}


def mom(sym):
    try:
        d = _get(f"{BASE}/kline?category=linear&symbol={sym}&interval=D&limit={LOOKBACK_DAYS + 2}")
        rows = sorted(d.get("result", {}).get("list", []), key=lambda r: int(r[0]))
        if len(rows) < LOOKBACK_DAYS + 1:
            return None
        return float(rows[-1][4]) / float(rows[-(LOOKBACK_DAYS + 1)][4]) - 1
    except Exception:
        return None


def round_qty(qty, step, min_qty):
    if step <= 0:
        return qty if qty >= min_qty else 0.0
    n = math.floor(qty / step) * step
    n = round(n, 10)
    return n if n >= min_qty - 1e-12 else 0.0


def load():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {"equity": CAPITAL, "last_rebalance": None, "rebalance_count": 0, "positions": {}, "locked": 0.0}


def open_return(positions, prices):
    """positions: {sym: {side:+1/-1, qty, entry}} ; weight = notional/(capital) at entry."""
    tot = 0.0
    for s, p in positions.items():
        if s in prices and p["entry"] > 0:
            w = p["qty"] * p["entry"] / CAPITAL          # fraction of capital
            tot += p["side"] * w * (prices[s] / p["entry"] - 1)
    return tot


def build_ticket():
    inst = instruments(); tk = tickers()
    per_pos = LEVERAGE * CAPITAL / (2 * K)               # target notional per position
    universe = sorted(tk.items(), key=lambda kv: kv[1][1], reverse=True)
    cand = []
    for s, (px, _) in universe:
        if s in EXCLUDE or s not in inst:
            continue
        mq = inst[s]["min_qty"]; step = inst[s]["step"]
        if mq * px > per_pos:                            # min order bigger than our per-position budget
            continue
        cand.append(s)
        if len(cand) >= UNIVERSE_TOPN:
            break
    scored = []
    for s in cand:
        m = mom(s)
        if m is not None:
            scored.append((s, m, tk[s][0]))
        time.sleep(0.03)
    scored.sort(key=lambda x: x[1])
    if len(scored) < 2 * K:
        return None, "not enough qualifying coins"
    longs = scored[-K:]; shorts = scored[:K]
    ticket = []
    for s, _, px in longs:
        q = round_qty(per_pos / px, inst[s]["step"], inst[s]["min_qty"])
        ticket.append(dict(side="BUY", sym=s, price=px, qty=q, notional=round(q * px, 2)))
    for s, _, px in shorts:
        q = round_qty(per_pos / px, inst[s]["step"], inst[s]["min_qty"])
        ticket.append(dict(side="SELL", sym=s, price=px, qty=q, notional=round(q * px, 2)))
    return ticket, None


def show_ticket(ticket):
    print(f"\n=== ORDER TICKET (DRY_RUN — nothing sent) | per-position target ${LEVERAGE*CAPITAL/(2*K):.2f} ===")
    print(f"{'SIDE':<5}{'SYMBOL':<14}{'price':>12}{'qty':>14}{'notional$':>11}")
    gl = gs = fee = 0.0
    for o in ticket:
        flag = " ⚠️unfilled(min)" if o["qty"] == 0 else ""
        print(f"{o['side']:<5}{o['sym']:<14}{o['price']:>12.5f}{o['qty']:>14g}{o['notional']:>11.2f}{flag}")
        if o["side"] == "BUY": gl += o["notional"]
        else: gs += o["notional"]
        fee += o["notional"] * TAKER
    print("-" * 56)
    print(f"long gross ${gl:.2f} | short gross ${gs:.2f} | net ${gl-gs:+.2f} | gross ${gl+gs:.2f} "
          f"({(gl+gs)/CAPITAL:.2f}x) | est fee ${fee:.3f}")
    skipped = sum(1 for o in ticket if o["qty"] == 0)
    if skipped:
        print(f"⚠️ {skipped} legs un-fillable at this size (qty rounded below exchange minimum) -> book not fully neutral")


def cycle():
    st = load(); st.setdefault("locked", 0.0)
    now = datetime.now(timezone.utc)
    tk = tickers(); prices = {s: v[0] for s, v in tk.items()}
    # catastrophe stop
    for s, p in list(st["positions"].items()):
        if s in prices and p["entry"] > 0:
            mv = prices[s] / p["entry"] - 1
            if (p["side"] > 0 and mv <= -STOP_LEVEL) or (p["side"] < 0 and mv >= STOP_LEVEL):
                w = p["qty"] * p["entry"] / CAPITAL
                st["locked"] += -STOP_LEVEL * w
                del st["positions"][s]
    due = (st["last_rebalance"] is None or
           (now - datetime.fromisoformat(st["last_rebalance"])).total_seconds() >= REBALANCE_DAYS * 86400)
    mtm = st["locked"] + open_return(st["positions"], prices)
    live = round(st["equity"] * (1 + mtm), 2)
    if due:
        ticket, err = build_ticket()
        if err:
            print("rebalance skipped:", err); return
        if st["positions"] or st["locked"]:
            st["equity"] = round(st["equity"] * (1 + mtm) - (LEVERAGE * CAPITAL) * (TAKER + SLIP), 2)
        st["locked"] = 0.0
        st["positions"] = {}
        for o in ticket:
            if o["qty"] > 0:
                st["positions"][o["sym"]] = dict(side=1 if o["side"] == "BUY" else -1, qty=o["qty"], entry=o["price"])
        st["last_rebalance"] = now.isoformat(); st["rebalance_count"] += 1
        live = st["equity"]
        print(f"\n[REBALANCE #{st['rebalance_count']}] {now.isoformat(timespec='seconds')}")
        show_ticket(ticket)
    else:
        print(f"[MTM] eq ${st['equity']:.2f} | live ${live:.2f} ({mtm*100:+.2f}%) | next rebalance in "
              f"{REBALANCE_DAYS - (now - datetime.fromisoformat(st['last_rebalance'])).days}d")
    json.dump(st, open(STATE, "w"), indent=2)
    open(EQLOG, "a").write(json.dumps({"ts": now.isoformat(), "equity": st["equity"], "live": live,
                                       "mtm_pct": round(mtm*100, 3), "n": len(st["positions"])}) + "\n")
    print(f"equity ${st['equity']:.2f} ({(st['equity']/CAPITAL-1)*100:+.2f}%) | rebalances {st['rebalance_count']} | DRY_RUN={DRY_RUN}")


if __name__ == "__main__":
    if "--ticket" in sys.argv:
        t, err = build_ticket()
        print(err or "") ; show_ticket(t) if t else None
    elif "--live" in sys.argv:
        print("LIVE trading is intentionally NOT implemented in this mechanics simulator.\n"
              "It needs BYBIT_API_KEY/SECRET + signed order code + your explicit risk acknowledgment.\n"
              "Build/learn on DRY_RUN first; ask to add a guarded live path only when ready.")
    else:
        cycle()
