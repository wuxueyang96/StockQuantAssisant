"""趋势层：双通道 + 战略仓位（参见 docs/algorithm.md §一）。

突破判定使用 **上一交易日已确定的轨线**（T-1 channel vs Close_T），避免未来函数。
当日计算的 channel_T 仅作状态更新与次日参考，不参与 T 日突破判断。
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pandas as pd

from app.algos.config import (
    DEFAULT_CONFIG,
    PrimaryRegime,
    StrategyConfig,
    TacticalState,
    TrendState,
)


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

    if config.channel_price_source == 'close':
        upper_series = df['Close']
        lower_series = df['Close']
    elif config.channel_price_source == 'high_low':
        upper_series = df['High']
        lower_series = df['Low']
    else:
        raise ValueError("channel_price_source must be 'close' or 'high_low'")

    short_rmax = upper_series.rolling(ns, min_periods=1).max()
    short_rmin = lower_series.rolling(ns, min_periods=1).min()
    long_rmax = upper_series.rolling(nl, min_periods=1).max()
    long_rmin = lower_series.rolling(nl, min_periods=1).min()

    result['short_upper'] = short_rmax.ewm(span=ns, adjust=False).mean()
    result['short_lower'] = short_rmin.ewm(span=ns, adjust=False).mean()
    result['long_upper'] = long_rmax.ewm(span=nl, adjust=False).mean()
    result['long_lower'] = long_rmin.ewm(span=nl, adjust=False).mean()

    result['short_mid'] = (result['short_upper'] + result['short_lower']) / 2
    result['long_mid'] = (result['long_upper'] + result['long_lower']) / 2

    atr = _compute_atr(df, config.atr_period)
    result['atr'] = atr
    result['atr_pct'] = (atr / df['Close']).replace([np.inf, -np.inf], np.nan)
    result['atr_stop_line'] = (
        df['Close'].rolling(config.atr_period, min_periods=1).max()
        - atr * config.atr_stop_multiple
    )

    return result


def _legacy_trend_state(
    primary_regime: PrimaryRegime,
    tactical_state: TacticalState,
) -> TrendState:
    """Map the new two-level trend model onto the legacy API enum."""
    if primary_regime == PrimaryRegime.BULL:
        if tactical_state == TacticalState.ACCELERATION:
            return TrendState.UP_STRONG
        if tactical_state == TacticalState.WEAK:
            return TrendState.UP_WEAK
        return TrendState.UP_PULLBACK
    if primary_regime == PrimaryRegime.NEUTRAL:
        return TrendState.RANGE
    if primary_regime == PrimaryRegime.BEAR:
        if tactical_state == TacticalState.REBOUND:
            return TrendState.DOWN_REBOUND
        return TrendState.DOWN_STRONG
    return TrendState.UNKNOWN


def classify_trend_components(
    close: float,
    short_upper_prev: float,
    short_lower_prev: float,
    long_upper_prev: float,
    long_lower_prev: float,
    short_mid_prev: float = np.nan,
    long_mid_prev: float = np.nan,
    short_mid_slope: float = np.nan,
    long_mid_slope: float = np.nan,
    previous_20_high: float = np.nan,
) -> Tuple[PrimaryRegime, TacticalState, TrendState]:
    """根据 T 日收盘价与 T-1 轨线判定趋势核心状态（无未来函数）。"""
    vals = (short_upper_prev, short_lower_prev, long_upper_prev, long_lower_prev)
    if any(pd.isna(v) for v in vals) or pd.isna(close):
        return PrimaryRegime.UNKNOWN, TacticalState.NORMAL, TrendState.UNKNOWN

    has_long_mid = pd.notna(long_mid_prev) and pd.notna(long_mid_slope)
    has_short_mid = pd.notna(short_mid_prev) and pd.notna(short_mid_slope)
    long_mid_up = has_long_mid and close > long_mid_prev and long_mid_slope > 0
    long_mid_down = has_long_mid and close < long_mid_prev and long_mid_slope <= 0
    hard_down = (
        close < long_lower_prev
        or (close < short_lower_prev and pd.notna(long_mid_prev) and close < long_mid_prev)
    )

    if long_mid_up:
        primary = PrimaryRegime.BULL
    elif hard_down or (long_mid_down and (not has_short_mid or close < short_mid_prev)):
        primary = PrimaryRegime.BEAR
    else:
        primary = PrimaryRegime.NEUTRAL

    if primary == PrimaryRegime.BULL:
        breakout_20 = pd.notna(previous_20_high) and close > previous_20_high
        short_mid_up = has_short_mid and close > short_mid_prev and short_mid_slope > 0
        if breakout_20:
            tactical = TacticalState.ACCELERATION
        elif short_mid_up:
            tactical = TacticalState.NORMAL
        elif pd.notna(short_mid_prev) and close < short_mid_prev:
            tactical = TacticalState.PULLBACK
        else:
            tactical = TacticalState.WEAK
    elif primary == PrimaryRegime.BEAR:
        rebound = (
            (has_short_mid and close > short_mid_prev and short_mid_slope > 0)
            or close > short_upper_prev
        )
        tactical = TacticalState.REBOUND if rebound else TacticalState.RISK
    elif primary == PrimaryRegime.NEUTRAL:
        tactical = TacticalState.NORMAL
    else:
        tactical = TacticalState.NORMAL

    return primary, tactical, _legacy_trend_state(primary, tactical)


def classify_trend_state(
    close: float,
    short_upper_prev: float,
    short_lower_prev: float,
    long_upper_prev: float,
    long_lower_prev: float,
    short_mid_prev: float = np.nan,
    long_mid_prev: float = np.nan,
    short_mid_slope: float = np.nan,
    long_mid_slope: float = np.nan,
    previous_20_high: float = np.nan,
) -> TrendState:
    """兼容旧调用：返回 legacy trend_state。"""
    _, _, state = classify_trend_components(
        close,
        short_upper_prev,
        short_lower_prev,
        long_upper_prev,
        long_lower_prev,
        short_mid_prev=short_mid_prev,
        long_mid_prev=long_mid_prev,
        short_mid_slope=short_mid_slope,
        long_mid_slope=long_mid_slope,
        previous_20_high=previous_20_high,
    )
    return state


def _state_targets(
    primary_regime: PrimaryRegime,
    tactical_state: TacticalState,
    config: StrategyConfig,
) -> Tuple[float, float, float, str]:
    """返回 (base_target, cap, floor, reason)。"""
    if primary_regime == PrimaryRegime.BULL:
        if tactical_state == TacticalState.ACCELERATION:
            return 10.0, 10.0, 8.0, 'BULL/ACCELERATION：长中轨上行且突破前 20 根高点，趋势核心仓位满仓'
        if tactical_state == TacticalState.NORMAL:
            return 8.0, 10.0, 6.0, 'BULL/NORMAL：长中轨上行且站上短中轨，趋势核心仓位至少 8'
        if tactical_state == TacticalState.PULLBACK:
            return 6.0, 8.0, 6.0, 'BULL/PULLBACK：长中轨上行中的回调，趋势 floor 保持 6'
        return 6.0, 8.0, 4.0, 'BULL/WEAK：多头未破坏但短线偏弱，保持核心观察仓'
    if primary_regime == PrimaryRegime.NEUTRAL:
        return config.range_target_position, 6.0, 0.0, 'NEUTRAL：趋势不明，使用区间基准仓位'
    if primary_regime == PrimaryRegime.BEAR:
        if tactical_state == TacticalState.REBOUND:
            return 2.0, 4.0, 0.0, 'BEAR/REBOUND：下降趋势中的反弹，仅允许轻仓'
        return 0.0, 0.0, 0.0, 'BEAR/RISK：风险趋势，只有硬退出才允许降到 4 以下'
    return np.nan, 0.0, 0.0, '数据不足或冷启动'


def compute_trend_decision(
    df: pd.DataFrame,
    config: StrategyConfig = DEFAULT_CONFIG,
) -> pd.DataFrame:
    """逐日计算趋势层输出（含 base_target_position / trend_state / cap / floor）。"""
    ch = compute_channels(df, config)
    n = len(ch)

    states = []
    primary_regimes = []
    tactical_states = []
    bases = []
    caps = []
    floors = []
    reasons = []
    prev_positions = []
    transition_days_list = []
    qualities = []
    long_mid_bear_confirm_days = 0

    prev_position = np.nan
    transition_days = 0
    ch['previous_20_high'] = ch['High'].shift(1).rolling(20, min_periods=1).max()

    for i in range(n):
        row = ch.iloc[i]
        close = row['Close']
        sm_slope = np.nan
        lm_slope = np.nan

        if i == 0:
            sh_up_p = sh_lo_p = lg_up_p = lg_lo_p = np.nan
            sh_mid_p = lg_mid_p = np.nan
        else:
            prev = ch.iloc[i - 1]
            sh_up_p = prev['short_upper']
            sh_lo_p = prev['short_lower']
            lg_up_p = prev['long_upper']
            lg_lo_p = prev['long_lower']
            sh_mid_p = prev['short_mid']
            lg_mid_p = prev['long_mid']
            sm0, sm1 = ch['short_mid'].iloc[i - 1], ch['short_mid'].iloc[i]
            lm0, lm1 = ch['long_mid'].iloc[i - 1], ch['long_mid'].iloc[i]
            if pd.notna(sm0) and pd.notna(sm1) and sm0 != 0:
                sm_slope = (sm1 - sm0) / sm0
            if pd.notna(lm0) and pd.notna(lm1) and lm0 != 0:
                lm_slope = (lm1 - lm0) / lm0

        if (
            pd.notna(close)
            and pd.notna(lg_mid_p)
            and pd.notna(lm_slope)
            and close < lg_mid_p
            and lm_slope <= 0
        ):
            long_mid_bear_confirm_days += 1
        else:
            long_mid_bear_confirm_days = 0

        primary_regime, tactical_state, state = classify_trend_components(
            close, sh_up_p, sh_lo_p, lg_up_p, lg_lo_p,
            short_mid_prev=sh_mid_p,
            long_mid_prev=lg_mid_p,
            short_mid_slope=sm_slope,
            long_mid_slope=lm_slope,
            previous_20_high=row.get('previous_20_high', np.nan),
        )
        states.append(state.value)
        primary_regimes.append(primary_regime.value)
        tactical_states.append(tactical_state.value)

        if state == TrendState.UNKNOWN:
            base, cap, floor, reason = np.nan, 0.0, 0.0, '冷启动或轨线未就绪'
            transition_days = 0
        else:
            if primary_regime == PrimaryRegime.NEUTRAL:
                transition_days += 1
            else:
                transition_days = 0
            base, cap, floor, reason = _state_targets(primary_regime, tactical_state, config)

        prev_positions.append(prev_position)
        bases.append(base)
        caps.append(cap)
        floors.append(floor)
        reasons.append(reason)
        transition_days_list.append(transition_days)

        if not pd.isna(base):
            prev_position = base

        width_pct = np.nan
        if pd.notna(row['short_upper']) and pd.notna(row['short_lower']) and close:
            width_pct = (row['short_upper'] - row['short_lower']) / close
        qualities.append({
            'short_mid_slope': sm_slope,
            'long_mid_slope': lm_slope,
            'channel_width_pct': width_pct,
            'atr_pct': row.get('atr_pct', np.nan),
            'long_mid_bear_confirm_days': long_mid_bear_confirm_days,
        })

    out = ch.copy()
    out['trend_state'] = states
    out['primary_regime'] = primary_regimes
    out['tactical_state'] = tactical_states
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
    out['short_mid_prev'] = out['short_mid'].shift(1)
    out['long_mid_prev'] = out['long_mid'].shift(1)
    out['atr_stop_line_prev'] = out['atr_stop_line'].shift(1)

    out['short_mid_slope'] = [q['short_mid_slope'] for q in qualities]
    out['long_mid_slope'] = [q['long_mid_slope'] for q in qualities]
    out['channel_width_pct'] = [q['channel_width_pct'] for q in qualities]
    out['long_mid_bear_confirm_days'] = [
        q['long_mid_bear_confirm_days'] for q in qualities
    ]

    # 向后兼容：position = 趋势层 base_target_position
    out['position'] = out['base_target_position']

    return out


class TrendChannel:
    """Trend channel helper."""

    def __init__(
        self,
        short_period: int = 26,
        long_period: int = 90,
        config: Optional[StrategyConfig] = None,
    ):
        self.short_period = short_period
        self.long_period = long_period
        self.config = config or StrategyConfig(
            short_period=short_period,
            long_period=long_period,
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
