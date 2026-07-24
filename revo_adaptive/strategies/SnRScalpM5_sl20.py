# Varian sweep SL: lebar SL = 2.0 x ATR. Sisanya identik dgn base.
from SnRScalpM5 import SnRScalpM5


class SnRScalpM5_sl20(SnRScalpM5):
    sl_atr_mult = 2.0
