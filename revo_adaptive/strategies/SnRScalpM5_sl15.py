# Varian sweep SL: lebar SL = 1.5 x ATR. Sisanya identik dgn base.
from SnRScalpM5 import SnRScalpM5


class SnRScalpM5_sl15(SnRScalpM5):
    sl_atr_mult = 1.5
