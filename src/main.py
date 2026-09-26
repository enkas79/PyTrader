"""Entry point: loop asincrono sincronizzato con la chiusura delle candele 15m.

Il loop si risveglia a ogni multiplo esatto del timeframe sull'orologio di
sistema (00, 15, 30, 45 di ogni ora UTC) più un piccolo ritardo configurabile,
scarica le candele chiuse, riconcilia il trade aperto e valuta nuovi segnali.

Su Raspberry Pi (privo di RTC) l'orologio dipende da NTP: all'avvio viene
misurato lo scarto rispetto al server dell'exchange e segnalato nel log.

Uso::

    python src/main.py        # legge la configurazione da .env / ambiente
"""

from __future__ import annotations

import asyncio
import logging
import math
import signal
import sqlite3
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ccxt.base.errors import BaseError as CcxtError

from config import BotConfig, ConfigError, __version__, load_config
from db_manager import DatabaseManager, Trade
from execution import ExecutionEngine, Fill
from strategy import Side, Signal, SizingError, Strategy

logger = logging.getLogger("pytrader")

#: Chiave della tabella ``state`` con l'ultima candela processata (ms).
STATE_LAST_CANDLE = "last_processed_candle_ms"
#: Scarto massimo tollerato tra orologio locale ed exchange.
MAX_CLOCK_DRIFT_MS = 1_000.0
#: Tentativi di download se l'exchange non ha ancora consolidato la candela.
CANDLE_FETCH_ATTEMPTS = 4


# ---------------------------------------------------------------------- #
# Funzioni pure di sincronizzazione temporale
# ---------------------------------------------------------------------- #
def seconds_until_next_candle(now_s: float, timeframe_s: int, delay_s: float = 0.0) -> float:
    """Secondi da attendere fino alla prossima chiusura di candela.

    Args:
        now_s: Istante corrente (epoch, secondi).
        timeframe_s: Durata della candela in secondi.
        delay_s: Ritardo aggiuntivo dopo la chiusura teorica.

    Returns:
        Attesa in secondi (sempre > 0) fino a ``boundary + delay``.

    Example:
        >>> seconds_until_next_candle(1_700_000_200.0, 900, 5.0)
        805.0
    """
    next_boundary = (math.floor(now_s / timeframe_s) + 1) * timeframe_s
    wait = next_boundary + delay_s - now_s
    # Se siamo già dentro la finestra di ritardo della candela corrente,
    # la chiusura "attuale" è già passata: si punta alla successiva.
    if wait > timeframe_s:
        wait -= timeframe_s
    return wait


def last_closed_candle_open_ms(now_s: float, timeframe_s: int) -> int:
    """Timestamp di apertura (ms) dell'ultima candela chiusa.

    Args:
        now_s: Istante corrente (epoch, secondi).
        timeframe_s: Durata della candela in secondi.

    Returns:
        L'apertura della candela terminata all'ultimo multiplo del timeframe.
    """
    current_open = math.floor(now_s / timeframe_s) * timeframe_s
    return int((current_open - timeframe_s) * 1000)


