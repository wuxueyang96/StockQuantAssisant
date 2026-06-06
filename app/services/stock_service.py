import logging
import math
import re
import json
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import List, Tuple
import pytz
import pandas as pd
import yfinance as yf
from app.config import Config
from app.models.database import db_manager
from app.services.data_sources import (
    FetchRequest,
    active_data_source,
    estimate_api_usage as estimate_source_api_usage,
    fetch_5m as fetch_5m_from_sources,
)

logger = logging.getLogger(__name__)
MARKET_LABEL = {'a': 'A', 'hk': 'HK', 'us': 'US'}

_akshare_available = None
_itick_rate_lock = threading.Lock()
_itick_next_request_at = 0.0


class ITickRateLimitError(RuntimeError):
    """Raised when iTick rejects requests because the token is rate limited."""


def _is_akshare_available():
    global _akshare_available
    if _akshare_available is None:
        try:
            import akshare
            _akshare_available = True
        except ImportError:
            _akshare_available = False
    return _akshare_available


def format_stock_code(market: str, stock_code: str) -> str:
    if market == 'a':
        suffix = '.SS' if stock_code.startswith(('5', '6')) else '.SZ'
    elif market == 'hk':
        suffix = '.HK'
    elif market == 'us':
        suffix = '.US'
    else:
        raise ValueError(f"未知市场: {market}")
    return f"{stock_code}{suffix}"


def get_registration_id(market: str, stock_code: str, interval: str) -> str:
    code = format_stock_code(market, stock_code)
    return f"{MARKET_LABEL[market]}_{code}_{interval}"


def _is_etf(stock_code: str) -> bool:
    return stock_code.startswith('5')


def _detect_by_code(stock_input: str) -> List[Tuple[str, str]]:
    stock_input = stock_input.strip().upper()

    if stock_input.endswith('.SZ') or stock_input.endswith('.SS'):
        code = stock_input.rsplit('.', 1)[0]
        if re.match(r'^\d{6}$', code) and code[0] in ('6', '0', '3', '5'):
            return [('a', code)]
        raise ValueError(f"无法识别A股代码: {stock_input}")

    if stock_input.endswith('.HK'):
        code = stock_input.rsplit('.', 1)[0]
        if re.match(r'^\d{4,5}$', code):
            return [('hk', code.zfill(5))]
        raise ValueError(f"无法识别港股代码: {stock_input}")

    if stock_input.endswith('.US'):
        code = stock_input.rsplit('.', 1)[0]
        if re.match(r'^[A-Z0-9]{1,10}$', code):
            return [('us', code.upper())]
        raise ValueError(f"无法识别美股代码: {stock_input}")

    if re.match(r'^\d{5,6}$', stock_input):
        if len(stock_input) == 6:
            if stock_input[0] in ('6', '0', '3', '5'):
                return [('a', stock_input)]
        if len(stock_input) == 5:
            return [('hk', stock_input.zfill(5))]

    if re.match(r'^[A-Za-z]{1,5}$', stock_input):
        return [('us', stock_input.upper())]

    return []


def resolve_stock_name(name: str) -> List[Tuple[str, str]]:
    name = name.strip()

    row = db_manager.get_stock_codes(name)
    if row is None:
        raise ValueError(f"stock_codes 表中未找到 '{name}'，请先通过 POST /api/stock/code 录入该股票名称与代码映射")

    a_code, hk_code, us_code = row
    results = []
    if a_code:
        results.append(('a', a_code))
    if hk_code:
        results.append(('hk', hk_code))
    if us_code:
        results.append(('us', us_code))

    if not results:
        raise ValueError(f"stock_codes 表中 '{name}' 没有有效的市场代码")

    return results


def detect_market(stock_input: str) -> List[Tuple[str, str]]:
    results = _detect_by_code(stock_input)
    if results:
        return results
    return resolve_stock_name(stock_input)


def get_yfinance_ticker(market: str, stock_code: str) -> str:
    mapper = Config.YFINANCE_TICKER_MAP.get(market)
    if not mapper:
        raise ValueError(f"未知市场: {market}")
    return mapper(stock_code)


