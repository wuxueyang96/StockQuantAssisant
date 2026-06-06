"""三层整合决策场景测试。"""
import numpy as np
import pandas as pd
import pytest

from app.algos.config import DEFAULT_CONFIG, StrategyConfig, TrendState
from app.algos.integrated_decision import build_daily_trading_plan, merge_trend_structure_sequence
from app.algos.structure import aggregate_structure_context, compute_structure_adjustment


def _trend_row(state: str, base: float = 10.0, cap: float = 10.0, floor: float = 6.0) -> pd.Series:
    return pd.Series({
        'trend_state': state,
        'base_target_position': base,
        'position_cap': cap,
        'position_floor': floor,
        'previous_position': base,
        'atr_pct': 0.02,
        'high9_active': False,
        'low9_active': False,
    })


class TestIntegratedDecision:
    def test_up_strong_top_structure_no_sell_to_zero(self):
        struct_ctx = {
            'has_conflict': False,
            'top_active': True,
            'bottom_active': False,
            'active_periods': ['120min'],
            'strongest_event': 'top_100',
            'resonance_count': 1,
            'resonance_weight': 1.0,
        }
        adj, bias, warnings = compute_structure_adjustment(
            TrendState.UP_STRONG.value, struct_ctx, DEFAULT_CONFIG,
        )
        assert adj <= 0
        assert adj >= -2
        _, _, _, decision, _, _ = merge_trend_structure_sequence(
            _trend_row(TrendState.UP_STRONG.value),
            struct_ctx, adj, bias.value, warnings,
            seq_rules=[],
            actual_position=10.0,
        )
        assert decision.final_target_position >= 6.0
        assert decision.action != 'SELL' or decision.final_target_position > 0
        assert 'NO_CHASE' in warnings or 'TRIM_ALLOWED' in warnings

    def test_down_strong_bottom_no_buy(self):
        struct_ctx = {
            'has_conflict': False,
            'top_active': False,
            'bottom_active': True,
            'active_periods': ['60min'],
            'strongest_event': 'bottom_100',
            'resonance_count': 1,
            'resonance_weight': 1.0,
        }
        adj, bias, warnings = compute_structure_adjustment(
            TrendState.DOWN_STRONG.value, struct_ctx, DEFAULT_CONFIG,
        )
        assert adj == 0
        _, _, _, decision, _, _ = merge_trend_structure_sequence(
            _trend_row(TrendState.DOWN_STRONG.value, base=0, cap=0, floor=0),
            struct_ctx, adj, bias.value, warnings,
            seq_rules=[],
            actual_position=4.0,
            hard_exit=True,
        )
        assert decision.action == 'SELL'
        assert decision.final_target_position == 0.0
        assert 'BOTTOM_WATCH' in warnings or 'NO_PANIC_SELL' in warnings

    def test_up_pullback_bottom_low9_pullback_candidate(self):
        from app.algos.sequence import compute_sequence_execution_rules
        struct_ctx = {
            'has_conflict': False,
            'top_active': False,
            'bottom_active': True,
            'active_periods': ['90min'],
            'strongest_event': 'bottom_75',
            'resonance_count': 1,
            'resonance_weight': 1.0,
        }
        adj, bias, warnings = compute_structure_adjustment(
            TrendState.UP_PULLBACK.value, struct_ctx, DEFAULT_CONFIG,
        )
        rules = compute_sequence_execution_rules(
            trend_state=TrendState.UP_PULLBACK.value,
            high9_active=False,
            low9_active=True,
            bottom_structure_active=True,
        )
        row = _trend_row(TrendState.UP_PULLBACK.value, base=6, cap=8, floor=4)
        row['low9_active'] = True
        _, _, _, decision, _, _ = merge_trend_structure_sequence(
            row, struct_ctx, adj, bias.value, warnings, rules,
            actual_position=4.0,
        )
        assert decision.final_target_position <= 8.0
        assert 'PULLBACK_BUY_CANDIDATE' in rules
        assert decision.final_target_position >= 4.0

    def test_high9_does_not_change_final_target_by_default(self):
        from app.algos.sequence import compute_sequence_execution_rules
        rules = compute_sequence_execution_rules(
            trend_state=TrendState.UP_STRONG.value,
            high9_active=True,
            low9_active=False,
        )
        row = _trend_row(TrendState.UP_STRONG.value)
        row['high9_active'] = True
        struct_ctx = aggregate_structure_context({}, pd.Timestamp('2024-06-01'))
        _, _, _, decision, _, _ = merge_trend_structure_sequence(
            row, struct_ctx, 0, 'NEUTRAL', [], rules,
            actual_position=10.0,
        )
        assert decision.final_target_position == 10.0
        assert 'NO_CHASE' in rules

    def test_order_weight_bounded(self):
        struct_ctx = {
            'has_conflict': False,
            'top_active': True,
            'bottom_active': True,
            'active_periods': ['60min', '90min', '120min'],
            'resonance_count': 3,
            'resonance_weight': 2.0,
        }
        row = _trend_row(TrendState.UP_STRONG.value)
        _, _, _, decision, seq_ctx, _ = merge_trend_structure_sequence(
            row, struct_ctx, 0, 'CONFLICT', ['CONFLICT_WARNING'], ['NO_CHASE'],
            actual_position=0.0,
        )
        assert 0.0 <= decision.order_weight <= 1.0
        assert decision.signal_strength >= decision.order_weight

    def test_range_transition_decay(self):
        from app.algos.trend import compute_trend_decision
        n = 30
        closes = [100.0] * n
        dates = pd.date_range('2024-01-01', periods=n, freq='B')
        df = pd.DataFrame({
            'Open': closes, 'High': closes, 'Low': closes, 'Close': closes,
            'Volume': [1e6] * n,
        }, index=dates)
        cfg = StrategyConfig(
            short_period=5, long_period=10,
            transition_decay_days=3, range_target_position=4.0,
        )
        ev = compute_trend_decision(df, cfg)
        last = ev.iloc[-1]
        assert last['trend_state'] == 'RANGE'
        assert last['base_target_position'] == 4.0

    def test_up_strong_top_structure_cannot_drop_to_4(self):
        struct_ctx = {
            'has_conflict': False,
            'top_active': True,
            'bottom_active': False,
            'active_periods': ['120min'],
            'strongest_event': 'top_100',
            'resonance_count': 1,
            'resonance_weight': 1.0,
        }
        adj, bias, warnings = compute_structure_adjustment(
            TrendState.UP_STRONG.value, struct_ctx, DEFAULT_CONFIG,
        )
        row = _trend_row(TrendState.UP_STRONG.value, base=10, cap=10, floor=8)
        _, _, _, decision, _, _ = merge_trend_structure_sequence(
            row, struct_ctx, adj, bias.value, warnings, [],
            actual_position=10.0,
        )
        assert decision.raw_target_position == 8.0
        assert decision.final_target_position == 8.0

    def test_up_pullback_high9_does_not_direct_sell(self):
        from app.algos.sequence import compute_sequence_execution_rules
        row = _trend_row(TrendState.UP_PULLBACK.value, base=8, cap=10, floor=6)
        row['high9_active'] = True
        rules = compute_sequence_execution_rules(
            trend_state=TrendState.UP_PULLBACK.value,
            high9_active=True,
            low9_active=False,
        )
        struct_ctx = aggregate_structure_context({}, pd.Timestamp('2024-06-01'))
        _, _, _, decision, _, _ = merge_trend_structure_sequence(
            row, struct_ctx, 0, 'NEUTRAL', [], rules,
            actual_position=8.0,
        )
        assert decision.final_target_position == 8.0
        assert decision.action == 'HOLD'
        assert 'NO_CHASE' in rules

    def test_down_strong_low9_does_not_buy(self):
        from app.algos.sequence import compute_sequence_execution_rules
        row = _trend_row(TrendState.DOWN_STRONG.value, base=0, cap=0, floor=0)
        row['low9_active'] = True
        rules = compute_sequence_execution_rules(
            trend_state=TrendState.DOWN_STRONG.value,
            high9_active=False,
            low9_active=True,
        )
        struct_ctx = aggregate_structure_context({}, pd.Timestamp('2024-06-01'))
        _, _, _, decision, _, _ = merge_trend_structure_sequence(
            row, struct_ctx, 0, 'NEUTRAL', [], rules,
            actual_position=0.0,
        )
        assert decision.final_target_position == 0.0
        assert decision.action == 'HOLD'
        assert 'NO_PANIC_SELL' in rules

    def test_10_to_4_requires_hard_exit(self):
        row = _trend_row(TrendState.RANGE.value, base=4, cap=6, floor=0)
        struct_ctx = aggregate_structure_context({}, pd.Timestamp('2024-06-01'))
        _, _, _, decision, _, _ = merge_trend_structure_sequence(
            row, struct_ctx, 0, 'NEUTRAL', [], [],
            actual_position=10.0,
            hard_exit=False,
        )
        assert decision.raw_target_position == 4.0
        assert decision.final_target_position == 8.0

        _, _, _, hard_decision, _, _ = merge_trend_structure_sequence(
            row, struct_ctx, 0, 'NEUTRAL', [], [],
            actual_position=10.0,
            hard_exit=True,
        )
        assert hard_decision.final_target_position == 4.0
        assert hard_decision.hard_exit is True

    def test_hard_exit_does_not_buy(self):
        row = _trend_row(TrendState.UP_PULLBACK.value, base=8, cap=10, floor=6)
        struct_ctx = aggregate_structure_context({}, pd.Timestamp('2024-06-01'))
        _, _, _, decision, _, _ = merge_trend_structure_sequence(
            row, struct_ctx, 0, 'NEUTRAL', [], [],
            actual_position=6.0,
            hard_exit=True,
        )
        assert decision.final_target_position == 6.0
        assert decision.action == 'HOLD'
        assert decision.hard_exit is True

    def test_long_mid_up_not_plain_range(self):
        from app.algos.trend import classify_trend_state
        state = classify_trend_state(
            close=105.0,
            short_upper_prev=110.0,
            short_lower_prev=100.0,
            long_upper_prev=120.0,
            long_lower_prev=90.0,
            short_mid_prev=104.0,
            long_mid_prev=100.0,
            long_mid_slope=0.01,
        )
        assert state in (TrendState.UP_WEAK, TrendState.UP_PULLBACK)

    def test_ablation_switches_disable_structure_and_sequence(self):
        from app.algos.integrated_decision import evaluate_integrated_dataframe
        dates = pd.date_range('2024-01-01', periods=40, freq='B')
        closes = np.linspace(10.0, 20.0, len(dates))
        df = pd.DataFrame({
            'Open': closes,
            'High': closes * 1.01,
            'Low': closes * 0.99,
            'Close': closes,
            'Volume': [100000] * len(dates),
        }, index=dates)
        cfg = StrategyConfig(
            short_period=5,
            long_period=10,
            enable_structure=False,
            enable_sequence=False,
        )
        result = evaluate_integrated_dataframe(df, config=cfg)
        assert result['structure_adjustment'].fillna(0).eq(0).all()
        assert result['sequence_adjustment'].fillna(0).eq(0).all()
        assert not result['high9_active'].any()
        assert not result['low9_active'].any()
