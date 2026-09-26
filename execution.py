"""Interfaccia asincrona con l'exchange tramite ``ccxt.async_support``.

Responsabilità:

* connessione, caricamento mercati, leva/margine (derivati), testnet;
* download delle sole candele **chiuse**;
* calcolo della size eseguibile (delegato a :class:`risk.PositionSizer`) con
  i vincoli reali del mercato (precisione, minimi, ``contractSize``);
* invio di ordini market di ingresso/uscita e ordini di protezione SL/TP
  reduce-only lato exchange;
* riconciliazione: posizione residua sull'exchange e prezzo medio di uscita.

In modalità ``dry_run`` gli ordini sono simulati al prezzo ``last`` del ticker
(nessuna chiamata privata), mentre i dati di mercato restano reali.

Nota: gli ordini di creazione **non** vengono ritentati automaticamente in
caso di errore di rete, per evitare ordini duplicati; lo sono solo le
chiamate di sola lettura.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import ccxt.async_support as ccxt_async
import pandas as pd
from ccxt.base.errors import (
    InvalidOrder,
    NetworkError,
    NotSupported,
    OrderNotFound,
)

from config import BotConfig
from risk import MarketLimits, PositionSizer, SizingResult
from strategy import Side

logger = logging.getLogger(__name__)

#: Parametro ccxt unificato per interrogare/cancellare ordini condizionali
#: (su Binance USDM gli stop sono "algo orders" con endpoint dedicati).
TRIGGER_PARAMS: dict[str, Any] = {"trigger": True}


@dataclass(frozen=True, slots=True)
class Fill:
    """Esecuzione di un ordine.

    Attributes:
        order_id: Id dell'ordine sull'exchange (o ``paper-...`` in dry-run).
        price: Prezzo medio di esecuzione.
        amount: Quantità eseguita in asset base.
    """

    order_id: str
    price: float
    amount: float


async def retry_async[T](
    func: Callable[[], Awaitable[T]], attempts: int = 3, base_delay: float = 2.0
) -> T:
    """Esegue una coroutine ritentando sugli errori di rete (backoff esponenziale).

    Args:
        func: Factory senza argomenti che restituisce la coroutine da eseguire.
        attempts: Numero massimo di tentativi.
        base_delay: Attesa iniziale in secondi (raddoppia a ogni tentativo).

    Returns:
        Il risultato della coroutine.

    Raises:
        NetworkError: Se tutti i tentativi falliscono.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await func()
        except NetworkError as exc:
            if attempt == attempts:
                raise
            delay = base_delay * 2 ** (attempt - 1)
            logger.warning("Errore di rete (%s), nuovo tentativo tra %.0fs", exc, delay)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


