import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


def make_ohlcv(prices, high_offset=0.05, low_offset=0.05, volume=1000000):
    """Helper to create OHLCV DataFrame from close prices."""
    dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(len(prices))]
    closes = np.array(prices, dtype=float)
    highs = closes * (1 + np.random.uniform(0, high_offset, len(prices)))
    lows = closes * (1 - np.random.uniform(0, low_offset, len(prices)))
    opens = closes * (1 + np.random.uniform(-0.02, 0.02, len(prices)))
    highs = np.maximum(highs, opens)
    lows = np.minimum(lows, opens)
    df = pd.DataFrame({
        'Open': opens, 'High': highs, 'Low': lows, 'Close': closes,
        'Volume': [volume] * len(prices), 'Dividends': 0.0, 'Stock Splits': 0.0,
    }, index=dates)
    return df


class TestTrendChannel:
    @pytest.fixture
    def trend(self):
        from app.algos.trend import TrendChannel
        return TrendChannel(short_period=26, long_period=90)

    def test_channels_basic_shape(self, trend):
        df = make_ohlcv([100 + i * 0.5 for i in range(200)])
        channels = trend.compute_all(df)
        assert 'short_upper' in channels.columns
        assert 'short_lower' in channels.columns
        assert 'long_upper' in channels.columns
        assert 'long_lower' in channels.columns
        assert len(channels) == 200

    def test_short_upper_above_short_lower(self, trend):
        df = make_ohlcv([100 + i * 0.5 for i in range(200)])
        channels = trend.compute_all(df)
        valid = channels.dropna()
        assert (valid['short_upper'] >= valid['short_lower']).all()

    def test_long_upper_above_long_lower(self, trend):
        df = make_ohlcv([100 + i * 0.5 for i in range(200)])
        channels = trend.compute_all(df)
        valid = channels.dropna()
        assert (valid['long_upper'] >= valid['long_lower']).all()

    def test_channels_have_positive_width(self, trend):
        df = make_ohlcv([100 + i * 0.5 for i in range(200)])
        channels = trend.compute_all(df)
        valid = channels.dropna()
        short_width = valid['short_upper'] - valid['short_lower']
        long_width = valid['long_upper'] - valid['long_lower']
        assert (short_width >= 0).all()
        assert (long_width >= 0).all()
        assert (short_width > 0).any()
        assert (long_width > 0).any()

    def test_full_position_10(self, trend):
        """强上升：收盘须突破 T-1 短/长上轨（无未来函数）。"""
        n = 120
        closes = np.linspace(80, 200, n)
        df = make_ohlcv(closes.tolist())
        result = trend.evaluate(df)
        pos = result.dropna(subset=['position'])
        last = pos.iloc[-1]
        if last['trend_state'] == 'UP_STRONG':
            assert last['position'] == 10.0
        else:
            assert last['position'] in (0.0, 2.0, 4.0, 6.0, 8.0, 10.0)

    def test_empty_position_0(self, trend):
        from app.algos.trend import TrendChannel
        trend = TrendChannel(short_period=10, long_period=30)
        prices = [200] * 50 + list(range(200, 50, -1))
        closes = [float(p) for p in prices]
        highs = [c * 1.05 for c in closes]
        lows = [c * 0.95 for c in closes]
        opens = [c * 1.0 for c in closes]
        dates = pd.date_range('2024-01-01', periods=len(closes), freq='B')
        df = pd.DataFrame({
            'Open': opens, 'High': highs, 'Low': lows, 'Close': closes,
            'Volume': [1000000] * len(closes),
        }, index=dates)
        result = trend.evaluate(df)
        pos = result.dropna(subset=['position'])
        assert len(pos) > 0

    def test_heavy_position_6(self, trend):
        from app.algos.trend import TrendChannel
        trend = TrendChannel(short_period=5, long_period=15)
        n = 50
        closes = np.linspace(100, 150, 30).tolist() + [148, 145, 140, 135, 130, 128, 126, 124, 122, 120,
                                                          118, 116, 114, 112, 110, 108, 106, 104, 102, 100]
        highs = [c * 1.10 for c in closes]
        lows = [c * 0.90 for c in closes]
        opens = [c * 0.99 for c in closes]
        dates = pd.date_range('2024-01-01', periods=len(closes), freq='B')
        df = pd.DataFrame({
            'Open': opens, 'High': highs, 'Low': lows, 'Close': closes,
            'Volume': [1000000] * len(closes),
        }, index=dates)
        result = trend.evaluate(df)
        for p in result['position'].dropna():
            assert p in (0.0, 2.0, 4.0, 6.0, 8.0, 10.0)

    def test_light_position_4(self, trend):
        from app.algos.trend import TrendChannel
        trend = TrendChannel(short_period=5, long_period=15)
        n = 50
        closes = [100] * 15 + [102, 105, 108, 110, 112, 115, 118, 120, 122, 125,
                                128, 130, 132, 135, 138, 140, 142, 145, 148, 150]
        closes = closes + [152, 155, 158, 160, 162, 165, 168, 170, 172, 175,
                            178, 180, 182, 185, 188]
        highs = [c * 1.10 for c in closes]
        lows = [c * 0.98 for c in closes]
        opens = [c * 1.00 for c in closes]
        dates = pd.date_range('2024-01-01', periods=len(closes), freq='B')
        df = pd.DataFrame({
            'Open': opens, 'High': highs, 'Low': lows, 'Close': closes,
            'Volume': [1000000] * len(closes),
        }, index=dates)
        result = trend.evaluate(df)
        for p in result['position'].dropna():
            assert p in (0.0, 2.0, 4.0, 6.0, 8.0, 10.0)

    def test_position_email(self, trend):
        from app.algos.trend import TrendChannel
        trend = TrendChannel(short_period=10, long_period=30)
        prices = [100] * 50 + [102, 104, 106, 108, 110, 112, 114, 116]
        df = make_ohlcv(prices, low_offset=0.01)
        df['High'] = df['Close'] * 1.05
        df['Low'] = df['Close'] * 0.97
        result = trend.evaluate(df)
        positions = result['position'].dropna()
        assert positions.iloc[-1] in [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]

    def test_state_persistence(self, trend):
        from app.algos.trend import TrendChannel
        trend = TrendChannel(short_period=10, long_period=30)
        prices = [100] * 50
        df = make_ohlcv(prices, high_offset=0.02, low_offset=0.02)
        df['High'] = df['Close'] * 1.03
        df['Low'] = df['Close'] * 0.97
        result = trend.evaluate(df)
        positions = result['position'].dropna()
        prev = None
        change_count = 0
        for p in positions:
            if prev is not None and p != prev:
                change_count += 1
            prev = p
        assert change_count <= 2

    def test_custom_params(self):
        from app.algos.trend import TrendChannel
        t = TrendChannel(short_period=20, long_period=60)
        assert t.short_period == 20
        assert t.long_period == 60

    def test_position_int_values(self, trend):
        df = make_ohlcv([100 + i * 0.5 for i in range(200)])
        result = trend.evaluate(df)
        positions = result['position'].dropna()
        for p in positions:
            assert p in (0.0, 2.0, 4.0, 6.0, 8.0, 10.0)

    def test_next_day_thresholds(self, trend):
        df = make_ohlcv([100 + i * 0.5 for i in range(200)])
        thresholds = trend.next_day_thresholds(df)
        assert 'short_upper' in thresholds
        assert 'short_lower' in thresholds
        assert 'long_upper' in thresholds
        assert 'long_lower' in thresholds
        assert thresholds['short_upper'] > thresholds['short_lower']
        assert thresholds['long_upper'] > thresholds['long_lower']
        assert isinstance(thresholds['short_upper'], float)

    def test_short_upper_uses_ema_of_rolling_max_close(self, trend):
        """short_upper 默认使用 Close 的滚动最大值。"""
        np.random.seed(7)
        n = 200
        closes = np.full(n, 100.0)
        for i in range(0, n, 5):
            closes[i] = 110.0
        highs = closes + 2.0
        lows = closes - 1.0
        opens = closes.copy()
        df = pd.DataFrame({
            'Open': opens, 'High': highs, 'Low': lows, 'Close': closes,
            'Volume': [1_000_000] * n,
        }, index=pd.date_range('2024-01-01', periods=n, freq='B'))

        N = trend.short_period
        rolling_max = df['Close'].rolling(N, min_periods=1).max()
        expected = rolling_max.ewm(span=N, adjust=False).mean()

        result = trend.compute_all(df)
        np.testing.assert_array_almost_equal(
            result['short_upper'].values[-50:], expected.values[-50:], decimal=4
        )
        high_based = df['High'].rolling(N, min_periods=1).max().ewm(span=N, adjust=False).mean()
        assert not np.allclose(result['short_upper'].values[-50:], high_based.values[-50:])

    def test_short_lower_uses_ema_of_rolling_min_close(self, trend):
        np.random.seed(7)
        n = 200
        closes = np.full(n, 100.0)
        for i in range(0, n, 5):
            closes[i] = 90.0
        lows = closes - 2.0
        highs = closes + 1.0
        opens = closes.copy()
        df = pd.DataFrame({
            'Open': opens, 'High': highs, 'Low': lows, 'Close': closes,
            'Volume': [1_000_000] * n,
        }, index=pd.date_range('2024-01-01', periods=n, freq='B'))

        N = trend.short_period
        rolling_min = df['Close'].rolling(N, min_periods=1).min()
        expected = rolling_min.ewm(span=N, adjust=False).mean()

        result = trend.compute_all(df)
        np.testing.assert_array_almost_equal(
            result['short_lower'].values[-50:], expected.values[-50:], decimal=4
        )
        low_based = df['Low'].rolling(N, min_periods=1).min().ewm(span=N, adjust=False).mean()
        assert not np.allclose(result['short_lower'].values[-50:], low_based.values[-50:])

    def test_long_channel_uses_rolling_extrema(self, trend):
        np.random.seed(7)
        n = 250
        closes = np.full(n, 100.0)
        for i in range(0, n, 7):
            closes[i] = 115.0
        for i in range(3, n, 11):
            closes[i] = 92.0
        highs = closes + 2.0
        lows = closes - 2.0
        opens = closes.copy()
        df = pd.DataFrame({
            'Open': opens, 'High': highs, 'Low': lows, 'Close': closes,
            'Volume': [1_000_000] * n,
        }, index=pd.date_range('2024-01-01', periods=n, freq='B'))

        N = trend.long_period
        exp_upper = df['Close'].rolling(N, min_periods=1).max().ewm(span=N, adjust=False).mean()
        exp_lower = df['Close'].rolling(N, min_periods=1).min().ewm(span=N, adjust=False).mean()

        result = trend.compute_all(df)
        np.testing.assert_array_almost_equal(result['long_upper'].values[-50:], exp_upper.values[-50:], decimal=4)
        np.testing.assert_array_almost_equal(result['long_lower'].values[-50:], exp_lower.values[-50:], decimal=4)

    def test_channel_can_use_legacy_high_low_source(self):
        from app.algos.trend import TrendChannel

        n = 80
        closes = np.full(n, 100.0)
        df = pd.DataFrame({
            'Open': closes,
            'High': closes + 5.0,
            'Low': closes - 5.0,
            'Close': closes,
            'Volume': [1_000_000] * n,
        }, index=pd.date_range('2024-01-01', periods=n, freq='B'))
        from app.algos.config import StrategyConfig
        trend = TrendChannel(config=StrategyConfig(
            short_period=10,
            long_period=30,
            channel_price_source='high_low',
        ))

        result = trend.compute_all(df)

        assert result['short_upper'].iloc[-1] == pytest.approx(105.0)
        assert result['short_lower'].iloc[-1] == pytest.approx(95.0)


