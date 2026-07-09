from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from user_data.revo_alpha.pair_context.event_bus import SQLiteEventBus
from user_data.revo_alpha.pair_context.f3_flow_adapter import f3_records_to_flow_events


EXPECTED_TYPES = {
    "flow.market_cache_updated",
    "flow.oi_interpreted",
    "flow.cvd_updated",
    "flow.trap_warning_updated",
    "flow.coverage_gap_detected",
}


def sample_records() -> list[dict[str, object]]:
    return [
        {
            "pair": "BTC/USDT:USDT",
            "market": "bybit",
            "timeframe": "5m",
            "oi_1h_delta": 0.21,
            "oi_state": "TRAP_WARNING",
            "price_state": "UP",
            "oi_interpretation": "TRAP_WARNING",
            "market_regime": "RANGING",
            "trap_warning": True,
            "trap_reason": "SHORT_SQUEEZE_RISK",
            "flow_score": 41,
            "cvd": -2400.5,
            "cvd_5m_delta": -310.25,
            "taker_buy_sell_ratio": 0.72,
            "taker_buy_volume": 77000,
            "taker_sell_volume": 107000,
            "aggression": "SELL_DOMINANT",
            "aggression_delta": -0.22,
            "coverage_status": "LOW_CONFIDENCE_PARTIAL",
            "coverage_state": "CVD_GAP",
            "source_confidence": "LOW",
            "cvd_confidence": "LOW",
            "low_confidence_reason": "CVD_SOURCE_BRIDGE_PARTIAL",
            "source_name": "f3a_market_wide_flow_cache",
            "source_kind": "sqlite_cache",
            "source_priority": 2,
            "source_age_sec": 720,
            "schema_bridge": "F3_SCHEMA_BRIDGE_V1",
            "overlay_used": True,
            "bridge_source": "fallback_overlay",
            "fresh": False,
            "stale": True,
            "age_sec": 720,
            "max_age_sec": 300,
            "cache_age_sec": 720,
            "confidence": "LOW",
        }
    ]


def stored_event_types(bus: SQLiteEventBus) -> tuple[int, list[str]]:
    con = bus.connect()
    try:
        rows = con.execute(
            """
            select event_type, count(*) as n
            from pair_context_events
            group by event_type
            order by event_type
            """
        ).fetchall()
        return sum(int(row["n"]) for row in rows), [str(row["event_type"]) for row in rows]
    finally:
        con.close()


def main() -> None:
    events = f3_records_to_flow_events(
        sample_records(),
        producer="F3_EVENTBUS_SMOKE",
        cycle_id="F3_EVENTBUS_SMOKE_CYCLE",
        source_path="sample/f3_eventbus_smoke.json",
    )
    generated_types = sorted({event.event_type for event in events})
    missing_types = sorted(EXPECTED_TYPES - set(generated_types))
    assert events
    assert not missing_types, missing_types

    with tempfile.TemporaryDirectory(prefix="paircontext_f3_eventbus_") as tmp:
        db_path = Path(tmp) / "pair_context_store.sqlite"
        bus = SQLiteEventBus(db_path)
        inserted_count = bus.append_many(events)
        duplicate_inserted_count = bus.append_many(events)
        stored_count, stored_types = stored_event_types(bus)

        assert inserted_count == len(events)
        assert duplicate_inserted_count == 0
        assert stored_count == len(events)
        assert EXPECTED_TYPES.issubset(set(stored_types))

        summary = {
            "db_path": "<temp>/pair_context_store.sqlite",
            "duplicate_inserted_count": duplicate_inserted_count,
            "event_types": generated_types,
            "generated_count": len(events),
            "idempotency_ok": duplicate_inserted_count == 0 and stored_count == inserted_count,
            "inserted_count": inserted_count,
            "stored_count": stored_count,
            "stored_event_types": stored_types,
        }
        print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
