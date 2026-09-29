"""Simulazione a barre: una posizione alla volta, SL valutato prima del TP (conservativo).

Riusa i setup del ``SignalEngine``: nessuna logica di segnale duplicata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from pytrader.models import Direction, TradeSetup


class TradeOutcome(str, Enum):
    WIN = "win"
    LOSS = "loss"
    OPEN = "open"  # ancora in corso sull'ultima candela
    PENDING = "pending"  # segnale sull'ultima candela: ingresso non ancora avvenuto
    SKIPPED = "skipped"  # scartato perché già in posizione


@dataclass(frozen=True)
class BacktestParams:
    fee_rate: float = 0.0  # commissione per lato, frazione del nozionale (0.001 = 0.1%)
    one_position_at_a_time: bool = True


@dataclass(frozen=True)
class TradeResult:
    setup: TradeSetup
    outcome: TradeOutcome
    exit_index: Optional[int] = None
    exit_price: Optional[float] = None
    r_multiple: Optional[float] = None  # risultato netto in multipli del rischio


@dataclass
class BacktestResult:
    trades: list[TradeResult] = field(default_factory=list)

    @property
    def closed(self) -> list[TradeResult]:
        return [t for t in self.trades if t.r_multiple is not None]

    @property
    def n_trades(self) -> int:
        return len(self.closed)

    @property
    def win_rate(self) -> float:
        closed = self.closed
        return sum(t.r_multiple > 0 for t in closed) / len(closed) if closed else 0.0  # type: ignore[operator]

    @property
    def expectancy(self) -> float:
        closed = self.closed
        return float(np.mean([t.r_multiple for t in closed])) if closed else 0.0

    @property
    def total_r(self) -> float:
        return float(sum(t.r_multiple for t in self.closed))  # type: ignore[misc]

    @property
    def profit_factor(self) -> float:
        rs = [t.r_multiple for t in self.closed]
        gains = sum(r for r in rs if r > 0)  # type: ignore[operator]
        losses = -sum(r for r in rs if r < 0)  # type: ignore[operator]
        if losses == 0:
            return float("inf") if gains > 0 else 0.0
        return gains / losses

    @property
    def max_drawdown_r(self) -> float:
        """Massimo drawdown della curva cumulativa in R (valore positivo)."""
        rs = np.array([t.r_multiple for t in self.closed], dtype=float)
        if rs.size == 0:
            return 0.0
        equity = np.concatenate([[0.0], np.cumsum(rs)])
        return float(np.max(np.maximum.accumulate(equity) - equity))

    def summary(self) -> dict[str, float]:
        return {
            "trades": float(self.n_trades),
            "win_rate": self.win_rate,
            "expectancy_r": self.expectancy,
            "total_r": self.total_r,
            "profit_factor": self.profit_factor,
            "max_drawdown_r": self.max_drawdown_r,
        }


class Backtester:
    def __init__(self, params: Optional[BacktestParams] = None) -> None:
        self.params = params or BacktestParams()

    def run(self, df: pd.DataFrame, setups: list[TradeSetup]) -> BacktestResult:
        o = df["open"].to_numpy()
        h = df["high"].to_numpy()
        lo = df["low"].to_numpy()
        n = len(df)
        result = BacktestResult()
        busy_until = -1  # ultima candela occupata dalla posizione corrente

        for setup in sorted(setups, key=lambda s: s.signal_index):
            entry_idx = setup.signal_index + 1
            if entry_idx >= n:
                result.trades.append(TradeResult(setup, TradeOutcome.PENDING))
                continue
            if self.params.one_position_at_a_time and entry_idx <= busy_until:
                result.trades.append(TradeResult(setup, TradeOutcome.SKIPPED))
                continue
            trade = self._simulate(setup, entry_idx, o, h, lo)
            result.trades.append(trade)
            busy_until = trade.exit_index if trade.exit_index is not None else n
        return result

    def _simulate(
        self, s: TradeSetup, entry_idx: int, o: np.ndarray, h: np.ndarray, lo: np.ndarray
    ) -> TradeResult:
        long = s.direction is Direction.LONG
        for j in range(entry_idx, len(o)):
            # Gap oltre lo stop all'apertura (non sulla candela d'ingresso: l'entry è l'open)
            if j > entry_idx and (o[j] <= s.stop_loss if long else o[j] >= s.stop_loss):
                return self._close(s, j, float(o[j]))
            if j > entry_idx and (o[j] >= s.take_profit if long else o[j] <= s.take_profit):
                return self._close(s, j, float(o[j]))
            hit_sl = lo[j] <= s.stop_loss if long else h[j] >= s.stop_loss
            hit_tp = h[j] >= s.take_profit if long else lo[j] <= s.take_profit
            if hit_sl:  # conservativo: se entrambi toccati nella stessa candela, vince lo SL
                return self._close(s, j, s.stop_loss)
            if hit_tp:
                return self._close(s, j, s.take_profit)
        return TradeResult(s, TradeOutcome.OPEN)

    def _close(self, s: TradeSetup, j: int, price: float) -> TradeResult:
        sign = 1.0 if s.direction is Direction.LONG else -1.0
        gross = sign * (price - s.entry)
        fees = self.params.fee_rate * (s.entry + price)
        r = (gross - fees) / s.risk
        outcome = TradeOutcome.WIN if r > 0 else TradeOutcome.LOSS
        return TradeResult(s, outcome, exit_index=j, exit_price=price, r_multiple=r)
