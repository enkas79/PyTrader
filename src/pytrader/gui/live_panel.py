"""Pannello Live: watchlist, monitoraggio a chiusura candela e storico dei segnali."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Optional

import pandas as pd
from PyQt6.QtCore import Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pytrader.backtest import MoneyParams
from pytrader.gui.formatting import fmt_money, fmt_price, fmt_qty, it_num
from pytrader.gui.theme import current_palette
from pytrader.gui.widgets import ColumnChooser
from pytrader.gui.workers import Worker
from pytrader.live import (
    LiveScanner,
    LiveSignal,
    ScanResult,
    SignalHistory,
    WatchItem,
    Watchlist,
    allowed_intervals,
    format_interval,
    next_check_time,
)
from pytrader.services import SourceKind
from pytrader.signals import SignalParams

logger = logging.getLogger(__name__)

WATCH_COLUMNS = (
    "Mercato", "Controllo", "Ultima candela chiusa", "Prezzo", "Prossimo controllo", "Stato",
)  # fmt: skip
INTERVAL_COL = 1
SIGNAL_COLUMNS = (
    "Rilevato", "Mercato", "Candela", "Direzione", "Pattern", "Entry ~",
    "Stop Loss", "Take Profit", "R:R", "Quantità", "Rischio",
)  # fmt: skip
TICK_MS = 5_000
ERROR_RETRY = pd.Timedelta(minutes=1)


def _local(ts: pd.Timestamp, fmt: str = "%d/%m %H:%M") -> str:
    """Timestamp UTC -> ora locale del PC."""
    tz = datetime.now().astimezone().tzinfo
    return ts.tz_convert(tz).strftime(fmt)


def next_due(item: WatchItem, now: pd.Timestamp) -> pd.Timestamp:
    """Prossimo controllo del mercato secondo l'intervallo scelto (o automatico)."""
    return next_check_time(
        item.timeframe, now, aligned=item.kind is SourceKind.CCXT, interval_min=item.interval_min
    )


def interval_combo(item: WatchItem) -> QComboBox:
    """Scelte ammesse per il mercato: automatico + intervalli plausibili per sorgente e
    timeframe (vedi ``allowed_intervals``)."""
    combo = QComboBox()
    combo.addItem("Automatico", None)
    for minutes in allowed_intervals(item.kind, item.timeframe):
        combo.addItem(f"Ogni {format_interval(minutes)}", minutes)
    combo.setCurrentIndex(max(0, combo.findData(item.interval_min)))
    auto = (
        "a ogni chiusura di candela"
        if item.kind is SourceKind.CCXT
        else "a ogni chiusura di candela, e comunque almeno ogni 5 minuti"
    )
    limit = ""
    if item.kind is SourceKind.YFINANCE:
        limit = "Yahoo: minimo 2 minuti (limite di richieste). "
    combo.setToolTip(
        f"Automatico: {auto}. {limit}Gli intervalli proposti non superano la durata della "
        "candela e la dividono esattamente, così nessuna chiusura viene saltata."
    )
    return combo


def suggested_size(signal: LiveSignal, money: MoneyParams) -> tuple[float, float]:
    """Quantità e rischio in valuta sul capitale impostato (stesse regole del backtest)."""
    if signal.risk <= 0 or signal.entry <= 0:
        return 0.0, 0.0
    qty = min(
        money.initial_capital * money.risk_pct / 100 / signal.risk,
        money.initial_capital * money.max_leverage / signal.entry,
    )
    return qty, qty * signal.risk


