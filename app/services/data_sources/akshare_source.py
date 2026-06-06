from __future__ import annotations

import importlib.util
import logging
import math
import time

import pandas as pd

from app.config import Config
from app.services.data_sources.base import FetchRequest, MarketDataSource
from app.services.data_sources.utils import fetch_window_bounds, format_fetch_window

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


def normalize_daily(df) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={
        '日期': 'Date',
        '开盘': 'Open',
        '最高': 'High',
        '最低': 'Low',
        '收盘': 'Close',
        '成交量': 'Volume',
    })
    if 'Date' not in df.columns:
        return pd.DataFrame()
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.set_index('Date')
    cols = ['Open', 'High', 'Low', 'Close']
    if 'Volume' in df.columns:
        cols.append('Volume')
    df = df[cols]
    for col in cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.dropna(subset=['Open', 'High', 'Low', 'Close'])


def _fmt_daily_date(value) -> str:
    return pd.Timestamp(value).strftime('%Y%m%d')


def _display_date(value) -> str:
    return pd.Timestamp(value).strftime('%Y-%m-%d')


def _display_ts(value) -> str:
    return pd.Timestamp(value).isoformat()


def _chunk_windows(start, end, chunk_days: int) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
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


def _expected_min_bars(market: str) -> int:
    if market == 'a':
        return int(Config.AKSHARE_EXPECTED_A_5MIN_BARS)
    if market == 'hk':
        return int(Config.AKSHARE_EXPECTED_HK_5MIN_BARS)
    return 0


def _minute_quality_report(df: pd.DataFrame, market: str) -> dict:
    if df is None or df.empty:
        return {
            'trading_days': 0,
            'bar_count_issues': [],
            'issue_count': 0,
        }
    normalized = df.copy()
    normalized.index = pd.to_datetime(normalized.index)
    counts = normalized.groupby(normalized.index.date).size().sort_index()
    expected = _expected_min_bars(market)
    issues = []
    if expected > 0:
        for day, count in counts.items():
            if int(count) < expected:
                day_df = normalized[normalized.index.date == day]
                issues.append({
                    'date': str(day),
                    'bars': int(count),
                    'expected_min_bars': expected,
                    'first_time': day_df.index.min().strftime('%H:%M'),
                    'last_time': day_df.index.max().strftime('%H:%M'),
                })
    return {
        'trading_days': int(len(counts)),
        'expected_min_bars': expected,
        'min_bars_per_day': int(counts.min()) if len(counts) else 0,
        'max_bars_per_day': int(counts.max()) if len(counts) else 0,
        'bar_count_issues': issues[:50],
        'issue_count': int(len(issues)),
    }


def _minute_daily_agg(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    normalized = df.copy()
    normalized.index = pd.to_datetime(normalized.index)
    normalized['trade_date'] = normalized.index.date
    agg = normalized.groupby('trade_date').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum',
    })
    agg.index = pd.to_datetime(agg.index)
    return agg


def _timestamp_for_index(value, index: pd.Index) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    tz = getattr(index, 'tz', None)
    if tz is not None:
        return ts.tz_localize(tz) if ts.tzinfo is None else ts.tz_convert(tz)
    if ts.tzinfo is not None:
        return ts.tz_localize(None)
    return ts


