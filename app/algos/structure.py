"""MACD 结构量化（参见 docs/algorithm.md §二 / docs/design.md §3.5）。

状态机（顶部，底部对称）：
    normal
      └─ [价格创窗口新高 + DIF 未创窗口新高] ─▶ top_divergence
            │      （peak_dif 每根 K 线都尝试更新，保留正负号）
            │      [价格再创新高 且 DIF 也再创新高] ─▶ 钝化破坏 → normal
            │
            └─ [DIF 连续 K 根下行 且 DIF < peak_dif] ─▶ top_75
                  │
                  └─ [上一根 DIF ≥ DEA 且 当根 DIF < DEA] ─▶ top_100
                        │
                        └─ 100% 触发当根记录后立即 reset → normal
"""
from __future__ import annotations

import math
from datetime import timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.algos.config import (
    DEFAULT_CONFIG,
    STRUCTURE_INTERVAL_ORDER,
    STRUCTURE_PERIOD_PRIORITY,
    STRUCTURE_RESONANCE_INTERVALS,
    StrategyConfig,
    StructureBias,
    StructureState,
    TrendState,
    _STRUCTURE_ENUM_SCORE,
)


def strictly_greater(a: float, b: float, eps: float = 0.001) -> bool:
    """带符号的相对阈值比较：a 严格大于 b。

    `(a - b) > eps × max(|a|, |b|, 1e-6)`。对负数与小数 DIF 都安全，弃用原
    "数量级前两位取整"规则（该规则对负值与 |DIF|<1 存在语义反转和除零问题）。

    `eps` 是过滤浮点/数值扰动用的相对容差，默认 0.001（0.1%）。算法文档把
    它写成 2% 的上限语义，但实际只用作"避免假相等"，过大会让稳定上涨场景下
    合理增长被误判为钝化，因此默认取小值。
    """
    if a is None or b is None:
        return False
    try:
        if math.isnan(a) or math.isnan(b):
            return False
    except (TypeError, ValueError):
        return False
    return (a - b) > eps * max(abs(a), abs(b), 1e-6)


def _magnitude_prefix(value: float, scale: int = None) -> int:
    """已弃用：保留以维持兼容性。仅供旧测试与可视化使用，状态机不再依赖。

    取数值数量级的前两位数字，例如 168.93 → digits=3, scale=1 → 16。
    """
    if value == 0 or np.isnan(value) or np.isinf(value):
        return 0
    abs_val = abs(value)
    if scale is None:
        digits = len(str(int(abs_val)))
        scale = max(0, digits - 2)
    return int(abs_val / 10 ** scale)


