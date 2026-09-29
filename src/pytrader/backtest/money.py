"""Conversione dei risultati in R in importi monetari: position sizing a rischio fisso.

Quantità = (capitale × rischio%) / |entry − stop|, limitata da ``max_leverage``:
il nozionale non può superare capitale × leva. Con stop molto stretti il limite di leva
riduce il rischio effettivo sotto la percentuale impostata (segnalato da ``capped``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from pytrader.backtest.engine import TradeOutcome, TradeResult


@dataclass(frozen=True)
class MoneyParams:
    initial_capital: float = 10_000.0
    risk_pct: float = 1.0  # percentuale del capitale rischiata per trade
    max_leverage: float = 1.0  # 1 = nessuna leva: nozionale <= capitale
    compounding: bool = True  # True: rischio sul capitale corrente; False: su quello iniziale

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("Il capitale deve essere positivo")
        if not 0 < self.risk_pct <= 100:
            raise ValueError("Il rischio per trade deve essere tra 0 e 100%")
        if self.max_leverage <= 0:
            raise ValueError("La leva massima deve essere positiva")


@dataclass(frozen=True)
class PositionPlan:
    """Dimensionamento ed esito monetario di un trade."""

    trade: TradeResult
    quantity: float  # unità dello strumento
    notional: float  # quantità × entry
    risk_amount: float  # perdita allo stop (commissioni escluse)
    reward_amount: float  # guadagno al target (commissioni escluse)
    pnl: Optional[float]  # risultato netto realizzato; None se non chiuso
    equity_before: float
    equity_after: float
    capped: bool  # quantità ridotta dal limite di leva


@dataclass
class MoneyResult:
    params: MoneyParams
    plans: list[PositionPlan] = field(default_factory=list)

    @property
    def final_equity(self) -> float:
        return self.plans[-1].equity_after if self.plans else self.params.initial_capital

    @property
    def net_profit(self) -> float:
        return self.final_equity - self.params.initial_capital

    @property
    def return_pct(self) -> float:
        return self.net_profit / self.params.initial_capital * 100

    def _curve(self) -> list[float]:
        return [self.params.initial_capital] + [
            p.equity_after for p in self.plans if p.pnl is not None
        ]

    @property
    def max_drawdown_amount(self) -> float:
        peak, worst = float("-inf"), 0.0
        for eq in self._curve():
            peak = max(peak, eq)
            worst = max(worst, peak - eq)
        return worst

    @property
    def max_drawdown_pct(self) -> float:
        peak, worst = float("-inf"), 0.0
        for eq in self._curve():
            peak = max(peak, eq)
            if peak > 0:
                worst = max(worst, (peak - eq) / peak * 100)
        return worst

    def summary(self) -> dict[str, float]:
        return {
            "initial_capital": self.params.initial_capital,
            "final_equity": self.final_equity,
            "net_profit": self.net_profit,
            "return_pct": self.return_pct,
            "max_drawdown_amount": self.max_drawdown_amount,
            "max_drawdown_pct": self.max_drawdown_pct,
        }


def simulate_money(trades: list[TradeResult], params: MoneyParams) -> MoneyResult:
    """Applica il sizing ai trade in ordine cronologico (come prodotti dal ``Backtester``).

    Il P&L netto è ``quantità × rischio_unitario × R``: le commissioni sono già incluse in R.
    I trade saltati hanno quantità zero; quelli aperti/in attesa mostrano il sizing previsto
    sul capitale corrente, senza P&L.
    """
    result = MoneyResult(params=params)
    equity = params.initial_capital
    for trade in trades:
        s = trade.setup
        base = equity if params.compounding else params.initial_capital
        if trade.outcome is TradeOutcome.SKIPPED or base <= 0:
            qty = 0.0
            capped = False
        else:
            qty_risk = base * params.risk_pct / 100 / s.risk
            qty_lev = base * params.max_leverage / s.entry
            qty = min(qty_risk, qty_lev)
            capped = qty_lev < qty_risk
        pnl: Optional[float] = None
        before = equity
        if trade.r_multiple is not None and qty > 0:
            pnl = qty * s.risk * trade.r_multiple
            equity += pnl
        result.plans.append(
            PositionPlan(
                trade=trade,
                quantity=qty,
                notional=qty * s.entry,
                risk_amount=qty * s.risk,
                reward_amount=qty * s.reward,
                pnl=pnl,
                equity_before=before,
                equity_after=equity,
                capped=capped,
            )
        )
    return result
