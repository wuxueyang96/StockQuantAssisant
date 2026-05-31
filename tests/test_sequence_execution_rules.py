"""序列层执行纪律：不改变 final_target。"""
from app.algos.config import TrendState
from app.algos.sequence import compute_sequence_execution_rules


class TestSequenceExecutionRules:
    def test_high9_no_chase(self):
        rules = compute_sequence_execution_rules(
            trend_state=TrendState.UP_STRONG.value,
            high9_active=True,
            low9_active=False,
        )
        assert 'NO_CHASE' in rules

    def test_low9_no_panic_sell(self):
        rules = compute_sequence_execution_rules(
            trend_state=TrendState.DOWN_STRONG.value,
            high9_active=False,
            low9_active=True,
        )
        assert 'NO_PANIC_SELL' in rules

    def test_trim_when_up_strong_top_structure(self):
        rules = compute_sequence_execution_rules(
            trend_state=TrendState.UP_STRONG.value,
            high9_active=True,
            low9_active=False,
            top_structure_active=True,
        )
        assert 'NO_CHASE' in rules
        assert 'TRIM_ALLOWED' in rules
