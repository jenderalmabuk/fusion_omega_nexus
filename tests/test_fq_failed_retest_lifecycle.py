import tempfile
from pathlib import Path

import pandas as pd
import numpy as np
import fusion_quantum.paper_runner as runner


def test_zone_key_normalizes_timestamp_precision():
    micro = {'t': '2026-07-14T00:00:00.000000', 'zlow': 1.0, 'zhigh': 2.0}
    nano = {'t': '2026-07-14T00:00:00.000000000', 'zlow': 1.0, 'zhigh': 2.0}

    assert runner.zone_key(micro, 'BULL') == runner.zone_key(nano, 'BULL')


def market():
    t = pd.date_range('2026-01-01', periods=20, freq='15min', tz='UTC')
    l = pd.DataFrame({'open_time': t, 'open': 100., 'high': 101., 'low': 99., 'close': 100., 'volume': 1.})
    h = l.copy()
    return h, l


def test_first_touch_waits_for_full_confirmation_window(monkeypatch):
    h, l = market()
    zone = {'t': 1, 'zlow': 98., 'zhigh': 102.}
    monkeypatch.setattr(runner, '_valid_obs', lambda df, side: [zone] if side == 'BULL' else [])
    monkeypatch.setattr(runner, '_imbalances', lambda df, side: [{'ce': 2, 't': 3, 'leg_low': 99., 'leg_high': 101.}] if side == 'BULL' else [])
    monkeypatch.setattr(runner, '_atr', lambda df: np.ones(len(df)))
    monkeypatch.setattr(runner, 'mss_confirm', lambda *a, **k: None)

    result = runner.lifecycle_scan('TESTUSDT', h, l.iloc[:10])

    assert result.consumed[0]['outcome'] == 'awaiting_confirmation'
    assert result.setups == []


def test_awaiting_status_does_not_block_later_confirmation():
    state = runner.State(Path(tempfile.mkdtemp()) / 'state.json')
    setup = {'zone_key': ['BULL', '2026-01-01T00:00:00', 1.0, 2.0]}
    state.data['setups']['z1'] = {'status': 'awaiting_confirmation', 'setup': setup}

    assert not runner.lifecycle_claimed(state, setup)


def test_failed_status_blocks_later_confirmation():
    state = runner.State(Path(tempfile.mkdtemp()) / 'state.json')
    setup = {'zone_key': ['BULL', '2026-01-01T00:00:00', 1.0, 2.0]}
    state.data['setups']['z1'] = {'status': 'failed_confirmation', 'setup': setup}

    assert runner.lifecycle_claimed(state, setup)


def test_successful_first_retest_emits_setup_and_consumes_zone(monkeypatch):
    h, l = market()
    zone = {'t': 1, 'zlow': 98., 'zhigh': 102.}
    monkeypatch.setattr(runner, '_valid_obs', lambda df, side: [zone] if side == 'BULL' else [])
    monkeypatch.setattr(runner, '_imbalances', lambda df, side: [{'ce': 2, 't': 3, 'leg_low': 99., 'leg_high': 101.}] if side == 'BULL' else [])
    monkeypatch.setattr(runner, '_atr', lambda df: np.ones(len(df)))
    monkeypatch.setattr(runner, 'mss_confirm', lambda *a, **k: {'i': 13, 'sweep': 98., 'disp': 103., 'side': 'BULL'})

    result = runner.lifecycle_scan('TESTUSDT', h, l)

    assert len(result.setups) == 1
    assert result.consumed[0]['outcome'] == 'confirmed'
    assert result.setups[0]['zone_key'] == result.consumed[0]['zone_key']