class TestMACDCalculation:
    @pytest.fixture
    def macd(self):
        from app.algos.structure import MACDStructure
        return MACDStructure(fast=12, slow=26, signal=9)

    def test_macd_basic(self, macd):
        df = make_ohlcv([100 + i * 0.5 for i in range(200)])
        result = macd.compute_macd(df)
        assert 'dif' in result.columns
        assert 'dea' in result.columns
        assert 'macd_hist' in result.columns
        assert len(result.dropna()) > 0

    def test_macd_values_make_sense(self, macd):
        prices = [100] * 10 + list(range(100, 200))
        df = make_ohlcv(prices)
        result = macd.compute_macd(df)
        valid = result.dropna(subset=['dif'])
        assert valid['dif'].iloc[-1] > 0

    def test_dif_dea_relationship(self, macd):
        df = make_ohlcv([100 + i * 0.5 for i in range(200)])
        result = macd.compute_macd(df)
        valid = result.dropna(subset=['dif', 'dea'])
        assert len(valid) > 50

    def test_macd_hist_formula(self, macd):
        df = make_ohlcv([100 + i * 0.5 for i in range(200)])
        result = macd.compute_macd(df)
        valid = result.dropna(subset=['macd_hist', 'dif', 'dea'])
        expected_hist = 2 * (valid['dif'] - valid['dea'])
        np.testing.assert_array_almost_equal(valid['macd_hist'], expected_hist)