class AkshareDataSource(MarketDataSource):
    name = 'akshare'

    def supports(self, market: str) -> bool:
        return market in ('a', 'hk')

    def estimate_api_usage(
        self,
        market: str,
        *,
        trading_days: int = None,
        rows: int = None,
        request_count: int = None,
        limit: int = None,
    ) -> dict:
        strict = bool(Config.AKSHARE_STRICT_BACKFILL and request_count is None)
        window_count = int(request_count or 1)
        daily_request_count = 0
        if request_count is None and strict:
            if trading_days is not None:
                calendar_days = max(1, int(math.ceil(int(trading_days) * 7 / 5)) + 21)
            else:
                calendar_days = int(Config.INITIAL_5MIN_HISTORY_DAYS)
            chunk_days = max(1, int(Config.AKSHARE_BACKFILL_CHUNK_DAYS))
            window_count = max(1, int(math.ceil(calendar_days / chunk_days)))
            daily_request_count = 1 if Config.AKSHARE_DAILY_CHECK else 0
            request_count = window_count + daily_request_count
        elif request_count is None:
            request_count = 1
        return {
            'data_source': self.name,
            'free_mode': False,
            'strict': strict,
            'market': market,
            'request_count': int(request_count),
            'window_count': int(window_count),
            'daily_request_count': int(daily_request_count),
            'min_interval_seconds': 0.0,
            'estimated_seconds': 0.0,
        }

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

    def _fetch_daily(self, market: str, stock_code: str, start, end) -> pd.DataFrame:
        if not Config.AKSHARE_DAILY_CHECK:
            return pd.DataFrame()
        if not is_available():
            raise RuntimeError('未安装 akshare')
        import akshare as ak

        start_date = _fmt_daily_date(start)
        end_date = _fmt_daily_date(end)
        if market == 'a':
            if stock_code.startswith('5'):
                df = ak.fund_etf_hist_em(
                    symbol=stock_code,
                    period='daily',
                    start_date=start_date,
                    end_date=end_date,
                    adjust='qfq',
                )
            else:
                df = ak.stock_zh_a_hist(
                    symbol=stock_code,
                    period='daily',
                    start_date=start_date,
                    end_date=end_date,
                    adjust='qfq',
                )
            return normalize_daily(df)
        if market == 'hk':
            df = ak.stock_hk_hist(
                symbol=stock_code,
                period='daily',
                start_date=start_date,
                end_date=end_date,
                adjust='qfq',
            )
            return normalize_daily(df)
        return pd.DataFrame()

    def _daily_check(self, request: FetchRequest, df: pd.DataFrame, start, end) -> dict:
        report = {
            'enabled': bool(Config.AKSHARE_DAILY_CHECK),
            'checked': False,
            'daily_rows': 0,
            'missing_daily_dates': [],
            'price_mismatches': [],
            'volume_mismatches': [],
            'issue_count': 0,
            'request_count': 0,
        }
        if not Config.AKSHARE_DAILY_CHECK:
            return report
        try:
            report['request_count'] = 1
            daily = self._fetch_daily(request.market, request.stock_code, start, end)
        except Exception as exc:
            report['error'] = str(exc)
            report['issue_count'] = 1
            return report

        if daily.empty:
            report['checked'] = True
            report['empty_reference'] = True
            return report

        minute_daily = _minute_daily_agg(df)
        report['checked'] = True
        report['daily_rows'] = int(len(daily))
        daily_dates = {pd.Timestamp(x).date() for x in daily.index}
        minute_dates = {pd.Timestamp(x).date() for x in minute_daily.index}
        missing_dates = sorted(daily_dates - minute_dates)
        report['missing_daily_dates'] = [str(x) for x in missing_dates[:50]]

        price_tol = float(Config.AKSHARE_DAILY_PRICE_TOLERANCE)
        volume_tol = float(Config.AKSHARE_DAILY_VOLUME_REL_TOLERANCE)
        price_mismatches = []
        volume_mismatches = []
        for day in sorted(daily_dates & minute_dates):
            daily_row = daily.loc[pd.Timestamp(day)]
            minute_row = minute_daily.loc[pd.Timestamp(day)]
            for col in ('Open', 'High', 'Low', 'Close'):
                dv = daily_row.get(col)
                mv = minute_row.get(col)
                if pd.isna(dv) or pd.isna(mv):
                    continue
                if abs(float(dv) - float(mv)) > price_tol:
                    price_mismatches.append({
                        'date': str(day),
                        'field': col.lower(),
                        'daily': round(float(dv), 4),
                        'minute_agg': round(float(mv), 4),
                    })
            dv = daily_row.get('Volume')
            mv = minute_row.get('Volume')
            if pd.notna(dv) and pd.notna(mv):
                base = max(abs(float(dv)), 1.0)
                rel = abs(float(dv) - float(mv)) / base
                if rel > volume_tol:
                    volume_mismatches.append({
                        'date': str(day),
                        'daily': round(float(dv), 4),
                        'minute_agg': round(float(mv), 4),
                        'relative_diff': round(float(rel), 6),
                    })

        report['price_mismatches'] = price_mismatches[:50]
        report['volume_mismatches'] = volume_mismatches[:50]
        report['issue_count'] = (
            len(missing_dates) + len(price_mismatches) + len(volume_mismatches)
        )
        return report

    def fetch_5m_strict(self, request: FetchRequest) -> dict:
        start, end = fetch_window_bounds(request.history_days, request.start_date, request.end_date)
        chunk_days = max(1, int(Config.AKSHARE_BACKFILL_CHUNK_DAYS))
        retries = max(0, int(Config.AKSHARE_BACKFILL_RETRIES))
        retry_sleep = max(0.0, float(Config.AKSHARE_BACKFILL_RETRY_SLEEP_SECONDS))
        chunk_delay = max(0.0, float(Config.AKSHARE_BACKFILL_CHUNK_DELAY_SECONDS))
        windows = _chunk_windows(start, end, chunk_days)
        frames = []
        window_reports = []
        request_count = 0

        for idx, (chunk_start, chunk_end) in enumerate(windows):
            window = {
                'start': _display_ts(chunk_start),
                'end': _display_ts(chunk_end),
                'rows': 0,
                'status': 'pending',
                'attempts': 0,
            }
            last_error = None
            for attempt in range(1, retries + 2):
                window['attempts'] = attempt
                request_count += 1
                try:
                    df = self.fetch_5m(FetchRequest(
                        market=request.market,
                        stock_code=request.stock_code,
                        interval='5min',
                        start_date=chunk_start,
                        end_date=chunk_end,
                    ))
                    if df is None or df.empty:
                        window['status'] = 'empty'
                    else:
                        window['status'] = 'ok'
                        window['rows'] = int(len(df))
                        frames.append(df)
                    break
                except Exception as exc:
                    last_error = str(exc)
                    window['status'] = 'failed'
                    window['error'] = last_error
                    if attempt <= retries and retry_sleep > 0:
                        time.sleep(retry_sleep)
            window_reports.append(window)
            if chunk_delay > 0 and idx < len(windows) - 1:
                time.sleep(chunk_delay)

        if frames:
            df_all = pd.concat(frames).sort_index()
            df_all = df_all[~df_all.index.duplicated(keep='last')]
            start_filter = _timestamp_for_index(start, df_all.index)
            end_filter = _timestamp_for_index(end, df_all.index)
            df_all = df_all[(df_all.index >= start_filter) & (df_all.index <= end_filter)]
        else:
            df_all = pd.DataFrame()

        minute_quality = _minute_quality_report(df_all, request.market)
        daily_check = self._daily_check(request, df_all, start, end)
        daily_request_count = int(daily_check.get('request_count') or 0)
        failed = [w for w in window_reports if w['status'] == 'failed']
        empty = [w for w in window_reports if w['status'] == 'empty']
        quality_issue_count = (
            int(minute_quality.get('issue_count') or 0)
            + int(daily_check.get('issue_count') or 0)
            + len(failed)
        )
        return {
            'df': df_all,
            'data_source': self.name,
            'strict': True,
            'request_count': int(request_count + daily_request_count),
            'minute_request_count': int(request_count),
            'daily_request_count': daily_request_count,
            'window_count': int(len(window_reports)),
            'completed_windows': int(len([w for w in window_reports if w['status'] == 'ok'])),
            'failed_windows': failed[:50],
            'empty_windows': empty[:50],
            'windows': window_reports,
            'quality_report': {
                'start_date': _display_date(start),
                'end_date': _display_date(end),
                'minute': minute_quality,
                'daily_check': daily_check,
                'issue_count': int(quality_issue_count),
            },
        }
