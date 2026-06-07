"""每日交易计划与三层决策上下文（dataclass）。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TrendKeyLines:
    short_upper_prev: Optional[float] = None
    short_lower_prev: Optional[float] = None
    long_upper_prev: Optional[float] = None
    long_lower_prev: Optional[float] = None
    short_mid_prev: Optional[float] = None
    long_mid_prev: Optional[float] = None
    previous_20_high: Optional[float] = None
    short_upper_current: Optional[float] = None
    short_lower_current: Optional[float] = None
    long_upper_current: Optional[float] = None
    long_lower_current: Optional[float] = None
    short_mid_current: Optional[float] = None
    long_mid_current: Optional[float] = None


@dataclass
class TrendQuality:
    short_mid_slope: Optional[float] = None
    long_mid_slope: Optional[float] = None
    channel_width_pct: Optional[float] = None
    atr_pct: Optional[float] = None


@dataclass
class TrendContext:
    state: str
    base_target_position: Optional[float]
    position_cap: float
    position_floor: float
    previous_position: Optional[float]
    primary_regime: str = 'UNKNOWN'
    tactical_state: str = 'NORMAL'
    key_lines: TrendKeyLines = field(default_factory=TrendKeyLines)
    quality: TrendQuality = field(default_factory=TrendQuality)
    reason: str = ''


@dataclass
class StructureContext:
    adjustment: int = 0
    bias: str = 'NEUTRAL'
    active_periods: List[str] = field(default_factory=list)
    strongest_event: str = 'none'
    highest_timeframe_event: str = 'none'
    resonance_count: int = 0
    resonance_weight: float = 0.0
    warnings: List[str] = field(default_factory=list)


@dataclass
class SequenceContext:
    high9_active: bool = False
    low9_active: bool = False
    probe: bool = False
    near_historical_extreme: bool = False
    execution_rules: List[str] = field(default_factory=list)


@dataclass
class DecisionContext:
    actual_position: Optional[float]
    final_target_position: Optional[float]
    raw_target_position: Optional[float] = None
    order_delta: float = 0.0
    order_weight: float = 0.0
    action: str = 'WAIT'
    signal_strength: float = 0.0
    confidence_label: str = 'trend'
    principle: str = ''
    forbidden_actions: List[str] = field(default_factory=list)
    no_trade_condition: str = ''
    invalidation: str = ''
    hard_exit: bool = False
    hard_exit_reason: str = ''
    target_reversal_warning: str = ''
    structure_effect: str = ''
    sequence_effect: str = ''


@dataclass
class DailyTradingPlan:
    date: str
    symbol: str
    close: float
    execute_date: str
    execute_at: str
    trend: TrendContext
    structure: StructureContext
    sequence: SequenceContext
    decision: DecisionContext
    explanation: List[str] = field(default_factory=list)

    def to_api_dict(self) -> Dict[str, Any]:
        """嵌套字典，供 API next_day_plan 字段。"""
        d = asdict(self)
        return d

    def legacy_confidence(self) -> str:
        """旧 API confidence 映射。"""
        label = self.decision.confidence_label
        if label == 'structure_edge':
            return 'core'
        if label == 'resonance':
            return 'resonance'
        return 'trend'

    def position_label(self) -> str:
        from app.algos.config import POSITION_LABEL
        pos = self.decision.final_target_position
        if pos is None or (isinstance(pos, float) and pos != pos):
            return '冷启动'
        return POSITION_LABEL.get(float(pos), '冷启动')