def setup_logging(log_path: Path, level: str) -> None:
    """Configura logging su file con rotazione (tutela della SD card) e stdout.

    Args:
        log_path: File di log.
        level: Livello di logging.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    root.setLevel(level)
    logging.getLogger("ccxt").setLevel(logging.WARNING)


# ---------------------------------------------------------------------- #
# Bot
# ---------------------------------------------------------------------- #
class TradingBot:
    """Orchestratore: dati → strategia → rischio → esecuzione → persistenza.

    Invarianti:
        * al massimo un trade aperto per simbolo (vincolo anche su SQLite);
        * nessuna posizione resta senza protezione: se SL/TP non possono
          essere piazzati la posizione viene chiusa immediatamente;
        * ogni candela viene processata al più una volta, anche tra riavvii.

    Attributes:
        config: Configurazione completa.
        db: Persistenza SQLite.
        engine: Adattatore verso l'exchange.
        strategy: Generatore di segnali.
    """

    def __init__(
        self,
        config: BotConfig,
        db: DatabaseManager,
        engine: ExecutionEngine,
        strategy: Strategy,
    ) -> None:
        """Inizializza il bot con le dipendenze iniettate.

        Args:
            config: Configurazione completa.
            db: Persistenza SQLite (già connessa).
            engine: Adattatore exchange (già connesso).
            strategy: Strategia.
        """
        self.config = config
        self.db = db
        self.engine = engine
        self.strategy = strategy
        self.symbol = config.exchange.symbol
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ #
    # Ciclo di vita
    # ------------------------------------------------------------------ #
    def request_stop(self) -> None:
        """Richiede l'arresto ordinato (SIGINT/SIGTERM)."""
        logger.info("Arresto richiesto")
        self._stop.set()

    async def _sleep(self, seconds: float) -> bool:
        """Attende ``seconds`` o fino alla richiesta di arresto.

        Returns:
            ``True`` se è stato richiesto l'arresto.
        """
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(seconds, 0.0))
        except TimeoutError:
            return False
        return True

    async def startup_checks(self) -> None:
        """Controllo orologio e recupero dello stato dopo un riavvio."""
        drift = await self.engine.clock_drift_ms()
        if drift is not None:
            log = logger.warning if abs(drift) > MAX_CLOCK_DRIFT_MS else logger.info
            log("Scarto orologio locale-exchange: %+.0f ms", drift)

        trade = self.db.get_open_trade(self.symbol)
        if trade is not None:
            logger.info(
                "Recuperato trade aperto #%s: %s %.8f @ %.2f (SL %.2f / TP %.2f)",
                trade.id, trade.side, trade.amount, trade.entry_price,
                trade.stop_loss, trade.take_profit,
            )
            async with self._lock:
                await self._reconcile(trade)
        elif self._live_contract:
            orphan = await self.engine.fetch_position_amount()
            if orphan > 0:
                logger.critical(
                    "Posizione %.8f su %s non registrata nel DB: nessun nuovo "
                    "ingresso finché non viene chiusa manualmente", orphan, self.symbol,
                )

    async def run(self) -> None:
        """Esegue il loop principale fino alla richiesta di arresto."""
        await self.startup_checks()
        tasks = [asyncio.create_task(self._candle_loop(), name="candle-loop")]
        if not self.config.runtime.use_exchange_stops:
            tasks.append(asyncio.create_task(self._exit_monitor(), name="exit-monitor"))
        await self._stop.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _candle_loop(self) -> None:
        """Si risveglia a ogni chiusura di candela e processa i dati."""
        tf_s = self.config.strategy.timeframe_seconds
        delay = self.config.runtime.candle_close_delay
        while not self._stop.is_set():
            wait = seconds_until_next_candle(time.time(), tf_s, delay)
            logger.debug("Prossimo ciclo tra %.1fs", wait)
            if await self._sleep(wait):
                return
            try:
                await self.process_candle(time.time())
            except CcxtError as exc:
                logger.error("Errore exchange nel ciclo: %s", exc)
            except Exception:
                logger.exception("Errore inatteso nel ciclo")

    # ------------------------------------------------------------------ #
    # Logica per candela
    # ------------------------------------------------------------------ #
    async def process_candle(self, now_s: float) -> None:
        """Processa l'ultima candela chiusa.

        Args:
            now_s: Istante del risveglio (epoch, secondi).
        """
        tf_s = self.config.strategy.timeframe_seconds
        expected_ms = last_closed_candle_open_ms(now_s, tf_s)
        if self.db.get_state(STATE_LAST_CANDLE) == str(expected_ms):
            logger.info("Candela %s già processata", expected_ms)
            return

        df = None
        for attempt in range(CANDLE_FETCH_ATTEMPTS):
            df = await self.engine.fetch_closed_ohlcv(int(time.time() * 1000))
            if len(df) and int(df.index[-1].timestamp() * 1000) == expected_ms:
                break
            await self._sleep(2.0 * (attempt + 1))
        else:
            logger.warning("Candela %s non disponibile: ciclo saltato", expected_ms)
            return

        async with self._lock:
            trade = self.db.get_open_trade(self.symbol)
            if trade is not None:
                await self._reconcile(trade)
                trade = self.db.get_open_trade(self.symbol)

            if trade is None:
                sig = self.strategy.evaluate(df)
                last = df.iloc[-1]
                logger.info(
                    "Candela %s chiusa: close=%.2f segnale=%s",
                    df.index[-1].isoformat(), float(last["close"]),
                    sig.side.value if sig else "nessuno",
                )
                if sig is not None:
                    await self._enter(sig)
            self.db.set_state(STATE_LAST_CANDLE, str(expected_ms))

    async def _account_balance(self) -> float:
        """Capitale su cui calcolare il rischio (reale o simulato)."""
        if self.config.runtime.dry_run:
            return self.config.runtime.paper_balance + self.db.realized_pnl(self.symbol)
        return await self.engine.fetch_equity()

    @property
    def _live_contract(self) -> bool:
        """``True`` se si opera live su un derivato."""
        return not self.config.runtime.dry_run and self.engine.is_contract

    async def _enter(self, sig: Signal) -> None:
        """Esegue un ingresso: size, ordine market, persistenza, protezione.

        Args:
            sig: Segnale da eseguire.
        """
        balance = await self._account_balance()
        try:
            sizing = self.engine.compute_order_size(balance, sig.stop_distance, sig.close)
        except SizingError as exc:
            logger.warning("Segnale %s scartato: %s", sig.side.value, exc)
            return
        if sizing.capped:
            logger.warning(
                "Size limitata dal tetto al nozionale: rischio effettivo %.2f%% invece di %.2f%%",
                sizing.risk_fraction * 100, self.config.risk.risk_per_trade * 100,
            )
        if self._live_contract and await self.engine.fetch_position_amount() > 0:
            logger.error("Posizione già presente sull'exchange: ingresso annullato")
            return

        fill = await self.engine.open_position(sig.side, sizing.base_amount)
        stop_loss, take_profit = self.strategy.levels(sig.side, fill.price, sig.atr)
        trade = self.db.insert_trade(
            Trade(
                symbol=self.symbol,
                side=sig.side.value,
                entry_price=fill.price,
                amount=fill.amount,
                stop_loss=stop_loss,
                take_profit=take_profit,
                atr=sig.atr,
                risk_amount=fill.amount * sig.stop_distance,
                signal_candle=sig.candle_ts,
                entry_order_id=fill.order_id,
            )
        )
        logger.info(
            "APERTO #%s %s %.8f @ %.2f | SL %.2f | TP %.2f | rischio %.2f (%.2f%%) "
            "| nozionale %.2f",
            trade.id, trade.side, trade.amount, trade.entry_price, stop_loss, take_profit,
            trade.risk_amount, trade.risk_amount / balance * 100, fill.amount * fill.price,
        )
        if self.config.runtime.use_exchange_stops:
            await self._protect(trade)

    async def _protect(self, trade: Trade) -> None:
        """Piazza SL/TP sull'exchange; se fallisce chiude subito la posizione.

        Args:
            trade: Trade appena aperto (o recuperato senza protezione).
        """
        assert trade.id is not None
        side = Side(trade.side)
        try:
            sl_id, tp_id = await self.engine.place_protection(
                side, trade.amount, trade.stop_loss, trade.take_profit
            )
        except Exception as exc:
            logger.critical("Protezione SL/TP fallita (%s): chiusura d'emergenza", exc)
            open_ids = await self.engine.open_trigger_order_ids() or set()
            await self.engine.cancel_protection(list(open_ids))
            fill = await self.engine.close_position(side, trade.amount)
            self._finalize(trade, fill, "protection_failed")
            return
        self.db.update_protective_orders(trade.id, sl_id, tp_id)
        logger.info("Protezione attiva: SL #%s, TP #%s", sl_id, tp_id)

    async def _reconcile(self, trade: Trade) -> None:
        """Allinea il trade nel DB con lo stato reale dell'exchange.

        * Derivati live con stop sull'exchange: se la posizione è stata chiusa
          (SL/TP eseguiti, anche mentre il Raspberry era spento) il trade viene
          chiuso nel DB e l'ordine di protezione residuo cancellato; se la
          posizione è aperta ma mancano SL/TP, questi vengono ripiazzati.
        * Uscite software: nessuna azione (se ne occupa :meth:`_exit_monitor`).

        Args:
            trade: Trade aperto nel DB.
        """
        if not (self._live_contract and self.config.runtime.use_exchange_stops):
            return
        side = Side(trade.side)
        position = await self.engine.fetch_position_amount()
        if position <= trade.amount * 1e-6:
            since_ms = int(datetime.fromisoformat(trade.opened_at).timestamp() * 1000) - 1_000
            fill = await self.engine.fetch_exit_fill(side, since_ms)
            await self.engine.cancel_protection([trade.sl_order_id, trade.tp_order_id])
            if fill is None:
                logger.warning("Trade #%s chiuso esternamente, fill non ricostruibile", trade.id)
                fill = Fill("unknown", await self.engine.fetch_last_price(), trade.amount)
                self._finalize(trade, fill, "external_unknown")
                return
            reason = (
                "stop_loss"
                if abs(fill.price - trade.stop_loss) <= abs(fill.price - trade.take_profit)
                else "take_profit"
            )
            self._finalize(trade, fill, reason)
            return

        open_ids = await self.engine.open_trigger_order_ids()
        if open_ids is None:
            return
        protective = {trade.sl_order_id, trade.tp_order_id}
        if None in protective or not protective <= open_ids:
            logger.critical("Trade #%s senza protezione completa: ripiazzo SL/TP", trade.id)
            await self.engine.cancel_protection([trade.sl_order_id, trade.tp_order_id])
            trade.amount = position
            await self._protect(trade)

    def _finalize(self, trade: Trade, fill: Fill, reason: str) -> None:
        """Registra la chiusura del trade nel DB.

        Args:
            trade: Trade chiuso.
            fill: Esecuzione di uscita.
            reason: Motivo della chiusura.
        """
        assert trade.id is not None
        pnl = trade.gross_pnl(fill.price)
        self.db.close_trade(trade.id, fill.price, reason, pnl)
        r_multiple = pnl / trade.risk_amount if trade.risk_amount else float("nan")
        logger.info(
            "CHIUSO #%s %s @ %.2f (%s) | PnL lordo %.2f (%.2fR)",
            trade.id, trade.side, fill.price, reason, pnl, r_multiple,
        )

    # ------------------------------------------------------------------ #
    # Uscite software (paper trading o spot)
    # ------------------------------------------------------------------ #
    @staticmethod
    def exit_reason(trade: Trade, price: float) -> str | None:
        """Verifica se ``price`` ha toccato SL o TP del trade.

        Args:
            trade: Trade aperto.
            price: Prezzo corrente.

        Returns:
            ``stop_loss``, ``take_profit`` oppure ``None``.
        """
        if trade.side == Side.LONG:
            if price <= trade.stop_loss:
                return "stop_loss"
            if price >= trade.take_profit:
                return "take_profit"
        else:
            if price >= trade.stop_loss:
                return "stop_loss"
            if price <= trade.take_profit:
                return "take_profit"
        return None

    async def _exit_monitor(self) -> None:
        """Polling del prezzo per le uscite gestite via software."""
        interval = self.config.runtime.exit_poll_seconds
        while not self._stop.is_set():
            try:
                async with self._lock:
                    trade = self.db.get_open_trade(self.symbol)
                    if trade is not None:
                        price = await self.engine.fetch_last_price()
                        reason = self.exit_reason(trade, price)
                        if reason is not None:
                            side = Side(trade.side)
                            fill = await self.engine.close_position(side, trade.amount)
                            self._finalize(trade, fill, reason)
            except CcxtError as exc:
                logger.error("Errore exchange nel monitor uscite: %s", exc)
            except Exception:
                logger.exception("Errore inatteso nel monitor uscite")
            if await self._sleep(interval):
                return


async def main_async() -> int:
    """Costruisce le dipendenze ed esegue il bot.

    Returns:
        Codice di uscita del processo.
    """
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configurazione non valida: {exc}", file=sys.stderr)
        return 2

    setup_logging(config.runtime.log_path, config.runtime.log_level)
    logger.info(
        "PyTrader v%s | %s %s | %s | rischio %.2f%% | R:R 1:%.1f",
        __version__, config.exchange.exchange_id, config.exchange.symbol,
        "PAPER" if config.runtime.dry_run else "LIVE",
        config.risk.risk_per_trade * 100, config.strategy.reward_risk_ratio,
    )

    db = DatabaseManager(config.runtime.db_path)
    engine = ExecutionEngine(config)
    try:
        db.connect()
        await engine.connect()
        bot = TradingBot(config, db, engine, Strategy(config.strategy))
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, bot.request_stop)
        await bot.run()
    except (CcxtError, sqlite3.Error, ValueError) as exc:
        logger.critical("Errore fatale: %s", exc, exc_info=True)
        return 1
    finally:
        await engine.close()
        db.close()
    logger.info("PyTrader arrestato")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