class LivePanel(QWidget):
    """Controlla i mercati della watchlist in background e segnala i nuovi setup."""

    new_signal = pyqtSignal(object)  # LiveSignal
    status_message = pyqtSignal(str)
    open_requested = pyqtSignal(object)  # WatchItem da aprire nel grafico
    running_changed = pyqtSignal(bool)

    def __init__(
        self,
        params_provider: Callable[[], SignalParams],
        money_provider: Callable[[], MoneyParams],
        current_item_provider: Callable[[], Optional[WatchItem]],
        scanner: Optional[LiveScanner] = None,
        watchlist: Optional[Watchlist] = None,
        history: Optional[SignalHistory] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._params_provider = params_provider
        self._money_provider = money_provider
        self._current_item_provider = current_item_provider
        self.scanner = scanner or LiveScanner()
        self.watchlist = watchlist or Watchlist.load()
        self.history = history or SignalHistory.load()
        self._due: dict[str, pd.Timestamp] = {}
        self._state: dict[str, tuple[str, str, str]] = {}  # key -> (candela, prezzo, stato)
        self._running_keys: set[str] = set()
        self._workers: set[Worker] = set()
        self._running = False
        self._row_keys: list[str] = []  # mercati a cui corrispondono i selettori d'intervallo

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._build_ui()
        self._refresh_watchlist()
        self._refresh_signals()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        self.add_button = QPushButton("Aggiungi mercato corrente")
        self.add_button.clicked.connect(self._add_current)
        self.remove_button = QPushButton("Rimuovi")
        self.remove_button.setObjectName("secondaryButton")
        self.remove_button.clicked.connect(self._remove_selected)
        self.check_button = QPushButton("Controlla ora")
        self.check_button.setObjectName("secondaryButton")
        self.check_button.clicked.connect(self.check_now)
        self.toggle_button = QPushButton("Avvia monitoraggio")
        self.toggle_button.setCheckable(True)
        self.toggle_button.toggled.connect(self._on_toggled)
        self.state_label = QLabel("Monitoraggio fermo")
        self.state_label.setObjectName("hint")

        bar = QHBoxLayout()
        bar.setSpacing(8)
        for w in (self.add_button, self.check_button):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.state_label)
        bar.addWidget(self.toggle_button)

        self.watch_table = self._make_table(WATCH_COLUMNS)
        self.signal_table = self._make_table(SIGNAL_COLUMNS)
        self.signal_table.cellDoubleClicked.connect(self._on_signal_double_clicked)
        self.signal_table.setToolTip("Doppio clic per aprire il mercato nel grafico")
        self.signal_columns = ColumnChooser(self.signal_table, SIGNAL_COLUMNS)
        self.clear_button = QPushButton("Svuota storico")
        self.clear_button.setObjectName("secondaryButton")
        self.clear_button.clicked.connect(self._clear_history)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        watch_header = QHBoxLayout()
        watch_header.addWidget(QLabel("Watchlist"))
        watch_header.addStretch(1)
        watch_header.addWidget(self.remove_button)
        left_layout.addLayout(watch_header)
        left_layout.addWidget(self.watch_table)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        header = QHBoxLayout()
        header.addWidget(QLabel("Segnali rilevati"))
        header.addStretch(1)
        header.addWidget(self.clear_button)
        right_layout.addLayout(header)
        right_layout.addWidget(self.signal_table)

        # Watchlist sopra, segnali sotto: la tabella dei segnali (11 colonne) usa tutta la larghezza
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        self.splitter = split

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addLayout(bar)
        layout.addWidget(split)

    @staticmethod
    def _make_table(columns: tuple[str, ...]) -> QTableWidget:
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    # ------------------------------------------------------------ watchlist
    def _add_current(self) -> None:
        try:
            item = self._current_item_provider()
        except ValueError as exc:
            QMessageBox.warning(self, "Watchlist", str(exc))
            return
        if item is None:
            QMessageBox.information(
                self, "Watchlist", "Seleziona una sorgente remota (ccxt o Yahoo) e un simbolo."
            )
            return
        if not self.add_items([item]):
            self.status_message.emit(f"{item.label} è già nella watchlist")
            return
        self.status_message.emit(f"{item.label} aggiunto alla watchlist")

    def add_items(self, items: list[WatchItem]) -> int:
        """Aggiunge i mercati non ancora presenti; restituisce quanti sono stati aggiunti."""
        added = [item for item in items if self.watchlist.add(item)]
        if not added:
            return 0
        self._save_watchlist()
        self._refresh_watchlist()
        if self._running:
            now = pd.Timestamp.now(tz="UTC")
            for item in added:
                self._due[item.key] = now
        return len(added)

    def _remove_selected(self) -> None:
        rows = self.watch_table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.watchlist.items[rows[0].row()]
        self.watchlist.remove(item.key)
        self._due.pop(item.key, None)
        self._state.pop(item.key, None)
        self._save_watchlist()
        self._refresh_watchlist()

    def _save_watchlist(self) -> None:
        try:
            self.watchlist.save()
        except OSError as exc:
            logger.warning("Salvataggio watchlist non riuscito: %s", exc)

    def _refresh_watchlist(self) -> None:
        """Aggiorna i testi; i selettori d'intervallo si ricreano solo se cambiano i mercati
        (ricrearli a ogni aggiornamento chiuderebbe un menu a tendina aperto)."""
        items = self.watchlist.items
        if self._row_keys != [i.key for i in items]:
            self._rebuild_interval_combos()
        for row, item in enumerate(items):
            candle, price, state = self._state.get(item.key, ("—", "—", "In attesa"))
            due = self._due.get(item.key)
            values = {
                0: item.label,
                2: candle,
                3: price,
                4: _local(due, "%H:%M:%S") if (due is not None and self._running) else "—",
                5: state,
            }
            for col, text in values.items():
                self.watch_table.setItem(row, col, QTableWidgetItem(text))

    def _rebuild_interval_combos(self) -> None:
        items = self.watchlist.items
        self.watch_table.setRowCount(len(items))
        for row, item in enumerate(items):
            combo = interval_combo(item)
            combo.currentIndexChanged.connect(
                lambda _i, key=item.key, c=combo: self._on_interval_changed(key, c.currentData())
            )
            self.watch_table.setCellWidget(row, INTERVAL_COL, combo)
        self._row_keys = [i.key for i in items]

    def _on_interval_changed(self, key: str, minutes: Optional[int]) -> None:
        item = next((i for i in self.watchlist.items if i.key == key), None)
        if item is None or item.interval_min == minutes:
            return
        try:
            updated = replace(item, interval_min=minutes)
        except ValueError as exc:  # non dovrebbe accadere: le scelte sono già filtrate
            QMessageBox.warning(self, "Watchlist", str(exc))
            return
        self.watchlist.update(updated)
        self._save_watchlist()
        if self._running and key not in self._running_keys:
            self._due[key] = next_due(updated, pd.Timestamp.now(tz="UTC"))
        label = "automatico" if minutes is None else f"ogni {format_interval(minutes)}"
        self.status_message.emit(f"{updated.label}: controllo {label}")
        self._refresh_watchlist()

    # ----------------------------------------------------------- monitoraggio
    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        if not self.toggle_button.isChecked():
            self.toggle_button.setChecked(True)  # richiama _on_toggled

    def stop(self) -> None:
        if self.toggle_button.isChecked():
            self.toggle_button.setChecked(False)

    def _on_toggled(self, checked: bool) -> None:
        self._running = checked
        self.toggle_button.setText("Ferma monitoraggio" if checked else "Avvia monitoraggio")
        self.state_label.setText("Monitoraggio attivo" if checked else "Monitoraggio fermo")
        self.watchlist.active = checked
        self._save_watchlist()
        if checked:
            now = pd.Timestamp.now(tz="UTC")
            self._due = {item.key: now for item in self.watchlist.items}
            self._timer.start()
            self._tick()
        else:
            self._timer.stop()
        self._refresh_watchlist()
        self.running_changed.emit(checked)

    def check_now(self) -> None:
        now = pd.Timestamp.now(tz="UTC")
        for item in self.watchlist.items:
            self._due[item.key] = now
        self._tick(force=True)

    def _tick(self, force: bool = False) -> None:
        if not (self._running or force):
            return
        now = pd.Timestamp.now(tz="UTC")
        for item in list(self.watchlist.items):
            if item.key in self._running_keys:
                continue
            if now >= self._due.get(item.key, now):
                self._scan(item)

    def _scan(self, item: WatchItem) -> None:
        # Parametri letti nel thread GUI; il worker riceve solo dati immutabili
        params = self._params_provider()
        worker = Worker(self.scanner.scan, item, params)
        self._workers.add(worker)
        self._running_keys.add(item.key)
        self._set_state(item, state="Controllo…")

        def done(result: ScanResult) -> None:
            self._finish(worker, item)
            self._on_result(result)

        def fail(message: str) -> None:
            self._finish(worker, item)
            now = pd.Timestamp.now(tz="UTC")
            self._due[item.key] = max(next_due(item, now), now + ERROR_RETRY)
            self._set_state(item, state=f"Errore: {message}")

        worker.signals.finished.connect(done)
        worker.signals.failed.connect(fail)
        QThreadPool.globalInstance().start(worker)

    def _finish(self, worker: Worker, item: WatchItem) -> None:
        self._workers.discard(worker)
        self._running_keys.discard(item.key)

    def _set_state(
        self, item: WatchItem, candle: Optional[str] = None, price: Optional[str] = None,
        state: Optional[str] = None,
    ) -> None:  # fmt: skip
        old = self._state.get(item.key, ("—", "—", ""))
        self._state[item.key] = (candle or old[0], price or old[1], state or old[2])
        self._refresh_watchlist()

    def _on_result(self, result: ScanResult) -> None:
        item = result.item
        now = pd.Timestamp.now(tz="UTC")
        self._due[item.key] = next_due(item, now)
        state = "Nessun segnale"
        if result.setup is not None:
            signal = LiveSignal.from_setup(item, result.setup, now)
            if self.history.add(signal):
                try:
                    self.history.save()
                except OSError as exc:
                    logger.warning("Salvataggio storico segnali non riuscito: %s", exc)
                self._refresh_signals()
                self.new_signal.emit(signal)
            state = f"Segnale {result.setup.direction.value.upper()}"
        self._set_state(
            item,
            candle=_local(result.last_closed),
            price=fmt_price(result.last_close),
            state=state,
        )

    # --------------------------------------------------------------- segnali
    def _refresh_signals(self) -> None:
        signals = list(reversed(self.history.signals))  # più recenti in alto
        money = self._money_provider()
        palette = current_palette()
        up, down = QColor(palette.up), QColor(palette.down)
        self.signal_table.setRowCount(len(signals))
        for row, sig in enumerate(signals):
            qty, risk_amount = suggested_size(sig, money)
            long = sig.direction == "long"
            values = (
                _local(pd.Timestamp(sig.detected_at)),
                sig.item_label,
                _local(pd.Timestamp(sig.signal_time)),
                "Long" if long else "Short",
                sig.pattern.replace("_", " ").title(),
                fmt_price(sig.entry),
                fmt_price(sig.stop_loss),
                fmt_price(sig.take_profit),
                it_num(f"1:{sig.risk_reward:.2f}"),
                fmt_qty(qty),
                fmt_money(risk_amount) if qty else "—",
            )
            for col, text in enumerate(values):
                cell = QTableWidgetItem(text)
                if col == 3:
                    cell.setForeground(up if long else down)
                self.signal_table.setItem(row, col, cell)

    def refresh_sizes(self) -> None:
        """Da chiamare quando cambiano capitale/rischio."""
        self._refresh_signals()

    def _clear_history(self) -> None:
        if not self.history.signals:
            return
        answer = QMessageBox.question(self, "Storico segnali", "Eliminare tutti i segnali salvati?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.history.clear()
        try:
            self.history.save()
        except OSError as exc:
            logger.warning("Salvataggio storico segnali non riuscito: %s", exc)
        self._refresh_signals()

    def _on_signal_double_clicked(self, row: int, _col: int) -> None:
        signals = list(reversed(self.history.signals))
        if 0 <= row < len(signals):
            with contextlib.suppress(ValueError):  # chiave non valida: ignorata
                self.open_requested.emit(WatchItem.from_key(signals[row].item_key))

    def shutdown(self) -> None:
        """Arresto alla chiusura dell'app: timer fermo, stato 'attivo' preservato su disco."""
        self._timer.stop()
