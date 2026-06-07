from __future__ import annotations

import json
import logging
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from app.config import Config
from app.services.data_sources.base import FetchRequest, MarketDataSource
from app.services.data_sources.utils import fetch_window_bounds, filter_regular_session, market_tz

logger = logging.getLogger(__name__)

_rate_lock = threading.Lock()
_next_request_at = 0.0


class ITickRateLimitError(RuntimeError):
    """Raised when iTick rejects requests because the token is rate limited."""


def region_code(market: str, stock_code: str) -> tuple[str, str]:
    if market == 'a':
        region = 'SH' if stock_code.startswith(('5', '6')) else 'SZ'
        return region, stock_code
    if market == 'hk':
        code = stock_code.lstrip('0') or '0'
        return 'HK', code
    if market == 'us':
        return 'US', stock_code.upper()
    raise ValueError(f"未知市场: {market}")


def bars_per_trading_day(market: str) -> int:
    return 48 if market == 'a' else 78


def target_rows(market: str, history_days: int = None, start_date=None, end_date=None) -> int:
    if history_days is not None:
        days = int(history_days)
    elif start_date or end_date:
        start, end = fetch_window_bounds(history_days, start_date, end_date)
        days = max(1, int((end.normalize() - start.normalize()).days) + 1)
    else:
        days = int(Config.INITIAL_5MIN_HISTORY_DAYS)
    return max(bars_per_trading_day(market), days * bars_per_trading_day(market))


def apply_free_rate_limit():
    global _next_request_at
    min_interval = max(0.0, float(Config.ITICK_FREE_MIN_INTERVAL_SECONDS))
    if min_interval <= 0:
        return
    with _rate_lock:
        now = time.monotonic()
        wait_seconds = _next_request_at - now
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        _next_request_at = time.monotonic() + min_interval


def normalize_kline(items, market: str) -> pd.DataFrame:
    if not items:
        return pd.DataFrame()
    rows = []
    for item in items:
        ts = item.get('t')
        if ts is None:
            continue
        rows.append({
            'Date': pd.to_datetime(int(ts), unit='ms', utc=True).tz_convert(market_tz(market)),
            'Open': item.get('o'),
            'High': item.get('h'),
            'Low': item.get('l'),
            'Close': item.get('c'),
            'Volume': item.get('v', 0),
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).dropna(subset=['Open', 'High', 'Low', 'Close'])
    if df.empty:
        return pd.DataFrame()
    df = df.drop_duplicates(subset=['Date'], keep='last').sort_values('Date')
    df = df.set_index('Date')
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=['Open', 'High', 'Low', 'Close'])
    df['Volume'] = df['Volume'].fillna(0)
    df['Dividends'] = 0.0
    df['Stock Splits'] = 0.0
    return filter_regular_session(
        df[['Open', 'High', 'Low', 'Close', 'Volume', 'Dividends', 'Stock Splits']],
        market,
    )