class MACDStructure:
    def __init__(
        self,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
        lookback: int = 50,
        smooth_k: int = 2,
        eps: float = 0.001,
        effective_horizon: int = 5,
    ):
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self.lookback = lookback
        self.smooth_k = smooth_k
        self.eps = eps
        self.effective_horizon = effective_horizon
        self.alpha_fast = 2.0 / (fast + 1)
        self.alpha_slow = 2.0 / (slow + 1)
        self.alpha_diff = self.alpha_fast - self.alpha_slow

    # ------------------------------------------------------------------ MACD
    def compute_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        ema_fast = df['Close'].ewm(span=self.fast, adjust=False).mean()
        ema_slow = df['Close'].ewm(span=self.slow, adjust=False).mean()
        result['dif'] = ema_fast - ema_slow
        result['dea'] = result['dif'].ewm(span=self.signal, adjust=False).mean()
        result['macd_hist'] = 2 * (result['dif'] - result['dea'])
        result['_ema_fast'] = ema_fast
        result['_ema_slow'] = ema_slow
        return result

    # ------------------------------------------------------------------ helpers
    def _greater(self, a: float, b: float) -> bool:
        return strictly_greater(a, b, eps=self.eps)

    def _less(self, a: float, b: float) -> bool:
        """a 严格小于 b（带符号相对阈值）。"""
        return strictly_greater(b, a, eps=self.eps)

    # ------------------------------------------------------------------ main
    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        result = self.compute_macd(df)
        n = len(result)

        # 初始化输出列
        for col in (
            'top_divergence', 'bottom_divergence',
            'top_structure_75', 'top_structure_100',
            'bottom_structure_75', 'bottom_structure_100',
            'top_structure_active', 'bottom_structure_active',
        ):
            result[col] = False
        result['top_structure_level'] = 0.0
        result['bottom_structure_level'] = 0.0
        result['structure_effective_until'] = pd.Series([pd.NaT] * n, index=result.index, dtype='object')
        result['top_structure_active_event'] = 'none'
        result['bottom_structure_active_event'] = 'none'
        result['top_structure_event_at'] = pd.Series([pd.NaT] * n, index=result.index, dtype='object')
        result['bottom_structure_event_at'] = pd.Series([pd.NaT] * n, index=result.index, dtype='object')
        result['top_structure_effective_until'] = pd.Series([pd.NaT] * n, index=result.index, dtype='object')
        result['bottom_structure_effective_until'] = pd.Series([pd.NaT] * n, index=result.index, dtype='object')

        # 数据不够直接返回
        min_bars = max(self.slow + self.signal + 2, 2)
        if n < min_bars:
            return append_structure_state_machine(
                result,
                horizon_bars=self.effective_horizon,
            )

        H = result['High'].values
        L = result['Low'].values
        DIF = result['dif'].values
        DEA = result['dea'].values
        idx = result.index

        # ------------------------------------------------ 顶部状态机
        top_state = 'normal'
        top_peak_price: Optional[float] = None
        top_peak_dif: Optional[float] = None
        top_div_start: Optional[int] = None
        top_decline_run = 0
        top_active_until = -1  # 截止 K 线 idx（含）
        top_active_event = 'none'
        top_active_event_at: Optional[int] = None

        # ------------------------------------------------ 底部状态机
        bot_state = 'normal'
        bot_valley_price: Optional[float] = None
        bot_valley_dif: Optional[float] = None
        bot_div_start: Optional[int] = None
        bot_rise_run = 0
        bot_active_until = -1
        bot_active_event = 'none'
        bot_active_event_at: Optional[int] = None

        eff_until_idx = [None] * n  # 记录每根 K 线在该时刻的有效期截止 idx

        for i in range(n):
            d = DIF[i]; a = DEA[i]; h = H[i]; l = L[i]
            if pd.isna(d) or pd.isna(a):
                # 仍要更新 active 列
                if i <= top_active_until:
                    result.at[result.index[i], 'top_structure_active'] = True
                    result.at[result.index[i], 'top_structure_active_event'] = top_active_event
                    if top_active_event_at is not None:
                        result.at[result.index[i], 'top_structure_event_at'] = idx[top_active_event_at]
                    result.at[result.index[i], 'top_structure_effective_until'] = idx[min(top_active_until, n - 1)]
                if i <= bot_active_until:
                    result.at[result.index[i], 'bottom_structure_active'] = True
                    result.at[result.index[i], 'bottom_structure_active_event'] = bot_active_event
                    if bot_active_event_at is not None:
                        result.at[result.index[i], 'bottom_structure_event_at'] = idx[bot_active_event_at]
                    result.at[result.index[i], 'bottom_structure_effective_until'] = idx[min(bot_active_until, n - 1)]
                continue
            prev_d = DIF[i - 1] if i > 0 else float('nan')
            prev_a = DEA[i - 1] if i > 0 else float('nan')

            # ---------------- 顶部逻辑 ----------------
            top_event = self._step_top(
                i, h, d, a, prev_d, prev_a, result,
                top_state, top_peak_price, top_peak_dif, top_div_start, top_decline_run,
            )
            (top_state, top_peak_price, top_peak_dif, top_div_start,
             top_decline_run, top_fired_at) = top_event
            if top_fired_at is not None:
                top_active_until = max(top_active_until, i + self.effective_horizon)
                top_active_event = (
                    'top_100' if bool(result.at[result.index[i], 'top_structure_100'])
                    else 'top_75'
                )
                top_active_event_at = i

            # ---------------- 底部逻辑 ----------------
            bot_event = self._step_bottom(
                i, l, d, a, prev_d, prev_a, result,
                bot_state, bot_valley_price, bot_valley_dif, bot_div_start, bot_rise_run,
            )
            (bot_state, bot_valley_price, bot_valley_dif, bot_div_start,
             bot_rise_run, bot_fired_at) = bot_event
            if bot_fired_at is not None:
                bot_active_until = max(bot_active_until, i + self.effective_horizon)
                bot_active_event = (
                    'bottom_100' if bool(result.at[result.index[i], 'bottom_structure_100'])
                    else 'bottom_75'
                )
                bot_active_event_at = i

            # ---------------- active 与有效期 ----------------
            if i <= top_active_until:
                result.at[result.index[i], 'top_structure_active'] = True
                result.at[result.index[i], 'top_structure_active_event'] = top_active_event
                if top_active_event_at is not None:
                    result.at[result.index[i], 'top_structure_event_at'] = idx[top_active_event_at]
                result.at[result.index[i], 'top_structure_effective_until'] = idx[min(top_active_until, n - 1)]
            if i <= bot_active_until:
                result.at[result.index[i], 'bottom_structure_active'] = True
                result.at[result.index[i], 'bottom_structure_active_event'] = bot_active_event
                if bot_active_event_at is not None:
                    result.at[result.index[i], 'bottom_structure_event_at'] = idx[bot_active_event_at]
                result.at[result.index[i], 'bottom_structure_effective_until'] = idx[min(bot_active_until, n - 1)]

            # 记录任一方向最远的有效期截止
            far_until = max(top_active_until, bot_active_until)
            if far_until >= i:
                eff_until_idx[i] = min(far_until, n - 1)

        # 把 effective_until 用时间戳填充
        eff_until_series = []
        for j in eff_until_idx:
            if j is None:
                eff_until_series.append(pd.NaT)
            else:
                eff_until_series.append(idx[j])
        result['structure_effective_until'] = eff_until_series

        return append_structure_state_machine(
            result,
            horizon_bars=self.effective_horizon,
        )

    # ------------------------------------------------------------------ top step
    def _step_top(self, i, h, d, a, prev_d, prev_a, result,
                  state, peak_price, peak_dif, div_start, decline_run):
        fired_at = None
        if state == 'normal':
            # 累积窗口：normal 期间也跟踪 peak_price / peak_dif
            if peak_price is None or h > peak_price:
                # price 创新高
                if peak_dif is None or self._greater(d, peak_dif):
                    # DIF 也创新高 → 同步更新极值，保持 normal
                    peak_price = h
                    peak_dif = d
                else:
                    # DIF 没创新高 → 进入钝化
                    state = 'top_divergence'
                    div_start = i
                    peak_price = h
                    decline_run = 0
                    result.at[result.index[i], 'top_divergence'] = True
            else:
                # price 没创新高，但要尝试更新 peak_dif（每根 K 线）
                if peak_dif is None or self._greater(d, peak_dif):
                    peak_dif = d

        elif state in ('top_divergence', 'top_75'):
            # 钝化窗口内：每根 K 线都尝试更新 peak_dif
            if peak_dif is None:
                peak_dif = d

            if h > peak_price:
                # price 创窗口新高
                if self._greater(d, peak_dif):
                    # DIF 也创新高 → 钝化破坏，回 normal
                    state = 'normal'
                    peak_price = h
                    peak_dif = d
                    div_start = None
                    decline_run = 0
                    return state, peak_price, peak_dif, div_start, decline_run, fired_at
                else:
                    # DIF 没创新高，钝化"事件"在这一根 K 线触发
                    peak_price = h
                    if state == 'top_divergence':
                        result.at[result.index[i], 'top_divergence'] = True
            else:
                # price 没创新高，仅尝试更新 peak_dif（不视为破坏）
                if self._greater(d, peak_dif):
                    peak_dif = d

            # ---- 判定 100%（优先级最高，可同根叠加 75%） ----
            if not pd.isna(prev_d) and not pd.isna(prev_a):
                if prev_d >= prev_a and d < a:
                    # DIF 由上而下穿过 DEA → 顶部 100% 完成
                    result.at[result.index[i], 'top_structure_100'] = True
                    # 若 75% 还没触发，则同根一并标记（顶部确认事件本身已含动能转向）
                    if state == 'top_divergence':
                        result.at[result.index[i], 'top_structure_75'] = True
                    if div_start is not None:
                        result.at[result.index[i], 'top_structure_level'] = float(i - div_start)
                    fired_at = i
                    # reset 回 normal，开始下一轮跟踪
                    state = 'normal'
                    peak_price = h
                    peak_dif = d
                    div_start = None
                    decline_run = 0
                    return state, peak_price, peak_dif, div_start, decline_run, fired_at

            # ---- 判定 75% ----
            if state == 'top_divergence':
                if not pd.isna(prev_d) and d < prev_d:
                    decline_run += 1
                else:
                    decline_run = 0
                if decline_run >= self.smooth_k and self._less(d, peak_dif):
                    state = 'top_75'
                    result.at[result.index[i], 'top_structure_75'] = True
                    fired_at = i

        return state, peak_price, peak_dif, div_start, decline_run, fired_at

    # ------------------------------------------------------------------ bottom step
    def _step_bottom(self, i, l, d, a, prev_d, prev_a, result,
                     state, valley_price, valley_dif, div_start, rise_run):
        fired_at = None
        if state == 'normal':
            if valley_price is None or l < valley_price:
                # price 创新低
                if valley_dif is None or self._less(d, valley_dif):
                    # DIF 也创新低 → 同步更新极值
                    valley_price = l
                    valley_dif = d
                else:
                    # DIF 没创新低 → 进入底部钝化
                    state = 'bottom_divergence'
                    div_start = i
                    valley_price = l
                    rise_run = 0
                    result.at[result.index[i], 'bottom_divergence'] = True
            else:
                # price 没创新低，仍要尝试更新 valley_dif（变得更负）
                if valley_dif is None or self._less(d, valley_dif):
                    valley_dif = d

        elif state in ('bottom_divergence', 'bottom_75'):
            if valley_dif is None:
                valley_dif = d

            if l < valley_price:
                # price 创窗口新低
                if self._less(d, valley_dif):
                    # DIF 也创新低 → 钝化破坏
                    state = 'normal'
                    valley_price = l
                    valley_dif = d
                    div_start = None
                    rise_run = 0
                    return state, valley_price, valley_dif, div_start, rise_run, fired_at
                else:
                    # DIF 没创新低 → 钝化"事件"在这一根 K 线触发
                    valley_price = l
                    if state == 'bottom_divergence':
                        result.at[result.index[i], 'bottom_divergence'] = True
            else:
                if self._less(d, valley_dif):
                    valley_dif = d

            # ---- 判定 100%（优先级最高，可同根叠加 75%） ----
            if not pd.isna(prev_d) and not pd.isna(prev_a):
                if prev_d <= prev_a and d > a:
                    result.at[result.index[i], 'bottom_structure_100'] = True
                    if state == 'bottom_divergence':
                        result.at[result.index[i], 'bottom_structure_75'] = True
                    if div_start is not None:
                        result.at[result.index[i], 'bottom_structure_level'] = float(i - div_start)
                    fired_at = i
                    state = 'normal'
                    valley_price = l
                    valley_dif = d
                    div_start = None
                    rise_run = 0
                    return state, valley_price, valley_dif, div_start, rise_run, fired_at

            # ---- 判定 75% ----
            if state == 'bottom_divergence':
                if not pd.isna(prev_d) and d > prev_d:
                    rise_run += 1
                else:
                    rise_run = 0
                if rise_run >= self.smooth_k and self._greater(d, valley_dif):
                    state = 'bottom_75'
                    result.at[result.index[i], 'bottom_structure_75'] = True
                    fired_at = i

        return state, valley_price, valley_dif, div_start, rise_run, fired_at

    # ------------------------------------------------------------------ thresholds
    def next_period_thresholds(self, df: pd.DataFrame) -> dict:
        """返回下一周期结构量化标准（触发各类结构信号的收盘价阈值）。

        基于 DIF = A + B × C 的线性关系反推：
            A = EMA_fast_cur × (1 - αf) - EMA_slow_cur × (1 - αs)
            B = αf - αs（正）
            C_next = (DIF_target - A) / B
        """
        computed = self.compute_macd(df)
        last = computed.iloc[-1]
        result = {
            'dif': round(float(last['dif']), 4) if pd.notna(last.get('dif')) else None,
            'dea': round(float(last['dea']), 4) if pd.notna(last.get('dea')) else None,
        }

        ema_fast = last.get('_ema_fast')
        ema_slow = last.get('_ema_slow')
        if pd.isna(ema_fast) or pd.isna(ema_slow):
            return result

        A = ema_fast * (1 - self.alpha_fast) - ema_slow * (1 - self.alpha_slow)
        B = self.alpha_diff
        if abs(B) < 1e-10:
            return result

        dea_cur = last['dea']
        if pd.notna(dea_cur):
            result['macd_dif_cross_dea_price'] = round(float((dea_cur - A) / B), 4)

        dif_cur = last['dif']
        if pd.notna(dif_cur):
            result['macd_dif_turn_price'] = round(float((dif_cur - A) / B), 4)

        return result


