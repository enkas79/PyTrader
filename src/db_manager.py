"""Persistenza dello stato su SQLite.

Il database conserva:

* la tabella ``trades`` con tutti i parametri del trade (entry, size, SL, TP,
  ATR, id degli ordini) così che, dopo un riavvio del Raspberry Pi, il bot
  possa riprendere la gestione del trade aperto;
* la tabella ``state`` (chiave/valore) per metadati come l'ultima candela
  processata o il saldo simulato in modalità paper.

La modalità WAL con ``synchronous=NORMAL`` riduce le scritture sulla SD card
mantenendo la consistenza in caso di interruzione di corrente.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Self


class TradeStatus(StrEnum):
    """Stato del ciclo di vita di un trade."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass(slots=True)
class Trade:
    """Rappresentazione di un trade persistito.

    Attributes:
        symbol: Simbolo ccxt.
        side: ``long`` o ``short``.
        entry_price: Prezzo medio di esecuzione dell'ingresso.
        amount: Quantità in asset base.
        stop_loss: Prezzo di stop loss.
        take_profit: Prezzo di take profit.
        atr: Valore dell'ATR alla candela di segnale.
        risk_amount: Rischio monetario teorico (valuta di quotazione).
        signal_candle: Timestamp (ms) di apertura della candela di segnale.
        entry_order_id: Id dell'ordine di ingresso.
        sl_order_id: Id dell'ordine di stop sull'exchange (se presente).
        tp_order_id: Id dell'ordine di take profit sull'exchange (se presente).
        status: Stato corrente.
        opened_at: Timestamp ISO-8601 UTC di apertura.
        closed_at: Timestamp ISO-8601 UTC di chiusura.
        exit_price: Prezzo di uscita.
        exit_reason: Motivo dell'uscita (``stop_loss``, ``take_profit``, ...).
        pnl: Profitto/perdita lordo in valuta di quotazione.
        id: Chiave primaria (assegnata dal database).
    """

    symbol: str
    side: str
    entry_price: float
    amount: float
    stop_loss: float
    take_profit: float
    atr: float
    risk_amount: float
    signal_candle: int
    entry_order_id: str | None = None
    sl_order_id: str | None = None
    tp_order_id: str | None = None
    status: TradeStatus = TradeStatus.OPEN
    opened_at: str = ""
    closed_at: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    pnl: float | None = None
    id: int | None = None

    def gross_pnl(self, exit_price: float) -> float:
        """Calcola il PnL lordo (senza commissioni) a un dato prezzo di uscita.

        Args:
            exit_price: Prezzo di uscita ipotetico o effettivo.

        Returns:
            Il PnL in valuta di quotazione.
        """
        direction = 1.0 if self.side == "long" else -1.0
        return (exit_price - self.entry_price) * self.amount * direction


