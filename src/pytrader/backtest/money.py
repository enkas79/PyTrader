"""Conversione dei risultati in R in importi monetari: dimensionamento delle posizioni.

Tre modalità (``SizingMode``):

- **Rischio %** (predefinita): quantità = capitale × rischio% / |entry − stop|. La leva è un
  *tetto*: il controvalore non supera capitale × leva; con stop molto stretti il tetto riduce
  il rischio effettivo sotto la percentuale impostata (``capped``).
- **Importo fisso**: ogni trade impegna lo stesso margine (mai oltre il capitale disponibile);
  controvalore = margine × leva.
- **% del capitale**: margine = capitale × quota%; controvalore = margine × leva.

Nelle ultime due la leva *moltiplica* la posizione e il rischio per trade dipende dalla
distanza dello stop: può superare di molto l'1-2 % e, con leva alta, perfino il margine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from pytrader.backtest.engine import TradeOutcome, TradeResult


class SizingMode(str, Enum):
    RISK = "risk"  # rischio % per trade, leva come tetto
    AMOUNT = "amount"  # importo fisso di margine per trade, leva come moltiplicatore
    PERCENT = "percent"  # % del capitale come margine, leva come moltiplicatore


@dataclass(frozen=True)
class MoneyParams:
    initial_capital: float = 10_000.0
    risk_pct: float = 1.0  # percentuale del capitale rischiata per trade (modalità RISK)
    max_leverage: float = 1.0  # RISK: tetto al nozionale; AMOUNT/PERCENT: moltiplicatore
    compounding: bool = True  # True: base sul capitale corrente; False: su quello iniziale
    sizing: SizingMode = SizingMode.RISK
    position_amount: float = 1_000.0  # margine per trade (modalità AMOUNT), in valuta
    position_pct: float = 10.0  # margine in % del capitale (modalità PERCENT)

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("Il capitale deve essere positivo")
        if not 0 < self.risk_pct <= 100:
            raise ValueError("Il rischio per trade deve essere tra 0 e 100%")
        if self.max_leverage <= 0:
            raise ValueError("La leva deve essere positiva")
        if self.position_amount <= 0:
            raise ValueError("L'importo per trade deve essere positivo")
        if not 0 < self.position_pct <= 100:
            raise ValueError("La quota del capitale deve essere tra 0 e 100%")

    @property
    def leverage_multiplies(self) -> bool:
        """True se la leva moltiplica la posizione invece di limitarla."""
        return self.sizing is not SizingMode.RISK


def position_size(
    params: MoneyParams, base: float, available: float, entry: float, unit_risk: float
) -> tuple[float, float, bool]:
    """Quantità, margine impegnato e flag di riduzione per un trade.

    ``base``: capitale su cui calcolare rischio o quota (corrente o iniziale);
    ``available``: capitale effettivamente disponibile (il margine non può superarlo).
    """
    if base <= 0 or available <= 0 or entry <= 0 or unit_risk <= 0:
        return 0.0, 0.0, False
    lev = params.max_leverage
    if params.sizing is SizingMode.RISK:
        qty_risk = base * params.risk_pct / 100 / unit_risk
        qty_lev = base * lev / entry
        qty = min(qty_risk, qty_lev)
        return qty, qty * entry / lev, qty_lev < qty_risk
    wanted = (
        params.position_amount
        if params.sizing is SizingMode.AMOUNT
        else base * params.position_pct / 100
    )
    margin = min(wanted, available)
    return margin * lev / entry, margin, margin < wanted


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
    capped: bool  # quantità ridotta (tetto di leva o capitale disponibile)
    margin: float = 0.0  # capitale impegnato: controvalore / leva
    base: float = 0.0  # capitale di riferimento del dimensionamento


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

    def _sized(self) -> list[PositionPlan]:
        return [p for p in self.plans if p.quantity > 0 and p.base > 0]

    @property
    def max_leverage_used(self) -> float:
        """Massimo controvalore / capitale di riferimento (corrente o iniziale) tra i trade."""
        return max((p.notional / p.base for p in self._sized()), default=0.0)

    def risk_pcts(self) -> list[float]:
        """Perdita allo stop di ogni trade in % del capitale di riferimento."""
        return [p.risk_amount / p.base * 100 for p in self._sized()]

    @property
    def over_margin_count(self) -> int:
        """Trade in cui la perdita allo stop supera il margine: un broker chiuderebbe prima."""
        return sum(p.risk_amount > p.margin * 1.0000001 for p in self._sized())

    @property
    def capped_count(self) -> int:
        return sum(p.capped for p in self.plans)

    @property
    def required_leverage(self) -> float:
        """Leva minima con cui nessuna posizione verrebbe ridotta: rischio% × entry / rischio
        unitario (non dipende dal capitale)."""
        sized = [p for p in self.plans if p.trade.outcome is not TradeOutcome.SKIPPED]
        return max(
            (self.params.risk_pct / 100 * p.trade.setup.entry / p.trade.setup.risk for p in sized),
            default=0.0,
        )

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
        if trade.outcome is TradeOutcome.SKIPPED:
            qty, margin, capped = 0.0, 0.0, False
        else:
            qty, margin, capped = position_size(params, base, equity, s.entry, s.risk)
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
                margin=margin,
                base=base,
            )
        )
    return result
