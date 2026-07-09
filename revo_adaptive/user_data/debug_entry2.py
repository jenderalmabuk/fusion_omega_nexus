import ccxt, pandas as pd, numpy as np, sys, json
sys.path.insert(0, "/freqtrade/user_data/strategies")
from RevoAdaptiveStrategy import RevoAdaptiveStrategy

exchange = ccxt.bybit({"options": {"defaultType": "spot"}})
s = RevoAdaptiveStrategy({})

pairs = ["BTC/USDT:USDT", "ETH/USDT:USDT", "VANRY/USDT:USDT", "LIT/USDT:USDT"]
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
            "entry_score": int(last["entry_score"]),
            "at_discount": int(last["at_discount"]),
            "rsi_ok": int(last["rsi_ok"]),
            "cvd_ok": int(last["cvd_ok"]),
            "oi_ok": int(last["oi_ok"]),
            "funding_ok": int(last["funding_ok"]),
            "pair_uptrend_pullback": int(last["pair_uptrend_pullback"]),
            "btc_ok": int(last["btc_ok"]),
            "vol_ok": int(last["vol_ok"]),
            "er_chop": int(last["er_chop"]),
            "btc_dump": int(last["btc_dump"]),
            "atr_explosive": int(last["atr_explosive"]),
            "funding_crowded": int(last["funding_crowded"]),
        }))
    except Exception as e:
        print(json.dumps({"pair": pair, "error": str(e)}))