def normalize_daily_kline(items, market: str) -> pd.DataFrame:
    if not items:
        return pd.DataFrame()
    rows = []
    for item in items:
        ts = item.get('t')
        if ts is None:
            continue
        rows.append({
            'Date': pd.to_datetime(int(ts), unit='ms', utc=True).tz_convert(market_tz(market)),
            'Open': item.get('o'),
            'High': item.get('h'),
            'Low': item.get('l'),
            'Close': item.get('c'),
            'Volume': item.get('v', 0),
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).dropna(subset=['Open', 'High', 'Low', 'Close'])
    if df.empty:
        return pd.DataFrame()
    df = df.drop_duplicates(subset=['Date'], keep='last').sort_values('Date')
    df = df.set_index('Date')
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.dropna(subset=['Open', 'High', 'Low', 'Close'])


class ITickDataSource(MarketDataSource):
    name = 'itick'

    @property
    def free_mode(self) -> bool:
        return True

    @property
    def refresh_limit(self) -> int:
        return int(Config.ITICK_FREE_REFRESH_LIMIT)

    def estimate_api_usage(
        self,
        market: str,
        *,
        trading_days: int = None,
        rows: int = None,
        request_count: int = None,
        limit: int = None,
    ) -> dict:
        page_limit = max(1, int(limit or Config.ITICK_PAGE_LIMIT))
        bars_per_day = bars_per_trading_day(market)
        if request_count is None:
            if rows is None:
                rows = max(bars_per_day, int(trading_days or 1) * bars_per_day)
            request_count = max(1, math.ceil(int(rows) / page_limit))
        free_interval = max(0.0, float(Config.ITICK_FREE_MIN_INTERVAL_SECONDS))
        estimated_seconds = max(0, int(request_count) - 1) * free_interval
        return {
            'data_source': self.name,
            'free_mode': True,
            'market': market,
            'bars_per_trading_day': bars_per_day,
            'page_limit': page_limit,
            'request_count': int(request_count),
            'min_interval_seconds': free_interval,
            'estimated_seconds': estimated_seconds,
        }

    def request(
        self,
        region: str,
        code: str,
        *,
        et: int = None,
        limit: int = None,
        k_type: str = '2',
    ) -> dict:
        token = Config.ITICK_TOKEN
        if not token:
            raise RuntimeError('未配置 ITICK_TOKEN 或 ITICK_API_KEY')

        query = {
            'region': region,
            'code': code,
            'kType': str(k_type),
            'limit': str(int(limit or Config.ITICK_PAGE_LIMIT)),
        }
        if et is not None:
            query['et'] = str(int(et))
        url = f"{Config.ITICK_BASE_URL.rstrip('/')}/kline?{urllib.parse.urlencode(query)}"
        req = urllib.request.Request(
            url,
            headers={
                'accept': 'application/json',
                'token': token,
            },
            method='GET',
        )
        last_exc = None
        retries = max(1, int(Config.ITICK_RETRIES))
        for attempt in range(1, retries + 1):
            try:
                apply_free_rate_limit()
                with urllib.request.urlopen(req, timeout=20) as resp:
                    body = resp.read().decode('utf-8')
                break
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code == 429:
                    retry_after = exc.headers.get('Retry-After') if exc.headers else None
                    if attempt >= retries:
                        raise ITickRateLimitError('iTick 请求过快: HTTP 429 Too Many Requests') from exc
                    try:
                        sleep_seconds = float(retry_after) if retry_after else 1.0 * attempt
                    except ValueError:
                        sleep_seconds = 1.0 * attempt
                    time.sleep(min(5.0, sleep_seconds))
                    continue
                raise RuntimeError(f'iTick 请求失败: HTTP {exc.code}') from exc
            except urllib.error.URLError as exc:
                last_exc = exc
                if attempt >= retries:
                    raise RuntimeError(f'iTick 请求失败: {exc}') from exc
                time.sleep(min(2.0, 0.4 * attempt))
        else:
            raise RuntimeError(f'iTick 请求失败: {last_exc}')

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError('iTick 返回非 JSON 数据') from exc

        if payload.get('code') not in (0, '0', None):
            raise RuntimeError(f"iTick 返回错误: {payload.get('msg') or payload.get('code')}")
        return payload

    def _fetch_daily_reference(
        self,
        region: str,
        code: str,
        market: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> pd.DataFrame:
        days = max(1, int((end.normalize() - start.normalize()).days) + 1)
        limit = max(30, min(1000, days + 10))
        et = int(end.tz_convert('UTC').timestamp() * 1000)
        payload = self.request(region, code, et=et, limit=limit, k_type='8')
        daily = normalize_daily_kline(payload.get('data') or [], market)
        if daily.empty:
            return pd.DataFrame()
        daily = daily[(daily.index >= start.normalize()) & (daily.index <= end)]
        return daily

    def _validate_against_daily(
        self,
        request: FetchRequest,
        df: pd.DataFrame,
        region: str,
        code: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
    ) -> None:
        if not Config.ITICK_DAILY_CHECK or df is None or df.empty:
            return
        daily = self._fetch_daily_reference(region, code, request.market, start, end)
        if daily.empty:
            return

        minute_daily = df.copy()
        minute_daily.index = pd.to_datetime(minute_daily.index)
        minute_daily['trade_date'] = minute_daily.index.date
        minute_daily = minute_daily.groupby('trade_date').agg({
            'Open': 'first',
            'High': 'max',
            'Low': 'min',
            'Close': 'last',
            'Volume': 'sum',
        })
        minute_daily.index = pd.to_datetime(minute_daily.index)

        price_tol = float(Config.ITICK_DAILY_PRICE_TOLERANCE)
        volume_tol = float(Config.ITICK_DAILY_VOLUME_REL_TOLERANCE)
        issues = []
        daily_by_date = {pd.Timestamp(idx).date(): row for idx, row in daily.iterrows()}
        daily_dates = set(daily_by_date)
        minute_dates = {pd.Timestamp(x).date() for x in minute_daily.index}
        for day in sorted(daily_dates & minute_dates):
            daily_row = daily_by_date[day]
            minute_row = minute_daily.loc[pd.Timestamp(day)]
            for col in ('Open', 'High', 'Low', 'Close'):
                dv = daily_row.get(col)
                mv = minute_row.get(col)
                if pd.isna(dv) or pd.isna(mv):
                    continue
                if abs(float(dv) - float(mv)) > price_tol:
                    issues.append(
                        f"{day} {col}: daily={float(dv):.4f} minute={float(mv):.4f}"
                    )
            dv = daily_row.get('Volume')
            mv = minute_row.get('Volume')
            if pd.notna(dv) and pd.notna(mv):
                base = max(abs(float(dv)), 1.0)
                rel = abs(float(dv) - float(mv)) / base
                if rel > volume_tol:
                    issues.append(
                        f"{day} Volume: daily={float(dv):.0f} minute={float(mv):.0f} rel={rel:.2%}"
                    )
            if len(issues) >= 8:
                break
        if issues:
            raise RuntimeError('iTick 5min 与 daily 校验不一致: ' + '; '.join(issues))

    def fetch_5m(self, request: FetchRequest) -> pd.DataFrame:
        region, code = region_code(request.market, request.stock_code)
        start, end = fetch_window_bounds(request.history_days, request.start_date, request.end_date)
        tz = market_tz(request.market)
        start = start.tz_localize(tz) if start.tzinfo is None else start.tz_convert(tz)
        end = end.tz_localize(tz) if end.tzinfo is None else end.tz_convert(tz)
        rows = target_rows(request.market, request.history_days, request.start_date, request.end_date)
        if request.limit is not None:
            page_limit = max(1, int(request.limit))
        else:
            page_limit = max(1, min(int(Config.ITICK_PAGE_LIMIT), rows))
        max_pages = max(1, int(request.max_pages if request.max_pages is not None else Config.ITICK_MAX_PAGES))
        logger.info(f"iTick 拉取: region={region} code={code} interval=5m rows≈{rows}")

        all_items = []
        et = int((end + pd.Timedelta(minutes=5)).tz_convert('UTC').timestamp() * 1000)
        seen_ts = set()
        for _ in range(max_pages):
            try:
                payload = self.request(region, code, et=et, limit=page_limit)
            except ITickRateLimitError as exc:
                if all_items:
                    logger.warning(
                        f"iTick 限流 ({region}/{code})，使用已获取的 {len(all_items)} 条原始 K 线: {exc}"
                    )
                    break
                raise
            except Exception as exc:
                if all_items:
                    logger.warning(
                        f"iTick 分页中断 ({region}/{code})，使用已获取的 {len(all_items)} 条原始 K 线: {exc}"
                    )
                    break
                raise
            items = payload.get('data') or []
            if not items:
                break
            new_items = []
            for item in items:
                ts = item.get('t')
                if ts is None or ts in seen_ts:
                    continue
                seen_ts.add(ts)
                new_items.append(item)
            if not new_items:
                break
            all_items.extend(new_items)
            min_ts = min(int(item['t']) for item in new_items if item.get('t') is not None)
            min_time = pd.to_datetime(min_ts, unit='ms', utc=True).tz_convert(tz)
            if min_time <= start or len(all_items) >= rows:
                break
            et = min_ts - 1
        df = normalize_kline(all_items, request.market)
        if df.empty:
            logger.warning(f"iTick 返回空数据: {region}/{code}")
            return df
        df = df[(df.index >= start) & (df.index <= end)]
        self._validate_against_daily(request, df, region, code, start, end)
        logger.info(f"iTick 获取 {region}/{code} 数据 {len(df)} 行")
        return df
