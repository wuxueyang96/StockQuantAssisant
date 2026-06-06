"""Data status, refresh, and historical backfill services."""
from __future__ import annotations

import math
from typing import Any, Optional

import pandas as pd

from app.config import Config
from app.models.database import db_manager
from app.services.resample import resample_ohlcv
from app.services.stock_service import (
    MARKET_LABEL,
    collect_and_store,
    detect_market,
    estimate_data_api_usage,
    fetch_stock_data,
    format_stock_code,
    get_table_name,
    get_workflow_id,
)
from app.services.data_sources import active_data_source
from app.services.workflow_service import workflow_service


def _to_iso(value) -> Optional[str]:
    if value is None or pd.isna(value):
        return None
    try:
        return pd.Timestamp(value).isoformat()
    except Exception:
        return str(value)


def _to_display_date(value) -> Optional[str]:
    if value is None or value == '':
        return None
    return _to_iso(value)


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    if 'timestamp' in df.columns:
        df = df.set_index('timestamp')
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df.rename(columns={c: c.capitalize() for c in df.columns})
    return df


def _calendar_days_for_trading_days(trading_days: int) -> int:
    """Convert an intended trading-day count to a conservative calendar window."""
    return max(1, int(math.ceil(int(trading_days) * 7 / 5)) + 21)


def _trim_to_trading_days(df: pd.DataFrame, trading_days: int, before=None) -> pd.DataFrame:
    normalized = _normalize_ohlcv(df)
    if normalized.empty:
        return normalized
    if before is not None:
        normalized = normalized[normalized.index < pd.Timestamp(before)]
    if normalized.empty:
        return normalized
    dates = []
    for ts in normalized.index:
        key = pd.Timestamp(ts).date()
        if not dates or dates[-1] != key:
            dates.append(key)
    keep_dates = set(dates[-int(trading_days):])
    return normalized[[pd.Timestamp(ts).date() in keep_dates for ts in normalized.index]]


def _daily_bars(market: str, table_name: str) -> int:
    raw = db_manager.get_data(market, table_name, limit=200000)
    df = _normalize_ohlcv(raw)
    if df.empty or 'Close' not in df.columns:
        return 0
    daily = resample_ohlcv(df, 'daily')
    return int(len(daily)) if daily is not None else 0


def _aggregate_api_budget(results: list[dict]) -> dict:
    budgets = [r.get('api_budget') for r in results if r.get('api_budget')]
    if not budgets:
        return {}
    total_requests = sum(int(b.get('request_count') or 0) for b in budgets)
    free_mode = any(bool(b.get('free_mode')) for b in budgets)
    interval = max((float(b.get('min_interval_seconds') or 0) for b in budgets), default=0.0)
    estimated_seconds = max(0, total_requests - 1) * interval if free_mode else 0.0
    return {
        'data_source': budgets[0].get('data_source'),
        'free_mode': free_mode,
        'request_count': int(total_requests),
        'min_interval_seconds': interval if free_mode else 0.0,
        'estimated_seconds': estimated_seconds,
    }


def _base_record(market: str, stock_code: str) -> dict:
    table = get_table_name(market, stock_code, '5min')
    wf_id = get_workflow_id(market, stock_code, '5min')
    return {
        'market': market,
        'market_label': MARKET_LABEL[market],
        'stock_code': stock_code,
        'display_code': format_stock_code(market, stock_code),
        'workflow_id': wf_id,
        'registered': wf_id in workflow_service.workflows,
        'table': table,
        'data_source': Config.DATA_SOURCE,
        'free_mode': bool(active_data_source().free_mode),
        'itick_free_mode': bool(Config.ITICK_FREE_MODE),
    }


def data_status_for_market(market: str, stock_code: str) -> dict:
    record = _base_record(market, stock_code)
    stats = db_manager.get_table_stats(market, record['table'])
    rows = int(stats.get('rows') or 0)
    record.update({
        'exists': rows > 0,
        'rows': rows,
        'first_timestamp': stats.get('first_timestamp'),
        'last_timestamp': stats.get('last_timestamp'),
        'daily_bars': _daily_bars(market, record['table']) if rows > 0 else 0,
        'refresh_api_budget': estimate_data_api_usage(
            market,
            request_count=1,
            limit=active_data_source().refresh_limit,
        ),
    })
    return record


