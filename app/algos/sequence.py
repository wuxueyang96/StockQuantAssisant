"""九转序列（简化版 TD Setup），参见 docs/algorithm.md §三。

- 高 9 卖出：连续 9 根 `Close[i] > Close[i-4]`，强度确认 `max(High[8],H[9]) ≥ max(High[6],H[7])`。
- 低 9 买入：连续 9 根 `Close[i] < Close[i-4]`，强度确认 `min(Low[8],L[9]) ≤ min(Low[6],L[7])`。

事件 `high9_signal` / `low9_signal` 只在第 9 根 K 线为 True。`*_active` 在事件之后
保持 `effective_horizon` 根 K 线为 True，可被反向破位（高 9 下破/低 9 上破当时
9 根区间内极值）提前失效。

**序列层默认不改变战略仓位**，仅输出 execution_rules（执行纪律）。
"""
from __future__ import annotations

from typing import List, Sequence, Set

import pandas as pd

from app.algos.config import TrendState


class NineSequence:
    def __init__(self, effective_horizon: int = 5):
        self.effective_horizon = effective_horizon

    def evaluate(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        n = len(result)

        result['high9_count'] = 0
        result['low9_count'] = 0
        result['high9_signal'] = False
        result['low9_signal'] = False
        result['high9_active'] = False
        result['low9_active'] = False
        result['sequence_effective_until'] = pd.NaT

        if n < 9:
            return result

        high9_count = 0
        low9_count = 0

        # 失效跟踪
        high9_active_until = -1
        low9_active_until = -1
        high9_window_low = None      # 高 9 形成后跟踪的"9 区间内最低 Low"
        low9_window_high = None      # 低 9 形成后跟踪的"9 区间内最高 High"

        eff_until_idx: list = [None] * n
        H = result['High'].values
        L = result['Low'].values
        C = result['Close'].values
        idx = result.index

        for i in range(4, n):
            close_cur = C[i]
            close_prev4 = C[i - 4]

            if close_cur > close_prev4:
                high9_count += 1
            else:
                high9_count = 0

            if close_cur < close_prev4:
                low9_count += 1
            else:
                low9_count = 0

            result.at[idx[i], 'high9_count'] = high9_count
            result.at[idx[i], 'low9_count'] = low9_count

            # ---------------- 高 9 触发 ----------------
            if high9_count == 9:
                bar6, bar7, bar8, bar9 = i - 3, i - 2, i - 1, i
                high_89 = max(H[bar8], H[bar9])
                high_67 = max(H[bar6], H[bar7])
                if high_89 >= high_67:
                    result.at[idx[i], 'high9_signal'] = True
                    high9_active_until = i + self.effective_horizon
                    # 记录"9 区间内最低 Low"作为失效阈值
                    high9_window_low = float(min(L[i - 8:i + 1]))

            # ---------------- 低 9 触发 ----------------
            if low9_count == 9:
                bar6, bar7, bar8, bar9 = i - 3, i - 2, i - 1, i
                low_89 = min(L[bar8], L[bar9])
                low_67 = min(L[bar6], L[bar7])
                if low_89 <= low_67:
                    result.at[idx[i], 'low9_signal'] = True
                    low9_active_until = i + self.effective_horizon
                    low9_window_high = float(max(H[i - 8:i + 1]))

            # ---------------- 反向破位失效 ----------------
            if high9_window_low is not None and L[i] < high9_window_low and i > 0:
                # 已经被跌破，立即失效（i 之前若有 active，也终止于 i-1）
                if high9_active_until >= i:
                    high9_active_until = i - 1

            if low9_window_high is not None and H[i] > low9_window_high and i > 0:
                if low9_active_until >= i:
                    low9_active_until = i - 1

            # ---------------- active 列 ----------------
            if i <= high9_active_until:
                result.at[idx[i], 'high9_active'] = True
            if i <= low9_active_until:
                result.at[idx[i], 'low9_active'] = True

            far = max(high9_active_until, low9_active_until)
            if far >= i:
                eff_until_idx[i] = min(far, n - 1)

        # 把 effective_until 填成时间戳
        eff_until_series = []
        for j in eff_until_idx:
            eff_until_series.append(pd.NaT if j is None else idx[j])
        result['sequence_effective_until'] = eff_until_series

        return result


def is_near_historical_extreme(
    df: pd.DataFrame | None = None,
    *,
    close: Sequence[float] | pd.Series | None = None,
    high: Sequence[float] | pd.Series | None = None,
    low: Sequence[float] | pd.Series | None = None,
    lookback: int = 120,
    tolerance_pct: float = 0.03,
) -> bool:
    """判断最新收盘价是否接近 lookback 内历史高/低点（仅 probe / warning，不驱动交易）。

    可传入标准 OHLC DataFrame，或显式传入 close/high/low 序列；函数只读取截至
    最新一根的历史窗口，不参与交易确认，也不引入未来函数。
    """
    if df is not None:
        if len(df) < 2:
            return False
        close_s = df['Close']
        high_s = df['High']
        low_s = df['Low']
    else:
        if close is None or high is None or low is None:
            return False
        close_s = pd.Series(close)
        high_s = pd.Series(high)
        low_s = pd.Series(low)
        if len(close_s) < 2 or len(high_s) < 2 or len(low_s) < 2:
            return False

    n = min(lookback, len(close_s), len(high_s), len(low_s))
    if n < 2:
        return False
    close_window = close_s.iloc[-n:]
    high_window = high_s.iloc[-n:]
    low_window = low_s.iloc[-n:]
    close_value = float(close_window.iloc[-1])
    hi = float(high_window.max())
    lo = float(low_window.min())
    if hi <= 0 or lo <= 0:
        return False
    near_high = abs(close_value - hi) / hi <= tolerance_pct
    near_low = abs(close_value - lo) / lo <= tolerance_pct
    return near_high or near_low


def compute_sequence_execution_rules(
    *,
    trend_state: str,
    high9_active: bool,
    low9_active: bool,
    top_structure_active: bool = False,
    bottom_structure_active: bool = False,
) -> List[str]:
    """序列层执行纪律（**不修改** final_target_position）。"""
    rules: List[str] = []
    ts = trend_state

    if high9_active:
        rules.append('NO_CHASE')
        if ts == TrendState.UP_STRONG.value and top_structure_active:
            rules.append('TRIM_ALLOWED')
        if ts in (TrendState.DOWN_REBOUND.value, TrendState.DOWN_STRONG.value):
            rules.append('REBOUND_SELL_CANDIDATE')

    if low9_active:
        rules.append('NO_PANIC_SELL')
        if ts in (TrendState.UP_STRONG.value, TrendState.UP_PULLBACK.value) and bottom_structure_active:
            rules.append('PULLBACK_BUY_CANDIDATE')
        if ts == TrendState.DOWN_STRONG.value:
            rules.append('BOTTOM_WATCH')

    # 去重保序
    seen: Set[str] = set()
    out: List[str] = []
    for r in rules:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out