def get_itick_region_code(market: str, stock_code: str) -> tuple[str, str]:
    if market == 'a':
        region = 'SH' if stock_code.startswith(('5', '6')) else 'SZ'
        return region, stock_code
    if market == 'hk':
        code = stock_code.lstrip('0') or '0'
        return 'HK', code
    if market == 'us':
        return 'US', stock_code.upper()
    raise ValueError(f"未知市场: {market}")


def _format_fetch_window(history_days: int = None, start_date=None, end_date=None) -> tuple[str, str]:
    days = int(history_days or Config.INITIAL_5MIN_HISTORY_DAYS)
    end = pd.Timestamp(end_date) if end_date else pd.Timestamp(datetime.now())
    start = pd.Timestamp(start_date) if start_date else end - pd.DateOffset(days=days)
    return start.strftime('%Y-%m-%d %H:%M:%S'), end.strftime('%Y-%m-%d %H:%M:%S')


def _fetch_window_bounds(history_days: int = None, start_date=None, end_date=None) -> tuple[pd.Timestamp, pd.Timestamp]:
    days = int(history_days or Config.INITIAL_5MIN_HISTORY_DAYS)
    end = pd.Timestamp(end_date) if end_date else pd.Timestamp(datetime.now())
    start = pd.Timestamp(start_date) if start_date else end - pd.DateOffset(days=days)
    return start, end


def _market_tz(market: str) -> str:
    return Config.TRADING_HOURS.get(market, {}).get('tz', 'UTC')


