"""
Offline verification for the Signal Copy validation fix.

Proves:
  A. A high-confluence signal whose price has DRIFTED off the entry zone is no
     longer hard-rejected (the BNBUSDT SHORT score=72.5 case observed live).
  B. Genuinely bad signals (no price, SL absurdly wide) are STILL rejected.
  C. Off-zone is scored as a routing input (partial+passed), not a 0/REJECT.

Runs BOTH strict and legacy validation paths so we see the before/after.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_copy.signal_schema import ParsedSignal, SignalSide, SignalSource
from signal_copy.validation_engine import validate_signal, Verdict


def mk(symbol, side, lo, hi, sl, tps, entry_type="market"):
    return ParsedSignal(
        symbol=symbol, side=side, entry_low=lo, entry_high=hi,
        stop_loss=sl, take_profits=tps, leverage=10, entry_type=entry_type,
        source=SignalSource.TELEGRAM, source_name="test",
    )


# --- Real signal captured live: BNBUSDT SHORT, price drifted BELOW the zone ---
# entry 580.31-597.719 | SL 616.869 | live price observed 568.66 (3.5% below mid)
bnb = mk("BNBUSDT", SignalSide.SHORT, 580.31, 597.719, 616.869,
         [576.911, 564.661, 552.411, 540.161])
bnb_metrics = {
    "data_valid": True, "price": 568.66,
    "oi_change_15m_pct": 0.8, "oi_change_1h_pct": 1.2,
    "cvd_zscore": -1.5, "imbalance": -0.3, "funding_rate": 0.0002,
    "rsi": 42.0, "price_change_15m_pct": -0.4, "regime_label": "TRENDING",
    "atr_pct": 1.2,
    "mtf_alignment": {"score": 85, "entry_trend": "DOWN", "tf4h_trend": "SHORT", "d1_trend": "SHORT"},
    "tradingview": {"score": 87, "details": ["RSI bearish", "EMA down"]},
}

# --- Bad signal: no live price data (should ALWAYS reject) ---
nodata = mk("XYZUSDT", SignalSide.LONG, 100, 102, 98, [105, 110])
nodata_metrics = {"data_valid": False, "price": 0.0}

# --- Bad signal: SL absurdly wide (>20%, should reject) ---
wide = mk("ABCUSDT", SignalSide.LONG, 100, 102, 70, [110, 120])  # SL ~30% away
wide_metrics = {"data_valid": True, "price": 101.0, "rsi": 50, "regime_label": "RANGING"}

# --- Good signal, price INSIDE zone (control: should be VALID both paths) ---
inzone = mk("GOODUSDT", SignalSide.LONG, 100, 102, 98, [105, 110, 115])
inzone_metrics = {
    "data_valid": True, "price": 101.0,
    "oi_change_15m_pct": 1.0, "oi_change_1h_pct": 1.5, "cvd_zscore": 1.8,
    "funding_rate": 0.0001, "rsi": 45, "price_change_15m_pct": 0.3,
    "regime_label": "TRENDING", "atr_pct": 1.0,
    "mtf_alignment": {"score": 80, "tf4h_trend": "LONG", "d1_trend": "LONG"},
    "tradingview": {"score": 75, "details": ["bullish"]},
}

cases = [
    ("BNB SHORT off-zone (LIVE case, was rejected)", bnb, bnb_metrics, "should NOT reject"),
    ("No price data", nodata, nodata_metrics, "MUST reject"),
    ("SL 30% wide", wide, wide_metrics, "MUST reject"),
    ("In-zone good signal (control)", inzone, inzone_metrics, "should be VALID"),
]

for mode_label, legacy in [("STRICT", "0"), ("LEGACY", "1")]:
    os.environ["SIGNAL_COPY_LEGACY_VALIDATION"] = legacy
    print(f"\n{'='*70}\n  MODE: {mode_label}  (SIGNAL_COPY_LEGACY_VALIDATION={legacy})\n{'='*70}")
    for name, sig, metrics, expect in cases:
        r = validate_signal(sig, metrics)
        price_factor = next((f for f in r.factors if "Price" in f.name), None)
        pf = f"{price_factor.score:.0f}/{price_factor.max_score:.0f} passed={price_factor.passed}" if price_factor else "n/a"
        hb = "; ".join(r.hard_blocks) if r.hard_blocks else "-"
        print(f"\n  {name}")
        print(f"    verdict={r.verdict.value:7s} score={r.score:.1f}  [expect: {expect}]")
        print(f"    price_factor={pf}")
        print(f"    hard_blocks={hb}")
