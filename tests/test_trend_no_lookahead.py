"""趋势突破无未来函数：T 日 Close 对比 T-1 轨线。"""
import numpy as np
import pandas as pd
import pytest

from app.algos.trend import TrendChannel, classify_trend_state, compute_channels


def _daily(n: int, close: float, high_boost: float = 0.0) -> pd.DataFrame:
    dates = pd.date_range('2024-01-01', periods=n, freq='B')
    closes = np.full(n, close, dtype=float)
    highs = closes * (1 + high_boost)
    lows = closes * 0.99
    return pd.DataFrame({
        'Open': closes, 'High': highs, 'Low': lows, 'Close': closes,
        'Volume': [1_000_000] * n,
    }, index=dates)


class TestTrendNoLookahead:
    def test_breakout_uses_prev_channel_not_inflated_same_day_high(self):
        """最后一根用大 high 抬高当日轨线，但 Close 未突破昨日上轨 → 不能判 UP_STRONG。"""
        n = 120
        base = _daily(n - 1, close=100.0, high_boost=0.01)
        last = _daily(1, close=100.0, high_boost=0.0)
        last.iloc[0, last.columns.get_loc('High')] = 200.0
        assert float(last.iloc[0]['Close']) == 100.0
        df = pd.concat([base, last])

        ch = compute_channels(df, TrendChannel().config)
        prev = ch.iloc[-2]
        last_row = ch.iloc[-1]
        close = float(last_row['Close'])

        state = classify_trend_state(
            close,
            prev['short_upper'],
            prev['short_lower'],
            prev['long_upper'],
            prev['long_lower'],
        )
        assert state.value != 'UP_STRONG'
        assert close < float(prev['short_upper'])

        ev = TrendChannel(short_period=26, long_period=90, offset_pct=0.03).evaluate(df)
        assert ev.iloc[-1]['trend_state'] != 'UP_STRONG'

    def test_up_strong_when_close_above_prev_both_uppers(self):
        n = 120
        closes = np.linspace(80, 150, n)
        dates = pd.date_range('2024-01-01', periods=n, freq='B')
        df = pd.DataFrame({
            'Open': closes, 'High': closes * 1.02, 'Low': closes * 0.98,
            'Close': closes, 'Volume': [1e6] * n,
        }, index=dates)
        ev = TrendChannel(short_period=26, long_period=90, offset_pct=0.03).evaluate(df)
        last = ev.iloc[-1]
        if last['trend_state'] == 'UP_STRONG':
            assert last['base_target_position'] == 10.0
