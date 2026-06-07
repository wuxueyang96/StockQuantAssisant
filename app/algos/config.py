"""策略集中配置与枚举（三层决策系统）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict


class TrendState(str, Enum):
    UP_STRONG = 'UP_STRONG'
    UP_PULLBACK = 'UP_PULLBACK'
    UP_WEAK = 'UP_WEAK'
    RANGE = 'RANGE'
    DOWN_REBOUND = 'DOWN_REBOUND'
    DOWN_STRONG = 'DOWN_STRONG'
    UNKNOWN = 'UNKNOWN'


class PrimaryRegime(str, Enum):
    BULL = 'BULL'
    NEUTRAL = 'NEUTRAL'
    BEAR = 'BEAR'
    UNKNOWN = 'UNKNOWN'


class TacticalState(str, Enum):
    ACCELERATION = 'ACCELERATION'
    NORMAL = 'NORMAL'
    PULLBACK = 'PULLBACK'
    WEAK = 'WEAK'
    REBOUND = 'REBOUND'
    RISK = 'RISK'


class StructureState(str, Enum):
    NONE = 'NONE'
    WARNING = 'WARNING'
    CONFIRMED = 'CONFIRMED'
    FAILED = 'FAILED'
    EXPIRED = 'EXPIRED'


class StructureBias(str, Enum):
    BULLISH_EDGE = 'BULLISH_EDGE'
    BEARISH_EDGE = 'BEARISH_EDGE'
    NEUTRAL = 'NEUTRAL'
    CONFLICT = 'CONFLICT'


class Action(str, Enum):
    BUY = 'BUY'
    SELL = 'SELL'
    HOLD = 'HOLD'
    WAIT = 'WAIT'


class ConfidenceLabel(str, Enum):
    TREND = 'trend'
    STRUCTURE_EDGE = 'structure_edge'
    RESONANCE = 'resonance'
    WATCH = 'watch'


# 多周期结构：周期越大优先级越高（daily > 120 > 90 > 60）
STRUCTURE_INTERVAL_ORDER = ('daily', '120min', '90min', '60min')
STRUCTURE_RESONANCE_INTERVALS = ('60min', '90min', '120min')
STRUCTURE_PERIOD_PRIORITY: Dict[str, int] = {
    'daily': 4,
    '120min': 3,
    '90min': 2,
    '60min': 1,
}

_STRUCTURE_ENUM_SCORE: Dict[str, int] = {
    'top_100': 6,
    'bottom_100': 6,
    'top_75': 4,
    'bottom_75': 4,
    'none': 0,
}

POSITION_LABEL: Dict[float, str] = {
    10.0: '满仓',
    8.0: '重仓',
    6.0: '重仓',
    4.0: '轻仓',
    2.0: '轻仓',
    0.0: '空仓',
}


@dataclass
class StrategyConfig:
    short_period: int = 26
    long_period: int = 90
    atr_period: int = 14
    channel_price_source: str = 'close'
    transition_decay_days: int = 3
    range_target_position: float = 4.0
    max_structure_adjustment: int = 2
    normal_max_position_step: float = 2.0
    long_mid_break_confirm_days: int = 2
    atr_stop_multiple: float = 2.0
    max_chase_gap_pct: float | None = None
    max_chase_gap_atr_multiple: float = 0.8
    max_panic_gap_pct: float | None = None
    max_panic_gap_atr_multiple: float = 0.8
    enable_trend: bool = True
    enable_structure: bool = True
    enable_structure_position_adjustment: bool = False
    enable_sequence: bool = True
    enable_execution_rules: bool = True
    structure_valid_days_by_period: Dict[str, int] = field(
        default_factory=lambda: {
            '60min': 1,
            '90min': 1,
            '120min': 2,
            'daily': 5,
        }
    )
    allow_partial_90m_for_display: bool = True
    allow_partial_90m_for_trade: bool = False
    sequence_execution_bias_enabled: bool = False
    sequence_strength_bonus: float = 0.1
    structure_event_horizon_bars: int = 5


DEFAULT_CONFIG = StrategyConfig()