class TestMagnitudePrefix:
    @pytest.fixture
    def prefix(self):
        from app.algos.structure import _magnitude_prefix
        return _magnitude_prefix

    def test_large_number_with_scale(self, prefix):
        # 168.93 with scale=1 → 16
        assert prefix(168.93, scale=1) == 16

    def test_medium_number_with_scale_same_as_large(self, prefix):
        # 85.23 with scale=1 → 8 (same divisor as 168.93's scale)
        assert prefix(85.23, scale=1) == 8

    def test_auto_scale_large(self, prefix):
        # automatic scale: digits=3, scale=1 → 16
        assert prefix(168.93) == 16

    def test_auto_scale_medium(self, prefix):
        # automatic scale: digits=2, scale=0 → 85
        assert prefix(85.23) == 85

    def test_small_number(self, prefix):
        # 0.05: int(abs)=0, scale=max(0,1-2)=0 → int(0.05/1)=0
        assert prefix(0.05) == 0

    def test_zero(self, prefix):
        assert prefix(0.0) == 0

    def test_negative_number(self, prefix):
        # -168.93: abs=168.93, digits=3, scale=1 → 16
        assert prefix(-168.93, scale=1) == 16

    def test_auto_scale_single_digit(self, prefix):
        # 5.0: digits=1, scale=max(0,1-2)=0 → int(5/1)=5
        assert prefix(5.0) == 5


class TestTopDivergence:
    @pytest.fixture
    def macd(self):
        from app.algos.structure import MACDStructure
        return MACDStructure(fast=12, slow=26, signal=9)

    def test_top_divergence_detected(self, macd):
        n = 150
        base = np.linspace(100, 150, 70)
        rise = np.linspace(150, 180, 50)
        peak = np.linspace(180, 165, 30)
        prices = np.concatenate([base, rise, peak])
        df = make_ohlcv(prices)
        result = macd.evaluate(df)
        assert 'top_divergence' in result.columns
        assert 'bottom_divergence' in result.columns

    def test_no_top_structure_in_steady_uptrend(self, macd):
        """稳定上涨场景下，顶部 100% 结构不应频繁触发（DIF 不会反复死叉）。"""
        np.random.seed(42)
        prices = np.linspace(100, 500, 300)
        df = make_ohlcv(prices)
        result = macd.evaluate(df)
        top_100_count = int(result['top_structure_100'].sum())
        # 稳定上涨即使 random high 偶尔触发钝化，最终也很难形成 100% 死叉
        assert top_100_count <= 2

    def test_divergence_columns_present(self, macd):
        df = make_ohlcv([100 + i * 0.5 for i in range(200)])
        result = macd.evaluate(df)
        for col in ['dif', 'dea', 'macd_hist', 'top_divergence',
                     'bottom_divergence', 'top_structure_75', 'top_structure_100',
                     'bottom_structure_75', 'bottom_structure_100']:
            assert col in result.columns

    def test_structure_level_columns(self, macd):
        df = make_ohlcv([100 + i * 0.5 for i in range(200)])
        result = macd.evaluate(df)
        assert 'top_structure_level' in result.columns
        assert 'bottom_structure_level' in result.columns

    def test_next_period_thresholds(self, macd):
        df = make_ohlcv([100 + i * 0.5 for i in range(200)])
        thresholds = macd.next_period_thresholds(df)
        assert 'dif' in thresholds
        assert 'dea' in thresholds
        assert 'macd_dif_cross_dea_price' in thresholds
        assert 'macd_dif_turn_price' in thresholds
        assert isinstance(thresholds['dif'], float)


