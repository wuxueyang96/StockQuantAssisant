"""Backtest dataclasses for API/service payloads."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class BacktestConfig:
    stock: str = ''
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    initial_cash: float = 100000.0
    commission_rate: float = 0.0003
    slippage_bps: float = 5.0
    min_bars: int = 90
    lot_size: int = 1
    benchmark: str = 'buy_hold'

    def to_api_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TradeRecord:
    date: str
    signal_date: str
    side: str
    action: str
    price: float
    quantity: float
    gross_value: float
    commission: float
    cash_after: float
    shares_after: float
    target_position: Optional[float]
    target_weight: Optional[float]
    realized_pnl: float = 0.0
    confidence_label: Optional[str] = None

    def to_api_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EquityPoint:
    date: str
    close: float
    equity: float
    cash: float
    position_value: float
    shares: float
    position_weight: float
    benchmark_equity: float
    drawdown: float

    def to_api_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BacktestMetrics:
    total_return: float = 0.0
    annual_return: float = 0.0
    annual_volatility: float = 0.0
    sharpe: Optional[float] = None
    max_drawdown: float = 0.0
    win_rate: Optional[float] = None
    profit_factor: Optional[float] = None
    trade_count: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    average_position: float = 0.0
    exposure: float = 0.0
    turnover: float = 0.0
    benchmark_total_return: float = 0.0
    excess_return: float = 0.0

    def to_api_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BacktestResult:
    config: BacktestConfig
    metrics: BacktestMetrics
    equity_curve: List[EquityPoint] = field(default_factory=list)
    trades: List[TradeRecord] = field(default_factory=list)
    signals: List[Dict[str, Any]] = field(default_factory=list)

    def to_api_dict(self) -> Dict[str, Any]:
        return {
            'config': self.config.to_api_dict(),
            'metrics': self.metrics.to_api_dict(),
            'equity_curve': [x.to_api_dict() for x in self.equity_curve],
            'drawdown': [
                {'date': x.date, 'drawdown': x.drawdown}
                for x in self.equity_curve
            ],
            'positions': [
                {
                    'date': x.date,
                    'position_weight': x.position_weight,
                    'shares': x.shares,
                    'position_value': x.position_value,
                }
                for x in self.equity_curve
            ],
            'trades': [x.to_api_dict() for x in self.trades],
            'signals': self.signals,
        }
