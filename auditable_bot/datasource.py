from __future__ import annotations

from pathlib import Path

from .models import MarketFrame


def freqtrade_pair_to_file_stem(pair: str) -> str:
    return pair.replace("/", "_").replace(":", "_")


def market_frame_from_row(pair: str, row: dict, data_age_sec: int = 0) -> MarketFrame:
    close = float(row["close"])
    volume = float(row.get("volume", 0.0))
    ema55 = float(row.get("ema55", close))
    return MarketFrame(
        pair,
        close,
        volume,
        float(row.get("rsi", 50.0)),
        ema55,
        float(row.get("atr_pct", 0.0)),
        float(row.get("cvd_z", 0.0)),
        float(row.get("oi_delta_pct", 0.0)),
        float(row.get("funding_z", 0.0)),
        str(row.get("flow", "neutral")),
        str(row.get("btc_mode", "neutral")),
        str(row.get("btc_coupling", "coupled")),
        data_age_sec,
    )


def latest_from_feather(pair: str, datadir: Path) -> MarketFrame:
    import pandas as pd  # optional: available inside freqtrade container

    path = Path(datadir) / "bybit" / "futures" / f"{freqtrade_pair_to_file_stem(pair)}-5m-futures.feather"
    df = pd.read_feather(path)
    if df.empty:
        raise ValueError(f"no candles: {path}")
    close = df["close"].astype(float)
    high = df.get("high", close).astype(float)
    low = df.get("low", close).astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=1).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=1).mean()
    rsi = 100 - (100 / (1 + (gain / loss.replace(0, pd.NA))))
    prev = close.shift(1)
    tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr_pct = tr.rolling(14, min_periods=1).mean() / close * 100
    row = df.tail(1).to_dict("records")[0]
    row["ema55"] = float(close.ewm(span=55, adjust=False).mean().iloc[-1])
    row["rsi"] = float(rsi.fillna(50).iloc[-1])
    row["atr_pct"] = float(atr_pct.fillna(0).iloc[-1])
    return market_frame_from_row(pair, row)
