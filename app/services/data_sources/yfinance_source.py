from __future__ import annotations

import io
import logging
import sys

import pandas as pd
import yfinance as yf

from app.config import Config
from app.services.data_sources.base import FetchRequest, MarketDataSource
from app.services.data_sources.utils import yfinance_ticker

logger = logging.getLogger(__name__)


class YFinanceDataSource(MarketDataSource):
    name = 'yfinance'

    def fetch_5m(self, request: FetchRequest) -> pd.DataFrame:
        ticker_symbol = yfinance_ticker(request.market, request.stock_code)
        logger.info(f"yfinance 拉取: {ticker_symbol} interval=5m")
        ticker = yf.Ticker(ticker_symbol)

        old_stderr = sys.stderr
        stderr_buf = io.StringIO()
        sys.stderr = stderr_buf
        try:
            if request.start_date or request.end_date:
                kwargs = {'interval': '5m'}
                if request.start_date:
                    kwargs['start'] = pd.Timestamp(request.start_date).strftime('%Y-%m-%d')
                if request.end_date:
                    kwargs['end'] = pd.Timestamp(request.end_date).strftime('%Y-%m-%d')
                df = ticker.history(**kwargs)
            else:
                days = int(request.history_days or Config.INITIAL_5MIN_HISTORY_DAYS)
                df = ticker.history(period=f'{days}d', interval='5m')
        finally:
            sys.stderr = old_stderr
            stderr_content = stderr_buf.getvalue()
            if stderr_content.strip():
                logger.warning(f"yfinance stderr ({ticker_symbol}): {stderr_content.strip()}")

        if df is None or df.empty:
            logger.warning(f"yfinance 返回空数据: {ticker_symbol}")
            return pd.DataFrame()
        logger.info(f"yfinance 获取 {ticker_symbol} 数据 {len(df)} 行")
        return df