def _structure_enum_from_row(row: pd.Series) -> str:
    if bool(row.get('top_structure_100')):
        return 'top_100'
    if bool(row.get('top_structure_75')):
        return 'top_75'
    if bool(row.get('bottom_structure_100')):
        return 'bottom_100'
    if bool(row.get('bottom_structure_75')):
        return 'bottom_75'
    top_active = _top_structure_enum_from_row(row)
    bot_active = _bottom_structure_enum_from_row(row)
    candidates = [e for e in (top_active, bot_active) if e != 'none']
    if candidates:
        candidates.sort(key=lambda e: _STRUCTURE_ENUM_SCORE.get(e, 0), reverse=True)
        return candidates[0]
    return 'none'


def _event_direction(enum_val: str) -> Optional[str]:
    if enum_val.startswith('top'):
        return 'top'
    if enum_val.startswith('bottom'):
        return 'bottom'
    return None


def append_structure_state_machine(
    df: pd.DataFrame,
    *,
    horizon_bars: int = 5,
    timeframe: str = '',
    short_period: int = 26,
    pivot_lookback: int = 5,
) -> pd.DataFrame:
    """Append first-stage structure state-machine diagnostics.

    The state columns are diagnostic only in phase 1. They do not imply a
    position adjustment unless a later layer explicitly consumes them.
    """
    result = df.copy()
    n = len(result)
    for col in (
        'top_structure_state', 'bottom_structure_state', 'structure_state',
        'top_structure_state_event', 'bottom_structure_state_event',
        'structure_event', 'structure_event_direction',
        'structure_event_timeframe',
    ):
        result[col] = (
            timeframe if col == 'structure_event_timeframe'
            else ('none' if col.endswith('event') or col in ('structure_event', 'structure_event_direction') else StructureState.NONE.value)
        )
    result['top_structure_warning_at'] = pd.Series([pd.NaT] * n, index=result.index, dtype='object')
    result['bottom_structure_warning_at'] = pd.Series([pd.NaT] * n, index=result.index, dtype='object')
    result['top_structure_reference_high'] = np.nan
    result['top_structure_pivot_low'] = np.nan
    result['bottom_structure_reference_low'] = np.nan
    result['bottom_structure_pivot_high'] = np.nan

    if n == 0:
        return result

    high = pd.to_numeric(result.get('High'), errors='coerce')
    low = pd.to_numeric(result.get('Low'), errors='coerce')
    close = pd.to_numeric(result.get('Close'), errors='coerce')
    pivot_low = low.shift(1).rolling(pivot_lookback, min_periods=1).min()
    pivot_high = high.shift(1).rolling(pivot_lookback, min_periods=1).max()
    short_mid_prev = (
        (high.rolling(short_period, min_periods=1).max()
         + low.rolling(short_period, min_periods=1).min()) / 2.0
    ).shift(1)

    top_active: Optional[dict] = None
    bottom_active: Optional[dict] = None
    horizon = max(1, int(horizon_bars or 1))

    for i, idx in enumerate(result.index):
        c = close.iloc[i]
        h = high.iloc[i]
        l = low.iloc[i]
        sm = short_mid_prev.iloc[i]
        top_event = 'none'
        bottom_event = 'none'

        if top_active is not None and i > top_active['start_i']:
            age = i - top_active['start_i']
            confirmed = (
                (pd.notna(c) and pd.notna(top_active['pivot_low']) and c < top_active['pivot_low'])
                or (pd.notna(c) and pd.notna(sm) and c < sm)
            )
            failed = (
                (pd.notna(h) and pd.notna(top_active['reference_high']) and h > top_active['reference_high'])
                or (pd.notna(c) and pd.notna(top_active['reference_high']) and c > top_active['reference_high'])
            )
            expired = age >= horizon
            if confirmed:
                result.at[idx, 'top_structure_state'] = StructureState.CONFIRMED.value
                top_event = 'TOP_CONFIRMED'
                top_active = None
            elif failed or (expired and pd.notna(c) and c > top_active['warning_close']):
                result.at[idx, 'top_structure_state'] = StructureState.FAILED.value
                top_event = 'TOP_FAILED'
                top_active = None
            elif expired:
                result.at[idx, 'top_structure_state'] = StructureState.EXPIRED.value
                top_event = 'TOP_EXPIRED'
                top_active = None
            else:
                result.at[idx, 'top_structure_state'] = StructureState.WARNING.value
                result.at[idx, 'top_structure_warning_at'] = top_active['warning_at']
                result.at[idx, 'top_structure_reference_high'] = top_active['reference_high']
                result.at[idx, 'top_structure_pivot_low'] = top_active['pivot_low']

        top_warning = (
            bool(result.at[idx, 'top_divergence']) if 'top_divergence' in result.columns else False
        ) or (
            bool(result.at[idx, 'top_structure_75']) if 'top_structure_75' in result.columns else False
        ) or (
            bool(result.at[idx, 'top_structure_100']) if 'top_structure_100' in result.columns else False
        )
        if top_active is None and top_event == 'none' and top_warning:
            ref_high = h if pd.notna(h) else c
            top_active = {
                'start_i': i,
                'warning_at': idx,
                'warning_close': c,
                'reference_high': ref_high,
                'pivot_low': pivot_low.iloc[i],
            }
            result.at[idx, 'top_structure_state'] = StructureState.WARNING.value
            result.at[idx, 'top_structure_warning_at'] = idx
            result.at[idx, 'top_structure_reference_high'] = ref_high
            result.at[idx, 'top_structure_pivot_low'] = pivot_low.iloc[i]
            top_event = 'TOP_WARNING'

        if bottom_active is not None and i > bottom_active['start_i']:
            age = i - bottom_active['start_i']
            confirmed = (
                (pd.notna(c) and pd.notna(bottom_active['pivot_high']) and c > bottom_active['pivot_high'])
                or (pd.notna(c) and pd.notna(sm) and c > sm)
            )
            failed = (
                (pd.notna(l) and pd.notna(bottom_active['reference_low']) and l < bottom_active['reference_low'])
                or (pd.notna(c) and pd.notna(bottom_active['reference_low']) and c < bottom_active['reference_low'])
            )
            expired = age >= horizon
            if confirmed:
                result.at[idx, 'bottom_structure_state'] = StructureState.CONFIRMED.value
                bottom_event = 'BOTTOM_CONFIRMED'
                bottom_active = None
            elif failed:
                result.at[idx, 'bottom_structure_state'] = StructureState.FAILED.value
                bottom_event = 'BOTTOM_FAILED'
                bottom_active = None
            elif expired:
                result.at[idx, 'bottom_structure_state'] = StructureState.EXPIRED.value
                bottom_event = 'BOTTOM_EXPIRED'
                bottom_active = None
            else:
                result.at[idx, 'bottom_structure_state'] = StructureState.WARNING.value
                result.at[idx, 'bottom_structure_warning_at'] = bottom_active['warning_at']
                result.at[idx, 'bottom_structure_reference_low'] = bottom_active['reference_low']
                result.at[idx, 'bottom_structure_pivot_high'] = bottom_active['pivot_high']

        bottom_warning = (
            bool(result.at[idx, 'bottom_divergence']) if 'bottom_divergence' in result.columns else False
        ) or (
            bool(result.at[idx, 'bottom_structure_75']) if 'bottom_structure_75' in result.columns else False
        ) or (
            bool(result.at[idx, 'bottom_structure_100']) if 'bottom_structure_100' in result.columns else False
        )
        if bottom_active is None and bottom_event == 'none' and bottom_warning:
            ref_low = l if pd.notna(l) else c
            bottom_active = {
                'start_i': i,
                'warning_at': idx,
                'warning_close': c,
                'reference_low': ref_low,
                'pivot_high': pivot_high.iloc[i],
            }
            result.at[idx, 'bottom_structure_state'] = StructureState.WARNING.value
            result.at[idx, 'bottom_structure_warning_at'] = idx
            result.at[idx, 'bottom_structure_reference_low'] = ref_low
            result.at[idx, 'bottom_structure_pivot_high'] = pivot_high.iloc[i]
            bottom_event = 'BOTTOM_WARNING'

        if top_event != 'none':
            result.at[idx, 'top_structure_state_event'] = top_event
        if bottom_event != 'none':
            result.at[idx, 'bottom_structure_state_event'] = bottom_event

        events = [e for e in (top_event, bottom_event) if e != 'none']
        if len(events) == 1:
            result.at[idx, 'structure_event'] = events[0]
            result.at[idx, 'structure_event_direction'] = events[0].split('_', 1)[0]
            result.at[idx, 'structure_state'] = events[0].split('_', 1)[1]
        elif len(events) > 1:
            result.at[idx, 'structure_event'] = '|'.join(events)
            result.at[idx, 'structure_event_direction'] = 'CONFLICT'
            result.at[idx, 'structure_state'] = 'CONFLICT'

    return result


