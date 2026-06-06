"""三层决策整合：趋势定仓 → 结构修边 → 序列纪律。

每日收盘后生成次日计划（T 判定，T+1 执行），无未来函数。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from pandas.tseries.offsets import BDay

from app.algos.config import (
    DEFAULT_CONFIG,
    STRUCTURE_RESONANCE_INTERVALS,
    ConfidenceLabel,
    StrategyConfig,
    TrendState,
)
from app.algos.sequence import (
    NineSequence,
    compute_sequence_execution_rules,
    is_near_historical_extreme,
)
from app.algos.structure import (
    MACDStructure,
    _structure_enum_from_row,
    aggregate_structure_context,
    compute_structure_adjustment,
    enrich_structure_row_with_trading_validity,
)
from app.algos.trend import TrendChannel, compute_trend_decision
from app.schemas.decision import (
    DailyTradingPlan,
    DecisionContext,
    SequenceContext,
    StructureContext,
    TrendContext,
    TrendKeyLines,
    TrendQuality,
)
from app.services.resample import drop_partial_bars_for_trade

# 生产整合：60/90/120 结构（与 analysis_service 一致）
INTRADAY_STRUCTURE_INTERVALS = ('60min', '90min', '120min')


def _to_iso(v) -> Optional[str]:
    if v is None or (isinstance(v, float) and np.isnan(v)) or pd.isna(v):
        return None
    try:
        return pd.Timestamp(v).isoformat()
    except Exception:
        return str(v)


def _round4(v) -> Optional[float]:
    if v is None or pd.isna(v):
        return None
    return round(float(v), 4)


def should_use_structure_row_for_trade(
    period: str,
    row: pd.Series,
    df_interval: Optional[pd.DataFrame],
    config: StrategyConfig,
) -> bool:
    """是否允许该周期末行参与交易确认（排除 partial 90min）。"""
    if period == '90min' and not config.allow_partial_90m_for_trade:
        if df_interval is not None and 'partial_bar' in df_interval.columns:
            if bool(df_interval['partial_bar'].iloc[-1]):
                return False
        if bool(row.get('partial_bar', False)):
            return False
    return True


def collect_structure_by_period(
    intraday: Dict[str, pd.DataFrame],
    structure: MACDStructure,
    as_of: pd.Timestamp,
    config: StrategyConfig = DEFAULT_CONFIG,
) -> Tuple[Dict[str, pd.Series], Dict[str, dict]]:
    """各周期结构末行 + 展示用 signals_by_period。"""
    struct_rows: Dict[str, pd.Series] = {}
    display_by_period: Dict[str, dict] = {}

    for period in INTRADAY_STRUCTURE_INTERVALS:
        df_i = intraday.get(period)
        if df_i is None or len(df_i) < 30:
            continue
        ev_display = structure.evaluate(df_i)
        last = ev_display.iloc[-1].copy()
        last.name = ev_display.index[-1]
        partial = False
        if 'partial_bar' in df_i.columns:
            partial = bool(df_i['partial_bar'].iloc[-1])

        en = _structure_enum_from_row(last)
        display_by_period[period] = {
            'structure': en,
            'structure_active': bool(last.get('top_structure_active'))
            or bool(last.get('bottom_structure_active')),
            'structure_until': _to_iso(last.get('structure_effective_until')),
            'partial_bar': partial,
        }

        df_trade = drop_partial_bars_for_trade(df_i, period, config)
        if df_trade is None or len(df_trade) < 30:
            continue

        ev_trade = structure.evaluate(df_trade)
        trade_last = ev_trade.iloc[-1].copy()
        trade_last.name = ev_trade.index[-1]
        if not should_use_structure_row_for_trade(period, trade_last, df_trade, config):
            continue

        trade_last = enrich_structure_row_with_trading_validity(trade_last, period, config)
        struct_rows[period] = trade_last

    return struct_rows, display_by_period


def _clamp_target(
    base: float,
    adjustment: int,
    floor: float,
    cap: float,
) -> float:
    raw = float(base) + float(adjustment)
    return float(np.clip(round(raw), floor, cap))


def _is_hard_exit(
    trend_row: pd.Series,
    config: StrategyConfig,
) -> Tuple[bool, str]:
    close = trend_row.get('Close')
    if close is None or pd.isna(close):
        return False, ''
    close = float(close)

    long_lower_prev = trend_row.get('long_lower_prev')
    if pd.notna(long_lower_prev) and close < float(long_lower_prev):
        return True, 'LONG_LOWER_BREAK'

    long_mid_prev = trend_row.get('long_mid_prev')
    long_mid_slope = trend_row.get('long_mid_slope')
    confirm_days = trend_row.get('long_mid_bear_confirm_days', 0)
    if (
        pd.notna(long_mid_prev)
        and pd.notna(long_mid_slope)
        and close < float(long_mid_prev)
        and float(long_mid_slope) <= 0
        and int(confirm_days or 0) >= int(config.long_mid_break_confirm_days)
    ):
        return True, 'LONG_MID_BEAR_CONFIRM'

    atr_stop = trend_row.get('atr_stop_line_prev')
    if pd.notna(atr_stop) and close < float(atr_stop):
        return True, 'ATR_STOP_BREAK'

    return False, ''


def _is_strong_breakout_confirmed(trend_row: pd.Series) -> bool:
    if str(trend_row.get('trend_state')) != TrendState.UP_STRONG.value:
        return False
    close = trend_row.get('Close')
    long_upper_prev = trend_row.get('long_upper_prev')
    short_upper_prev = trend_row.get('short_upper_prev')
    short_mid_slope = trend_row.get('short_mid_slope')
    long_mid_slope = trend_row.get('long_mid_slope')
    return (
        pd.notna(close)
        and pd.notna(long_upper_prev)
        and pd.notna(short_upper_prev)
        and float(close) > float(long_upper_prev)
        and float(close) > float(short_upper_prev)
        and pd.notna(short_mid_slope)
        and pd.notna(long_mid_slope)
        and float(short_mid_slope) > 0
        and float(long_mid_slope) > 0
    )


def _limit_target_change(
    target: float,
    actual_position: float,
    *,
    hard_exit: bool,
    config: StrategyConfig,
) -> float:
    step = float(config.normal_max_position_step)
    delta = float(target) - float(actual_position)
    if step <= 0 or abs(delta) <= step:
        return float(target)
    if hard_exit and delta < 0:
        return float(target)
    return float(actual_position) + (step if delta > 0 else -step)


def _structure_effect_text(struct_ctx: dict, struct_adj: int, warnings: List[str]) -> str:
    parts: List[str] = []
    if struct_adj:
        parts.append(f'adjustment={struct_adj:+d}')
    strongest = struct_ctx.get('strongest_event')
    if strongest and strongest != 'none':
        parts.append(f'event={strongest}')
    active = struct_ctx.get('active_periods') or []
    if active:
        parts.append(f'periods={",".join(active)}')
    if warnings:
        parts.append(f'rules={",".join(warnings)}')
    return '; '.join(parts) if parts else 'none'


def _sequence_effect_text(seq_ctx: SequenceContext) -> str:
    parts: List[str] = []
    if seq_ctx.high9_active:
        parts.append('high9')
    if seq_ctx.low9_active:
        parts.append('low9')
    if seq_ctx.execution_rules:
        parts.append(f'rules={",".join(seq_ctx.execution_rules)}')
    return '; '.join(parts) if parts else 'none'


def _compute_signal_strength(
    order_weight: float,
    struct_ctx: dict,
    seq_ctx: SequenceContext,
    config: StrategyConfig,
) -> float:
    """展示/排序用强度，可 >1，不用于真实下单。"""
    strength = float(order_weight)
    rw = float(struct_ctx.get('resonance_weight', 0.0))
    if rw >= 2.0:
        strength += 0.3
    elif rw >= 1.5:
        strength += 0.15
    if seq_ctx.high9_active or seq_ctx.low9_active:
        strength += config.sequence_strength_bonus
    if struct_ctx.get('has_conflict'):
        strength *= 0.5
    return round(strength, 4)


def _confidence_label(
    struct_ctx: dict,
    seq_ctx: SequenceContext,
    struct_adj: int,
) -> str:
    k = int(struct_ctx.get('resonance_count', 0))
    if k >= 2 and (seq_ctx.high9_active or seq_ctx.low9_active):
        return ConfidenceLabel.RESONANCE.value
    if struct_adj != 0 or struct_ctx.get('top_active') or struct_ctx.get('bottom_active'):
        return ConfidenceLabel.STRUCTURE_EDGE.value
    if seq_ctx.high9_active or seq_ctx.low9_active:
        return ConfidenceLabel.WATCH.value
    return ConfidenceLabel.TREND.value


def build_execution_guidance(
    *,
    action: str,
    trend_state: str,
    execution_rules: List[str],
    atr_pct: Optional[float],
    config: StrategyConfig,
) -> Tuple[str, List[str], str, str]:
    """次日执行原则（非盘中重算）。"""
    forbidden: List[str] = []
    principle_parts: List[str] = []
    no_trade = ''
    invalidation = ''

    chase_pct = config.max_chase_gap_pct
    if chase_pct is None and atr_pct is not None and not pd.isna(atr_pct):
        chase_pct = config.max_chase_gap_atr_multiple * float(atr_pct)
    elif chase_pct is None:
        chase_pct = 0.02

    panic_pct = config.max_panic_gap_pct
    if panic_pct is None and atr_pct is not None and not pd.isna(atr_pct):
        panic_pct = config.max_panic_gap_atr_multiple * float(atr_pct)
    elif panic_pct is None:
        panic_pct = 0.02

    if action == 'BUY':
        principle_parts.append('次日按目标仓位加仓；优先回调至短轨附近再执行。')
        if 'NO_CHASE' in execution_rules:
            principle_parts.append(
                f'禁止追高开：若开盘较 T 日收盘涨幅超过约 {chase_pct:.1%}，等待回落或下次收盘确认。'
            )
            forbidden.append('CHASE_GAP_UP')
        invalidation = '若收盘跌破短下轨（基于昨日轨线），加仓计划失效。'

    elif action == 'SELL':
        if trend_state == TrendState.DOWN_STRONG.value:
            principle_parts.append('强下降趋势，次日仍优先降仓至目标。')
        if 'NO_PANIC_SELL' in execution_rules:
            principle_parts.append(
                f'避免恐慌一次性卖出：若开盘跌幅超过约 {panic_pct:.1%}，可分批或等待反抽。'
            )
            forbidden.append('PANIC_GAP_DOWN_ALL_IN')
        else:
            principle_parts.append('次日按目标仓位减仓。')
        invalidation = '若收盘重新站上短上轨且长趋势转强，减仓节奏可放缓。'

    elif action == 'HOLD':
        principle_parts.append('目标仓位与当前一致，维持持仓并遵守执行纪律。')
        if 'NO_CHASE' in execution_rules:
            forbidden.append('ADD_ON_CHASE')
        if 'NO_PANIC_SELL' in execution_rules:
            forbidden.append('PANIC_TRIM')

    elif action == 'WAIT':
        principle_parts.append('趋势不明或数据不足，次日不产生交易动作。')
        no_trade = 'trend_state=UNKNOWN 或 final_target 无效'

    return ' '.join(principle_parts), forbidden, no_trade, invalidation


def _build_explanation(
    trend: TrendContext,
    structure: StructureContext,
    sequence: SequenceContext,
    decision: DecisionContext,
) -> List[str]:
    lines: List[str] = []
    if trend.reason:
        lines.append(f'日线趋势：{trend.reason}，战略仓位 {trend.base_target_position}。')
    if structure.adjustment != 0:
        lines.append(
            f'结构修边 {structure.adjustment:+d}（{structure.bias}），'
            f'共振 {structure.resonance_count} 周期。'
        )
    elif structure.warnings:
        lines.append(f'结构提示：{", ".join(structure.warnings)}。')
    if sequence.execution_rules:
        lines.append(f'序列纪律：{", ".join(sequence.execution_rules)}。')
    lines.append(
        f'次日目标仓位 {decision.final_target_position}，'
        f'调仓 {decision.action}，比例 {decision.order_weight:.2f}。'
    )
    return lines


def merge_trend_structure_sequence(
    trend_row: pd.Series,
    struct_ctx: dict,
    struct_adj: int,
    struct_bias: str,
    struct_warnings: List[str],
    seq_rules: List[str],
    *,
    actual_position: Optional[float],
    previous_action: Optional[str] = None,
    previous_final_target_position: Optional[float] = None,
    hard_exit: Optional[bool] = None,
    hard_exit_reason: str = '',
    strong_breakout_confirmed: Optional[bool] = None,
    config: StrategyConfig = DEFAULT_CONFIG,
) -> Tuple[float, float, float, DecisionContext, SequenceContext, StructureContext]:
    """合并三层，返回 final_target, cap, floor, decision, sequence, structure。"""
    trend_state = str(trend_row.get('trend_state', TrendState.UNKNOWN.value))
    base = trend_row.get('base_target_position')
    cap = float(trend_row.get('position_cap', 0))
    floor = float(trend_row.get('position_floor', 0))

    seq_ctx = SequenceContext(
        high9_active=bool(trend_row.get('high9_active', False)),
        low9_active=bool(trend_row.get('low9_active', False)),
        execution_rules=list(seq_rules) if config.enable_execution_rules else [],
    )

    structure_ctx = StructureContext(
        adjustment=struct_adj,
        bias=struct_bias,
        active_periods=list(struct_ctx.get('active_periods', [])),
        strongest_event=str(struct_ctx.get('strongest_event', 'none')),
        highest_timeframe_event=str(struct_ctx.get('highest_timeframe_event', 'none')),
        resonance_count=int(struct_ctx.get('resonance_count', 0)),
        resonance_weight=float(struct_ctx.get('resonance_weight', 0.0)),
        warnings=list(struct_warnings),
    )

    if trend_state == TrendState.UNKNOWN.value or pd.isna(base):
        decision = DecisionContext(
            actual_position=actual_position,
            final_target_position=None,
            raw_target_position=None,
            action='WAIT',
            confidence_label=ConfidenceLabel.WATCH.value,
            principle='数据不足或冷启动，等待趋势明确。',
            no_trade_condition='UNKNOWN trend or missing base target',
        )
        return np.nan, cap, floor, decision, seq_ctx, structure_ctx

    if actual_position is None or pd.isna(actual_position):
        prev = trend_row.get('previous_position')
        actual_position = 0.0 if pd.isna(prev) else float(prev)
    actual_position = float(actual_position)

    if hard_exit is None:
        hard_exit, hard_exit_reason = _is_hard_exit(trend_row, config)
    elif hard_exit and not hard_exit_reason:
        hard_exit_reason = 'HARD_EXIT'
    if strong_breakout_confirmed is None:
        strong_breakout_confirmed = _is_strong_breakout_confirmed(trend_row)

    raw_target = _clamp_target(float(base), struct_adj, floor, cap)
    final_target = raw_target
    target_reversal_warning = ''

    if hard_exit and final_target > actual_position:
        final_target = actual_position

    prev_action = str(previous_action or '').upper()
    prev_target = previous_final_target_position
    if (
        prev_action == 'BUY'
        and prev_target is not None
        and pd.notna(prev_target)
        and float(prev_target) >= 8.0
        and raw_target < 6.0
        and not hard_exit
    ):
        final_target = max(final_target, 6.0)
        target_reversal_warning = 'PREV_BUY_HIGH_TARGET_BLOCK_LOW_SELL'

    if (
        prev_action == 'SELL'
        and prev_target is not None
        and pd.notna(prev_target)
        and float(prev_target) <= 4.0
        and raw_target >= 10.0
        and not strong_breakout_confirmed
    ):
        final_target = min(final_target, 8.0)
        target_reversal_warning = 'PREV_SELL_LOW_TARGET_BLOCK_FULL_BUY'

    final_target = _limit_target_change(
        final_target,
        actual_position,
        hard_exit=bool(hard_exit),
        config=config,
    )
    final_target = float(np.clip(round(final_target), 0.0, 10.0))

    if config.sequence_execution_bias_enabled:
        pass  # 默认不改变 final_target

    order_delta = final_target - actual_position
    order_weight = float(np.clip(abs(order_delta) / 10.0, 0.0, 1.0))

    if order_delta > 0:
        action = 'BUY'
    elif order_delta < 0:
        action = 'SELL'
    else:
        action = 'HOLD'

    signal_strength = _compute_signal_strength(order_weight, struct_ctx, seq_ctx, config)
    conf = _confidence_label(struct_ctx, seq_ctx, struct_adj)

    atr_pct = trend_row.get('atr_pct')
    effective_warnings = struct_warnings if config.enable_execution_rules else []
    effective_seq_rules = list(seq_rules) if config.enable_execution_rules else []
    principle, forbidden, no_trade, invalidation = build_execution_guidance(
        action=action,
        trend_state=trend_state,
        execution_rules=effective_seq_rules + effective_warnings,
        atr_pct=atr_pct,
        config=config,
    )

    decision = DecisionContext(
        actual_position=actual_position,
        final_target_position=final_target,
        raw_target_position=raw_target,
        order_delta=order_delta,
        order_weight=round(order_weight, 6),
        action=action,
        signal_strength=signal_strength,
        confidence_label=conf,
        principle=principle,
        forbidden_actions=forbidden,
        no_trade_condition=no_trade,
        invalidation=invalidation,
        hard_exit=bool(hard_exit),
        hard_exit_reason=hard_exit_reason if hard_exit else '',
        target_reversal_warning=target_reversal_warning,
        structure_effect=_structure_effect_text(struct_ctx, struct_adj, struct_warnings),
        sequence_effect=_sequence_effect_text(seq_ctx),
    )
    return final_target, cap, floor, decision, seq_ctx, structure_ctx


def build_daily_trading_plan(
    df_daily: pd.DataFrame,
    intraday: Optional[Dict[str, pd.DataFrame]] = None,
    *,
    symbol: str = '',
    actual_position: Optional[float] = None,
    previous_action: Optional[str] = None,
    previous_final_target_position: Optional[float] = None,
    config: StrategyConfig = DEFAULT_CONFIG,
    trend: Optional[TrendChannel] = None,
    structure: Optional[MACDStructure] = None,
    sequence: Optional[NineSequence] = None,
) -> DailyTradingPlan:
    """T 日收盘后生成次日交易计划（可用于交易确认，无未来函数）。"""
    trend = trend or TrendChannel(config=config)
    structure = structure or MACDStructure()
    sequence = sequence or NineSequence()
    intraday = intraday or {}

    if df_daily is None or len(df_daily) < 1:
        raise ValueError('df_daily 为空')

    trend_df = trend.evaluate(df_daily)
    if config.enable_sequence:
        seq_df = sequence.evaluate(df_daily)
    else:
        seq_df = pd.DataFrame(index=df_daily.index)
        seq_df['high9_active'] = False
        seq_df['low9_active'] = False
        seq_df['high9_signal'] = False
        seq_df['low9_signal'] = False
    last_t = trend_df.iloc[-1]
    last_s = seq_df.iloc[-1]
    as_of = pd.Timestamp(df_daily.index[-1])
    close = float(df_daily['Close'].iloc[-1])

    if config.enable_structure:
        struct_rows, _ = collect_structure_by_period(
            intraday, structure, as_of, config,
        )
        struct_ctx = aggregate_structure_context(struct_rows, as_of, config)
        struct_adj, struct_bias, struct_warnings = compute_structure_adjustment(
            str(last_t.get('trend_state', TrendState.UNKNOWN.value)),
            struct_ctx,
            config,
        )
    else:
        struct_ctx = aggregate_structure_context({}, as_of, config)
        struct_adj, struct_bias, struct_warnings = 0, 'NEUTRAL', []

    if config.enable_sequence:
        seq_rules = compute_sequence_execution_rules(
            trend_state=str(last_t.get('trend_state', TrendState.UNKNOWN.value)),
            high9_active=bool(last_s.get('high9_active')),
            low9_active=bool(last_s.get('low9_active')),
            top_structure_active=struct_ctx.get('top_active', False),
            bottom_structure_active=struct_ctx.get('bottom_active', False),
        )
    else:
        seq_rules = []

    near_ext = is_near_historical_extreme(df_daily)
    probe = near_ext and (
        bool(last_s.get('high9_signal')) or bool(last_s.get('low9_signal'))
    ) and not struct_ctx.get('top_active') and not struct_ctx.get('bottom_active')

    last_t = last_t.copy()
    last_t['high9_active'] = bool(last_s.get('high9_active')) if config.enable_sequence else False
    last_t['low9_active'] = bool(last_s.get('low9_active')) if config.enable_sequence else False

    _, _, _, decision, seq_ctx, structure_ctx = merge_trend_structure_sequence(
        last_t,
        struct_ctx,
        struct_adj,
        struct_bias.value if hasattr(struct_bias, 'value') else str(struct_bias),
        struct_warnings,
        seq_rules,
        actual_position=actual_position,
        previous_action=previous_action,
        previous_final_target_position=previous_final_target_position,
        config=config,
    )
    seq_ctx.probe = probe
    seq_ctx.near_historical_extreme = near_ext

    key_lines = TrendKeyLines(
        short_upper_prev=_round4(last_t.get('short_upper_prev')),
        short_lower_prev=_round4(last_t.get('short_lower_prev')),
        long_upper_prev=_round4(last_t.get('long_upper_prev')),
        long_lower_prev=_round4(last_t.get('long_lower_prev')),
        short_upper_current=_round4(last_t.get('short_upper')),
        short_lower_current=_round4(last_t.get('short_lower')),
        long_upper_current=_round4(last_t.get('long_upper')),
        long_lower_current=_round4(last_t.get('long_lower')),
    )
    trend_ctx = TrendContext(
        state=str(last_t.get('trend_state', TrendState.UNKNOWN.value)),
        base_target_position=_round4(last_t.get('base_target_position'))
        if pd.notna(last_t.get('base_target_position')) else None,
        position_cap=float(last_t.get('position_cap', 0)),
        position_floor=float(last_t.get('position_floor', 0)),
        previous_position=_round4(last_t.get('previous_position'))
        if pd.notna(last_t.get('previous_position')) else None,
        key_lines=key_lines,
        quality=TrendQuality(
            short_mid_slope=_round4(last_t.get('short_mid_slope')),
            long_mid_slope=_round4(last_t.get('long_mid_slope')),
            channel_width_pct=_round4(last_t.get('channel_width_pct')),
            atr_pct=_round4(last_t.get('atr_pct')),
        ),
        reason=str(last_t.get('trend_reason', '')),
    )

    execute_ts = as_of + BDay(1)
    execute_at = _to_iso(execute_ts)

    plan = DailyTradingPlan(
        date=as_of.strftime('%Y-%m-%d'),
        symbol=symbol,
        close=round(close, 4),
        execute_date=execute_ts.strftime('%Y-%m-%d'),
        execute_at=execute_at or '',
        trend=trend_ctx,
        structure=structure_ctx,
        sequence=seq_ctx,
        decision=decision,
        explanation=_build_explanation(trend_ctx, structure_ctx, seq_ctx, decision),
    )
    return plan


def plan_to_legacy_summary(
    plan: DailyTradingPlan,
    *,
    display_by_period: Optional[Dict[str, dict]] = None,
    standards_trend: Optional[dict] = None,
    standards_structure: Optional[dict] = None,
    standards_by_period: Optional[Dict[str, dict]] = None,
    structure_reference_period: Optional[str] = None,
    timestamp: Optional[str] = None,
) -> dict:
    """将 DailyTradingPlan 映射为旧 API summary 格式并附加新字段。"""
    d = plan.decision
    trend = plan.trend
    struct = plan.structure
    seq = plan.sequence

    best_enum = 'none'
    if struct.strongest_event.startswith('conflict'):
        best_enum = struct.strongest_event.split(':')[1].split('/')[0]
    elif struct.strongest_event != 'none':
        best_enum = struct.strongest_event

    structure_active = bool(struct.active_periods)
    resonance = None
    if structure_active:
        resonance = {
            'level': struct.resonance_weight,
            'periods': [p for p in STRUCTURE_RESONANCE_INTERVALS if p in struct.active_periods],
        }

    sequence_enum = 'none'
    if seq.high9_active:
        sequence_enum = 'high9'
    elif seq.low9_active:
        sequence_enum = 'low9'

    result = {
        'close': plan.close,
        'timestamp': timestamp or plan.date,
        'action': d.action,
        'weight': d.order_weight,
        'confidence': plan.legacy_confidence(),
        'execute_at': plan.execute_at,
        'position': {
            'current': d.final_target_position,
            'prev': d.actual_position,
            'label': plan.position_label(),
        },
        'signals': {
            'structure': best_enum,
            'structure_active': structure_active,
            'structure_until': None,
            'structure_by_period': display_by_period or {},
            'sequence': sequence_enum,
            'sequence_active': seq.high9_active or seq.low9_active,
            'sequence_until': None,
            'resonance': resonance,
            'probe': seq.probe,
        },
        'standards': {
            'trend': standards_trend or {},
            'structure': standards_structure or {},
            'structure_reference_period': structure_reference_period,
            'structure_by_period': standards_by_period or {},
        },
        'next_day_plan': plan.to_api_dict(),
        'trend': plan.to_api_dict()['trend'],
        'structure': plan.to_api_dict()['structure'],
        'sequence': plan.to_api_dict()['sequence'],
        'decision': plan.to_api_dict()['decision'],
        'explanation': plan.explanation,
    }
    return result


def _format_signal_reason(plan: DailyTradingPlan) -> str:
    decision = plan.decision
    if decision.action not in ('BUY', 'SELL'):
        return ''

    verb = '加仓' if decision.action == 'BUY' else '减仓'
    parts = []
    if decision.actual_position is not None and decision.final_target_position is not None:
        parts.append(
            f"目标仓位由 {_round4(decision.actual_position)} 调整到 "
            f"{_round4(decision.final_target_position)}，需{verb} {_round4(abs(decision.order_delta or 0))}"
        )
    if plan.trend.state:
        parts.append(f"趋势 {plan.trend.state}")
    if plan.trend.reason:
        parts.append(plan.trend.reason)
    if plan.structure.adjustment:
        parts.append(f"结构修正 {_round4(plan.structure.adjustment)}")
    if plan.sequence.high9_active:
        parts.append('高九活跃，执行上避免追高')
    if plan.sequence.low9_active:
        parts.append('低九活跃，执行上避免恐慌杀跌')
    if decision.hard_exit:
        parts.append(f'硬退出触发：{decision.hard_exit_reason}')
    if decision.target_reversal_warning:
        parts.append(f'反转保护：{decision.target_reversal_warning}')
    if decision.confidence_label:
        parts.append(f"置信 {decision.confidence_label}")
    if decision.principle:
        parts.append(decision.principle)
    return '；'.join(str(x) for x in parts if x)


def evaluate_integrated_dataframe(
    df_daily: pd.DataFrame,
    intraday: Optional[Dict[str, pd.DataFrame]] = None,
    config: StrategyConfig = DEFAULT_CONFIG,
) -> pd.DataFrame:
    """逐日整合评估（回测/调试）；每行使用截至当日的历史数据。"""
    trend = TrendChannel(config=config)
    structure = MACDStructure()
    sequence = NineSequence()
    intraday = intraday or {}

    trend_df = trend.evaluate(df_daily)
    seq_df = sequence.evaluate(df_daily)
    n = len(df_daily)
    result = df_daily.copy()

    cols = [
        'trend_state', 'base_target_position', 'position_cap', 'position_floor',
        'final_target_position', 'raw_target_position', 'actual_position',
        'order_delta', 'order_weight',
        'action', 'signal_strength', 'confidence_label', 'structure_adjustment',
        'sequence_adjustment', 'trend_reason', 'decision_reason', 'principle',
        'structure_effect', 'sequence_effect', 'hard_exit_reason',
        'target_reversal_warning', 'high9_active', 'low9_active', 'hard_exit',
    ]
    object_cols = {
        'trend_state', 'action', 'confidence_label',
        'trend_reason', 'decision_reason', 'principle',
        'structure_effect', 'sequence_effect', 'hard_exit_reason',
        'target_reversal_warning',
    }
    bool_cols = {'high9_active', 'low9_active', 'hard_exit'}
    for c in cols:
        if c in object_cols:
            result[c] = None
        elif c in bool_cols:
            result[c] = False
        else:
            result[c] = np.nan

    carried_actual: Optional[float] = None
    carried_action: Optional[str] = None
    carried_final_target: Optional[float] = None

    for i in range(n):
        sub_daily = df_daily.iloc[: i + 1]
        sub_intra = {}
        as_of_day = pd.Timestamp(sub_daily.index[-1]).date()
        for k, df_i in intraday.items():
            if df_i is not None and len(df_i) > 0:
                # Daily bars are indexed by the first 5min bar of the trading
                # day.  For backtests we still need all intraday structure bars
                # from that same day, so compare by trading date instead of the
                # raw timestamp.
                mask = pd.Series(
                    pd.to_datetime(df_i.index).date <= as_of_day,
                    index=df_i.index,
                )
                sub = df_i.loc[mask]
                if len(sub) >= 30:
                    sub_intra[k] = sub

        try:
            plan = build_daily_trading_plan(
                sub_daily,
                sub_intra,
                actual_position=carried_actual,
                previous_action=carried_action,
                previous_final_target_position=carried_final_target,
                config=config,
                trend=trend,
                structure=structure,
                sequence=sequence,
            )
        except ValueError:
            continue

        idx = df_daily.index[i]
        dec = plan.decision
        result.at[idx, 'trend_state'] = plan.trend.state
        result.at[idx, 'base_target_position'] = plan.trend.base_target_position
        result.at[idx, 'position_cap'] = plan.trend.position_cap
        result.at[idx, 'position_floor'] = plan.trend.position_floor
        result.at[idx, 'final_target_position'] = dec.final_target_position
        result.at[idx, 'raw_target_position'] = dec.raw_target_position
        result.at[idx, 'actual_position'] = dec.actual_position
        result.at[idx, 'order_delta'] = dec.order_delta
        result.at[idx, 'order_weight'] = dec.order_weight
        result.at[idx, 'action'] = dec.action
        result.at[idx, 'signal_strength'] = dec.signal_strength
        result.at[idx, 'confidence_label'] = dec.confidence_label
        result.at[idx, 'structure_adjustment'] = plan.structure.adjustment
        result.at[idx, 'sequence_adjustment'] = 0
        result.at[idx, 'trend_reason'] = plan.trend.reason
        result.at[idx, 'decision_reason'] = _format_signal_reason(plan)
        result.at[idx, 'principle'] = dec.principle
        result.at[idx, 'structure_effect'] = dec.structure_effect
        result.at[idx, 'sequence_effect'] = dec.sequence_effect
        result.at[idx, 'hard_exit'] = dec.hard_exit
        result.at[idx, 'hard_exit_reason'] = dec.hard_exit_reason
        result.at[idx, 'target_reversal_warning'] = dec.target_reversal_warning
        result.at[idx, 'high9_active'] = plan.sequence.high9_active
        result.at[idx, 'low9_active'] = plan.sequence.low9_active
        result.at[idx, 'position'] = dec.final_target_position
        result.at[idx, 'prev_position'] = dec.actual_position

        if dec.final_target_position is not None and not pd.isna(dec.final_target_position):
            carried_actual = float(dec.final_target_position)
            carried_final_target = float(dec.final_target_position)
            carried_action = dec.action

    if config.enable_sequence:
        result['high9_active'] = result['high9_active'].fillna(False)
        result['low9_active'] = result['low9_active'].fillna(False)
    else:
        result['high9_active'] = False
        result['low9_active'] = False
    for col in ('top_structure_75', 'top_structure_100', 'bottom_structure_75',
                'bottom_structure_100', 'top_structure_active', 'bottom_structure_active'):
        if col not in result.columns:
            result[col] = False

    return result
