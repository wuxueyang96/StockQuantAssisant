from __future__ import annotations

import importlib.util
import logging

import pandas as pd

from app.services.data_sources.base import FetchRequest, MarketDataSource
from app.services.data_sources.utils import format_fetch_window

logger = logging.getLogger(__name__)


def is_available() -> bool:
    return importlib.util.find_spec('akshare') is not None


def normalize_minute(df, tz: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={
        '时间': 'Date',
        '开盘': 'Open',
        '最高': 'High',
        '最低': 'Low',
        '收盘': 'Close',
        '成交量': 'Volume',
    })
    if 'Date' not in df.columns:
        return pd.DataFrame()
    df['Date'] = pd.to_datetime(df['Date'])
    try:
        df['Date'] = df['Date'].dt.tz_localize(tz)
    except TypeError:
        df['Date'] = df['Date'].dt.tz_convert(tz)
    df = df.set_index('Date')
    cols = ['Open', 'High', 'Low', 'Close']
    if 'Volume' in df.columns:
        cols.append('Volume')
    df = df[cols]
    if 'Volume' not in df.columns:
        df['Volume'] = 0
    df['Dividends'] = 0.0
    df['Stock Splits'] = 0.0
    return df


class AkshareDataSource(MarketDataSource):
    name = 'akshare'

    def supports(self, market: str) -> bool:
        return market in ('a', 'hk')

    def fetch_5m(self, request: FetchRequest) -> pd.DataFrame:
        if not is_available():
            raise RuntimeError('未安装 akshare')
        import akshare as ak

        start_date, end_date = format_fetch_window(
            request.history_days,
            request.start_date,
            request.end_date,
        )
        if request.market == 'a':
            if request.stock_code.startswith('5'):
                df = ak.fund_etf_hist_min_em(
                    symbol=request.stock_code,
                    period='5',
                    start_date=start_date,
                    end_date=end_date,
                    adjust='qfq',
                )
            else:
                df = ak.stock_zh_a_hist_min_em(
                    symbol=request.stock_code,
                    period='5',
                    start_date=start_date,
                    end_date=end_date,
                    adjust='qfq',
                )
            return normalize_minute(df, tz='Asia/Shanghai')

        if request.market == 'hk':
            df = ak.stock_hk_hist_min_em(
                symbol=request.stock_code,
                period='5',
                start_date=start_date,
                end_date=end_date,
                adjust='qfq',
            )
            return normalize_minute(df, tz='Asia/Hong_Kong')

        raise RuntimeError(f"akshare 不支持市场: {request.market}")
