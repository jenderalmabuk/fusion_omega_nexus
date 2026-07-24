# Varian sweep RR: risk:reward = 1.5 (TP lebih dekat). SL tetap base 1.0xATR.
from SnRScalpM5 import SnRScalpM5


class SnRScalpM5_rr15(SnRScalpM5):
    rr = 1.5