class ExecutionEngine:
    """Adattatore asincrono verso l'exchange.

    Attributes:
        config: Configurazione completa del bot.
        symbol: Simbolo ccxt operato.
        exchange: Istanza ``ccxt.async_support`` dell'exchange.
        sizer: Calcolatore della size a rischio fisso.
    """

    def __init__(self, config: BotConfig, exchange: ccxt_async.Exchange | None = None) -> None:
        """Crea l'adattatore (senza aprire connessioni).

        Args:
            config: Configurazione completa del bot.
            exchange: Istanza ccxt già costruita (iniezione per i test).

        Raises:
            ValueError: Se ``exchange_id`` non è supportato da ccxt.
        """
        self.config = config
        self.symbol = config.exchange.symbol
        self.sizer = PositionSizer(
            risk_per_trade=config.risk.risk_per_trade,
            max_notional_multiple=config.risk.max_notional_multiple,
        )
        self.exchange = exchange or self._build_exchange()
        self._market: dict[str, Any] | None = None

    # ------------------------------------------------------------------ #
    # Connessione
    # ------------------------------------------------------------------ #
    def _build_exchange(self) -> ccxt_async.Exchange:
        """Istanzia la classe ccxt indicata in configurazione."""
        ex_cfg = self.config.exchange
        exchange_cls = getattr(ccxt_async, ex_cfg.exchange_id, None)
        if exchange_cls is None:
            raise ValueError(f"Exchange non supportato da ccxt: {ex_cfg.exchange_id}")
        params: dict[str, Any] = {
            "enableRateLimit": True,
            "options": {"adjustForTimeDifference": True},
        }
        if ex_cfg.api_key:
            params.update(apiKey=ex_cfg.api_key, secret=ex_cfg.api_secret)
        if ex_cfg.api_password:
            params["password"] = ex_cfg.api_password
        return exchange_cls(params)

    @property
    def dry_run(self) -> bool:
        """``True`` se gli ordini sono simulati."""
        return self.config.runtime.dry_run

    @property
    def is_contract(self) -> bool:
        """``True`` se il mercato è un derivato (swap/future)."""
        return bool(self.market.get("contract", False))

    @property
    def market(self) -> dict[str, Any]:
        """Metadati ccxt del mercato operato.

        Raises:
            RuntimeError: Se :meth:`connect` non è stato chiamato.
        """
        if self._market is None:
            raise RuntimeError("ExecutionEngine non connesso: chiamare connect()")
        return self._market

    async def connect(self) -> None:
        """Abilita l'eventuale testnet, carica i mercati e configura leva/margine.

        Raises:
            ValueError: Se il simbolo non esiste sull'exchange.
        """
        if self.config.exchange.sandbox:
            try:
                self.exchange.set_sandbox_mode(True)
            except NotSupported:
                # Binance futures: la testnet storica è deprecata in ccxt.
                logger.warning("Sandbox non supportata, attivo la demo trading")
                self.exchange.enable_demo_trading(True)

        await retry_async(lambda: self.exchange.load_markets())
        if self.symbol not in self.exchange.markets:
            raise ValueError(f"Simbolo {self.symbol} non disponibile su {self.exchange.id}")
        self._market = self.exchange.market(self.symbol)

        if self.is_contract and not self.dry_run:
            await self._configure_derivatives()
        logger.info(
            "Connesso a %s (%s, %s)",
            self.exchange.id,
            self.symbol,
            "PAPER" if self.dry_run else "LIVE",
        )

    async def _configure_derivatives(self) -> None:
        """Imposta modalità di margine e leva (errori non bloccanti)."""
        ex_cfg = self.config.exchange
        try:
            await self.exchange.set_margin_mode(ex_cfg.margin_mode, self.symbol)
        except Exception as exc:  # già impostato o non supportato
            logger.info("set_margin_mode ignorato: %s", exc)
        try:
            await self.exchange.set_leverage(ex_cfg.leverage, self.symbol)
        except Exception as exc:
            logger.warning("set_leverage fallito: %s", exc)

    async def close(self) -> None:
        """Chiude le sessioni HTTP di ccxt."""
        await self.exchange.close()

    async def clock_drift_ms(self) -> float | None:
        """Differenza tra orologio locale ed exchange (ms), se misurabile.

        Returns:
            ``locale - exchange`` in millisecondi, oppure ``None`` se
            l'exchange non espone ``fetchTime``.
        """
        if not self.exchange.has.get("fetchTime"):
            return None
        local_before = self.exchange.milliseconds()
        server = await retry_async(lambda: self.exchange.fetch_time())
        local_after = self.exchange.milliseconds()
        if server is None:
            return None
        return (local_before + local_after) / 2 - float(server)

    # ------------------------------------------------------------------ #
    # Dati di mercato
    # ------------------------------------------------------------------ #
    async def fetch_closed_ohlcv(self, now_ms: int) -> pd.DataFrame:
        """Scarica le ultime candele chiuse.

        Args:
            now_ms: Istante corrente (ms): le candele con ``open + tf > now``
                sono ancora in formazione e vengono scartate.

        Returns:
            DataFrame ``open, high, low, close, volume`` con ``DatetimeIndex`` UTC.
        """
        tf = self.config.strategy.timeframe
        tf_ms = self.config.strategy.timeframe_seconds * 1000
        limit = self.config.strategy.ohlcv_limit + 1
        raw = await retry_async(
            lambda: self.exchange.fetch_ohlcv(self.symbol, timeframe=tf, limit=limit)
        )
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = df[df["timestamp"] + tf_ms <= now_ms]
        df = df.drop_duplicates("timestamp").sort_values("timestamp")
        df.index = pd.to_datetime(df.pop("timestamp"), unit="ms", utc=True)
        df.index.name = "datetime"
        return df.astype(float)

    async def fetch_last_price(self) -> float:
        """Ultimo prezzo scambiato (``last``, con fallback su ``close``/bid/ask)."""
        ticker = await retry_async(lambda: self.exchange.fetch_ticker(self.symbol))
        for key in ("last", "close"):
            value = ticker.get(key)
            if value:
                return float(value)
        bid, ask = ticker.get("bid"), ticker.get("ask")
        if bid and ask:
            return (float(bid) + float(ask)) / 2
        raise RuntimeError(f"Ticker senza prezzo valido per {self.symbol}")

    async def fetch_equity(self) -> float:
        """Capitale del conto in valuta di quotazione (``total``).

        Returns:
            Il saldo totale della valuta di quotazione.
        """
        quote = self.config.exchange.quote_currency
        balance = await retry_async(lambda: self.exchange.fetch_balance())
        return float((balance.get("total") or {}).get(quote) or 0.0)

    # ------------------------------------------------------------------ #
    # Size
    # ------------------------------------------------------------------ #
    def market_limits(self) -> MarketLimits:
        """Estrae i vincoli di mercato dai metadati ccxt."""
        limits = self.market.get("limits") or {}
        amount = limits.get("amount") or {}
        cost = limits.get("cost") or {}
        return MarketLimits(
            min_amount=amount.get("min"),
            max_amount=amount.get("max"),
            min_cost=cost.get("min"),
            contract_size=float(self.market.get("contractSize") or 1.0),
        )

    def round_amount(self, amount: float) -> float:
        """Tronca la quantità (contratti/base) alla precisione del mercato.

        Args:
            amount: Quantità da arrotondare.

        Returns:
            La quantità troncata, ``0.0`` se inferiore al passo minimo.
        """
        try:
            return float(self.exchange.amount_to_precision(self.symbol, amount))
        except InvalidOrder:
            return 0.0

    def round_price(self, price: float) -> float:
        """Arrotonda un prezzo al tick size del mercato."""
        return float(self.exchange.price_to_precision(self.symbol, price))

    def compute_order_size(
        self, balance: float, stop_distance: float, price: float
    ) -> SizingResult:
        """Size che rischia ``risk_per_trade`` del capitale, rispettando i limiti.

        Formula: ``(balance · 0.01) / stop_distance``, poi tetto al nozionale,
        conversione in contratti, troncamento e verifica dei minimi.

        Args:
            balance: Capitale del conto.
            stop_distance: Distanza dello stop (2·ATR).
            price: Prezzo di riferimento.

        Returns:
            Il :class:`risk.SizingResult` eseguibile.

        Raises:
            risk.SizingError: Se la size viola i minimi dell'exchange.
        """
        return self.sizer.size(
            balance=balance,
            stop_distance=stop_distance,
            price=price,
            limits=self.market_limits(),
            round_amount=self.round_amount,
        )

    def _to_order_amount(self, base_amount: float) -> float:
        """Converte una quantità in asset base nell'unità d'ordine (contratti)."""
        return self.round_amount(base_amount / self.market_limits().contract_size)

    # ------------------------------------------------------------------ #
    # Ordini
    # ------------------------------------------------------------------ #
    async def open_position(self, side: Side, base_amount: float) -> Fill:
        """Apre la posizione con un ordine market.

        Args:
            side: Direzione del trade.
            base_amount: Quantità in asset base.

        Returns:
            Il :class:`Fill` con prezzo medio effettivo.
        """
        return await self._market_order(side.entry_order_side, base_amount, reduce_only=False)

    async def close_position(self, side: Side, base_amount: float) -> Fill:
        """Chiude (reduce-only sui derivati) la posizione con un ordine market.

        Sullo spot la quantità è limitata al saldo libero dell'asset base
        (la commissione d'acquisto può essere stata trattenuta in base).

        Args:
            side: Direzione del trade da chiudere.
            base_amount: Quantità in asset base.

        Returns:
            Il :class:`Fill` di uscita.
        """
        if not self.dry_run and not self.is_contract:
            free = await self._free_base_balance()
            base_amount = min(base_amount, free)
        return await self._market_order(side.exit_order_side, base_amount, reduce_only=True)

    async def _market_order(self, order_side: str, base_amount: float, reduce_only: bool) -> Fill:
        """Invia (o simula) un ordine market.

        Args:
            order_side: ``buy`` o ``sell``.
            base_amount: Quantità in asset base.
            reduce_only: Imposta ``reduceOnly`` (solo derivati).

        Returns:
            Il :class:`Fill` risultante.

        Raises:
            InvalidOrder: Se la quantità arrotondata è nulla.
        """
        if self.dry_run:
            price = await self.fetch_last_price()
            logger.info("[PAPER] %s %.8f %s @ %.2f", order_side, base_amount, self.symbol, price)
            return Fill(order_id=f"paper-{uuid.uuid4().hex[:12]}", price=price, amount=base_amount)

        amount = self._to_order_amount(base_amount)
        if amount <= 0:
            raise InvalidOrder(f"Quantità {base_amount} nulla dopo l'arrotondamento")
        params: dict[str, Any] = {"reduceOnly": True} if reduce_only and self.is_contract else {}
        order = await self.exchange.create_order(
            self.symbol, "market", order_side, amount, None, params
        )
        return await self._resolve_fill(order, amount)

    async def _resolve_fill(self, order: dict[str, Any], requested: float) -> Fill:
        """Ricava prezzo medio e quantità eseguita, interrogando l'ordine se serve.

        Alcuni exchange (es. Bybit) restituiscono in risposta alla creazione
        solo l'id: in tal caso l'ordine viene riletto.

        Args:
            order: Struttura ordine ccxt restituita da ``create_order``.
            requested: Quantità richiesta (unità d'ordine).

        Returns:
            Il :class:`Fill` con quantità convertita in asset base.
        """
        order_id = str(order["id"])
        for attempt in range(5):
            average = order.get("average") or order.get("price")
            filled = order.get("filled")
            if average and filled:
                contract_size = self.market_limits().contract_size
                return Fill(order_id, float(average), float(filled) * contract_size)
            await asyncio.sleep(0.5 * (attempt + 1))
            order = await retry_async(lambda: self.exchange.fetch_order(order_id, self.symbol))
        logger.warning("Fill non confermato per %s: uso il prezzo last", order_id)
        price = await self.fetch_last_price()
        return Fill(order_id, price, requested * self.market_limits().contract_size)

    async def place_protection(
        self, side: Side, base_amount: float, stop_loss: float, take_profit: float
    ) -> tuple[str, str]:
        """Piazza SL e TP come ordini condizionali market reduce-only.

        Usa i parametri unificati ccxt ``stopLossPrice``/``takeProfitPrice``
        (mappati p.es. su ``STOP_MARKET``/``TAKE_PROFIT_MARKET`` su Binance USDM).

        Args:
            side: Direzione del trade protetto.
            base_amount: Quantità in asset base.
            stop_loss: Prezzo di stop loss.
            take_profit: Prezzo di take profit.

        Returns:
            Tupla ``(sl_order_id, tp_order_id)``.
        """
        amount = self._to_order_amount(base_amount)
        exit_side = side.exit_order_side
        sl = await self.exchange.create_order(
            self.symbol,
            "market",
            exit_side,
            amount,
            None,
            {"stopLossPrice": self.round_price(stop_loss), "reduceOnly": True},
        )
        tp = await self.exchange.create_order(
            self.symbol,
            "market",
            exit_side,
            amount,
            None,
            {"takeProfitPrice": self.round_price(take_profit), "reduceOnly": True},
        )
        return str(sl["id"]), str(tp["id"])

    async def cancel_protection(self, order_ids: list[str | None]) -> None:
        """Cancella gli ordini di protezione residui (errori "non trovato" ignorati).

        Args:
            order_ids: Id degli ordini condizionali da cancellare.
        """
        for order_id in filter(None, order_ids):
            try:
                await self.exchange.cancel_order(order_id, self.symbol, dict(TRIGGER_PARAMS))
                logger.info("Ordine di protezione %s cancellato", order_id)
            except OrderNotFound:
                pass
            except Exception as exc:
                logger.warning("Cancellazione %s fallita: %s", order_id, exc)

    async def open_trigger_order_ids(self) -> set[str] | None:
        """Id degli ordini condizionali aperti sul simbolo.

        Returns:
            Insieme di id, oppure ``None`` se l'exchange non permette la verifica.
        """
        try:
            orders = await retry_async(
                lambda: self.exchange.fetch_open_orders(
                    self.symbol, None, None, dict(TRIGGER_PARAMS)
                )
            )
        except Exception as exc:
            logger.warning("Impossibile verificare gli ordini condizionali: %s", exc)
            return None
        return {str(o["id"]) for o in orders}

    # ------------------------------------------------------------------ #
    # Riconciliazione
    # ------------------------------------------------------------------ #
    async def fetch_position_amount(self) -> float:
        """Quantità aperta sul derivato, in asset base (valore assoluto).

        Returns:
            La size della posizione, ``0.0`` se flat.

        Raises:
            RuntimeError: Se chiamato su un mercato spot.
        """
        if not self.is_contract:
            raise RuntimeError("fetch_position_amount è definito solo per i derivati")
        positions = await retry_async(lambda: self.exchange.fetch_positions([self.symbol]))
        contract_size = self.market_limits().contract_size
        total = 0.0
        for pos in positions:
            if pos.get("symbol") == self.symbol:
                total += abs(float(pos.get("contracts") or 0.0)) * contract_size
        return total

    async def _free_base_balance(self) -> float:
        """Saldo libero dell'asset base (spot)."""
        base = self.config.exchange.base_currency
        balance = await retry_async(lambda: self.exchange.fetch_balance())
        return float((balance.get("free") or {}).get(base) or 0.0)

    async def fetch_exit_fill(self, side: Side, since_ms: int) -> Fill | None:
        """Ricostruisce il fill di uscita dai trade eseguiti dopo ``since_ms``.

        Args:
            side: Direzione del trade chiuso.
            since_ms: Timestamp di apertura del trade (ms).

        Returns:
            Il :class:`Fill` aggregato (prezzo medio ponderato), o ``None``.
        """
        try:
            trades = await retry_async(
                lambda: self.exchange.fetch_my_trades(self.symbol, since=since_ms)
            )
        except Exception as exc:
            logger.warning("fetch_my_trades fallito: %s", exc)
            return None
        exits = [
            t for t in trades
            if t.get("side") == side.exit_order_side and (t.get("timestamp") or 0) >= since_ms
        ]
        contract_size = self.market_limits().contract_size
        qty = sum(float(t["amount"]) for t in exits) * contract_size
        if qty <= 0 or not math.isfinite(qty):
            return None
        cost = sum(float(t["amount"]) * float(t["price"]) for t in exits) * contract_size
        order_id = str(exits[-1].get("order") or exits[-1].get("id"))
        return Fill(order_id=order_id, price=cost / qty, amount=qty)
