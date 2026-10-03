"""Backtest a barre dei setup generati dal SignalEngine."""

from pytrader.backtest.engine import (
    Backtester,
    BacktestParams,
    BacktestResult,
    TradeOutcome,
    TradeResult,
)
from pytrader.backtest.money import (
    MoneyParams,
    MoneyResult,
    PositionPlan,
    SizingMode,
    position_size,
    simulate_money,
)

__all__ = [
    "MoneyParams",
    "MoneyResult",
    "PositionPlan",
    "SizingMode",
    "position_size",
    "simulate_money",
    "BacktestParams",
    "BacktestResult",
    "Backtester",
    "TradeOutcome",
    "TradeResult",
]