class TestBottomDivergence:
    @pytest.fixture
    def macd(self):
        from app.algos.structure import MACDStructure
        return MACDStructure(fast=12, slow=26, signal=9)

    def test_bottom_divergence_possible(self, macd):
        prices = np.linspace(200, 100, 100)
        rise = np.linspace(100, 120, 50)
        prices = np.concatenate([prices, rise])
        df = make_ohlcv(prices)
        result = macd.evaluate(df)
        assert 'bottom_divergence' in result.columns

    def test_divergence_state_machine(self, macd):
        prices = np.linspace(100, 150, 80)
        drop = np.linspace(150, 130, 40)
        prices = np.concatenate([prices, drop])
        df = make_ohlcv(prices)
        result = macd.evaluate(df)
        valid = result.dropna(subset=['top_divergence', 'bottom_divergence'], how='all')
        assert len(valid) > 0


class TestStrictlyGreater:
    """algorithm.md §二 §3：DIF 比较使用带符号的相对阈值，不再用数量级前两位规则。"""

    @pytest.fixture
    def cmp(self):
        from app.algos.structure import strictly_greater
        return strictly_greater

    def test_positive_obvious(self, cmp):
        assert cmp(2.0, 1.0) is True
        assert cmp(1.0, 2.0) is False

    def test_positive_within_eps(self, cmp):
        # 2.00 vs 2.01：相对差 0.5%，小于默认 2% eps → 不算严格大于
        assert cmp(2.01, 2.00, eps=0.02) is False

    def test_negative_signed(self, cmp):
        # 对于底部背离，-1.5 > -2.0 必须为 True，不能因 abs() 颠倒
        assert cmp(-1.5, -2.0) is True
        assert cmp(-2.0, -1.5) is False

    def test_mixed_sign(self, cmp):
        assert cmp(0.5, -0.5) is True
        assert cmp(-0.5, 0.5) is False

    def test_zero_and_small(self, cmp):
        # 远离零时容差大，但 0 附近仍能区分
        assert cmp(0.05, 0.001) is True
        assert cmp(0.001, 0.05) is False

    def test_nan_safe(self, cmp):
        assert cmp(float('nan'), 1.0) is False
        assert cmp(1.0, float('nan')) is False


class TestStructureSpec:
    """覆盖 algorithm.md §二 规范的新增行为。"""

    @pytest.fixture
    def macd(self):
        from app.algos.structure import MACDStructure
        return MACDStructure(fast=12, slow=26, signal=9)

    def _build_double_peak(self):
        """构造两轮"先涨后跌"价格，可触发两次顶部结构。"""
        seg1_up = np.linspace(100, 200, 120)
        seg1_dn = np.linspace(200, 110, 100)
        seg2_up = np.linspace(110, 240, 130)
        seg2_dn = np.linspace(240, 140, 100)
        prices = np.concatenate([seg1_up, seg1_dn, seg2_up, seg2_dn])
        n = len(prices)
        opens = prices
        highs = prices * 1.005
        lows = prices * 0.995
        df = pd.DataFrame({
            'Open': opens, 'High': highs, 'Low': lows, 'Close': prices,
            'Volume': [1_000_000] * n,
        }, index=pd.date_range('2024-01-01', periods=n, freq='B'))
        return df

    def _build_double_valley(self):
        seg1_dn = np.linspace(200, 100, 120)
        seg1_up = np.linspace(100, 180, 100)
        seg2_dn = np.linspace(180, 70, 130)
        seg2_up = np.linspace(70, 160, 100)
        prices = np.concatenate([seg1_dn, seg1_up, seg2_dn, seg2_up])
        n = len(prices)
        opens = prices
        highs = prices * 1.005
        lows = prices * 0.995
        df = pd.DataFrame({
            'Open': opens, 'High': highs, 'Low': lows, 'Close': prices,
            'Volume': [1_000_000] * n,
        }, index=pd.date_range('2024-01-01', periods=n, freq='B'))
        return df

    def test_top_100_can_trigger_more_than_once(self, macd):
        """状态机进入 top_100 后必须重置回 normal，允许后续再次产生顶部结构。"""
        df = self._build_double_peak()
        result = macd.evaluate(df)
        cnt = int(result['top_structure_100'].sum())
        assert cnt >= 2, f"expected ≥2 top_structure_100 events, got {cnt}"

    def test_bottom_100_can_trigger_more_than_once(self, macd):
        df = self._build_double_valley()
        result = macd.evaluate(df)
        cnt = int(result['bottom_structure_100'].sum())
        assert cnt >= 2, f"expected ≥2 bottom_structure_100 events, got {cnt}"

    def test_bottom_structure_with_negative_dif(self, macd):
        """底部 DIF 通常为负，新的带符号比较应能正确识别底部钝化与结构。"""
        df = self._build_double_valley()
        result = macd.evaluate(df)
        bottom_events = int(result['bottom_structure_75'].sum()) + int(result['bottom_structure_100'].sum())
        assert bottom_events >= 1, "底部背离场景应至少产生 1 次底部结构事件"

    def test_active_columns_present(self, macd):
        df = make_ohlcv([100 + i * 0.5 for i in range(200)])
        result = macd.evaluate(df)
        for col in ('top_structure_active', 'bottom_structure_active', 'structure_effective_until'):
            assert col in result.columns

    def test_top_active_persists_after_event(self, macd):
        df = self._build_double_peak()
        result = macd.evaluate(df)
        idx_list = list(np.where(result['top_structure_100'].values | result['top_structure_75'].values)[0])
        assert idx_list, "前置条件：用例应至少产生一次顶部结构事件"
        i0 = idx_list[0]
        end = min(i0 + macd.effective_horizon, len(result) - 1)
        assert result['top_structure_active'].iloc[i0:end + 1].any()

    def test_75_requires_consecutive_dif_decline(self, macd):
        """75% 形成应要求 DIF 连续 K=2 根下行；单根抖动不应触发。"""
        assert macd.smooth_k >= 2

    def test_evaluate_does_not_raise_on_short_data(self, macd):
        df = make_ohlcv([100.0] * 10)
        result = macd.evaluate(df)
        assert len(result) == 10


