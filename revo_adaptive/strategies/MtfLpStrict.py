# MtfLpStrict — "research build" Section 33 spec v1.3. MODEL TERPISAH dari base
# (bukan asumsi lebih baik): hanya lolos setup grade-A.
#   Entry Mode        = Strict Sweep (buang rejection)
#   Require H1 BOS    = On
#   Require M15 BOS   = On
#   Require FVG       = On
#   Min impulse body  = 0.80 ATR (dari 0.50)
#   Confirmation      = strict (close > high sweep / < low sweep)
#   Deadline          = 3 candle (dari 4)
#   Stop reference    = wider(trigger, zona)
# Params live-guard (retest/age/max-trades) tetap ditunda: ini feasibility edge-test.
from MtfLpCore import MtfLpCore


class MtfLpStrict(MtfLpCore):
    require_h1_bos = True
    require_m15_bos = True
    require_fvg = True
    strict_sweep = True
    strict_confirm = True
    wider_stop = True

    min_impulse_atr = 0.80
    conf_deadline = 3
