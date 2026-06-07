"""Backtest service for the integrated daily decision model."""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from app.algos.config import StrategyConfig
from app.algos.integrated_decision import (
    INTRADAY_STRUCTURE_INTERVALS,
    evaluate_integrated_dataframe,
)
from app.algos.structure import compute_structure_event_study
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


def _warmup_bars(config: BacktestConfig, strategy_config: StrategyConfig) -> int:
    if config.warmup_bars is not None:
        return max(0, int(config.warmup_bars))
    return max(360, int(strategy_config.long_period) * 4)


def _first_index_on_or_after(daily: pd.DataFrame, start: pd.Timestamp) -> int:
    for i, ts in enumerate(daily.index):
        if _local_day(ts) >= start:
            return i
    return len(daily)


def _slice_intraday_from(
    intraday: Dict[str, pd.DataFrame],
    data_start: pd.Timestamp,
) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    for interval, df_i in intraday.items():
        if df_i is None or df_i.empty:
            continue
        mask = pd.Series(
            [_local_day(ts) >= data_start for ts in df_i.index],
            index=df_i.index,
        )
        sub = df_i.loc[mask]
        if len(sub) >= 30:
            out[interval] = sub
    return out


def _merge_event_studies(studies: list[dict]) -> dict:
    merged = {
        'by_event': {},
        'grouped_by_trend_state': {},
        'grouped_by_timeframe': {},
    }
    for study in studies:
        if not study:
            continue
        for section in merged:
            for key, value in study.get(section, {}).items():
                existing = merged[section].setdefault(key, {'count': 0})
                count_a = int(existing.get('count', 0))
                count_b = int(value.get('count', 0))
                total = count_a + count_b
                if total <= 0:
                    continue
                for metric, metric_value in value.items():
                    if metric == 'count':
                        continue
                    prev = float(existing.get(metric, 0.0))
                    existing[metric] = _round(
                        (prev * count_a + float(metric_value) * count_b) / total,
                        6,
                    )
                existing['count'] = total
    return merged


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


def _strategy_config_from_backtest(config: BacktestConfig) -> StrategyConfig:
    return StrategyConfig(
        enable_trend=bool(config.enable_trend),
        enable_structure=bool(config.enable_structure),
        enable_structure_position_adjustment=bool(config.enable_structure_position_adjustment),
        enable_sequence=bool(config.enable_sequence),
        enable_execution_rules=bool(config.enable_execution_rules),
    )


