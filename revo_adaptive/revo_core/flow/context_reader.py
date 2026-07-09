from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from revo_core.common.schemas import FlowDirection, PairFlow


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except Exception:
        return None


def _num(rec: dict[str, Any], *names: str, default: float = 0.0) -> float:
    for name in names:
        if name in rec and rec[name] not in (None, ''):
            try:
                return float(rec[name])
            except Exception:
                pass
    return default


def _direction(value: str | None) -> FlowDirection:
    v = (value or 'NO_TRADE').upper()
    if v in FlowDirection.__members__:
        return FlowDirection[v]
    if v in [x.value for x in FlowDirection]:
        return FlowDirection(v)
    if 'LONG' in v:
        return FlowDirection.LONG_ONLY
    if 'SHORT' in v:
        return FlowDirection.SHORT_ONLY
    return FlowDirection.NO_TRADE


def normalize_flow_record(rec: dict[str, Any], max_age_sec: int = 120) -> PairFlow:
    pair = rec.get('pair') or rec.get('symbol') or ''
    ts = _parse_ts(rec.get('ts') or rec.get('timestamp'))
    now = datetime.now(timezone.utc)
    stale = True
    if ts is not None:
        stale = (now - ts.astimezone(timezone.utc)).total_seconds() > max_age_sec
    data_ready = bool(rec.get('data_ready', rec.get('flow_ready', False))) and str(rec.get('data_quality', 'OK')).upper() == 'OK'
    return PairFlow(
        pair=pair,
        flow_direction=_direction(rec.get('flow_direction') or rec.get('flow_authority')),
        cvd_delta_15m=_num(rec, 'cvd_delta_15m', 'cvd_delta'),
        cvd_zscore_15m=_num(rec, 'cvd_zscore_15m', 'cvd_zscore', 'cvd_z_15m', 'cvd_z'),
        oi_delta_pct_15m=_num(rec, 'oi_delta_pct_15m', 'oi_delta_15m_pct', 'open_interest_delta_15m_pct', 'oi15'),
        funding_rate=_num(rec, 'funding_rate'),
        funding_zscore=_num(rec, 'funding_zscore'),
        volume_zscore_15m=_num(rec, 'volume_zscore_15m', 'volume_zscore', 'volume_z_15m', 'volume_z'),
        data_ready=data_ready,
        data_stale=stale,
        source='real' if rec.get('cvd_source') else 'proxy',
    )


def load_flow_context(path: str | Path, max_age_sec: int = 120) -> Dict[str, PairFlow]:
    path = Path(path)
    data = json.loads(path.read_text())
    # support either {pair: rec} or {pairs: {pair: rec}}
    records = data.get('pairs', data) if isinstance(data, dict) else {}
    out: Dict[str, PairFlow] = {}
    for key, rec in records.items():
        if not isinstance(rec, dict):
            continue
        norm = normalize_flow_record(rec, max_age_sec=max_age_sec)
        if not norm.pair:
            norm.pair = key
        out[norm.pair] = norm
    return out
