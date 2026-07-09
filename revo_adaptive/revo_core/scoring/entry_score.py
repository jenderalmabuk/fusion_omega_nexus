from __future__ import annotations
import pandas as pd


def add_entry_score(df: pd.DataFrame, min_score: int = 8) -> pd.DataFrame:
    # Expected boolean/int columns are created by strategy. Missing columns become 0.
    def col(name):
        return df[name].astype(int) if name in df.columns else 0
    score = (
        col('at_discount') * 2 +
        col('rsi_ok') +
        col('cvd_ok') * 2 +
        col('oi_ok') +
        col('funding_ok') * 2 +
        col('pair_uptrend_pullback') +
        col('btc_ok') +
        col('vol_ok') -
        col('er_chop') -
        col('btc_dump') -
        col('atr_explosive') -
        col('funding_crowded') * 2
    )
    df['revo_entry_score'] = score.astype(int)
    df['revo_min_score'] = min_score
    return df
