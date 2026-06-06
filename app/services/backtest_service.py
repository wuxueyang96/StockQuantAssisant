"""Backtest service for the integrated daily decision model."""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from app.algos.integrated_decision import (
    INTRADAY_STRUCTURE_INTERVALS,
    evaluate_integrated_dataframe,
)
from app.models.database import db_manager
from app.schemas.backtest import (
    BacktestConfig,
    BacktestMetrics,
    BacktestResult,
    EquityPoint,
    TradeRecord,
)
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
    required = ('Open', 'High', 'Low', 'Close', 'Volume')
    if not all(c in df.columns for c in required):
        return None
    return df


def _load_5min(market: str, stock_code: str, limit: int = 50000) -> Optional[pd.DataFrame]:
    table = get_table_name(market, stock_code, '5min')
    return _normalize_ohlcv(db_manager.get_data(market, table, limit=limit))


def _frames_from_5min(df_5m: pd.DataFrame) -> tuple[pd.DataFrame, Dict[str, pd.DataFrame]]:
    daily = resample_ohlcv(df_5m, 'daily')
    intraday: Dict[str, pd.DataFrame] = {}
    for interval in INTRADAY_STRUCTURE_INTERVALS:
        dfi = resample_ohlcv(df_5m, interval)
        if dfi is not None and len(dfi) >= 30:
            intraday[interval] = dfi
    return daily, intraday


def _date_or_none(value) -> Optional[pd.Timestamp]:
    if value in (None, ''):
        return None
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts.normalize()


def _local_day(ts) -> pd.Timestamp:
    value = pd.Timestamp(ts)
    if value.tzinfo is not None:
        value = value.tz_localize(None)
    return value.normalize()


def _in_range(ts, start: Optional[pd.Timestamp], end: Optional[pd.Timestamp]) -> bool:
    day = _local_day(ts)
    if start is not None and day < start:
        return False
    if end is not None and day > end:
        return False
    return True


def _to_iso(ts) -> str:
    return pd.Timestamp(ts).isoformat()


def _round(value: float, digits: int = 6) -> float:
    if value is None or pd.isna(value):
        return 0.0
    return round(float(value), digits)


def _target_weight(row: pd.Series) -> Optional[float]:
    target = row.get('final_target_position')
    if target is None or pd.isna(target):
        return None
    return float(np.clip(float(target) / 10.0, 0.0, 1.0))


def _round_lot(quantity: float, lot_size: int) -> float:
    lot = max(1, int(lot_size or 1))
    if lot <= 1:
        return math.floor(quantity)
    return math.floor(quantity / lot) * lot


def _build_signals(
    daily: pd.DataFrame,
    decisions: pd.DataFrame,
    start: Optional[pd.Timestamp],
    end: Optional[pd.Timestamp],
) -> list[dict]:
    rows = []
    for i in range(len(daily) - 1):
        signal_date = daily.index[i]
        execute_date = daily.index[i + 1]
        if not _in_range(signal_date, start, end):
            continue
        if not _in_range(execute_date, start, end):
            continue
        row = decisions.iloc[i]
        target = row.get('final_target_position')
        rows.append({
            'date': _to_iso(signal_date),
            'execute_date': _to_iso(execute_date),
            'action': row.get('action') or 'WAIT',
            'target_position': None if pd.isna(target) else _round(target, 4),
            'target_weight': None if pd.isna(target) else _round(float(target) / 10.0, 4),
            'order_weight': _round(row.get('order_weight'), 6),
            'order_delta': _round(row.get('order_delta'), 4),
            'confidence_label': row.get('confidence_label'),
            'trend_state': row.get('trend_state'),
            'structure_adjustment': _round(row.get('structure_adjustment'), 4),
            'high9_active': bool(row.get('high9_active', False)),
            'low9_active': bool(row.get('low9_active', False)),
        })
    return rows


