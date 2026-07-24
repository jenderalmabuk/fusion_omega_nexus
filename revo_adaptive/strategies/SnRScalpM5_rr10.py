# Varian sweep RR: risk:reward = 1.0 (TP lebih dekat). SL tetap base 1.0xATR.
from SnRScalpM5 import SnRScalpM5


class SnRScalpM5_rr10(SnRScalpM5):
    rr = 1.0
