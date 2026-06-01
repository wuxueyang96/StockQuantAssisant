"""趋势层：双通道 + 战略仓位（参见 docs/algorithm.md §一）。

突破判定使用 **上一交易日已确定的轨线**（T-1 channel vs Close_T），避免未来函数。
当日计算的 channel_T 仅作状态更新与次日参考，不参与 T 日突破判断。
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pandas as pd

from app.algos.config import DEFAULT_CONFIG, StrategyConfig, TrendState


def _mid_channel(upper: float, lower: float) -> float:
    return (upper + lower) / 2.0


def _compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    high = df['High']
    low = df['Low']
    close = df['Close']
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def compute_channels(df: pd.DataFrame, config: StrategyConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    """计算双通道轨线（无未来函数：仅用截至当根的历史 OHLC）。

    可用于展示与次日阈值；**突破判定**须配合 ``classify_trend_state`` 使用
    上一根轨线（``short_upper_prev`` 等）。
    """
    result = df.copy()
    ns = config.short_period
    nl = config.long_period

    upper_series = df['Close'] if config.channel_upper_source == 'close' else df['High']
    short_rmax = upper_series.rolling(ns, min_periods=1).max()
    short_rmin = df['Low'].rolling(ns, min_periods=1).min()
    long_rmax = upper_series.rolling(nl, min_periods=1).max()
    long_rmin = df['Low'].rolling(nl, min_periods=1).min()

    if config.use_adaptive_offset:
        atr = _compute_atr(df, config.atr_period)
        offset_s = (config.adaptive_offset_k * atr / df['Close']).clip(lower=0.005, upper=0.15)
        offset_l = offset_s
    else:
        offset_s = config.fixed_offset
        offset_l = config.fixed_offset

    result['short_upper'] = short_rmax.ewm(span=ns, adjust=False).mean() * (1 + offset_s)
    result['short_lower'] = short_rmin.ewm(span=ns, adjust=False).mean() * (1 - offset_s)
    result['long_upper'] = long_rmax.ewm(span=nl, adjust=False).mean() * (1 + offset_l)
    result['long_lower'] = long_rmin.ewm(span=nl, adjust=False).mean() * (1 - offset_l)

    result['short_mid'] = (result['short_upper'] + result['short_lower']) / 2
    result['long_mid'] = (result['long_upper'] + result['long_lower']) / 2

    if config.use_adaptive_offset:
        result['atr_pct'] = (atr / df['Close']).replace([np.inf, -np.inf], np.nan)
    else:
        atr = _compute_atr(df, config.atr_period)
        result['atr_pct'] = (atr / df['Close']).replace([np.inf, -np.inf], np.nan)

    return result


def classify_trend_state(
    close: float,
    short_upper_prev: float,
    short_lower_prev: float,
    long_upper_prev: float,
    long_lower_prev: float,
) -> TrendState:
    """根据 T 日收盘价与 T-1 轨线判定趋势状态（**无未来函数**）。"""
    vals = (short_upper_prev, short_lower_prev, long_upper_prev, long_lower_prev)
    if any(pd.isna(v) for v in vals) or pd.isna(close):
        return TrendState.UNKNOWN

    if close > short_upper_prev and close > long_upper_prev:
        return TrendState.UP_STRONG
    if close < short_lower_prev and close < long_lower_prev:
        return TrendState.DOWN_STRONG
    if close < short_lower_prev and close > long_upper_prev:
        return TrendState.UP_PULLBACK
    if close > short_upper_prev and close < long_upper_prev:
        return TrendState.DOWN_REBOUND
    return TrendState.RANGE


def _state_targets(state: TrendState, previous_position: float,
                   transition_days: int, config: StrategyConfig) -> Tuple[float, float, float, str]:
    """返回 (base_target, cap, floor, reason)。"""
    if state == TrendState.UP_STRONG:
        return 10.0, 10.0, 6.0, '收盘价突破短/长上轨（基于昨日轨线），强上升趋势'
    if state == TrendState.UP_PULLBACK:
        return 6.0, 8.0, 4.0, '长期向上、短期跌破短下轨（基于昨日轨线），上升趋势回调'
    if state == TrendState.DOWN_STRONG:
        return 0.0, 0.0, 0.0, '收盘价跌破短/长下轨（基于昨日轨线），强下降趋势'
    if state == TrendState.DOWN_REBOUND:
        return 4.0, 4.0, 0.0, '短期突破短上轨但仍在长上轨下方，下降中的反弹'
    if state == TrendState.RANGE:
        if transition_days >= config.transition_decay_days:
            base = config.range_target_position
            reason = f'连续 {transition_days} 日震荡/过渡，衰减至区间目标仓位 {base}'
        elif not pd.isna(previous_position):
            base = float(previous_position)
            reason = f'震荡/过渡，暂继承上一有效仓位 {base}（{transition_days}/{config.transition_decay_days} 日后衰减）'
        else:
            base = config.range_target_position
            reason = f'震荡/无明确趋势，使用区间目标仓位 {base}'
        return base, 4.0, 0.0, reason
    return np.nan, 0.0, 0.0, '数据不足或冷启动'


def compute_trend_decision(
    df: pd.DataFrame,
    config: StrategyConfig = DEFAULT_CONFIG,
) -> pd.DataFrame:
    """逐日计算趋势层输出（含 base_target_position / trend_state / cap / floor）。"""
    ch = compute_channels(df, config)
    n = len(ch)

    states = []
    bases = []
    caps = []
    floors = []
    reasons = []
    prev_positions = []
    transition_days_list = []
    qualities = []

    prev_position = np.nan
    transition_days = 0

    for i in range(n):
        row = ch.iloc[i]
        close = row['Close']

        if i == 0:
            sh_up_p = sh_lo_p = lg_up_p = lg_lo_p = np.nan
        else:
            prev = ch.iloc[i - 1]
            sh_up_p = prev['short_upper']
            sh_lo_p = prev['short_lower']
            lg_up_p = prev['long_upper']
            lg_lo_p = prev['long_lower']

        state = classify_trend_state(close, sh_up_p, sh_lo_p, lg_up_p, lg_lo_p)
        states.append(state.value)

        if state == TrendState.UNKNOWN:
            base, cap, floor, reason = np.nan, 0.0, 0.0, '冷启动或轨线未就绪'
            transition_days = 0
        else:
            if state == TrendState.RANGE:
                transition_days += 1
            else:
                transition_days = 0
            base, cap, floor, reason = _state_targets(
                state, prev_position, transition_days, config,
            )

        prev_positions.append(prev_position)
        bases.append(base)
        caps.append(cap)
        floors.append(floor)
        reasons.append(reason)
        transition_days_list.append(transition_days)

        if not pd.isna(base):
            prev_position = base

        # 质量指标
        sm_slope = np.nan
        lm_slope = np.nan
        if i >= 1:
            sm0, sm1 = ch['short_mid'].iloc[i - 1], ch['short_mid'].iloc[i]
            lm0, lm1 = ch['long_mid'].iloc[i - 1], ch['long_mid'].iloc[i]
            if pd.notna(sm0) and pd.notna(sm1) and sm0 != 0:
                sm_slope = (sm1 - sm0) / sm0
            if pd.notna(lm0) and pd.notna(lm1) and lm0 != 0:
                lm_slope = (lm1 - lm0) / lm0
        width_pct = np.nan
        if pd.notna(row['short_upper']) and pd.notna(row['short_lower']) and close:
            width_pct = (row['short_upper'] - row['short_lower']) / close
        qualities.append({
            'short_mid_slope': sm_slope,
            'long_mid_slope': lm_slope,
            'channel_width_pct': width_pct,
            'atr_pct': row.get('atr_pct', np.nan),
        })

    out = ch.copy()
    out['trend_state'] = states
    out['base_target_position'] = bases
    out['position_cap'] = caps
    out['position_floor'] = floors
    out['previous_position'] = prev_positions
    out['transition_days'] = transition_days_list
    out['trend_reason'] = reasons

    out['short_upper_prev'] = out['short_upper'].shift(1)
    out['short_lower_prev'] = out['short_lower'].shift(1)
    out['long_upper_prev'] = out['long_upper'].shift(1)
    out['long_lower_prev'] = out['long_lower'].shift(1)

    out['short_mid_slope'] = [q['short_mid_slope'] for q in qualities]
    out['long_mid_slope'] = [q['long_mid_slope'] for q in qualities]
    out['channel_width_pct'] = [q['channel_width_pct'] for q in qualities]

    # 向后兼容：position = 趋势层 base_target_position
    out['position'] = out['base_target_position']

    return out


class TrendChannel:
    """向后兼容封装：默认参数与旧版 TrendChannel 一致。"""

    def __init__(
        self,
        short_period: int = 26,
        long_period: int = 90,
        offset_pct: float = 0.03,
        config: Optional[StrategyConfig] = None,
    ):
        self.short_period = short_period
        self.long_period = long_period
        self.offset_pct = offset_pct
        self.config = config or StrategyConfig(
            short_period=short_period,
            long_period=long_period,
            fixed_offset=offset_pct,
        )

    def compute_all(self, df: pd.DataFrame) -> pd.DataFrame:
        return compute_channels(df, self.config)

    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        return compute_trend_decision(df, self.config)

    def next_day_thresholds(self, df: pd.DataFrame) -> dict:
        """下一交易日趋势阈值 = 今日收盘后确定的通道值（T 日 channel，供 T+1 参考）。"""
        result = compute_channels(df, self.config)
        last = result.iloc[-1]
        return {
            'short_upper': round(float(last['short_upper']), 4) if pd.notna(last.get('short_upper')) else None,
            'short_lower': round(float(last['short_lower']), 4) if pd.notna(last.get('short_lower')) else None,
            'long_upper': round(float(last['long_upper']), 4) if pd.notna(last.get('long_upper')) else None,
            'long_lower': round(float(last['long_lower']), 4) if pd.notna(last.get('long_lower')) else None,
        }
