from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.config import Config


def format_fetch_window(history_days: int = None, start_date=None, end_date=None) -> tuple[str, str]:
    days = int(history_days or Config.INITIAL_5MIN_HISTORY_DAYS)
    end = pd.Timestamp(end_date) if end_date else pd.Timestamp(datetime.now())
    start = pd.Timestamp(start_date) if start_date else end - pd.DateOffset(days=days)
    return start.strftime('%Y-%m-%d %H:%M:%S'), end.strftime('%Y-%m-%d %H:%M:%S')


def fetch_window_bounds(history_days: int = None, start_date=None, end_date=None) -> tuple[pd.Timestamp, pd.Timestamp]:
    days = int(history_days or Config.INITIAL_5MIN_HISTORY_DAYS)
    end = pd.Timestamp(end_date) if end_date else pd.Timestamp(datetime.now())
    start = pd.Timestamp(start_date) if start_date else end - pd.DateOffset(days=days)
    return start, end


def chunk_windows(start, end, chunk_days: int) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if start_ts > end_ts:
        return []
    chunk_days = max(1, int(chunk_days))
    windows = []
    cursor = start_ts.normalize()
    while cursor <= end_ts.normalize():
        day_end = min(cursor + pd.DateOffset(days=chunk_days - 1), end_ts.normalize())
        chunk_start = max(cursor, start_ts)
        chunk_end = min(day_end + pd.Timedelta(hours=23, minutes=59, seconds=59), end_ts)
        windows.append((chunk_start, chunk_end))
        cursor = day_end + pd.DateOffset(days=1)
    return windows


def market_tz(market: str) -> str:
    return Config.TRADING_HOURS.get(market, {}).get('tz', 'UTC')


def filter_regular_session(df: pd.DataFrame, market: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    trading_config = Config.TRADING_HOURS.get(market)
    if not trading_config:
        return df
    start = datetime.strptime(trading_config['start'], '%H:%M').time()
    end = datetime.strptime(trading_config['end'], '%H:%M').time()
    mask = [(start <= ts.time() <= end) for ts in df.index]
    return df.loc[mask]


def yfinance_ticker(market: str, stock_code: str) -> str:
    mapper = Config.YFINANCE_TICKER_MAP.get(market)
    if not mapper:
        raise ValueError(f"未知市场: {market}")
    return mapper(stock_code)
