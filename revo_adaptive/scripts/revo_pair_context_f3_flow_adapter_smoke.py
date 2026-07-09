from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from user_data.revo_alpha.pair_context.f3_flow_adapter import f3_record_to_flow_events


def main() -> None:
    record = {
        "pair": "BTC/USDT:USDT",
        "market": "bybit",
        "timeframe": "5m",
        "oi_1h_delta": 0.12,
        "funding_rate": 0.0001,
        "flow_score": 72,
        "oi_state": "UP",
        "price_state": "UP",
        "oi_interpretation": "OI_EXPANSION_PRICE_UP",
        "state": "LONG_FLOW",
        "market_regime": "TRENDING",
        "trap": False,
        "trap_reason": "TRAP_RISK_LOW",
        "bias": "LONG_FLOW",
        "side": "LONG",
        "coverage_ok": False,
        "missing_fields": ["cvd"],
        "confidence": "HIGH",
    }

    events = f3_record_to_flow_events(
        record,
        producer="F3_MIGRATION_ADAPTER_SMOKE",
        cycle_id="SMOKE_CYCLE",
        source_path="sample/f3_record.json",
    )

    assert events
    event_types = {event.event_type for event in events}

    assert "flow.market_cache_updated" in event_types
    assert "flow.oi_interpreted" in event_types
    assert "flow.funding_updated" in event_types
    assert "flow.score_updated" in event_types
    assert "flow.coverage_gap_detected" in event_types

    oi_event = next(event for event in events if event.event_type == "flow.oi_interpreted")
    interpretation = oi_event.payload["interpretation"]
    assert interpretation["oi_state"] == "UP"
    assert interpretation["price_state"] == "UP"
    assert interpretation["oi_price_interpretation"] == "OI_EXPANSION_PRICE_UP"
    assert interpretation["flow_state"] == "LONG_FLOW"
    assert interpretation["regime"] == "TRENDING"
    assert interpretation["trap_warning"] is False
    assert interpretation["trap_reason"] == "TRAP_RISK_LOW"
    assert interpretation["flow_bias"] == "LONG_FLOW"
    assert interpretation["direction"] == "LONG"
    assert interpretation["confidence"] == "HIGH"

    trap_record = {
        "pair": "ETH/USDT:USDT",
        "market": "bybit",
        "timeframe": "15m",
        "oi_15m_delta": -0.4,
        "price_state": "UP",
        "state": "TRAP_WARNING",
        "oi_interpretation": "TRAP_WARNING",
        "trap_warning": True,
        "trap_reason": "SHORT_SQUEEZE_RISK",
        "confidence": "MEDIUM",
    }
    trap_events = f3_record_to_flow_events(
        trap_record,
        producer="F3_MIGRATION_ADAPTER_SMOKE",
        cycle_id="SMOKE_TRAP_CYCLE",
        source_path="sample/f3_trap_record.json",
    )
    trap_types = {event.event_type for event in trap_events}
    assert "flow.oi_interpreted" in trap_types
    assert "flow.trap_warning_updated" in trap_types

    trap_event = next(event for event in trap_events if event.event_type == "flow.trap_warning_updated")
    assert trap_event.payload["market"] == "bybit"
    assert trap_event.payload["timeframe"] == "15m"
    assert trap_event.payload["interpretation"]["flow_state"] == "TRAP_WARNING"
    assert trap_event.payload["interpretation"]["oi_price_interpretation"] == "TRAP_WARNING"
    assert trap_event.payload["reason"] == "SHORT_SQUEEZE_RISK"
    assert trap_event.payload["confidence"] == "MEDIUM"
    assert trap_event.payload["source_family"] == "F3"

    cvd_record = {
        "pair": "SOL/USDT:USDT",
        "market": "bybit",
        "timeframe": "5m",
        "cvd": 1250.5,
        "cvd_5m_delta": 320.25,
        "taker_buy_sell_ratio": 1.42,
        "taker_buy_volume": 120000,
        "taker_sell_volume": 85000,
        "aggression": "BUY_DOMINANT",
        "aggression_delta": 0.18,
        "fresh": True,
        "cvd_ok": True,
        "confidence": "HIGH",
    }
    cvd_events = f3_record_to_flow_events(
        cvd_record,
        producer="F3_MIGRATION_ADAPTER_SMOKE",
        cycle_id="SMOKE_CVD_CYCLE",
        source_path="sample/f3_cvd_record.json",
    )
    cvd_types = {event.event_type for event in cvd_events}
    assert "flow.market_cache_updated" in cvd_types
    assert "flow.cvd_updated" in cvd_types

    cvd_event = next(event for event in cvd_events if event.event_type == "flow.cvd_updated")
    assert cvd_event.payload["market"] == "bybit"
    assert cvd_event.payload["timeframe"] == "5m"
    assert cvd_event.payload["metrics"] == {
        "cvd": 1250.5,
        "cvd_5m_delta": 320.25,
        "taker_buy_sell_ratio": 1.42,
        "taker_buy_volume": 120000,
        "taker_sell_volume": 85000,
        "aggression": "BUY_DOMINANT",
        "aggression_delta": 0.18,
    }
    assert cvd_event.payload["health"]["fresh"] is True
    assert cvd_event.payload["coverage"]["cvd_ok"] is True
    assert cvd_event.payload["confidence"] == "HIGH"
    assert cvd_event.payload["source_family"] == "F3"

    coverage_record = {
        "pair": "XRP/USDT:USDT",
        "market": "bybit",
        "timeframe": "5m",
        "coverage_status": "LOW_CONFIDENCE_PARTIAL",
        "coverage_state": "CVD_GAP",
        "source_confidence": "LOW",
        "cvd_confidence": "LOW",
        "low_confidence_reason": "CVD_SOURCE_BRIDGE_PARTIAL",
        "stale_reason": "SOURCE_AGE_TOO_OLD",
        "source_name": "f3a_market_wide_flow_cache",
        "source_kind": "sqlite_cache",
        "source_priority": 2,
        "source_age_sec": 720,
        "source_mtime": "2026-05-18T00:00:00+00:00",
        "schema_bridge": "F3_SCHEMA_BRIDGE_V1",
        "overlay_used": True,
        "bridge_source": "fallback_overlay",
        "fresh": False,
        "stale": True,
        "age_sec": 720,
        "max_age_sec": 300,
        "last_update_ts": "2026-05-18T00:00:00+00:00",
        "updated_at": "2026-05-18T00:00:00+00:00",
        "cache_age_sec": 720,
        "confidence": "LOW",
    }
    coverage_events = f3_record_to_flow_events(
        coverage_record,
        producer="F3_MIGRATION_ADAPTER_SMOKE",
        cycle_id="SMOKE_COVERAGE_CYCLE",
        source_path="sample/f3_coverage_record.json",
    )
    coverage_types = {event.event_type for event in coverage_events}
    assert "flow.market_cache_updated" in coverage_types
    assert "flow.source_health_updated" in coverage_types
    assert "flow.coverage_gap_detected" in coverage_types

    coverage_event = next(event for event in coverage_events if event.event_type == "flow.coverage_gap_detected")
    assert coverage_event.payload["coverage"]["coverage_status"] == "LOW_CONFIDENCE_PARTIAL"
    assert coverage_event.payload["coverage"]["coverage_state"] == "CVD_GAP"
    assert coverage_event.payload["coverage"]["source_confidence"] == "LOW"
    assert coverage_event.payload["coverage"]["cvd_confidence"] == "LOW"
    assert coverage_event.payload["coverage"]["schema_bridge"] == "F3_SCHEMA_BRIDGE_V1"
    assert coverage_event.payload["coverage"]["overlay_used"] is True
    assert coverage_event.payload["health"]["stale"] is True
    assert coverage_event.payload["health"]["cache_age_sec"] == 720
    assert coverage_event.payload["reason"] == "CVD_SOURCE_BRIDGE_PARTIAL"
    assert coverage_event.payload["confidence"] == "LOW"
    assert coverage_event.payload["source_family"] == "F3"

    print("events", len(events), sorted(event_types))
    print("trap_events", len(trap_events), sorted(trap_types))
    print("cvd_events", len(cvd_events), sorted(cvd_types))
    print("coverage_events", len(coverage_events), sorted(coverage_types))


if __name__ == "__main__":
    main()
