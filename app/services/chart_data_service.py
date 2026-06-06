"""JSON chart data for browser-side rendering."""
from __future__ import annotations

from typing import Optional

import pandas as pd

from app.algos.integrated_decision import (
    INTRADAY_STRUCTURE_INTERVALS,
    evaluate_integrated_dataframe,
)
from app.algos.sequence import NineSequence
from app.algos.structure import MACDStructure
from app.algos.trend import TrendChannel
from app.models.database import db_manager
from app.services.resample import resample_ohlcv
from app.services.stock_service import (
    MARKET_LABEL,
    detect_market,
    format_stock_code,
    get_table_name,
)


def _normalize_ohlcv(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    if df is None or df.empty:
        return None
    df = df.copy()
    if 'timestamp' in df.columns:
        df = df.set_index('timestamp')
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df.rename(columns={c: c.capitalize() for c in df.columns})
    if 'Close' not in df.columns:
        return None
    return df


def _load_5min(market: str, stock_code: str) -> Optional[pd.DataFrame]:
    table = get_table_name(market, stock_code, '5min')
    return _normalize_ohlcv(db_manager.get_data(market, table, limit=50000))


def _json_number(value, digits: int = 6):
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def _json_bool(value) -> bool:
    if value is None or pd.isna(value):
        return False
    return bool(value)


def _is_number(value) -> bool:
    return value is not None and not pd.isna(value)


def _time(ts) -> str:
    return pd.Timestamp(ts).isoformat()


def _candles(df: pd.DataFrame, bars: int) -> list[dict]:
    rows = []
    for ts, row in df.tail(bars).iterrows():
        rows.append({
            'time': _time(ts),
            'open': _json_number(row.get('Open'), 4),
            'high': _json_number(row.get('High'), 4),
            'low': _json_number(row.get('Low'), 4),
            'close': _json_number(row.get('Close'), 4),
            'volume': _json_number(row.get('Volume'), 0),
            'partial_bar': _json_bool(row.get('partial_bar', False)),
        })
    return rows


def _daily_start_date(daily_window: pd.DataFrame):
    if daily_window is None or daily_window.empty:
        return None
    return pd.Timestamp(daily_window.index.min()).date()


def _filter_from_date(df: pd.DataFrame, start_date) -> pd.DataFrame:
    if df is None or df.empty or start_date is None:
        return df
    mask = [pd.Timestamp(ts).date() >= start_date for ts in df.index]
    return df.loc[mask]


def _window_from_daily_start(df: pd.DataFrame, daily_window: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or daily_window is None or daily_window.empty:
        return df
    return _filter_from_date(df, _daily_start_date(daily_window))


def _trend_lines(df: pd.DataFrame, bars: int) -> list[dict]:
    channels = TrendChannel().compute_all(df)
    rows = []
    for ts, row in channels.tail(bars).iterrows():
        rows.append({
            'time': _time(ts),
            'short_upper': _json_number(row.get('short_upper'), 4),
            'short_lower': _json_number(row.get('short_lower'), 4),
            'long_upper': _json_number(row.get('long_upper'), 4),
            'long_lower': _json_number(row.get('long_lower'), 4),
        })
    return rows


def _sequence_points(df: pd.DataFrame, bars: int) -> list[dict]:
    seq = NineSequence().evaluate(df)
    rows = []
    for ts, row in seq.tail(bars).iterrows():
        rows.append({
            'time': _time(ts),
            'high9_count': int(row.get('high9_count') or 0),
            'low9_count': int(row.get('low9_count') or 0),
            'high9_signal': _json_bool(row.get('high9_signal')),
            'low9_signal': _json_bool(row.get('low9_signal')),
            'high9_active': _json_bool(row.get('high9_active')),
            'low9_active': _json_bool(row.get('low9_active')),
        })
    return rows


def _decision_points(df_daily: pd.DataFrame, intraday: dict[str, pd.DataFrame], bars: int) -> list[dict]:
    decisions = evaluate_integrated_dataframe(df_daily, intraday)
    rows = []
    for ts, row in decisions.tail(bars).iterrows():
        target = row.get('final_target_position')
        actual = row.get('actual_position')
        delta = row.get('order_delta')
        rows.append({
            'time': _time(ts),
            'action': row.get('action') or 'WAIT',
            'actual_position': _json_number(actual, 4) if _is_number(actual) else None,
            'target_position': None if pd.isna(target) else _json_number(target, 4),
            'order_delta': _json_number(delta, 4) if _is_number(delta) else None,
            'order_weight': _json_number(row.get('order_weight'), 6),
            'confidence_label': row.get('confidence_label'),
            'trend_state': row.get('trend_state'),
            'trend_reason': row.get('trend_reason'),
            'reason': row.get('decision_reason'),
            'principle': row.get('principle'),
            'structure_adjustment': _json_number(row.get('structure_adjustment'), 4),
            'high9_active': _json_bool(row.get('high9_active')),
            'low9_active': _json_bool(row.get('low9_active')),
        })
    return rows


def _macd_points(df: pd.DataFrame, bars: int, start_date=None) -> list[dict]:
    macd = MACDStructure().compute_macd(df)
    if start_date is not None:
        macd = _filter_from_date(macd, start_date)
        iter_df = macd
    else:
        iter_df = macd.tail(bars)
    rows = []
    for ts, row in iter_df.iterrows():
        rows.append({
            'time': _time(ts),
            'dif': _json_number(row.get('dif'), 6),
            'dea': _json_number(row.get('dea'), 6),
            'hist': _json_number(row.get('macd_hist'), 6),
        })
    return rows


def _structure_points(df: pd.DataFrame, bars: int, start_date=None) -> list[dict]:
    struct = MACDStructure().evaluate(df)
    if start_date is not None:
        struct = _filter_from_date(struct, start_date)
        iter_df = struct
    else:
        iter_df = struct.tail(bars)
    rows = []
    for ts, row in iter_df.iterrows():
        rows.append({
            'time': _time(ts),
            'top_75': _json_bool(row.get('top_structure_75')),
            'top_100': _json_bool(row.get('top_structure_100')),
            'bottom_75': _json_bool(row.get('bottom_structure_75')),
            'bottom_100': _json_bool(row.get('bottom_structure_100')),
            'top_active': _json_bool(row.get('top_structure_active')),
            'bottom_active': _json_bool(row.get('bottom_structure_active')),
        })
    return rows


def build_chart_data_for_market(market: str, stock_code: str, bars: int = 180) -> dict:
    df_5m = _load_5min(market, stock_code)
    table = get_table_name(market, stock_code, '5min')
    if df_5m is None or df_5m.empty:
        raise ValueError(f'数据表 {table} 不存在或为空，请先注册该股票并刷新/补历史数据')

    daily = resample_ohlcv(df_5m, 'daily')
    if daily is None or len(daily) < 30:
        raise ValueError('5min 数据不足以合成日线（需要至少 30 根日线）')

    intraday: dict[str, pd.DataFrame] = {}
    for interval in INTRADAY_STRUCTURE_INTERVALS:
        dfi = resample_ohlcv(df_5m, interval)
        if dfi is not None and len(dfi) >= 30:
            intraday[interval] = dfi

    daily_window = daily.tail(bars)
    daily_start_date = _daily_start_date(daily_window)
    result = {
        'market': market,
        'market_label': MARKET_LABEL[market],
        'stock_code': stock_code,
        'display_code': format_stock_code(market, stock_code),
        'bars': min(int(bars), len(daily)),
        'daily': {
            'candles': _candles(daily_window, len(daily_window)),
            'trend': _trend_lines(daily, bars),
            'sequence': _sequence_points(daily, bars),
            'decisions': _decision_points(daily, intraday, bars),
            'macd': _macd_points(daily, bars),
        },
        'intraday': {},
    }
    for interval in INTRADAY_STRUCTURE_INTERVALS:
        dfi = intraday.get(interval)
        if dfi is None:
            continue
        dfi_window = _window_from_daily_start(dfi, daily_window)
        result['intraday'][interval] = {
            'candles': _candles(dfi_window, len(dfi_window)),
            'macd': _macd_points(dfi, len(dfi_window), start_date=daily_start_date),
            'structure': _structure_points(dfi, len(dfi_window), start_date=daily_start_date),
        }
    return result


def build_chart_data(stock_input: str, bars: int = 180) -> dict:
    detections = detect_market(stock_input)
    results = []
    for market, stock_code in detections:
        try:
            results.append(build_chart_data_for_market(market, stock_code, bars=bars))
        except ValueError as e:
            results.append({
                'market': market,
                'market_label': MARKET_LABEL[market],
                'stock_code': stock_code,
                'display_code': format_stock_code(market, stock_code),
                'error': str(e),
            })
    return {
        'success': True,
        'input': stock_input,
        'count': len(results),
        'results': results,
    }
