"""5min OHLCV → daily / 60min / 90min / 120min 运行时合成。

设计要点：
- 采集层只保存最细粒度 5min 数据，决策时按需合成更高粒度，避免数据冗余。
- 按"交易日"分组，跨日不合并；日内按"K 线序号"切桶，跨午休按序号连续（A 股一天 48 根 5m，
  其中 24 根上午、24 根下午，第 25 根 5m 仍属于同一日的第 2 个 120min 桶）。
- OHLCV 聚合规则：Open=first, High=max, Low=min, Close=last, Volume=sum。
- 最后一根尾巴允许 < N 根 5m（如 90min 在 A 股一天会有一根 60m 大小的尾巴）。
"""
from __future__ import annotations

from typing import Optional

import pandas as pd


# 各 target_interval → 一个桶包含多少根 5min K 线
_BARS_PER_BUCKET = {
    '5min': 1,
    '60min': 12,
    '90min': 18,
    '120min': 24,
    'daily': None,   # None 表示 "全天 / 一桶"
}


def resample_ohlcv(df: pd.DataFrame, target_interval: str) -> pd.DataFrame:
    """把 5min OHLCV DataFrame 合成为 target_interval 的 OHLCV。

    Args:
        df: 索引为时间戳（建议带时区）的 5min K 线 DataFrame，至少含 Open/High/Low/Close/Volume。
        target_interval: 目标周期，必须是 `5min` / `60min` / `90min` / `120min` / `daily` 之一。

    Returns:
        合成后的 OHLCV DataFrame，索引为每个桶第一根 5m 的时间戳。
    """
    if target_interval not in _BARS_PER_BUCKET:
        raise ValueError(
            f'unsupported target_interval={target_interval}; '
            f'expected one of {sorted(_BARS_PER_BUCKET.keys())}'
        )

    if df is None or len(df) == 0:
        return df.iloc[0:0].copy() if df is not None else pd.DataFrame(
            columns=['Open', 'High', 'Low', 'Close', 'Volume']
        )

    df = df.sort_index()
    if target_interval == '5min':
        return df.copy()

    bars = _BARS_PER_BUCKET[target_interval]

    # 按交易日分组，跨日绝不合并
    # 注意：带时区索引取 .date 会按 UTC 折日，应转 local 时区。这里直接用索引的 normalize() 取
    # local "天"（即与索引同 timezone 的日期）。pandas 的 .date 会按索引时区取本地日期。
    grouped = df.groupby(df.index.date, sort=True)

    rows = []
    timestamps = []
    for _, day_df in grouped:
        n = len(day_df)
        if bars is None:
            # daily：全天一桶
            chunks = [day_df]
        else:
            chunks = [day_df.iloc[i:i + bars] for i in range(0, n, bars)]
        expected = bars if bars is not None else n
        for chunk in chunks:
            if len(chunk) == 0:
                continue
            partial = (bars is not None) and (len(chunk) < expected)
            rows.append({
                'Open': float(chunk['Open'].iloc[0]),
                'High': float(chunk['High'].max()),
                'Low': float(chunk['Low'].min()),
                'Close': float(chunk['Close'].iloc[-1]),
                'Volume': int(chunk['Volume'].sum()) if 'Volume' in chunk.columns else 0,
                'partial_bar': partial,
            })
            timestamps.append(chunk.index[0])

    out = pd.DataFrame(rows, index=pd.Index(timestamps, name=df.index.name))
    return out


def drop_partial_bars_for_trade(
    df: pd.DataFrame,
    period: str,
    config=None,
) -> pd.DataFrame:
    """剔除不参与交易确认的 partial K 线（默认 90min 末根尾巴）。

    展示用数据勿调用此函数；仅结构/决策确认路径使用。
    """
    from app.algos.config import DEFAULT_CONFIG

    cfg = config or DEFAULT_CONFIG
    if df is None or df.empty or 'partial_bar' not in df.columns:
        return df
    if period == '90min' and not cfg.allow_partial_90m_for_trade:
        return df.loc[~df['partial_bar'].astype(bool)].copy()
    return df