def _buy_hold_decisions(daily: pd.DataFrame) -> pd.DataFrame:
    decisions = pd.DataFrame(index=daily.index)
    decisions['final_target_position'] = 10.0
    decisions['raw_target_position'] = 10.0
    decisions['actual_position'] = 10.0
    decisions['order_delta'] = 0.0
    decisions['order_weight'] = 0.0
    decisions['action'] = 'HOLD'
    decisions['confidence_label'] = 'benchmark'
    decisions['trend_state'] = 'BUY_HOLD'
    decisions['structure_adjustment'] = 0.0
    decisions['sequence_adjustment'] = 0.0
    decisions['decision_reason'] = '买入持有基准'
    decisions['structure_effect'] = 'none'
    decisions['sequence_effect'] = 'none'
    decisions['hard_exit'] = False
    decisions['high9_active'] = False
    decisions['low9_active'] = False
    if len(decisions):
        decisions.iloc[0, decisions.columns.get_loc('actual_position')] = 0.0
        decisions.iloc[0, decisions.columns.get_loc('order_delta')] = 10.0
        decisions.iloc[0, decisions.columns.get_loc('order_weight')] = 1.0
        decisions.iloc[0, decisions.columns.get_loc('action')] = 'BUY'
    return decisions


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
            'primary_regime': row.get('primary_regime'),
            'tactical_state': row.get('tactical_state'),
            'structure_adjustment': _round(row.get('structure_adjustment'), 4),
            'sequence_adjustment': _round(row.get('sequence_adjustment'), 4),
            'signal_reason': row.get('decision_reason') or '',
            'structure_effect': row.get('structure_effect') or 'none',
            'sequence_effect': row.get('sequence_effect') or 'none',
            'hard_exit': bool(row.get('hard_exit', False)),
            'hard_exit_reason': row.get('hard_exit_reason') or '',
            'target_reversal_warning': row.get('target_reversal_warning') or '',
            'high9_active': bool(row.get('high9_active', False)),
            'low9_active': bool(row.get('low9_active', False)),
            'long_mid_prev': _round(row.get('long_mid_prev'), 4),
            'long_mid_slope': _round(row.get('long_mid_slope'), 8),
            'short_mid_prev': _round(row.get('short_mid_prev'), 4),
            'short_mid_slope': _round(row.get('short_mid_slope'), 8),
            'previous_20_high': _round(row.get('previous_20_high'), 4),
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
    cost_drag_value = 0.0
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
                            slippage_cost = qty * open_price * slip
                            gross = qty * price
                            commission = gross * commission_rate
                            if gross + commission <= cash + 1e-9:
                                cash -= gross + commission
                                avg_cost = (
                                    (shares * avg_cost) + gross + commission
                                ) / (shares + qty)
                                shares += qty
                                turnover_value += gross
                                cost_drag_value += commission + slippage_cost
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
                                    signal_reason=signal.get('decision_reason') or '',
                                    trend_state=signal.get('trend_state') or '',
                                    structure_effect=signal.get('structure_effect') or 'none',
                                    sequence_effect=signal.get('sequence_effect') or 'none',
                                    hard_exit=bool(signal.get('hard_exit', False)),
                                ))

                    elif delta_value < -1e-9 and shares > 0:
                        price = open_price * (1.0 - slip)
                        desired_qty = (-delta_value) / price
                        qty = min(shares, _round_lot(desired_qty, config.lot_size))
                        if qty > 0:
                            slippage_cost = qty * open_price * slip
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
                            cost_drag_value += commission + slippage_cost
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
                                signal_reason=signal.get('decision_reason') or '',
                                trend_state=signal.get('trend_state') or '',
                                structure_effect=signal.get('structure_effect') or 'none',
                                sequence_effect=signal.get('sequence_effect') or 'none',
                                hard_exit=bool(signal.get('hard_exit', False)),
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
        cost_drag_value=cost_drag_value,
        daily=daily,
        decisions=decisions,
        sim_indices=sim_indices,
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
    cost_drag_value: float = 0.0,
    daily: Optional[pd.DataFrame] = None,
    decisions: Optional[pd.DataFrame] = None,
    sim_indices: Optional[list[int]] = None,
) -> BacktestMetrics:
    if not equity_curve:
        return BacktestMetrics()

    equity = pd.Series([p.equity for p in equity_curve], dtype='float64')
    returns = equity.pct_change()
    returns_no_na = returns.dropna()
    final_equity = float(equity.iloc[-1])
    total_return = final_equity / initial_cash - 1.0
    periods = len(equity_curve)
    annual_return = (
        (final_equity / initial_cash) ** (252.0 / max(periods - 1, 1)) - 1.0
        if final_equity > 0 else -1.0
    )
    annual_vol = float(returns_no_na.std(ddof=0) * math.sqrt(252)) if len(returns_no_na) else 0.0
    sharpe = None
    if len(returns_no_na) and float(returns_no_na.std(ddof=0)) > 0:
        sharpe = float(returns_no_na.mean() / returns_no_na.std(ddof=0) * math.sqrt(252))

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
    position_series = pd.Series(positions, dtype='float64')
    avg_equity = float(equity.mean()) if len(equity) else initial_cash
    benchmark_return = (
        equity_curve[-1].benchmark_equity / initial_cash - 1.0
        if initial_cash > 0 else 0.0
    )
    avg_position = float(position_series.mean()) if len(position_series) else 0.0

    avg_position_on_up_days = 0.0
    avg_position_on_down_days = 0.0
    up_capture_ratio = 0.0
    down_capture_ratio = 0.0
    cumulative_allocation_drag = 0.0
    missed_upside_return = 0.0
    fixed_same_average_position_total_return = 0.0
    timing_alpha_vs_fixed_same_position = 0.0
    target_flip_count = 0
    count_10_to_4 = 0
    count_4_to_10 = 0
    below_60_long_up_days = 0
    below_60_bull_days = 0
    below_80_long_mid_up_days = 0
    structure_reduction_days = 0
    sequence_reduction_days = 0
    trend_distribution: Dict[str, int] = {}
    position_bucket_attribution: Dict[str, Dict[str, float]] = {}
    trend_state_attribution: Dict[str, Dict[str, float]] = {}
    trend_regime_attribution: Dict[str, Dict[str, float]] = {}

    if daily is not None and sim_indices is not None and len(sim_indices) == len(equity_curve):
        close = pd.Series(
            [float(daily['Close'].iloc[i]) for i in sim_indices],
            dtype='float64',
        )
        benchmark_returns = close.pct_change()
        aligned_strategy_returns = returns.iloc[1:]
        aligned_benchmark_returns = benchmark_returns.iloc[1:]
        aligned_positions = position_series.iloc[1:]
        fixed_equity = (1.0 + aligned_benchmark_returns.fillna(0.0) * avg_position).prod()
        fixed_same_average_position_total_return = float(fixed_equity - 1.0)
        timing_alpha_vs_fixed_same_position = float(
            total_return - fixed_same_average_position_total_return
        )

        up_mask = aligned_benchmark_returns > 0
        down_mask = aligned_benchmark_returns < 0
        if bool(up_mask.any()):
            avg_position_on_up_days = float(aligned_positions[up_mask].mean())
            denom = float(aligned_benchmark_returns[up_mask].sum())
            up_capture_ratio = (
                float(aligned_strategy_returns[up_mask].sum()) / denom
                if abs(denom) > 1e-12 else 0.0
            )
        if bool(down_mask.any()):
            avg_position_on_down_days = float(aligned_positions[down_mask].mean())
            denom = float(aligned_benchmark_returns[down_mask].sum())
            down_capture_ratio = (
                float(aligned_strategy_returns[down_mask].sum()) / denom
                if abs(denom) > 1e-12 else 0.0
            )

        prev_positions = position_series.iloc[:-1].reset_index(drop=True)
        bench_no_na = aligned_benchmark_returns.reset_index(drop=True)
        cumulative_allocation_drag = float(((prev_positions - 1.0) * bench_no_na).sum())
        missed_upside_return = float(
            ((1.0 - prev_positions).clip(lower=0.0) * bench_no_na.clip(lower=0.0)).sum()
        )

        attr_df = pd.DataFrame({
            'strategy_return': aligned_strategy_returns.reset_index(drop=True),
            'benchmark_return': aligned_benchmark_returns.reset_index(drop=True),
            'position': aligned_positions.reset_index(drop=True),
        }).dropna(subset=['strategy_return', 'benchmark_return'])
        if not attr_df.empty:
            buckets = [
                (0.0, 0.2, '0-20%'),
                (0.2, 0.4, '20-40%'),
                (0.4, 0.6, '40-60%'),
                (0.6, 0.8, '60-80%'),
                (0.8, 1.01, '80-100%'),
            ]
            for lo, hi, label in buckets:
                if hi >= 1.0:
                    mask = (attr_df['position'] >= lo) & (attr_df['position'] <= 1.0)
                else:
                    mask = (attr_df['position'] >= lo) & (attr_df['position'] < hi)
                sub = attr_df.loc[mask]
                position_bucket_attribution[label] = {
                    'days': int(len(sub)),
                    'avg_position': _round(float(sub['position'].mean()) if len(sub) else 0.0, 6),
                    'strategy_return': _round(float(sub['strategy_return'].sum()) if len(sub) else 0.0, 6),
                    'benchmark_return': _round(float(sub['benchmark_return'].sum()) if len(sub) else 0.0, 6),
                }

        if decisions is not None and not decisions.empty:
            decision_window = decisions.reindex(daily.index).iloc[sim_indices]
            trend_states = decision_window.get('trend_state', pd.Series(index=decision_window.index, dtype='object'))
            trend_distribution = {
                str(k): int(v)
                for k, v in trend_states.fillna('UNKNOWN').value_counts().to_dict().items()
            }
            long_up = trend_states.isin(['UP_STRONG', 'UP_PULLBACK', 'UP_WEAK'])
            below_60_long_up_days = int(((position_series < 0.6).values & long_up.fillna(False).values).sum())
            primary_regime = decision_window.get(
                'primary_regime',
                pd.Series(index=decision_window.index, dtype='object'),
            ).fillna('UNKNOWN')
            below_60_bull_days = int(
                ((position_series < 0.6).values & primary_regime.eq('BULL').values).sum()
            )
            long_mid_prev = pd.to_numeric(
                decision_window.get('long_mid_prev', pd.Series(index=decision_window.index)),
                errors='coerce',
            )
            long_mid_slope = pd.to_numeric(
                decision_window.get('long_mid_slope', pd.Series(index=decision_window.index)),
                errors='coerce',
            )
            close_window = pd.to_numeric(daily['Close'].reindex(decision_window.index), errors='coerce')
            long_mid_up_mask = (
                close_window.gt(long_mid_prev)
                & long_mid_slope.gt(0)
            ).fillna(False)
            below_80_long_mid_up_days = int(
                ((position_series < 0.8).values & long_mid_up_mask.values).sum()
            )

            attr_with_state = attr_df.copy() if 'attr_df' in locals() else pd.DataFrame()
            if not attr_with_state.empty:
                trend_aligned = trend_states.iloc[1:].reset_index(drop=True).fillna('UNKNOWN')
                regime_aligned = primary_regime.iloc[1:].reset_index(drop=True).fillna('UNKNOWN')
                attr_with_state['trend_state'] = trend_aligned.loc[attr_with_state.index].values
                attr_with_state['primary_regime'] = regime_aligned.loc[attr_with_state.index].values

                for state, sub in attr_with_state.groupby('trend_state'):
                    trend_state_attribution[str(state)] = {
                        'days': int(len(sub)),
                        'avg_position': _round(float(sub['position'].mean()), 6),
                        'strategy_return': _round(float(sub['strategy_return'].sum()), 6),
                        'benchmark_return': _round(float(sub['benchmark_return'].sum()), 6),
                    }
                for regime, sub in attr_with_state.groupby('primary_regime'):
                    trend_regime_attribution[str(regime)] = {
                        'days': int(len(sub)),
                        'avg_position': _round(float(sub['position'].mean()), 6),
                        'strategy_return': _round(float(sub['strategy_return'].sum()), 6),
                        'benchmark_return': _round(float(sub['benchmark_return'].sum()), 6),
                    }
            structure_adj = pd.to_numeric(
                decision_window.get('structure_adjustment', pd.Series(index=decision_window.index)),
                errors='coerce',
            ).fillna(0.0)
            sequence_adj = pd.to_numeric(
                decision_window.get('sequence_adjustment', pd.Series(index=decision_window.index)),
                errors='coerce',
            ).fillna(0.0)
            structure_reduction_days = int((structure_adj < 0).sum())
            sequence_reduction_days = int((sequence_adj < 0).sum())

            targets = pd.to_numeric(
                decision_window.get('final_target_position', pd.Series(index=decision_window.index)),
                errors='coerce',
            ).dropna()
            if len(targets) >= 2:
                prev_targets = targets.shift(1)
                target_flip_count = int((targets.sub(prev_targets).abs() >= 4.0).sum())
                count_10_to_4 = int(((prev_targets >= 9.5) & (targets <= 4.5)).sum())
                count_4_to_10 = int(((prev_targets <= 4.5) & (targets >= 9.5)).sum())

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
        average_position=_round(avg_position, 6),
        avg_position=_round(avg_position, 6),
        avg_position_on_up_days=_round(avg_position_on_up_days, 6),
        avg_position_on_down_days=_round(avg_position_on_down_days, 6),
        up_capture_ratio=_round(up_capture_ratio, 6),
        down_capture_ratio=_round(down_capture_ratio, 6),
        cumulative_allocation_drag=_round(cumulative_allocation_drag, 6),
        cumulative_cost_drag=_round(cost_drag_value / initial_cash if initial_cash > 0 else 0.0, 6),
        missed_upside_return=_round(missed_upside_return, 6),
        fixed_same_average_position_total_return=_round(fixed_same_average_position_total_return, 6),
        timing_alpha_vs_fixed_same_position=_round(timing_alpha_vs_fixed_same_position, 6),
        target_flip_count=target_flip_count,
        count_10_to_4=count_10_to_4,
        count_4_to_10=count_4_to_10,
        days_position_below_60pct_when_long_trend_up=below_60_long_up_days,
        days_position_below_60pct_when_bull=below_60_bull_days,
        days_position_below_80pct_when_close_above_long_mid=below_80_long_mid_up_days,
        structure_caused_reduction_days=structure_reduction_days,
        sequence_caused_reduction_days=sequence_reduction_days,
        trend_state_days_distribution=trend_distribution,
        position_bucket_attribution=position_bucket_attribution,
        trend_state_attribution=trend_state_attribution,
        trend_regime_attribution=trend_regime_attribution,
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

    strategy_config = _strategy_config_from_backtest(config)
    warmup_bars = _warmup_bars(config, strategy_config)
    requested_start = _date_or_none(config.start_date)
    if requested_start is not None:
        start_pos = _first_index_on_or_after(daily, requested_start)
        if start_pos >= len(daily):
            raise ValueError('回测起始日期晚于本地数据范围')
        data_pos = max(0, start_pos - warmup_bars)
    else:
        start_pos = warmup_bars if len(daily) > warmup_bars + 1 else 0
        data_pos = 0

    daily_for_signals = daily.iloc[data_pos:].copy()
    if daily_for_signals is None or len(daily_for_signals) < int(config.min_bars):
        raise ValueError(f'warm-up 后日线数据不足（至少需要 {config.min_bars} 根日线）')
    data_start_ts = pd.Timestamp(daily_for_signals.index[0])
    backtest_start_ts = pd.Timestamp(daily.index[start_pos])
    available_warmup = max(0, start_pos - data_pos)
    if available_warmup >= warmup_bars:
        effective_signal_start_ts = backtest_start_ts
    else:
        effective_pos = min(warmup_bars, len(daily_for_signals) - 1)
        effective_signal_start_ts = pd.Timestamp(daily_for_signals.index[effective_pos])

    intraday_for_signals = _slice_intraday_from(
        intraday,
        _local_day(data_start_ts),
    )

    if config.enable_trend:
        decisions = evaluate_integrated_dataframe(
            daily_for_signals,
            intraday_for_signals,
            config=strategy_config,
        )
    else:
        decisions = _buy_hold_decisions(daily_for_signals)
    result = simulate_backtest(daily_for_signals, decisions, config)
    payload = result.to_api_dict()
    trend_state_by_day = {
        _local_day(idx): str(row.get('trend_state') or 'UNKNOWN')
        for idx, row in decisions.iterrows()
    }
    event_studies = [
        compute_structure_event_study(
            daily_for_signals,
            timeframe='daily',
            trend_state_by_day=trend_state_by_day,
            config=strategy_config,
        )
    ]
    for interval, df_i in intraday_for_signals.items():
        event_studies.append(
            compute_structure_event_study(
                df_i,
                timeframe=interval,
                trend_state_by_day=trend_state_by_day,
                config=strategy_config,
            )
        )
    payload.update({
        'market': market,
        'market_label': MARKET_LABEL[market],
        'stock_code': stock_code,
        'display_code': format_stock_code(market, stock_code),
        'interval': 'integrated',
        'bars': len(daily_for_signals),
        'data_start': _to_iso(data_start_ts),
        'backtest_start': _to_iso(backtest_start_ts),
        'warmup_bars': warmup_bars,
        'available_warmup_bars': available_warmup,
        'effective_signal_start': _to_iso(effective_signal_start_ts),
        'backtest_bars': len(result.equity_curve),
        'structure_event_study': _merge_event_studies(event_studies),
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
