from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class FetchRequest:
    market: str
    stock_code: str
    interval: str = '5min'
    history_days: int | None = None
    start_date: Any = None
    end_date: Any = None
    max_pages: int | None = None
    limit: int | None = None


class MarketDataSource:
    """Common interface for all market data providers."""

    name = 'base'
    free_mode = False
    refresh_limit: int | None = None

    def supports(self, market: str) -> bool:
        return market in ('a', 'hk', 'us')

    def fetch_5m(self, request: FetchRequest) -> pd.DataFrame:
        raise NotImplementedError

    def estimate_api_usage(
        self,
        market: str,
        *,
        trading_days: int = None,
        rows: int = None,
        request_count: int = None,
        limit: int = None,
    ) -> dict:
        return {
            'data_source': self.name,
            'free_mode': bool(self.free_mode),
            'market': market,
            'request_count': int(request_count or 1),
            'min_interval_seconds': 0.0,
            'estimated_seconds': 0.0,
        }