def _round_stat(value: float) -> float:
    if value is None or pd.isna(value):
        return 0.0
    return round(float(value), 6)


def _aggregate_event_records(records: List[dict]) -> dict:
    if not records:
        return {
            'count': 0,
            'forward_return_1d': 0.0,
            'forward_return_3d': 0.0,
            'forward_return_5d': 0.0,
            'forward_return_10d': 0.0,
            'forward_return_20d': 0.0,
            'max_favorable_excursion': 0.0,
            'max_adverse_excursion': 0.0,
            'confirm_rate': 0.0,
            'fail_rate': 0.0,
            'expired_rate': 0.0,
        }
    out = {'count': len(records)}
    for key in (
        'forward_return_1d', 'forward_return_3d', 'forward_return_5d',
        'forward_return_10d', 'forward_return_20d',
        'max_favorable_excursion', 'max_adverse_excursion',
    ):
        vals = [r[key] for r in records if r.get(key) is not None and not pd.isna(r.get(key))]
        out[key] = _round_stat(float(np.mean(vals)) if vals else 0.0)
    out['confirm_rate'] = _round_stat(np.mean([1.0 if r.get('outcome') == 'CONFIRMED' else 0.0 for r in records]))
    out['fail_rate'] = _round_stat(np.mean([1.0 if r.get('outcome') == 'FAILED' else 0.0 for r in records]))
    out['expired_rate'] = _round_stat(np.mean([1.0 if r.get('outcome') == 'EXPIRED' else 0.0 for r in records]))
    return out


