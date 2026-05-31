"""分析服务：多周期整合决策 + API 映射。

业务规则（与产品约定一致）：
- **趋势**：仅日线。
- **结构**：仅 60 / 90 / 120 分钟（从 5min 合成后分别计算 MACD 结构，再合并）。
- **序列**：默认日线九转（与「趋势为王」同频；若以后单独指定序列周期可扩展）。

`/stock/decision` 的 `interval` 请求参数仅作**回显**（`requested_interval`），不再改变算法；
响应顶层 `interval` 固定为 `integrated`。
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from app.services.stock_service import (
    detect_market, get_table_name, format_stock_code, MARKET_LABEL,
)
from app.services.resample import drop_partial_bars_for_trade, resample_ohlcv
from app.models.database import db_manager
from app.algos.decision import DecisionEngine

logger = logging.getLogger(__name__)
_engine = DecisionEngine()

STRUCTURE_INTERVALS = ('60min', '90min', '120min')


def _normalize_ohlcv(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    if df is None or df.empty:
        return None
    if 'timestamp' in df.columns:
        df = df.set_index('timestamp')
    df = df.sort_index()
    df = df.rename(columns={c: c.capitalize() for c in df.columns})
    if 'Close' not in df.columns:
        return None
    return df


def _load_5min(market: str, stock_code: str) -> Optional[pd.DataFrame]:
    table = get_table_name(market, stock_code, '5min')
    df = db_manager.get_data(market, table, limit=20000)
    return _normalize_ohlcv(df)


def _resample_or_none(df_5m: Optional[pd.DataFrame], target_interval: str) -> Optional[pd.DataFrame]:
    if df_5m is None or df_5m.empty:
        return None
    try:
        return resample_ohlcv(df_5m, target_interval)
    except Exception as e:
        logger.warning(f"resample 失败 (target={target_interval}): {e}")
        return None


def _structure_active_on_interval(df_5m: pd.DataFrame, itv: str) -> bool:
    df = _resample_or_none(df_5m, itv)
    if df is None or len(df) < 30:
        return False
    df = drop_partial_bars_for_trade(df, itv)
    if df is None or len(df) < 30:
        return False
    try:
        ev = _engine.structure.evaluate(df)
        last = ev.iloc[-1]
        return bool(last.get('top_structure_active')) or bool(last.get('bottom_structure_active'))
    except Exception:
        return False


def _enrich_resonance_integrated(base_summary: dict, df_5m: pd.DataFrame) -> None:
    """共振：使用已通过交易确认过滤的三层计划结果。"""
    sig = base_summary.get('signals') or {}
    plan_struct = base_summary.get('structure') or {}
    active_periods = [
        p for p in (plan_struct.get('active_periods') or [])
        if p in STRUCTURE_INTERVALS
    ]
    if not active_periods:
        sig['resonance'] = None
        return

    k = len(active_periods)
    level = (plan_struct.get('resonance_weight')
             or (2.0 if k >= 3 else (1.5 if k == 2 else 1.0)))
    sig['resonance'] = {'level': level, 'periods': active_periods}


def analyze_stock(stock_input: str, interval: str = 'daily') -> dict:
    detections = detect_market(stock_input)

    results = []
    for market, stock_code in detections:
        display_code = format_stock_code(market, stock_code)
        df_5m = _load_5min(market, stock_code)

        if df_5m is None:
            table_name = get_table_name(market, stock_code, '5min')
            results.append({
                'market': market,
                'market_label': MARKET_LABEL[market],
                'stock_code': stock_code,
                'display_code': display_code,
                'error': f'数据表 {table_name} 不存在或为空，请先注册该股票工作流',
            })
            continue

        df_daily = _resample_or_none(df_5m, 'daily')
        if df_daily is None or len(df_daily) < 30:
            results.append({
                'market': market,
                'market_label': MARKET_LABEL[market],
                'stock_code': stock_code,
                'display_code': display_code,
                'error': '5min 数据不足以合成日线（至少需要 30 根日线）',
            })
            continue

        intraday: dict[str, pd.DataFrame] = {}
        for itv in STRUCTURE_INTERVALS:
            dfi = _resample_or_none(df_5m, itv)
            if dfi is not None and len(dfi) >= 30:
                intraday[itv] = dfi

        try:
            summary = _engine.summary_integrated(df_daily, intraday)
        except Exception as e:
            logger.error(f"决策引擎执行失败 ({market}/{stock_code}): {e}")
            results.append({
                'market': market,
                'market_label': MARKET_LABEL[market],
                'stock_code': stock_code,
                'display_code': display_code,
                'error': f'算法计算失败: {str(e)}',
            })
            continue

        _enrich_resonance_integrated(summary, df_5m)

        record = {
            'market': market,
            'market_label': MARKET_LABEL[market],
            'stock_code': stock_code,
            'display_code': display_code,
            'timestamp': summary.get('timestamp'),
            'close': summary.get('close'),
            'action': summary.get('action'),
            'weight': summary.get('weight'),
            'confidence': summary.get('confidence'),
            'execute_at': summary.get('execute_at'),
            'position': summary.get('position'),
            'signals': summary.get('signals'),
            'standards': summary.get('standards'),
            'view': summary.get('view'),
            'next_day_plan': summary.get('next_day_plan'),
            'trend': summary.get('trend'),
            'structure': summary.get('structure'),
            'sequence': summary.get('sequence'),
            'decision': summary.get('decision'),
            'explanation': summary.get('explanation'),
        }
        results.append(record)

    return {
        'success': True,
        'input': stock_input,
        'interval': 'integrated',
        'requested_interval': interval,
        'count': len(results),
        'results': results,
    }