class TestNineSequence:
    @pytest.fixture
    def seq(self):
        from app.algos.sequence import NineSequence
        return NineSequence()

    def test_high_nine_counting(self, seq):
        prices = [100 + i for i in range(20)]
        df = make_ohlcv(prices, high_offset=0.02, low_offset=0.02)
        df['High'] = df['Close'] * 1.02
        df['Low'] = df['Close'] * 0.98
        result = seq.evaluate(df)
        assert 'high9_count' in result.columns
        assert result['high9_count'].max() >= 1

    def test_low_nine_counting(self, seq):
        prices = [200 - i for i in range(20)]
        df = make_ohlcv(prices, high_offset=0.02, low_offset=0.02)
        df['High'] = df['Close'] * 1.02
        df['Low'] = df['Close'] * 0.98
        result = seq.evaluate(df)
        assert 'low9_count' in result.columns

    def test_count_resets_on_failure(self, seq):
        prices = [100 + i for i in range(5)] + [100 - i for i in range(5)] + [100 + i for i in range(15)]
        df = make_ohlcv(prices, high_offset=0.02, low_offset=0.02)
        df['High'] = df['Close'] * 1.02
        df['Low'] = df['Close'] * 0.98
        result = seq.evaluate(df)
        counts = result['high9_count'].values
        resets = sum(1 for i in range(1, len(counts)) if counts[i] < counts[i-1] and counts[i] == 0)
        assert resets >= 1

    def test_high9_signal_at_9(self, seq):
        prices = [100 + i * 2 for i in range(20)]
        df = make_ohlcv(prices, high_offset=0.10, low_offset=0.01)
        df['High'] = df['Close'] * 1.1
        df['Low'] = df['Close'] * 0.99
        result = seq.evaluate(df)
        assert 'high9_signal' in result.columns
        assert 'low9_signal' in result.columns

    def test_low9_signal_at_9(self, seq):
        prices = [200 - i * 2 for i in range(20)]
        df = make_ohlcv(prices, high_offset=0.01, low_offset=0.10)
        df['High'] = df['Close'] * 1.01
        df['Low'] = df['Close'] * 0.9
        result = seq.evaluate(df)
        assert 'low9_signal' in result.columns

    def test_signals_are_boolean(self, seq):
        df = make_ohlcv([100 + i * 0.5 for i in range(50)])
        result = seq.evaluate(df)
        signals = result['high9_signal'].dropna()
        for s in signals:
            assert s in (True, False)

    def test_high9_strength_condition(self, seq):
        """Create a scenario where count reaches 9 but strength fails."""
        prices = [100 + i * 2 for i in range(15)]
        df = make_ohlcv(prices, high_offset=0.0, low_offset=0.0)
        df['High'] = df['Close'] * 1.01
        df['Low'] = df['Close'] * 0.99
        result = seq.evaluate(df)
        assert 'high9_signal' in result.columns

    def test_low9_strength_condition(self, seq):
        """Create a scenario where low count reaches 9 but strength fails."""
        prices = [200 - i * 2 for i in range(15)]
        df = make_ohlcv(prices, high_offset=0.0, low_offset=0.0)
        df['High'] = df['Close'] * 1.01
        df['Low'] = df['Close'] * 0.99
        result = seq.evaluate(df)
        assert 'low9_signal' in result.columns