def simulate_backtest(
    daily: pd.DataFrame,
    decisions: pd.DataFrame,
    config: BacktestConfig,
) -> BacktestResult:
    """Simulate T close signal -> T+1 open execution."""
    if daily is None or daily.empty:
        raise ValueError('daily 数据为空')
    if len(daily) != len(decisions):
        raise ValueError('daily 与 decisions 长度不一致')
    if config.initial_cash <= 0:
        raise ValueError('initial_cash 必须大于 0')
    if config.commission_rate < 0:
        raise ValueError('commission_rate 不能为负')
    if config.slippage_bps < 0:
        raise ValueError('slippage_bps 不能为负')

    daily = daily.sort_index()
    decisions = decisions.reindex(daily.index)
    start = _date_or_none(config.start_date)
    end = _date_or_none(config.end_date)
    sim_indices = [
        i for i, ts in enumerate(daily.index)
        if _in_range(ts, start, end)
    ]
    if len(sim_indices) < 2:
        raise ValueError('回测区间至少需要 2 根日线')

    cash = float(config.initial_cash)
    shares = 0.0
    avg_cost = 0.0
    slip = float(config.slippage_bps) / 10000.0
    commission_rate = float(config.commission_rate)
    equity_curve: list[EquityPoint] = []
    trades: list[TradeRecord] = []
    realized: list[float] = []
    turnover_value = 0.0
    first_close = float(daily['Close'].iloc[sim_indices[0]])
    running_peak = float(config.initial_cash)

    for idx in sim_indices:
        ts = daily.index[idx]
        row = daily.iloc[idx]

        if idx > 0:
            signal_ts = daily.index[idx - 1]
            if _in_range(signal_ts, start, end):
                signal = decisions.iloc[idx - 1]
                target_weight = _target_weight(signal)
                signal_action = str(signal.get('action') or '').upper()
                if target_weight is not None and signal_action in ('BUY', 'SELL'):
                    open_price = float(row['Open'])
                    equity_at_open = cash + shares * open_price
                    target_value = equity_at_open * target_weight
                    current_value = shares * open_price
                    delta_value = target_value - current_value

                    if delta_value > 1e-9 and cash > 0:
                        price = open_price * (1.0 + slip)
                        gross = min(delta_value, cash / (1.0 + commission_rate))
                        qty = _round_lot(gross / price, config.lot_size)
                        if qty > 0:
                            gross = qty * price
                            commission = gross * commission_rate
                            if gross + commission <= cash + 1e-9:
                                cash -= gross + commission
                                avg_cost = (
                                    (shares * avg_cost) + gross + commission
                                ) / (shares + qty)
                                shares += qty
                                turnover_value += gross
                                trades.append(TradeRecord(
                                    date=_to_iso(ts),
                                    signal_date=_to_iso(signal_ts),
                                    side='BUY',
                                    action=str(signal.get('action') or 'BUY'),
                                    price=_round(price, 4),
                                    quantity=_round(qty, 4),
                                    gross_value=_round(gross, 4),
                                    commission=_round(commission, 4),
                                    cash_after=_round(cash, 4),
                                    shares_after=_round(shares, 4),
                                    target_position=_round(signal.get('final_target_position'), 4),
                                    target_weight=_round(target_weight, 4),
                                    confidence_label=signal.get('confidence_label'),
                                ))

                    elif delta_value < -1e-9 and shares > 0:
                        price = open_price * (1.0 - slip)
                        desired_qty = (-delta_value) / price
                        qty = min(shares, _round_lot(desired_qty, config.lot_size))
                        if qty > 0:
                            gross = qty * price
                            commission = gross * commission_rate
                            cash += gross - commission
                            pnl = (price - avg_cost) * qty - commission
                            shares -= qty
                            if shares <= 1e-9:
                                shares = 0.0
                                avg_cost = 0.0
                            realized.append(float(pnl))
                            turnover_value += gross
                            trades.append(TradeRecord(
                                date=_to_iso(ts),
                                signal_date=_to_iso(signal_ts),
                                side='SELL',
                                action=str(signal.get('action') or 'SELL'),
                                price=_round(price, 4),
                                quantity=_round(qty, 4),
                                gross_value=_round(gross, 4),
                                commission=_round(commission, 4),
                                cash_after=_round(cash, 4),
                                shares_after=_round(shares, 4),
                                target_position=_round(signal.get('final_target_position'), 4),
                                target_weight=_round(target_weight, 4),
                                realized_pnl=_round(pnl, 4),
                                confidence_label=signal.get('confidence_label'),
                            ))

        close = float(row['Close'])
        position_value = shares * close
        equity = cash + position_value
        running_peak = max(running_peak, equity)
        drawdown = equity / running_peak - 1.0 if running_peak > 0 else 0.0
        benchmark_equity = (
            float(config.initial_cash) * close / first_close
            if first_close > 0 else float(config.initial_cash)
        )
        equity_curve.append(EquityPoint(
            date=_to_iso(ts),
            close=_round(close, 4),
            equity=_round(equity, 4),
            cash=_round(cash, 4),
            position_value=_round(position_value, 4),
            shares=_round(shares, 4),
            position_weight=_round(position_value / equity if equity > 0 else 0.0, 6),
            benchmark_equity=_round(benchmark_equity, 4),
            drawdown=_round(drawdown, 6),
        ))

    metrics = _compute_metrics(
        equity_curve=equity_curve,
        trades=trades,
        realized=realized,
        initial_cash=float(config.initial_cash),
        turnover_value=turnover_value,
    )
    signals = _build_signals(daily, decisions, start, end)
    return BacktestResult(
        config=config,
        metrics=metrics,
        equity_curve=equity_curve,
        trades=trades,
        signals=signals,
    )


