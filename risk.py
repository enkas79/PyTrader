"""Position sizing a rischio fisso.

Formula di base (rischio dell'``r``% del capitale per trade)::

    size_base = (balance · r) / stop_distance,   stop_distance = 2 · ATR

La size viene poi resa *eseguibile* rispettando i vincoli dell'exchange:

1. tetto al nozionale (``balance · max_notional_multiple``) per limitare la
   leva implicita;
2. conversione in contratti (``contractSize``) per i derivati;
3. arrotondamento **per difetto** alla precisione dell'exchange (mai per
   eccesso: arrotondare in su farebbe rischiare più dell'1%);
4. verifica di quantità minima e controvalore minimo (``minCost``/min notional).

Se la size arrotondata viola i minimi il trade viene scartato invece di
aumentare la quantità: il vincolo di rischio ha la precedenza.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass


class SizingError(ValueError):
    """La size calcolata non è eseguibile nel rispetto del rischio massimo."""


@dataclass(frozen=True, slots=True)
class MarketLimits:
    """Vincoli di mercato rilevanti per la size (da ``exchange.market(symbol)``).

    Attributes:
        min_amount: Quantità minima dell'ordine (in contratti o base).
        max_amount: Quantità massima dell'ordine.
        min_cost: Controvalore minimo in valuta di quotazione.
        contract_size: Valore in asset base di un contratto (1 per lo spot).
    """

    min_amount: float | None = None
    max_amount: float | None = None
    min_cost: float | None = None
    contract_size: float = 1.0


@dataclass(frozen=True, slots=True)
class SizingResult:
    """Esito del position sizing.

    Attributes:
        order_amount: Quantità da inviare all'exchange (contratti o base).
        base_amount: Quantità equivalente in asset base.
        notional: Controvalore stimato (base_amount · price).
        risk_amount: Perdita teorica allo stop (base_amount · stop_distance).
        risk_fraction: ``risk_amount / balance`` effettivo (≤ rischio target).
        capped: ``True`` se è intervenuto il tetto al nozionale.
    """

    order_amount: float
    base_amount: float
    notional: float
    risk_amount: float
    risk_fraction: float
    capped: bool


class PositionSizer:
    """Calcolatore della dimensione della posizione.

    Attributes:
        risk_per_trade: Frazione del capitale rischiata (0.01 = 1%).
        max_notional_multiple: Tetto al nozionale in multipli del saldo.
    """

    def __init__(self, risk_per_trade: float = 0.01, max_notional_multiple: float = 3.0) -> None:
        """Inizializza il sizer.

        Args:
            risk_per_trade: Frazione del capitale rischiata per trade.
            max_notional_multiple: Tetto al nozionale in multipli del saldo.

        Raises:
            ValueError: Se i parametri non sono positivi.
        """
        if risk_per_trade <= 0 or max_notional_multiple <= 0:
            raise ValueError("risk_per_trade e max_notional_multiple devono essere > 0")
        self.risk_per_trade = risk_per_trade
        self.max_notional_multiple = max_notional_multiple

    def raw_size(self, balance: float, stop_distance: float) -> float:
        """Size teorica in asset base: ``(balance · r) / stop_distance``.

        Args:
            balance: Capitale del conto in valuta di quotazione.
            stop_distance: Distanza assoluta tra entry e stop (2·ATR).

        Returns:
            La quantità in asset base che rischia esattamente ``r`` del capitale.

        Raises:
            SizingError: Se saldo o distanza non sono positivi e finiti.
        """
        if not (math.isfinite(balance) and balance > 0):
            raise SizingError(f"Saldo non valido: {balance}")
        if not (math.isfinite(stop_distance) and stop_distance > 0):
            raise SizingError(f"Distanza stop non valida: {stop_distance}")
        return balance * self.risk_per_trade / stop_distance

    def size(
        self,
        balance: float,
        stop_distance: float,
        price: float,
        limits: MarketLimits,
        round_amount: Callable[[float], float],
    ) -> SizingResult:
        """Calcola la size eseguibile rispettando rischio e vincoli dell'exchange.

        Args:
            balance: Capitale del conto in valuta di quotazione.
            stop_distance: Distanza assoluta dello stop (2·ATR).
            price: Prezzo di riferimento per nozionale e costo minimo.
            limits: Vincoli di mercato.
            round_amount: Funzione che tronca la quantità alla precisione
                dell'exchange (es. ``exchange.amount_to_precision``).

        Returns:
            Un :class:`SizingResult` con quantità eseguibile e rischio effettivo.

        Raises:
            SizingError: Se la quantità risultante è sotto i minimi dell'exchange.
        """
        if not (math.isfinite(price) and price > 0):
            raise SizingError(f"Prezzo non valido: {price}")
        if limits.contract_size <= 0:
            raise SizingError(f"contractSize non valido: {limits.contract_size}")

        base_qty = self.raw_size(balance, stop_distance)
        max_base_qty = balance * self.max_notional_multiple / price
        capped = base_qty > max_base_qty
        base_qty = min(base_qty, max_base_qty)

        order_amount = base_qty / limits.contract_size
        if limits.max_amount is not None and order_amount > limits.max_amount:
            order_amount = limits.max_amount
            capped = True
        order_amount = round_amount(order_amount)

        if order_amount <= 0:
            raise SizingError("Quantità nulla dopo l'arrotondamento alla precisione")
        if limits.min_amount is not None and order_amount < limits.min_amount:
            raise SizingError(
                f"Quantità {order_amount} < minimo exchange {limits.min_amount}: "
                "capitale insufficiente per rischiare solo l'1% con questo stop"
            )

        base_amount = order_amount * limits.contract_size
        notional = base_amount * price
        if limits.min_cost is not None and notional < limits.min_cost:
            raise SizingError(f"Nozionale {notional:.2f} < minimo exchange {limits.min_cost}")

        risk_amount = base_amount * stop_distance
        return SizingResult(
            order_amount=order_amount,
            base_amount=base_amount,
            notional=notional,
            risk_amount=risk_amount,
            risk_fraction=risk_amount / balance,
            capped=capped,
        )