_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL,
    side            TEXT    NOT NULL CHECK (side IN ('long', 'short')),
    entry_price     REAL    NOT NULL,
    amount          REAL    NOT NULL CHECK (amount > 0),
    stop_loss       REAL    NOT NULL,
    take_profit     REAL    NOT NULL,
    atr             REAL    NOT NULL,
    risk_amount     REAL    NOT NULL,
    signal_candle   INTEGER NOT NULL,
    entry_order_id  TEXT,
    sl_order_id     TEXT,
    tp_order_id     TEXT,
    status          TEXT    NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
    opened_at       TEXT    NOT NULL,
    closed_at       TEXT,
    exit_price      REAL,
    exit_reason     TEXT,
    pnl             REAL
);
-- Al massimo un trade aperto per simbolo: protegge da doppi ingressi.
CREATE UNIQUE INDEX IF NOT EXISTS ux_trades_open_symbol
    ON trades(symbol) WHERE status = 'OPEN';
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _utc_now_iso() -> str:
    """Restituisce l'istante corrente in formato ISO-8601 UTC."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class DatabaseManager:
    """Gestore della persistenza SQLite.

    Le operazioni sono sincrone: su SQLite locale durano pochi millisecondi e
    non giustificano un thread pool. Ogni scrittura è una transazione atomica.

    Example:
        >>> with DatabaseManager(Path(":memory:")) as db:
        ...     db.get_open_trade("BTC/USDT:USDT") is None
        True
    """

    def __init__(self, db_path: Path) -> None:
        """Inizializza il gestore.

        Args:
            db_path: Percorso del file SQLite (``:memory:`` per i test).
        """
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------ #
    # Ciclo di vita della connessione
    # ------------------------------------------------------------------ #
    def connect(self) -> None:
        """Apre la connessione e crea lo schema se assente."""
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        conn.commit()
        self._conn = conn

    def close(self) -> None:
        """Chiude la connessione (idempotente)."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        """Connessione attiva.

        Raises:
            RuntimeError: Se :meth:`connect` non è stato chiamato.
        """
        if self._conn is None:
            raise RuntimeError("DatabaseManager non connesso: chiamare connect()")
        return self._conn

    # ------------------------------------------------------------------ #
    # Trade
    # ------------------------------------------------------------------ #
    def insert_trade(self, trade: Trade) -> Trade:
        """Registra un nuovo trade aperto.

        Args:
            trade: Trade da inserire (``id`` viene ignorato e assegnato).

        Returns:
            Lo stesso oggetto con ``id`` e ``opened_at`` valorizzati.

        Raises:
            sqlite3.IntegrityError: Se esiste già un trade aperto sul simbolo.
        """
        trade.opened_at = trade.opened_at or _utc_now_iso()
        trade.status = TradeStatus.OPEN
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO trades (symbol, side, entry_price, amount, stop_loss,
                    take_profit, atr, risk_amount, signal_candle, entry_order_id,
                    sl_order_id, tp_order_id, status, opened_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade.symbol,
                    trade.side,
                    trade.entry_price,
                    trade.amount,
                    trade.stop_loss,
                    trade.take_profit,
                    trade.atr,
                    trade.risk_amount,
                    trade.signal_candle,
                    trade.entry_order_id,
                    trade.sl_order_id,
                    trade.tp_order_id,
                    trade.status.value,
                    trade.opened_at,
                ),
            )
        trade.id = cur.lastrowid
        return trade

    def get_open_trade(self, symbol: str) -> Trade | None:
        """Restituisce il trade aperto sul simbolo, se esiste.

        Args:
            symbol: Simbolo ccxt.

        Returns:
            Il :class:`Trade` aperto oppure ``None``.
        """
        row = self.conn.execute(
            "SELECT * FROM trades WHERE symbol = ? AND status = 'OPEN'", (symbol,)
        ).fetchone()
        return self._row_to_trade(row) if row else None

    def get_trade(self, trade_id: int) -> Trade | None:
        """Restituisce un trade per chiave primaria.

        Args:
            trade_id: Id del trade.

        Returns:
            Il :class:`Trade` oppure ``None`` se inesistente.
        """
        row = self.conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        return self._row_to_trade(row) if row else None

    def update_protective_orders(
        self, trade_id: int, sl_order_id: str | None, tp_order_id: str | None
    ) -> None:
        """Aggiorna gli id degli ordini di protezione (SL/TP).

        Args:
            trade_id: Id del trade.
            sl_order_id: Id dell'ordine stop loss.
            tp_order_id: Id dell'ordine take profit.
        """
        with self.conn:
            self.conn.execute(
                "UPDATE trades SET sl_order_id = ?, tp_order_id = ? WHERE id = ?",
                (sl_order_id, tp_order_id, trade_id),
            )

    def close_trade(
        self, trade_id: int, exit_price: float, exit_reason: str, pnl: float
    ) -> None:
        """Marca un trade come chiuso.

        Args:
            trade_id: Id del trade.
            exit_price: Prezzo medio di uscita.
            exit_reason: Motivo della chiusura.
            pnl: PnL realizzato (lordo) in valuta di quotazione.
        """
        with self.conn:
            self.conn.execute(
                """
                UPDATE trades
                SET status = 'CLOSED', closed_at = ?, exit_price = ?,
                    exit_reason = ?, pnl = ?
                WHERE id = ? AND status = 'OPEN'
                """,
                (_utc_now_iso(), exit_price, exit_reason, pnl, trade_id),
            )

    def realized_pnl(self, symbol: str) -> float:
        """Somma dei PnL realizzati sul simbolo.

        Args:
            symbol: Simbolo ccxt.

        Returns:
            Il PnL cumulato (0.0 se nessun trade chiuso).
        """
        row = self.conn.execute(
            "SELECT COALESCE(SUM(pnl), 0.0) FROM trades WHERE symbol = ? AND status = 'CLOSED'",
            (symbol,),
        ).fetchone()
        return float(row[0])

    # ------------------------------------------------------------------ #
    # Stato chiave/valore
    # ------------------------------------------------------------------ #
    def get_state(self, key: str, default: str | None = None) -> str | None:
        """Legge un valore dalla tabella ``state``.

        Args:
            key: Chiave.
            default: Valore restituito se la chiave non esiste.

        Returns:
            Il valore salvato oppure ``default``.
        """
        row = self.conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else default

    def set_state(self, key: str, value: str) -> None:
        """Scrive (upsert) un valore nella tabella ``state``.

        Args:
            key: Chiave.
            value: Valore serializzato come stringa.
        """
        with self.conn:
            self.conn.execute(
                "INSERT INTO state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ------------------------------------------------------------------ #
    # Helper
    # ------------------------------------------------------------------ #
    @staticmethod
    def _row_to_trade(row: sqlite3.Row) -> Trade:
        """Converte una riga SQLite in :class:`Trade`."""
        data = dict(row)
        data["status"] = TradeStatus(data["status"])
        return Trade(**data)