def _filter_regular_session(df: pd.DataFrame, market: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    trading_config = Config.TRADING_HOURS.get(market)
    if not trading_config:
        return df
    start = datetime.strptime(trading_config['start'], '%H:%M').time()
    end = datetime.strptime(trading_config['end'], '%H:%M').time()
    mask = [(start <= ts.time() <= end) for ts in df.index]
    return df.loc[mask]


def itick_bars_per_trading_day(market: str) -> int:
    return 48 if market == 'a' else 78


def estimate_itick_api_usage(
    market: str,
    *,
    trading_days: int = None,
    rows: int = None,
    request_count: int = None,
    limit: int = None,
) -> dict:
    page_limit = max(1, int(limit or Config.ITICK_PAGE_LIMIT))
    bars_per_day = itick_bars_per_trading_day(market)
    if request_count is None:
        if rows is None:
            rows = max(bars_per_day, int(trading_days or 1) * bars_per_day)
        request_count = max(1, math.ceil(int(rows) / page_limit))
    free_interval = max(0.0, float(Config.ITICK_FREE_MIN_INTERVAL_SECONDS))
    estimated_seconds = max(0, int(request_count) - 1) * free_interval
    return {
        'data_source': Config.DATA_SOURCE,
        'free_mode': True,
        'market': market,
        'bars_per_trading_day': bars_per_day,
        'page_limit': page_limit,
        'request_count': int(request_count),
        'min_interval_seconds': free_interval,
        'estimated_seconds': estimated_seconds,
    }


def estimate_data_api_usage(
    market: str,
    *,
    trading_days: int = None,
    rows: int = None,
    request_count: int = None,
    limit: int = None,
) -> dict:
    return estimate_source_api_usage(
        market,
        trading_days=trading_days,
        rows=rows,
        request_count=request_count,
        limit=limit,
    )


def _itick_target_rows(market: str, history_days: int = None, start_date=None, end_date=None) -> int:
    if history_days is not None:
        days = int(history_days)
    elif start_date or end_date:
        start, end = _fetch_window_bounds(history_days, start_date, end_date)
        days = max(1, int((end.normalize() - start.normalize()).days) + 1)
    else:
        days = int(Config.INITIAL_5MIN_HISTORY_DAYS)
    bars_per_day = 48 if market == 'a' else 78
    return max(bars_per_day, days * bars_per_day)


def _apply_itick_free_rate_limit():
    global _itick_next_request_at
    min_interval = max(0.0, float(Config.ITICK_FREE_MIN_INTERVAL_SECONDS))
    if min_interval <= 0:
        return
    with _itick_rate_lock:
        now = time.monotonic()
        wait_seconds = _itick_next_request_at - now
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        _itick_next_request_at = time.monotonic() + min_interval


def _itick_request(region: str, code: str, *, et: int = None, limit: int = None) -> dict:
    token = Config.ITICK_TOKEN
    if not token:
        raise RuntimeError('未配置 ITICK_TOKEN 或 ITICK_API_KEY')

    query = {
        'region': region,
        'code': code,
        'kType': '2',
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
            _apply_itick_free_rate_limit()
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


def _normalize_itick_kline(items, market: str) -> pd.DataFrame:
    if not items:
        return pd.DataFrame()
    rows = []
    for item in items:
        ts = item.get('t')
        if ts is None:
            continue
        rows.append({
            'Date': pd.to_datetime(int(ts), unit='ms', utc=True).tz_convert(_market_tz(market)),
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
    return _filter_regular_session(
        df[['Open', 'High', 'Low', 'Close', 'Volume', 'Dividends', 'Stock Splits']],
        market,
    )


def _fetch_5m_itick(market: str, stock_code: str, history_days: int = None,
                    start_date=None, end_date=None, *, max_pages: int = None,
                    limit: int = None) -> pd.DataFrame:
    region, code = get_itick_region_code(market, stock_code)
    start, end = _fetch_window_bounds(history_days, start_date, end_date)
    start = start.tz_localize(_market_tz(market)) if start.tzinfo is None else start.tz_convert(_market_tz(market))
    end = end.tz_localize(_market_tz(market)) if end.tzinfo is None else end.tz_convert(_market_tz(market))
    target_rows = _itick_target_rows(market, history_days, start_date, end_date)
    if limit is not None:
        page_limit = max(1, int(limit))
    else:
        page_limit = max(1, min(int(Config.ITICK_PAGE_LIMIT), target_rows))
    max_pages = max(1, int(max_pages if max_pages is not None else Config.ITICK_MAX_PAGES))
    logger.info(f"iTick 拉取: region={region} code={code} interval=5m rows≈{target_rows}")

    all_items = []
    et = int(end.tz_convert('UTC').timestamp() * 1000)
    seen_ts = set()
    for _ in range(max_pages):
        try:
            payload = _itick_request(region, code, et=et, limit=page_limit)
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
        min_time = pd.to_datetime(min_ts, unit='ms', utc=True).tz_convert(_market_tz(market))
        if min_time <= start or len(all_items) >= target_rows:
            break
        et = min_ts - 1
    df = _normalize_itick_kline(all_items, market)
    if df.empty:
        logger.warning(f"iTick 返回空数据: {region}/{code}")
        return df
    df = df[(df.index >= start) & (df.index <= end)]
    logger.info(f"iTick 获取 {region}/{code} 数据 {len(df)} 行")
    return df


def _fetch_5m_akshare_a(stock_code: str, history_days: int = None,
                        start_date=None, end_date=None) -> pd.DataFrame:
    import akshare as ak
    start_date, end_date = _format_fetch_window(history_days, start_date, end_date)
    df = ak.stock_zh_a_hist_min_em(
        symbol=stock_code,
        period='5',
        start_date=start_date,
        end_date=end_date,
        adjust='qfq',
    )
    return _normalize_akshare_min(df, tz='Asia/Shanghai')


def _fetch_5m_akshare_etf(stock_code: str, history_days: int = None,
                          start_date=None, end_date=None) -> pd.DataFrame:
    import akshare as ak
    start_date, end_date = _format_fetch_window(history_days, start_date, end_date)
    df = ak.fund_etf_hist_min_em(
        symbol=stock_code,
        period='5',
        start_date=start_date,
        end_date=end_date,
        adjust='qfq',
    )
    return _normalize_akshare_min(df, tz='Asia/Shanghai')


def _fetch_5m_akshare_hk(stock_code: str, history_days: int = None,
                         start_date=None, end_date=None) -> pd.DataFrame:
    import akshare as ak
    start_date, end_date = _format_fetch_window(history_days, start_date, end_date)
    df = ak.stock_hk_hist_min_em(
        symbol=stock_code,
        period='5',
        start_date=start_date,
        end_date=end_date,
        adjust='qfq',
    )
    return _normalize_akshare_min(df, tz='Asia/Hong_Kong')


def _fetch_5m_yfinance(market: str, stock_code: str, history_days: int = None,
                       start_date=None, end_date=None) -> pd.DataFrame:
    ticker_symbol = get_yfinance_ticker(market, stock_code)
    logger.info(f"yfinance 拉取: {ticker_symbol} interval=5m")
    ticker = yf.Ticker(ticker_symbol)

    import io
    import sys
    old_stderr = sys.stderr
    stderr_buf = io.StringIO()
    sys.stderr = stderr_buf
    try:
        if start_date or end_date:
            kwargs = {'interval': '5m'}
            if start_date:
                kwargs['start'] = pd.Timestamp(start_date).strftime('%Y-%m-%d')
            if end_date:
                kwargs['end'] = pd.Timestamp(end_date).strftime('%Y-%m-%d')
            df = ticker.history(**kwargs)
        else:
            days = int(history_days or Config.INITIAL_5MIN_HISTORY_DAYS)
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


def _normalize_akshare_min(df, tz: str) -> pd.DataFrame:
    """akshare 分钟线返回中文列 + 时间字符串，统一转 OHLCV + tz-aware 索引。"""
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


def fetch_stock_data(
    market: str,
    stock_code: str,
    interval: str = '5min',
    *,
    history_days: int = None,
    start_date=None,
    end_date=None,
    max_pages: int = None,
    limit: int = None,
):
    """统一拉取 5min K 线（采集层最细粒度），其他周期由 resample 运行时合成。

    保留 `interval` 形参以兼容旧调用，但实际上只会拉 5min。
    """
    return fetch_5m_from_sources(FetchRequest(
        market=market,
        stock_code=stock_code,
        interval=interval,
        history_days=history_days,
        start_date=start_date,
        end_date=end_date,
        max_pages=max_pages,
        limit=limit,
    ))


def is_trading_time(market: str) -> bool:
    trading_config = Config.TRADING_HOURS.get(market)
    if not trading_config:
        return False

    tz = pytz.timezone(trading_config['tz'])
    now = datetime.now(tz)

    if now.weekday() >= 5:
        return False

    start = datetime.strptime(trading_config['start'], '%H:%M').time()
    end = datetime.strptime(trading_config['end'], '%H:%M').time()
    current_time = now.time()

    return start <= current_time <= end


def get_table_name(market: str, stock_code: str, interval: str) -> str:
    return get_registration_id(market, stock_code, interval)


def collect_and_store(market: str, stock_code: str, interval: str = '5min',
                      skip_trading_check: bool = False, history_days: int = None,
                      start_date=None, end_date=None, max_pages: int = None,
                      limit: int = None) -> int:
    """统一采集 5min K 线写入 `*_5min` 表。

    `interval` 参数保留为兼容形参；实际无论传什么都只采 5min。高粒度由
    `app.services.resample` 在决策时按需合成。
    """
    if not skip_trading_check and not is_trading_time(market):
        return 0

    df = fetch_stock_data(
        market, stock_code, '5min',
        history_days=history_days,
        start_date=start_date,
        end_date=end_date,
        max_pages=max_pages,
        limit=limit,
    )
    if df is None or df.empty:
        logger.warning(f"未获取到 5min 数据: {market}/{stock_code}")
        return 0

    table_name = get_table_name(market, stock_code, '5min')
    if not db_manager.table_exists(market, table_name):
        db_manager.create_stock_table(market, table_name)

    rows_inserted = db_manager.insert_data(market, table_name, df)
    logger.info(f"写入 {rows_inserted} 条 5min 数据到 {table_name}")
    return rows_inserted
