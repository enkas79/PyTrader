"""Backtest a barre dei setup generati dal SignalEngine."""

from pytrader.backtest.engine import (
    Backtester,
    BacktestParams,
    BacktestResult,
    TradeOutcome,
    TradeResult,
)

__all__ = ["BacktestParams", "BacktestResult", "Backtester", "TradeOutcome", "TradeResult"]