def _group_event_records(records: List[dict], group_key: str) -> dict:
    grouped: Dict[str, List[dict]] = {}
    for record in records:
        key = str(record.get(group_key) or 'UNKNOWN')
        grouped.setdefault(key, []).append(record)
    return {k: _aggregate_event_records(v) for k, v in sorted(grouped.items())}


def compute_structure_event_study(
    df: pd.DataFrame,
    *,
    timeframe: str = 'daily',
    trend_state_by_day: Optional[Dict[pd.Timestamp, str]] = None,
    config: StrategyConfig = DEFAULT_CONFIG,
    structure: Optional[MACDStructure] = None,
) -> dict:
    """Study forward returns after structure state-machine events."""
    if df is None or df.empty:
        return {
            'by_event': {},
            'grouped_by_trend_state': {},
            'grouped_by_timeframe': {},
        }

    evaluator = structure or MACDStructure(effective_horizon=config.structure_event_horizon_bars)
    ev = evaluator.evaluate(df)
    ev = append_structure_state_machine(
        ev,
        horizon_bars=config.structure_event_horizon_bars,
        timeframe=timeframe,
        short_period=config.short_period,
    )
    close = pd.to_numeric(ev['Close'], errors='coerce')
    high = pd.to_numeric(ev['High'], errors='coerce')
    low = pd.to_numeric(ev['Low'], errors='coerce')
    horizons = (1, 3, 5, 10, 20)
    records: List[dict] = []

    for i, idx in enumerate(ev.index):
        raw_events = str(ev.iloc[i].get('structure_event') or 'none')
        if raw_events == 'none':
            continue
        for event_name in raw_events.split('|'):
            if event_name == 'none' or event_name.endswith('_EXPIRED'):
                continue
            if not (
                event_name.endswith('_WARNING')
                or event_name.endswith('_CONFIRMED')
                or event_name.endswith('_FAILED')
            ):
                continue
            side, event_state = event_name.split('_', 1)
            entry = close.iloc[i]
            if pd.isna(entry) or entry == 0:
                continue
            record = {
                'event': event_name,
                'side': side,
                'event_state': event_state,
                'timeframe': timeframe,
                'trend_state': 'UNKNOWN',
                'outcome': event_state,
                'date': pd.Timestamp(idx).isoformat(),
            }
            if trend_state_by_day:
                day = pd.Timestamp(idx).normalize()
                record['trend_state'] = str(trend_state_by_day.get(day, 'UNKNOWN'))
            if event_state == 'WARNING':
                outcome = 'EXPIRED'
                max_j = min(len(ev) - 1, i + int(config.structure_event_horizon_bars))
                prefix = f'{side}_'
                for j in range(i + 1, max_j + 1):
                    candidate = str(ev.iloc[j].get('structure_event') or 'none')
                    if f'{prefix}CONFIRMED' in candidate:
                        outcome = 'CONFIRMED'
                        break
                    if f'{prefix}FAILED' in candidate:
                        outcome = 'FAILED'
                        break
                    if f'{prefix}EXPIRED' in candidate:
                        outcome = 'EXPIRED'
                        break
                record['outcome'] = outcome

            for horizon in horizons:
                j = i + horizon
                key = f'forward_return_{horizon}d'
                record[key] = (
                    float(close.iloc[j] / entry - 1.0)
                    if j < len(close) and pd.notna(close.iloc[j]) else np.nan
                )
            max_j = min(len(ev), i + 21)
            future_high = high.iloc[i + 1:max_j]
            future_low = low.iloc[i + 1:max_j]
            if side == 'TOP':
                record['max_favorable_excursion'] = (
                    float(entry / future_low.min() - 1.0)
                    if len(future_low.dropna()) else np.nan
                )
                record['max_adverse_excursion'] = (
                    float(entry / future_high.max() - 1.0)
                    if len(future_high.dropna()) else np.nan
                )
            else:
                record['max_favorable_excursion'] = (
                    float(future_high.max() / entry - 1.0)
                    if len(future_high.dropna()) else np.nan
                )
                record['max_adverse_excursion'] = (
                    float(future_low.min() / entry - 1.0)
                    if len(future_low.dropna()) else np.nan
                )
            records.append(record)

    by_event: Dict[str, List[dict]] = {}
    for record in records:
        by_event.setdefault(record['event'], []).append(record)

    return {
        'by_event': {k: _aggregate_event_records(v) for k, v in sorted(by_event.items())},
        'grouped_by_trend_state': _group_event_records(records, 'trend_state'),
        'grouped_by_timeframe': _group_event_records(records, 'timeframe'),
    }