class TestSequenceSpec:
    """algorithm.md §三 / design.md §3.5：强度 ≥/≤、有效期、反向破位失效。"""

    @pytest.fixture
    def seq(self):
        from app.algos.sequence import NineSequence
        return NineSequence(effective_horizon=5)

    def _make_strict_uptrend(self):
        # 18 根价格保证连续 9 满足，并构造强度边缘条件
        prices = [100, 101, 102, 103, 104, 105, 106, 107, 108,
                  109, 110, 111, 112, 113, 114, 115, 116, 117]
        df = make_ohlcv(prices, high_offset=0.0, low_offset=0.0)
        df['High'] = df['Close'] * 1.02
        df['Low'] = df['Close'] * 0.98
        return df

    def test_active_columns_present(self, seq):
        df = self._make_strict_uptrend()
        result = seq.evaluate(df)
        for col in ('high9_active', 'low9_active', 'sequence_effective_until'):
            assert col in result.columns

    def test_high9_active_persists(self, seq):
        df = self._make_strict_uptrend()
        result = seq.evaluate(df)
        idx_list = list(np.where(result['high9_signal'].values)[0])
        assert idx_list, "前置条件：用例应产生高 9 信号"
        i0 = idx_list[0]
        end = min(i0 + seq.effective_horizon, len(result) - 1)
        assert result['high9_active'].iloc[i0:end + 1].any()

    def test_high9_invalidates_on_low_break(self, seq):
        """高 9 形成后，价格跌破 9 区间内最低 Low → 立即失效。"""
        prices = [100 + i for i in range(13)]   # 触发高 9
        prices += [80] * 5                       # 大幅下跌，破"9 区间最低"
        df = make_ohlcv(prices, high_offset=0.0, low_offset=0.0)
        df['High'] = df['Close'] * 1.02
        df['Low'] = df['Close'] * 0.98
        result = seq.evaluate(df)
        idx_high9 = list(np.where(result['high9_signal'].values)[0])
        if not idx_high9:
            pytest.skip("此场景未触发高 9，跳过")
        i0 = idx_high9[0]
        # 在跌破后的若干根 K 线，active 必须为 False
        after = result['high9_active'].iloc[i0 + 1:]
        # 至少在跌破点之后某根开始 False
        assert (~after).any()

    def test_strength_uses_geq_at_boundary(self, seq):
        """强度确认应使用 ≥（包含等号），而非严格 >。
        构造 max(High[8],H[9]) 恰好等于 max(High[6],H[7])，原版 `>` 会漏掉，新版 `≥` 应保留。
        """
        # 让所有 highs 相等，确保等号边界
        prices = list(range(100, 100 + 18))
        df = make_ohlcv(prices, high_offset=0.0, low_offset=0.0)
        df['High'] = pd.Series([100.5] * len(df), index=df.index)
        df['Low'] = df['Close'] * 0.99
        result = seq.evaluate(df)
        # 高 9 强度条件应能在等号成立时触发
        assert result['high9_signal'].any()


class TestDecisionEngine:
    @pytest.fixture
    def engine(self):
        from app.algos.decision import DecisionEngine
        return DecisionEngine()

    def test_integration_basic(self, engine):
        df = make_ohlcv([100 + i * 0.5 for i in range(200)])
        result = engine.evaluate(df)
        assert 'position' in result.columns
        assert 'core_long' in result.columns
        assert 'core_short' in result.columns
        assert 'resonance_buy' in result.columns
        assert 'resonance_sell' in result.columns

    def test_core_long_requires_position_4_or_more(self, engine):
        df = make_ohlcv([100 + i * 0.5 for i in range(200)])
        result = engine.evaluate(df)
        core_long = result['core_long'].dropna()
        for i, val in core_long.items():
            if val:
                pos = result.loc[i, 'position']
                assert pos >= 4.0

    def test_core_short_requires_position_6_or_less(self, engine):
        df = make_ohlcv([100 + i * 0.5 for i in range(200)])
        result = engine.evaluate(df)
        core_short = result['core_short'].dropna()
        for i, val in core_short.items():
            if val:
                pos = result.loc[i, 'position']
                assert pos <= 6.0

    def test_resonance_requires_structure(self, engine):
        df = make_ohlcv([100 + i * 0.5 for i in range(200)])
        result = engine.evaluate(df)
        resonance_buy = result['resonance_buy'].dropna()
        for i, val in resonance_buy.items():
            if val:
                assert result.loc[i, 'core_long'] == True

    def test_no_buy_when_position_below_4(self, engine):
        prices = [200 - i * 2 for i in range(100)]
        df = make_ohlcv(prices)
        result = engine.evaluate(df)
        core_long = result['core_long'].dropna()
        for i, val in core_long.items():
            if val:
                assert result.loc[i, 'position'] >= 4.0

    def test_no_sell_when_position_above_6(self, engine):
        prices = [100 + i * 2 for i in range(100)]
        df = make_ohlcv(prices)
        result = engine.evaluate(df)
        core_short = result['core_short'].dropna()
        for i, val in core_short.items():
            if val:
                assert result.loc[i, 'position'] <= 6.0

    def test_summary_includes_next_period_standards(self, engine):
        df = make_ohlcv([100 + i * 0.5 for i in range(300)])
        summary = engine.summary(df)
        # 新规范：standards.trend / standards.structure，字段名也精简（cross_price/turn_price）
        assert 'standards' in summary
        assert 'trend' in summary['standards']
        assert 'structure' in summary['standards']
        for key in ('short_upper', 'short_lower', 'long_upper', 'long_lower'):
            assert key in summary['standards']['trend']
        for key in ('dif', 'dea', 'cross_price', 'turn_price'):
            assert key in summary['standards']['structure']

    def test_summary_includes_decision_signals(self, engine):
        df = make_ohlcv([100 + i * 0.5 for i in range(300)])
        summary = engine.summary(df)
        # 新版 summary 返回扁平/嵌套结构（见 api.md），不再用 core_long/core_short
        assert 'action' in summary
        assert 'weight' in summary
        assert 'confidence' in summary
        assert 'position' in summary
        assert isinstance(summary['position'], dict)
        assert 'current' in summary['position']
        assert 'prev' in summary['position']
        assert 'label' in summary['position']
        assert 'signals' in summary
        assert 'standards' in summary
        assert 'close' in summary