def estimate_backfill_api_usage(stock_input: str, days: int = None) -> dict:
    requested_trading_days = int(days or Config.INITIAL_5MIN_HISTORY_DAYS)
    detections = detect_market(stock_input)
    results = []
    for market, stock_code in detections:
        budget = estimate_data_api_usage(market, trading_days=requested_trading_days)
        results.append({
            'market': market,
            'market_label': MARKET_LABEL[market],
            'stock_code': stock_code,
            'display_code': format_stock_code(market, stock_code),
            'requested_trading_days': requested_trading_days,
            'request_calendar_days': _calendar_days_for_trading_days(requested_trading_days),
            'api_budget': budget,
        })
    return {
        'success': True,
        'input': stock_input,
        'count': len(results),
        'results': results,
        'api_budget': _aggregate_api_budget(results),
    }


def get_data_status(stock_input: str) -> dict:
    detections = detect_market(stock_input)
    return {
        'success': True,
        'input': stock_input,
        'count': len(detections),
        'results': [data_status_for_market(m, c) for m, c in detections],
    }


def refresh_market(market: str, stock_code: str, history_days: int = None) -> dict:
    record = data_status_for_market(market, stock_code)
    if not record['registered']:
        record['error'] = '该股票尚未注册工作流，请先注册'
        return record

    days = int(history_days or Config.REFRESH_5MIN_HISTORY_DAYS)
    before = db_manager.get_table_stats(market, record['table'])
    source = active_data_source()
    if source.free_mode:
        if not db_manager.table_exists(market, record['table']):
            db_manager.create_stock_table(market, record['table'])
        df = fetch_stock_data(
            market,
            stock_code,
            '5min',
            start_date=before.get('last_timestamp'),
            max_pages=1,
            limit=source.refresh_limit,
        )
        rows_inserted = db_manager.insert_data(market, record['table'], df) if df is not None else 0
        refresh_mode = 'free_latest_page'
        api_budget = estimate_data_api_usage(
            market,
            request_count=1,
            limit=source.refresh_limit,
        )
    else:
        rows_inserted = collect_and_store(
            market,
            stock_code,
            '5min',
            skip_trading_check=True,
            history_days=days,
        )
        refresh_mode = 'history_window'
        api_budget = estimate_data_api_usage(market, trading_days=days)
    after_record = data_status_for_market(market, stock_code)
    after_record.update({
        'refresh_days': days,
        'refresh_mode': refresh_mode,
        'rows_inserted': int(rows_inserted),
        'rows_before': int(before.get('rows') or 0),
        'rows_after': after_record.get('rows', 0),
        'api_budget': api_budget,
    })
    return after_record


def refresh_data(stock_input: str, history_days: int = None) -> dict:
    detections = detect_market(stock_input)
    results = [refresh_market(m, c, history_days=history_days) for m, c in detections]
    return {
        'success': True,
        'input': stock_input,
        'count': len(results),
        'results': results,
    }


def refresh_all_registered(history_days: int = None) -> dict:
    df = db_manager.get_all_stock_codes()
    results = []
    for _, row in df.iterrows():
        name = row['name']
        for market, code_col in [('a', 'a_code'), ('hk', 'hk_code'), ('us', 'us_code')]:
            code = row.get(code_col)
            if not code or pd.isna(code):
                continue
            result = refresh_market(market, code, history_days=history_days)
            result['stock'] = name
            results.append(result)
    ok_results = [r for r in results if not r.get('error')]
    error_results = [r for r in results if r.get('error')]
    return {
        'success': True,
        'total_stocks': int(len(df)),
        'total_queries': int(len(results)),
        'total_rows_inserted': int(sum(int(r.get('rows_inserted') or 0) for r in ok_results)),
        'errors': int(len(error_results)),
        'results': results,
        'api_budget': _aggregate_api_budget(ok_results),
    }


