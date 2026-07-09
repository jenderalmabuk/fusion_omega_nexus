import ccxt, pandas as pd, numpy as np, sys, json
sys.path.insert(0, "/freqtrade/user_data/strategies")
from RevoAdaptiveStrategy import RevoAdaptiveStrategy

exchange = ccxt.bybit({"options": {"defaultType": "spot"}})
s = RevoAdaptiveStrategy({})

pairs = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "HMSTR/USDT:USDT", "VANRY/USDT:USDT", "LIT/USDT:USDT"]
for pair in pairs:
    try:
        sym = pair.replace(":USDT", "")
        ohlcv = exchange.fetch_ohlcv(sym, "5m", limit=200)
        df = pd.DataFrame(ohlcv, columns=["date", "open", "high", "low", "close", "volume"])
        df["date"] = pd.to_datetime(df["date"], unit="ms")
        
        df = s.populate_indicators(df, {"pair": pair})
        last = df.iloc[-1]
        print(json.dumps({
            "pair": pair,
            "close": float(last["close"]),
            "at_discount": int(last["at_discount"]),
            "rsi": float(last["rsi"]),
            "rsi_ok": int(last["rsi_ok"]),
            "entry_score": int(last["entry_score"]),
            "atr_explosive": int(last["atr_explosive"]),
            "dist_ema55_pct": float(last["dist_ema55_pct"]),
            "liq_ok": int(last["liq_ok"]),
            "real_flow_hostile": int(last["real_flow_hostile"]),
        }))
    except Exception as e:
        print(json.dumps({"pair": pair, "error": str(e)}))