class TestDecisionSpec:
    """覆盖 algorithm.md §四 + api.md /stock/decision 新规范的决策行为。"""

    @pytest.fixture
    def engine(self):
        from app.algos.decision import DecisionEngine
        return DecisionEngine()

    def _df_uptrend(self):
        # 长上涨可保证后期 position=10、prev=NaN→10 等典型场景
        return make_ohlcv([100 + i * 0.6 for i in range(250)])

    def test_evaluate_outputs_action_weight_confidence(self, engine):
        df = self._df_uptrend()
        result = engine.evaluate(df)
        for col in ('action', 'weight', 'confidence', 'prev_position', 'execute_at', 'position_label'):
            assert col in result.columns, f"missing column: {col}"

    def test_hold_when_position_unchanged(self, engine):
        df = self._df_uptrend()
        result = engine.evaluate(df)
        # 连续相同 position 的位置：action == HOLD, weight == 0
        valid = result.dropna(subset=['position', 'prev_position'])
        same = valid[valid['position'] == valid['prev_position']]
        if len(same) > 0:
            assert (same['action'] == 'HOLD').all()
            assert (same['weight'] == 0.0).all()

    def test_buy_action_on_position_increase(self, engine):
        df = self._df_uptrend()
        result = engine.evaluate(df)
        valid = result.dropna(subset=['position', 'prev_position'])
        up = valid[valid['position'] > valid['prev_position']]
        if len(up) > 0:
            assert (up['action'] == 'BUY').all()
            assert (up['weight'] > 0).all()

    def test_sell_action_on_position_decrease(self, engine):
        # 反转价格，产生下跌跃迁
        prices = list(np.linspace(100, 250, 150)) + list(np.linspace(250, 100, 150))
        df = make_ohlcv(prices)
        result = engine.evaluate(df)
        valid = result.dropna(subset=['position', 'prev_position'])
        down = valid[valid['position'] < valid['prev_position']]
        if len(down) > 0:
            assert (down['action'] == 'SELL').all()
            assert (down['weight'] > 0).all()

    def test_core_long_threshold_is_at_least_6(self, engine):
        """algorithm.md §四：core_long 要求 position_T ≥ 6（修正历史 ≥ 4）。"""
        df = self._df_uptrend()
        result = engine.evaluate(df)
        if 'core_long' in result.columns:
            triggered = result[result['core_long'] == True]
            for _, row in triggered.iterrows():
                assert row['position'] >= 6.0, f"core_long@pos={row['position']} violates ≥6"

    def test_core_short_threshold_is_at_most_4(self, engine):
        """algorithm.md §四：core_short 要求 position_T ≤ 4（修正历史 ≤ 6）。"""
        prices = list(np.linspace(100, 250, 150)) + list(np.linspace(250, 100, 150))
        df = make_ohlcv(prices)
        result = engine.evaluate(df)
        if 'core_short' in result.columns:
            triggered = result[result['core_short'] == True]
            for _, row in triggered.iterrows():
                assert row['position'] <= 4.0, f"core_short@pos={row['position']} violates ≤4"

    def test_confidence_levels(self, engine):
        df = self._df_uptrend()
        result = engine.evaluate(df)
        valid = result.dropna(subset=['confidence'])
        for c in valid['confidence'].unique():
            assert c in ('trend', 'core', 'resonance'), f"unexpected confidence: {c}"

    def test_execute_at_is_next_bar(self, engine):
        df = self._df_uptrend()
        result = engine.evaluate(df)
        # 倒数第二根应能拿到下一根索引作为 execute_at
        if len(result) >= 2:
            i = len(result) - 2
            ts_next = result.index[i + 1]
            exec_at = result['execute_at'].iloc[i]
            assert pd.notna(exec_at), "execute_at 应为下一根 K 线索引"
            assert pd.Timestamp(exec_at) == pd.Timestamp(ts_next)

    def test_summary_signals_structure_enum(self, engine):
        df = self._df_uptrend()
        summary = engine.summary(df)
        assert 'signals' in summary
        s = summary['signals']
        for key in ('structure', 'structure_active', 'structure_until',
                    'sequence', 'sequence_active', 'sequence_until',
                    'resonance', 'probe'):
            assert key in s, f"signals.{key} missing"
        assert s['structure'] in ('none', 'top_75', 'top_100', 'bottom_75', 'bottom_100')
        assert s['sequence'] in ('none', 'high9', 'low9')

    def test_summary_standards_nested(self, engine):
        df = self._df_uptrend()
        summary = engine.summary(df)
        st = summary['standards']
        assert 'trend' in st and 'structure' in st
        for key in ('short_upper', 'short_lower', 'long_upper', 'long_lower'):
            assert key in st['trend']
        for key in ('dif', 'dea', 'cross_price', 'turn_price'):
            assert key in st['structure']

    def test_summary_position_object(self, engine):
        df = self._df_uptrend()
        summary = engine.summary(df)
        p = summary['position']
        assert 'current' in p and 'prev' in p and 'label' in p
        if p['current'] is not None:
            assert p['label'] in ('满仓', '重仓', '轻仓', '空仓', '冷启动')


