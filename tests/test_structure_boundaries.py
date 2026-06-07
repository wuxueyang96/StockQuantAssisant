"""结构层边界：周期优先级、冲突、反趋势。"""
import pandas as pd

from app.algos.config import STRUCTURE_PERIOD_PRIORITY
from app.algos.structure import (
    aggregate_structure_context,
    append_structure_state_machine,
    compute_structure_adjustment,
    enrich_structure_row_with_trading_validity,
)


def _row(enum_name: str, top_active: bool, bot_active: bool) -> pd.Series:
    flags = {
        'top_100': ('top_structure_100', 'top_structure_active'),
        'top_75': ('top_structure_75', 'top_structure_active'),
        'bottom_100': ('bottom_structure_100', 'bottom_structure_active'),
        'bottom_75': ('bottom_structure_75', 'bottom_structure_active'),
    }
    s = pd.Series({
        'top_structure_75': False,
        'top_structure_100': False,
        'bottom_structure_75': False,
        'bottom_structure_100': False,
        'top_structure_active': top_active and enum_name.startswith('top'),
        'bottom_structure_active': bot_active and enum_name.startswith('bottom'),
    })
    if enum_name in flags:
        s[flags[enum_name][0]] = True
        s[flags[enum_name][1]] = True
    return s


class TestStructureBoundaries:
    def test_period_priority_120_over_60(self):
        as_of = pd.Timestamp('2024-06-01')
        r60 = _row('top_75', True, False)
        r60.name = as_of
        r120 = _row('top_100', True, False)
        r120.name = as_of
        rows = {
            '60min': enrich_structure_row_with_trading_validity(r60, '60min'),
            '120min': enrich_structure_row_with_trading_validity(r120, '120min'),
        }
        ctx = aggregate_structure_context(rows, as_of)
        assert ctx['strongest_event'] == 'top_100'
        assert '120min' in ctx['active_periods']
        assert STRUCTURE_PERIOD_PRIORITY['120min'] > STRUCTURE_PERIOD_PRIORITY['60min']

    def test_conflict_zero_adjustment(self):
        as_of = pd.Timestamp('2024-06-01')
        r60 = _row('top_100', True, False)
        r60.name = as_of
        r90 = _row('bottom_100', False, True)
        r90.name = as_of
        rows = {
            '60min': enrich_structure_row_with_trading_validity(r60, '60min'),
            '90min': enrich_structure_row_with_trading_validity(r90, '90min'),
        }
        ctx = aggregate_structure_context(rows, as_of)
        assert ctx['has_conflict']
        adj, bias, warnings = compute_structure_adjustment('UP_STRONG', ctx)
        assert adj == 0
        assert bias.value == 'CONFLICT'
        assert 'CONFLICT_WARNING' in warnings

    def test_active_event_type_persists_after_trigger_bar(self):
        """结构有效期内，即使当前行不是触发当根，也要保留事件类型供交易整合。"""
        event_at = pd.Timestamp('2024-06-01')
        as_of = pd.Timestamp('2024-06-02')
        r120 = _row('none', True, False)
        r120.name = as_of
        r120['top_structure_active'] = True
        r120['top_structure_active_event'] = 'top_100'
        r120['top_structure_event_at'] = event_at

        rows = {'120min': enrich_structure_row_with_trading_validity(r120, '120min')}
        ctx = aggregate_structure_context(rows, as_of)

        assert ctx['strongest_event'] == 'top_100'
        assert ctx['highest_timeframe_event'] == '120min:top_100'
        assert ctx['active_periods'] == ['120min']

    def test_trading_valid_until_uses_event_time_not_last_row(self):
        event_at = pd.Timestamp('2024-06-01')
        as_of = pd.Timestamp('2024-06-03')
        r60 = _row('none', True, False)
        r60.name = as_of
        r60['top_structure_active'] = True
        r60['top_structure_active_event'] = 'top_100'
        r60['top_structure_event_at'] = event_at

        rows = {'60min': enrich_structure_row_with_trading_validity(r60, '60min')}
        ctx = aggregate_structure_context(rows, as_of)

        assert ctx['active_periods'] == []
        assert ctx['strongest_event'] == 'none'

    def test_structure_state_machine_confirms_top_warning(self):
        dates = pd.date_range('2024-01-01', periods=7, freq='B')
        closes = [10.0, 10.2, 10.1, 10.3, 10.4, 12.0, 9.5]
        df = pd.DataFrame({
            'Open': closes,
            'High': [x + 0.1 for x in closes],
            'Low': [x - 0.1 for x in closes],
            'Close': closes,
            'Volume': [1000] * len(closes),
            'top_divergence': [False, False, False, False, False, True, False],
            'top_structure_75': [False] * len(closes),
            'top_structure_100': [False] * len(closes),
            'bottom_divergence': [False] * len(closes),
            'bottom_structure_75': [False] * len(closes),
            'bottom_structure_100': [False] * len(closes),
        }, index=dates)

        out = append_structure_state_machine(df, horizon_bars=3, timeframe='daily')

        assert out.iloc[5]['structure_event'] == 'TOP_WARNING'
        assert out.iloc[6]['structure_event'] == 'TOP_CONFIRMED'
        assert out.iloc[6]['top_structure_state'] == 'CONFIRMED'
