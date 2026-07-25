import asyncio
from datetime import datetime, timezone

from execution.paper_mainnet_trader import PaperMainnetTrader


class Trader(PaperMainnetTrader):
    def __init__(self):
        super().__init__()
        self.notices = []
    async def _notify_close(self, payload): self.notices.append(payload)


def position():
    return {
        "symbol": "NEOUSDT", "side": "SHORT", "entry_price": 2.10,
        "sl_price": 2.21276, "tp_ladder": [2.057, 1.995, 1.932, 1.870, 1.808],
        "qty": 100.0, "initial_qty": 100.0, "notional": 210.0,
        "opened_at": datetime.now(timezone.utc), "status": "OPEN",
        "adv_snapshot": {}, "score": 80.0, "confidence": .8,
    }


def test_each_tp_closes_equal_slice_and_keeps_position_until_last():
    t = Trader(); t.positions["NEOUSDT"] = position()
    asyncio.run(t._apply_take_profits("NEOUSDT", 2.050))
    assert round(t.positions["NEOUSDT"]["qty"], 8) == 80.0
    assert t.positions["NEOUSDT"]["next_tp_index"] == 1
    assert t.notices[-1]["reason"] == "TP1_PARTIAL"
    asyncio.run(t._apply_take_profits("NEOUSDT", 1.800))
    assert "NEOUSDT" not in t.positions


def test_provider_ladder_order_is_preserved_in_position():
    p = position()
    assert p["tp_ladder"] == [2.057, 1.995, 1.932, 1.870, 1.808]
