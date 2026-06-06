from __future__ import annotations

import logging

import pandas as pd

from app.config import Config
from app.services.data_sources.akshare_source import AkshareDataSource
from app.services.data_sources.base import FetchRequest, MarketDataSource
from app.services.data_sources.itick import ITickDataSource
from app.services.data_sources.yfinance_source import YFinanceDataSource

logger = logging.getLogger(__name__)

_SOURCES: dict[str, MarketDataSource] = {
    'itick': ITickDataSource(),
    'akshare': AkshareDataSource(),
    'yfinance': YFinanceDataSource(),
}


def active_data_source() -> MarketDataSource:
    return _SOURCES.get(Config.DATA_SOURCE, _SOURCES['itick'])


def _fallback_chain(primary: str, market: str) -> list[MarketDataSource]:
    order = {
        'itick': ['itick', 'akshare', 'yfinance'],
        'akshare': ['akshare', 'yfinance'],
        'yfinance': ['yfinance'],
    }.get(primary, ['itick', 'akshare', 'yfinance'])
    sources = []
    for name in order:
        source = _SOURCES[name]
        if source.supports(market):
            sources.append(source)
    return sources


def fetch_5m(request: FetchRequest) -> pd.DataFrame:
    if request.interval != '5min':
        logger.info(
            f"采集只支持 5min（请求 interval={request.interval} 将忽略，请在决策层用 resample 合成）"
        )
    errors = []
    for source in _fallback_chain(Config.DATA_SOURCE, request.market):
        try:
            if source.name == 'itick' and not Config.ITICK_TOKEN:
                raise RuntimeError('未配置 ITICK_TOKEN 或 ITICK_API_KEY')
            return source.fetch_5m(request)
        except Exception as exc:
            errors.append(f'{source.name}: {exc}')
            logger.warning(
                f"{source.name} 5m 失败 ({request.market}/{request.stock_code}): {exc}"
            )
    raise RuntimeError('; '.join(errors) if errors else '没有可用的数据源')


def estimate_api_usage(
    market: str,
    *,
    trading_days: int = None,
    rows: int = None,
    request_count: int = None,
    limit: int = None,
) -> dict:
    return active_data_source().estimate_api_usage(
        market,
        trading_days=trading_days,
        rows=rows,
        request_count=request_count,
        limit=limit,
    )