def _top_structure_enum_from_row(row: pd.Series) -> str:
    """读取顶部结构事件；若当前非触发当根，则使用 active 期内保留的事件类型。"""
    if bool(row.get('top_structure_100')):
        return 'top_100'
    if bool(row.get('top_structure_75')):
        return 'top_75'
    active_event = row.get('top_structure_active_event')
    if bool(row.get('top_structure_active')) and active_event in ('top_100', 'top_75'):
        return str(active_event)
    return 'none'


def _bottom_structure_enum_from_row(row: pd.Series) -> str:
    """读取底部结构事件；若当前非触发当根，则使用 active 期内保留的事件类型。"""
    if bool(row.get('bottom_structure_100')):
        return 'bottom_100'
    if bool(row.get('bottom_structure_75')):
        return 'bottom_75'
    active_event = row.get('bottom_structure_active_event')
    if bool(row.get('bottom_structure_active')) and active_event in ('bottom_100', 'bottom_75'):
        return str(active_event)
    return 'none'


def _trading_valid_until(
    event_ts: pd.Timestamp,
    period: str,
    config: StrategyConfig,
) -> pd.Timestamp:
    """按交易日天数估算结构对次日计划的有效截止（日频决策用）。"""
    days = config.structure_valid_days_by_period.get(period, 1)
    return pd.Timestamp(event_ts) + timedelta(days=days)


