"""90min partial bar 标记与交易过滤。"""
import pandas as pd
import pytest

from app.services.resample import resample_ohlcv, drop_partial_bars_for_trade


def _make_a_share_day(date_str: str, base: float = 100.0) -> pd.DataFrame:
    am = pd.date_range(f'{date_str} 09:30', f'{date_str} 11:25', freq='5min', tz='Asia/Shanghai')
    pm = pd.date_range(f'{date_str} 13:00', f'{date_str} 14:55', freq='5min', tz='Asia/Shanghai')
    idx = am.append(pm)
    n = len(idx)
    closes = [base + i for i in range(n)]
    return pd.DataFrame({
        'Open': closes, 'High': [c + 0.5 for c in closes],
        'Low': [c - 0.5 for c in closes], 'Close': closes,
        'Volume': [1000] * n,
    }, index=idx)


class TestResamplePartialBar:
    def test_90min_last_bar_is_partial(self):
        df = _make_a_share_day('2024-01-02')
        out = resample_ohlcv(df, '90min')
        assert len(out) == 3
        assert bool(out['partial_bar'].iloc[-1]) is True
        assert bool(out['partial_bar'].iloc[0]) is False

    def test_drop_partial_for_trade_removes_last_90m(self):
        df = _make_a_share_day('2024-01-02')
        out = resample_ohlcv(df, '90min')
        filtered = drop_partial_bars_for_trade(out, '90min')
        assert len(filtered) == 2
        assert not filtered['partial_bar'].any()

    def test_partial_90m_not_used_for_structure_trade_confirm(self):
        from app.algos.integrated_decision import should_use_structure_row_for_trade
        from app.algos.config import DEFAULT_CONFIG
        df = _make_a_share_day('2024-01-02')
        out = resample_ohlcv(df, '90min')
        row = out.iloc[-1]
        assert should_use_structure_row_for_trade('90min', row, out, DEFAULT_CONFIG) is False

    def test_collect_structure_uses_last_complete_90m_for_trade(self):
        from app.algos.integrated_decision import collect_structure_by_period
        from app.algos.config import DEFAULT_CONFIG

        idx = pd.date_range('2024-01-01 09:30', periods=31, freq='90min')
        df90 = pd.DataFrame({
            'Open': range(31),
            'High': range(1, 32),
            'Low': range(31),
            'Close': range(31),
            'Volume': [1000] * 31,
            'partial_bar': [False] * 30 + [True],
        }, index=idx)

        class FakeStructure:
            def evaluate(self, df):
                out = df.copy()
                for col in (
                    'top_structure_75', 'top_structure_100',
                    'bottom_structure_75', 'bottom_structure_100',
                    'top_structure_active', 'bottom_structure_active',
                ):
                    out[col] = False
                out['top_structure_active_event'] = 'none'
                out['bottom_structure_active_event'] = 'none'
                out['top_structure_event_at'] = pd.NaT
                out['bottom_structure_event_at'] = pd.NaT
                out.iloc[-1, out.columns.get_loc('top_structure_active')] = True
                out.iloc[-1, out.columns.get_loc('top_structure_100')] = True
                out.iloc[-1, out.columns.get_loc('top_structure_active_event')] = 'top_100'
                out.iloc[-1, out.columns.get_loc('top_structure_event_at')] = out.index[-1]
                return out

        rows, display = collect_structure_by_period(
            {'90min': df90},
            FakeStructure(),
            pd.Timestamp(df90.index[-1]),
            DEFAULT_CONFIG,
        )

        assert display['90min']['partial_bar'] is True
        assert '90min' in rows
        assert rows['90min'].name == df90.index[-2]
        assert bool(rows['90min'].get('partial_bar')) is False