def _compute_metrics(
    *,
    equity_curve: list[EquityPoint],
    trades: list[TradeRecord],
    realized: list[float],
    initial_cash: float,
    turnover_value: float,
) -> BacktestMetrics:
    if not equity_curve:
        return BacktestMetrics()

    equity = pd.Series([p.equity for p in equity_curve], dtype='float64')
    returns = equity.pct_change().dropna()
    final_equity = float(equity.iloc[-1])
    total_return = final_equity / initial_cash - 1.0
    periods = len(equity_curve)
    annual_return = (
        (final_equity / initial_cash) ** (252.0 / max(periods - 1, 1)) - 1.0
        if final_equity > 0 else -1.0
    )
    annual_vol = float(returns.std(ddof=0) * math.sqrt(252)) if len(returns) else 0.0
    sharpe = None
    if len(returns) and float(returns.std(ddof=0)) > 0:
        sharpe = float(returns.mean() / returns.std(ddof=0) * math.sqrt(252))

    dd = [p.drawdown for p in equity_curve]
    max_drawdown = min(dd) if dd else 0.0
    wins = [x for x in realized if x > 0]
    losses = [x for x in realized if x < 0]
    realized_count = len(wins) + len(losses)
    win_rate = len(wins) / realized_count if realized_count else None
    profit_factor = None
    if losses:
        profit_factor = sum(wins) / abs(sum(losses)) if wins else 0.0
    elif wins:
        profit_factor = None

    positions = [p.position_weight for p in equity_curve]
    avg_equity = float(equity.mean()) if len(equity) else initial_cash
    benchmark_return = (
        equity_curve[-1].benchmark_equity / initial_cash - 1.0
        if initial_cash > 0 else 0.0
    )
    return BacktestMetrics(
        total_return=_round(total_return, 6),
        annual_return=_round(annual_return, 6),
        annual_volatility=_round(annual_vol, 6),
        sharpe=None if sharpe is None else _round(sharpe, 6),
        max_drawdown=_round(max_drawdown, 6),
        win_rate=None if win_rate is None else _round(win_rate, 6),
        profit_factor=None if profit_factor is None else _round(profit_factor, 6),
        trade_count=len(trades),
        winning_trades=len(wins),
        losing_trades=len(losses),
        average_position=_round(float(np.mean(positions)) if positions else 0.0, 6),
        exposure=_round(float(np.mean([p > 0.01 for p in positions])) if positions else 0.0, 6),
        turnover=_round(turnover_value / avg_equity if avg_equity > 0 else 0.0, 6),
        benchmark_total_return=_round(benchmark_return, 6),
        excess_return=_round(total_return - benchmark_return, 6),
    )


def run_backtest_for_market(
    market: str,
    stock_code: str,
    config: BacktestConfig,
) -> dict:
    df_5m = _load_5min(market, stock_code)
    table = get_table_name(market, stock_code, '5min')
    if df_5m is None or df_5m.empty:
        raise ValueError(f'数据表 {table} 不存在或为空，请先注册该股票工作流')

    daily, intraday = _frames_from_5min(df_5m)
    if daily is None or len(daily) < int(config.min_bars):
        raise ValueError(f'5min 数据不足以合成回测日线（至少需要 {config.min_bars} 根日线）')

    decisions = evaluate_integrated_dataframe(daily, intraday)
    result = simulate_backtest(daily, decisions, config)
    payload = result.to_api_dict()
    payload.update({
        'market': market,
        'market_label': MARKET_LABEL[market],
        'stock_code': stock_code,
        'display_code': format_stock_code(market, stock_code),
        'interval': 'integrated',
        'bars': len(daily),
    })
    return payload


def run_backtest(stock_input: str, **kwargs: Any) -> dict:
    config = BacktestConfig(stock=stock_input, **kwargs)
    detections = detect_market(stock_input)
    results = []
    for market, stock_code in detections:
        try:
            results.append(run_backtest_for_market(market, stock_code, config))
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