def aggregate_structure_context(
    struct_by_period: Dict[str, pd.Series],
    as_of: pd.Timestamp,
    config: StrategyConfig = DEFAULT_CONFIG,
) -> dict:
    """多周期结构整合：周期越大优先级越高（daily > 120 > 90 > 60）。

    仅统计 trading_valid_until >= as_of 的 active 结构。可用于交易确认。
    """
    active_periods: List[str] = []
    top_events: List[Tuple[str, str, int]] = []
    bot_events: List[Tuple[str, str, int]] = []

    for period in STRUCTURE_INTERVAL_ORDER:
        row = struct_by_period.get(period)
        if row is None:
            continue
        top_enum = _top_structure_enum_from_row(row)
        bot_enum = _bottom_structure_enum_from_row(row)
        top_a = top_enum != 'none' and bool(row.get('top_structure_active'))
        bot_a = bot_enum != 'none' and bool(row.get('bottom_structure_active'))

        top_until = row.get('top_trading_valid_until', row.get('trading_valid_until'))
        bot_until = row.get('bottom_trading_valid_until', row.get('trading_valid_until'))
        if top_a and top_until is not None and pd.notna(top_until):
            top_a = pd.Timestamp(top_until) >= pd.Timestamp(as_of)
        if bot_a and bot_until is not None and pd.notna(bot_until):
            bot_a = pd.Timestamp(bot_until) >= pd.Timestamp(as_of)

        if not top_a and not bot_a:
            continue
        active_periods.append(period)
        if top_a:
            top_events.append((period, top_enum, _STRUCTURE_ENUM_SCORE[top_enum]))
        if bot_a:
            bot_events.append((period, bot_enum, _STRUCTURE_ENUM_SCORE[bot_enum]))

    def _pick_best(events: List[Tuple[str, str, int]]) -> Tuple[str, str]:
        if not events:
            return 'none', ''
        events.sort(
            key=lambda x: (x[2], STRUCTURE_PERIOD_PRIORITY.get(x[0], 0)),
            reverse=True,
        )
        return events[0][1], events[0][0]

    strongest_top, _ = _pick_best(top_events)
    strongest_bot, _ = _pick_best(bot_events)

    if strongest_top != 'none' and strongest_bot != 'none':
        strongest_event = f'conflict:{strongest_top}/{strongest_bot}'
    elif strongest_top != 'none':
        strongest_event = strongest_top
    elif strongest_bot != 'none':
        strongest_event = strongest_bot
    else:
        strongest_event = 'none'

    highest_tf = 'none'
    highest_period = ''
    best_pri = -1
    for period in STRUCTURE_INTERVAL_ORDER:
        if period not in active_periods:
            continue
        row = struct_by_period.get(period)
        if row is None:
            continue
        candidates = [
            _top_structure_enum_from_row(row),
            _bottom_structure_enum_from_row(row),
        ]
        candidates = [en for en in candidates if en != 'none']
        if not candidates:
            continue
        candidates.sort(key=lambda en: _STRUCTURE_ENUM_SCORE.get(en, 0), reverse=True)
        en = candidates[0]
        pri = STRUCTURE_PERIOD_PRIORITY.get(period, 0)
        if pri > best_pri:
            best_pri = pri
            highest_tf = en
            highest_period = period
    highest_timeframe_event = f'{highest_period}:{highest_tf}' if highest_period else 'none'

    res_periods = [p for p in STRUCTURE_RESONANCE_INTERVALS if p in active_periods]
    k = len(res_periods)
    resonance_weight = 2.0 if k >= 3 else (1.5 if k == 2 else (1.0 if k == 1 else 0.0))

    return {
        'active_periods': active_periods,
        'strongest_event': strongest_event,
        'highest_timeframe_event': highest_timeframe_event,
        'resonance_count': k,
        'resonance_weight': resonance_weight,
        'resonance_periods': res_periods,
        'top_active': len(top_events) > 0,
        'bottom_active': len(bot_events) > 0,
        'has_conflict': strongest_top != 'none' and strongest_bot != 'none',
    }


