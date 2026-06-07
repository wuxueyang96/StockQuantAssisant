import numpy as np
import pandas as pd
import pytest

from app.schemas.backtest import BacktestConfig
from app.services.backtest_service import simulate_backtest


def _daily_frame() -> pd.DataFrame:
    idx = pd.bdate_range('2024-01-01', periods=4)
    return pd.DataFrame({
        'Open': [10.0, 20.0, 30.0, 40.0],
        'High': [10.0, 20.0, 30.0, 40.0],
        'Low': [10.0, 20.0, 30.0, 40.0],
        'Close': [10.0, 20.0, 30.0, 40.0],
        'Volume': [1000] * 4,
    }, index=idx)


def _decisions() -> pd.DataFrame:
    idx = pd.bdate_range('2024-01-01', periods=4)
    return pd.DataFrame({
        'final_target_position': [10.0, 0.0, 0.0, 0.0],
        'order_weight': [1.0, 1.0, 0.0, 0.0],
        'order_delta': [10.0, -10.0, 0.0, 0.0],
        'action': ['BUY', 'SELL', 'HOLD', 'HOLD'],
        'confidence_label': ['trend'] * 4,
        'trend_state': ['UP_STRONG', 'DOWN_STRONG', 'DOWN_STRONG', 'DOWN_STRONG'],
        'structure_adjustment': [0.0] * 4,
        'high9_active': [False] * 4,
        'low9_active': [False] * 4,
    }, index=idx)


def _make_5min(n_days: int = 120) -> pd.DataFrame:
    dates = pd.bdate_range('2024-01-01', periods=n_days)
    am = pd.timedelta_range(start='9:30:00', periods=24, freq='5min')
    pm = pd.timedelta_range(start='13:00:00', periods=24, freq='5min')
    idx = []
    for d in dates:
        for off in am:
            idx.append(d + off)
        for off in pm:
            idx.append(d + off)
    idx = pd.DatetimeIndex(idx)
    closes = np.linspace(20.0, 80.0, len(idx))
    return pd.DataFrame({
        'timestamp': idx,
        'open': closes,
        'high': closes * 1.01,
        'low': closes * 0.99,
        'close': closes,
        'volume': [100000] * len(idx),
    })


def test_simulate_backtest_executes_t_plus_1_open():
    result = simulate_backtest(
        _daily_frame(),
        _decisions(),
        BacktestConfig(initial_cash=1000.0, commission_rate=0.0, slippage_bps=0.0),
    )
    trades = result.trades
    assert len(trades) == 2
    assert trades[0].side == 'BUY'
    assert trades[0].date.startswith('2024-01-02')
    assert trades[0].signal_date.startswith('2024-01-01')
    assert trades[0].price == 20.0
    assert trades[0].quantity == 50.0
    assert trades[1].side == 'SELL'
    assert trades[1].date.startswith('2024-01-03')
    assert trades[1].price == 30.0
    assert result.equity_curve[-1].equity == 1500.0
    assert result.metrics.total_return == 0.5


def test_simulate_backtest_respects_slippage_and_commission():
    result = simulate_backtest(
        _daily_frame(),
        _decisions(),
        BacktestConfig(initial_cash=1000.0, commission_rate=0.001, slippage_bps=100.0),
    )
    buy = result.trades[0]
    sell = result.trades[1]
    assert buy.price == 20.2
    assert sell.price == 29.7
    assert buy.commission > 0
    assert sell.commission > 0
    assert result.metrics.total_return < 0.5


def test_simulate_backtest_does_not_rebalance_on_hold_signal():
    daily = pd.DataFrame({
        'Open': [10.0, 20.0, 40.0],
        'High': [10.0, 20.0, 40.0],
        'Low': [10.0, 20.0, 40.0],
        'Close': [10.0, 20.0, 40.0],
        'Volume': [1000] * 3,
    }, index=pd.bdate_range('2024-01-01', periods=3))
    decisions = pd.DataFrame({
        'final_target_position': [5.0, 5.0, 5.0],
        'order_weight': [0.5, 0.0, 0.0],
        'order_delta': [5.0, 0.0, 0.0],
        'action': ['BUY', 'HOLD', 'HOLD'],
        'confidence_label': ['trend'] * 3,
        'trend_state': ['RANGE'] * 3,
        'structure_adjustment': [0.0] * 3,
        'high9_active': [False] * 3,
        'low9_active': [False] * 3,
    }, index=daily.index)

    result = simulate_backtest(
        daily,
        decisions,
        BacktestConfig(initial_cash=1000.0, commission_rate=0.0, slippage_bps=0.0),
    )

    assert len(result.trades) == 1
    assert result.trades[0].side == 'BUY'
    assert result.trades[0].date.startswith('2024-01-02')


def test_metrics_include_fixed_same_average_position_benchmark():
    result = simulate_backtest(
        _daily_frame(),
        _decisions(),
        BacktestConfig(initial_cash=1000.0, commission_rate=0.0, slippage_bps=0.0),
    )

    assert hasattr(result.metrics, 'fixed_same_average_position_total_return')
    assert hasattr(result.metrics, 'timing_alpha_vs_fixed_same_position')
    assert isinstance(result.metrics.position_bucket_attribution, dict)
    assert '80-100%' in result.metrics.position_bucket_attribution


def test_simulate_backtest_accepts_tz_aware_index_with_date_range():
    daily = _daily_frame()
    daily.index = daily.index.tz_localize('Asia/Shanghai')
    decisions = _decisions()
    decisions.index = decisions.index.tz_localize('Asia/Shanghai')

    result = simulate_backtest(
        daily,
        decisions,
        BacktestConfig(
            initial_cash=1000.0,
            commission_rate=0.0,
            slippage_bps=0.0,
            start_date='2024-01-01',
            end_date='2024-01-04',
        ),
    )

    assert result.trades[0].date.startswith('2024-01-02')
    assert result.metrics.total_return == 0.5


def test_backtest_api_returns_metrics(client):
    from app.models.database import db_manager
    db_manager.insert_data('a', 'A_000001.SZ_5min', _make_5min())

    resp = client.post('/api/stock/backtest', json={
        'stock': '000001',
        'initial_cash': 100000,
        'commission_rate': 0,
        'slippage_bps': 0,
        'min_bars': 30,
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data['success'] is True
    result = data['results'][0]
    assert result['display_code'] == '000001.SZ'
    assert 'metrics' in result
    assert 'equity_curve' in result
    assert 'signals' in result
    assert len(result['equity_curve']) >= 2


def test_backtest_api_outputs_warmup_boundaries(client):
    from app.models.database import db_manager
    dates = pd.bdate_range('2024-01-01', periods=80)
    start_date = dates[50].strftime('%Y-%m-%d')
    expected_data_start = dates[30].strftime('%Y-%m-%d')
    db_manager.insert_data('a', 'A_000002.SZ_5min', _make_5min(80))

    resp = client.post('/api/stock/backtest', json={
        'stock': '000002',
        'start_date': start_date,
        'initial_cash': 100000,
        'commission_rate': 0,
        'slippage_bps': 0,
        'min_bars': 30,
        'warmup_bars': 20,
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    result = resp.get_json()['results'][0]
    assert result['data_start'].startswith(expected_data_start)
    assert result['backtest_start'].startswith(start_date)
    assert result['warmup_bars'] == 20
    assert result['available_warmup_bars'] == 20
    assert result['effective_signal_start'].startswith(start_date)


def test_backtest_api_missing_stock(client):
    resp = client.post('/api/stock/backtest', json={})
    assert resp.status_code == 400
    assert resp.get_json()['success'] is False