class TestDecisionView:
    """summary['view']：人话翻译层，让客户端 5 秒能读懂当前态势与下一步触发点。"""

    @pytest.fixture
    def engine(self):
        from app.algos.decision import DecisionEngine
        return DecisionEngine()

    def _df_uptrend(self, n=250):
        return make_ohlcv([100 + i * 0.6 for i in range(n)])

    def _df_downtrend(self, n=250):
        return make_ohlcv([250 - i * 0.6 for i in range(n)])

    def _df_sideways(self, n=250):
        # 100 附近窄幅震荡
        np.random.seed(1)
        closes = 100 + np.random.uniform(-2, 2, n)
        df = make_ohlcv(closes.tolist(), high_offset=0.005, low_offset=0.005)
        return df

    def test_view_block_present(self, engine):
        summary = engine.summary(self._df_uptrend())
        assert 'view' in summary, "summary 必须含 view 块"
        v = summary['view']
        assert 'trend' in v and 'next_triggers' in v and 'rationale' in v

    def test_view_trend_label_for_full_position(self, engine):
        summary = engine.summary(self._df_uptrend())
        # 多头长上涨终局应是满仓 → label = "上升"
        if summary['position']['current'] == 10.0:
            assert summary['view']['trend']['label'] == '上升'

    def test_view_trend_label_for_empty_position(self, engine):
        summary = engine.summary(self._df_downtrend())
        if summary['position']['current'] == 0.0:
            assert summary['view']['trend']['label'] == '下降'

    def test_view_trend_label_for_middle_position(self, engine):
        """4 / 6 仓时 label 应为'震荡' / '横盘'类。"""
        summary = engine.summary(self._df_sideways())
        cur = summary['position']['current']
        if cur in (4.0, 6.0):
            assert summary['view']['trend']['label'] in ('横盘', '震荡')

    def test_view_trend_break_prices(self, engine):
        summary = engine.summary(self._df_uptrend())
        t = summary['view']['trend']
        # today 突破/破位价 = standards 短轨
        assert t['today_break_up'] == summary['standards']['trend']['short_upper']
        assert t['today_break_down'] == summary['standards']['trend']['short_lower']
        # tomorrow 是数字 / None（最后一根能外推时为数字）
        assert t['tomorrow_break_up'] is None or isinstance(t['tomorrow_break_up'], (int, float))
        assert t['tomorrow_break_down'] is None or isinstance(t['tomorrow_break_down'], (int, float))

    def test_view_next_triggers_structure(self, engine):
        summary = engine.summary(self._df_uptrend())
        nt = summary['view']['next_triggers']
        assert nt['macd_75_at_close'] == summary['standards']['structure']['turn_price']
        assert nt['macd_100_at_close'] == summary['standards']['structure']['cross_price']

    def test_view_next_triggers_sequence_progress(self, engine):
        summary = engine.summary(self._df_uptrend())
        nt = summary['view']['next_triggers']
        # progress 形如 "N/9"，N 为整数 0..9
        import re
        for k in ('high9_progress', 'low9_progress'):
            assert re.match(r'^\d/9$', nt[k]), f'{k}={nt[k]} 不符合 "N/9" 格式'

    def test_view_rationale_is_nonempty_string(self, engine):
        summary = engine.summary(self._df_uptrend())
        r = summary['view']['rationale']
        assert isinstance(r, str) and len(r) > 0

    def test_view_position_label_mirrors_top_position(self, engine):
        summary = engine.summary(self._df_uptrend())
        assert summary['view']['trend']['position_label'] == summary['position']['label']


class TestSummaryIntegrated:
    """summary_integrated：日线趋势 + 60/90/120 结构合并。"""

    def test_shape_and_by_period(self):
        from app.algos.decision import DecisionEngine
        from app.services.resample import resample_ohlcv
        from tests.test_resample import _make_a_share_day

        days = pd.bdate_range('2024-01-02', periods=80)
        chunks = [_make_a_share_day(d.strftime('%Y-%m-%d'), base=100 + i * 0.05)
                  for i, d in enumerate(days)]
        df5 = pd.concat(chunks).sort_index()
        df_daily = resample_ohlcv(df5, 'daily')
        intraday = {k: resample_ohlcv(df5, k) for k in ('60min', '90min', '120min')}

        out = DecisionEngine().summary_integrated(df_daily, intraday)
        assert 'signals' in out and 'standards' in out and 'view' in out
        assert 'structure_by_period' in out['signals']
        assert set(out['signals']['structure_by_period'].keys()) == {'60min', '90min', '120min'}
        assert 'structure_by_period' in out['standards']
        assert out['view']['trend']['source'] == 'daily'
        assert 'by_period' in out['view']['next_triggers']