def compute_structure_adjustment(
    trend_state: str,
    struct_ctx: dict,
    config: StrategyConfig = DEFAULT_CONFIG,
) -> Tuple[int, StructureBias, List[str]]:
    """结构层修边：仅在趋势 cap/floor 内微调，不反转趋势方向。"""
    warnings: List[str] = []

    if struct_ctx.get('has_conflict'):
        return 0, StructureBias.CONFLICT, ['CONFLICT_WARNING']

    top_active = struct_ctx.get('top_active', False)
    bot_active = struct_ctx.get('bottom_active', False)
    ts = trend_state
    max_adj = config.max_structure_adjustment

    if not top_active and not bot_active:
        return 0, StructureBias.NEUTRAL, warnings

    adj = 0

    if top_active:
        if ts == TrendState.UP_STRONG.value:
            adj = -min(2, max_adj)
            warnings.extend(['NO_CHASE', 'TRIM_ALLOWED'])
        elif ts == TrendState.UP_PULLBACK.value:
            adj = -1
            warnings.append('NO_CHASE')
        elif ts == TrendState.UP_WEAK.value:
            adj = -min(2, max_adj)
            warnings.extend(['NO_CHASE', 'TRIM_ALLOWED'])
        elif ts in (TrendState.DOWN_REBOUND.value, TrendState.DOWN_STRONG.value):
            adj = -1
            warnings.append('SELL_PRIORITY')
        elif ts == TrendState.RANGE.value:
            adj = -1
            warnings.append('RANGE_UPPER_EDGE')

    if bot_active:
        if ts in (TrendState.UP_STRONG.value, TrendState.UP_PULLBACK.value, TrendState.UP_WEAK.value):
            bot_adj = min(2, max_adj)
            adj = max(adj, bot_adj) if adj > 0 else bot_adj
            if ts in (TrendState.UP_PULLBACK.value, TrendState.UP_WEAK.value):
                warnings.append('PULLBACK_BUY_CANDIDATE')
        elif ts == TrendState.DOWN_STRONG.value:
            warnings.extend(['NO_PANIC_SELL', 'BOTTOM_WATCH'])
        elif ts == TrendState.DOWN_REBOUND.value:
            bot_adj = min(1, max_adj)
            adj = max(adj, bot_adj) if adj > 0 else bot_adj

    adj = int(np.clip(adj, -max_adj, max_adj))

    if top_active and bot_active:
        return 0, StructureBias.CONFLICT, ['CONFLICT_WARNING']

    if adj > 0:
        bias = StructureBias.BULLISH_EDGE
    elif adj < 0:
        bias = StructureBias.BEARISH_EDGE
    else:
        bias = StructureBias.NEUTRAL

    return adj, bias, warnings


def enrich_structure_row_with_trading_validity(
    row: pd.Series,
    period: str,
    config: StrategyConfig = DEFAULT_CONFIG,
) -> pd.Series:
    """为结构末行补充 trading_valid_until（日频决策用）。"""
    row = row.copy()
    fallback_idx = row.name

    def _event_ts(field: str) -> Optional[pd.Timestamp]:
        v = row.get(field)
        if v is not None and pd.notna(v):
            return pd.Timestamp(v)
        if fallback_idx is not None:
            return pd.Timestamp(fallback_idx)
        return None

    valid_untils = []
    if bool(row.get('top_structure_active')):
        ts = _event_ts('top_structure_event_at')
        if ts is not None:
            row['top_trading_valid_until'] = _trading_valid_until(ts, period, config)
            valid_untils.append(row['top_trading_valid_until'])
    if bool(row.get('bottom_structure_active')):
        ts = _event_ts('bottom_structure_event_at')
        if ts is not None:
            row['bottom_trading_valid_until'] = _trading_valid_until(ts, period, config)
            valid_untils.append(row['bottom_trading_valid_until'])

    if valid_untils:
        row['trading_valid_until'] = max(valid_untils)
    elif fallback_idx is not None:
        row['trading_valid_until'] = _trading_valid_until(pd.Timestamp(fallback_idx), period, config)
    return row