def backfill_market(
    market: str,
    stock_code: str,
    *,
    days: int = None,
    start_date: Any = None,
    end_date: Any = None,
) -> dict:
    record = data_status_for_market(market, stock_code)
    if not record['registered']:
        record['error'] = '该股票尚未注册工作流，请先注册'
        return record

    requested_trading_days = int(days or Config.INITIAL_5MIN_HISTORY_DAYS)
    request_calendar_days = _calendar_days_for_trading_days(requested_trading_days)
    api_budget = estimate_data_api_usage(market, trading_days=requested_trading_days)
    explicit_window = bool(start_date or end_date)
    effective_start = start_date
    effective_end = end_date
    request_mode = 'explicit_window' if explicit_window else 'recent_window'

    if not explicit_window and record.get('first_timestamp'):
        first_ts = pd.Timestamp(record['first_timestamp'])
        effective_end = first_ts
        effective_start = first_ts - pd.DateOffset(days=request_calendar_days)
        request_mode = 'backfill_before_first_timestamp'

    fetch_history_days = None if explicit_window else requested_trading_days
    df = fetch_stock_data(
        market,
        stock_code,
        '5min',
        history_days=fetch_history_days,
        start_date=effective_start,
        end_date=effective_end,
    )

    if df is not None and not df.empty and not explicit_window:
        before = effective_end if request_mode == 'backfill_before_first_timestamp' else None
        df = _trim_to_trading_days(df, requested_trading_days, before=before)

    # Some providers do not support arbitrary 5m start/end windows. Fall back to
    # the provider's recent-period mode so the user can still refresh/merge what
    # the source is willing to return instead of getting a hard empty response.
    if (df is None or df.empty) and not explicit_window and request_mode == 'backfill_before_first_timestamp':
        df = fetch_stock_data(
            market,
            stock_code,
            '5min',
            history_days=requested_trading_days,
        )
        if df is not None and not df.empty:
            df = _trim_to_trading_days(df, requested_trading_days)
        request_mode = 'recent_window_fallback'

    if df is None or df.empty:
        no_data_payload = {
            'requested_days': requested_trading_days,
            'requested_trading_days': requested_trading_days,
            'request_calendar_days': request_calendar_days,
            'request_mode': request_mode,
            'start_date': _to_display_date(effective_start),
            'end_date': _to_display_date(effective_end),
            'input_rows': 0,
            'inserted_rows': 0,
            'updated_rows': 0,
            'rows_before': int(record.get('rows') or 0),
            'rows_after': int(record.get('rows') or 0),
            'no_data': True,
            'api_budget': api_budget,
        }
        if int(record.get('rows') or 0) > 0:
            record.update({
                **no_data_payload,
                'warning': '数据源未返回更早的 5min 历史数据，已保留当前已有数据',
            })
            return record
        record.update({
            **no_data_payload,
            'error': '数据源未返回历史数据',
        })
        return record

    if not db_manager.table_exists(market, record['table']):
        db_manager.create_stock_table(market, record['table'])
    stats = db_manager.upsert_data(market, record['table'], df)
    after_record = data_status_for_market(market, stock_code)

    normalized = _normalize_ohlcv(df)
    actual_start = _to_iso(normalized.index.min()) if not normalized.empty else None
    actual_end = _to_iso(normalized.index.max()) if not normalized.empty else None
    actual_days = 0
    if not normalized.empty:
        actual_days = int(len({pd.Timestamp(x).date() for x in normalized.index}))

    after_record.update({
        'requested_days': requested_trading_days,
        'requested_trading_days': requested_trading_days,
        'request_calendar_days': request_calendar_days,
        'request_mode': request_mode,
        'start_date': _to_display_date(effective_start),
        'end_date': _to_display_date(effective_end),
        'api_budget': api_budget,
        'source_rows': int(len(df)),
        'source_first_timestamp': actual_start,
        'source_last_timestamp': actual_end,
        'source_trading_days': actual_days,
        'partial': bool(days and actual_days < requested_trading_days),
        **stats,
    })
    return after_record


def backfill_data(
    stock_input: str,
    *,
    days: int = None,
    start_date: Any = None,
    end_date: Any = None,
) -> dict:
    detections = detect_market(stock_input)
    results = [
        backfill_market(m, c, days=days, start_date=start_date, end_date=end_date)
        for m, c in detections
    ]
    return {
        'success': True,
        'input': stock_input,
        'count': len(results),
        'results': results,
    }
