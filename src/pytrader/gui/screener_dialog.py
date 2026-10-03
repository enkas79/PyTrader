"""Dialogo dello screener: universo e pesi, classifica attuale e verifica storica."""

from __future__ import annotations

import contextlib
import math
import threading
from typing import Optional

from PyQt6.QtCore import Qt, QThreadPool, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pytrader.gui.formatting import fmt_price, it_num
from pytrader.gui.theme import current_palette
from pytrader.gui.widgets import dspin, spin
from pytrader.gui.workers import Worker
from pytrader.live import WatchItem
from pytrader.screener import (
    MAX_SYMBOLS,
    PRESETS,
    RankRow,
    ScreenerConfig,
    ScreenerOutcome,
    ScreenerParams,
    ScreenerRequest,
    ValidationResult,
    parse_symbols,
    run_screener,
)
from pytrader.services import SourceKind

TIMEFRAMES = ("1h", "4h", "1d", "1w")
RANK_COLUMNS = (
    "#", "Simbolo", "Punteggio", "Vol. relativo", "Momentum %", "ATR %", "Ultima chiusura",
    "Ultima candela",
)  # fmt: skip
SOURCES = (
    (SourceKind.YFINANCE, "Yahoo Finance"),
    (SourceKind.CCXT, "Exchange crypto (ccxt)"),
)


class NumericItem(QTableWidgetItem):
    """Cella ordinata per valore numerico (NaN in fondo) invece che per testo."""

    def __init__(self, text: str, value: float) -> None:
        super().__init__(text)
        self.value = value
        self.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, NumericItem):
            a = -math.inf if math.isnan(self.value) else self.value
            b = -math.inf if math.isnan(other.value) else other.value
            return a < b
        return super().__lt__(other)


def _num(value: float, fmt: str, suffix: str = "") -> str:
    return "—" if not math.isfinite(value) else it_num(format(value, fmt)) + suffix


class ScreenerDialog(QDialog):
    """Esegue ``run_screener`` nel thread pool; i risultati alimentano watchlist e grafico."""

    add_requested = pyqtSignal(object)  # list[WatchItem]
    open_requested = pyqtSignal(object)  # WatchItem

    def __init__(
        self, config: Optional[ScreenerConfig] = None, parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Screener multi-simbolo")
        self.resize(1280, 880)
        self._config = config or ScreenerConfig.load()
        self._cancel = threading.Event()
        self._worker: Optional[Worker] = None
        self._outcome: Optional[ScreenerOutcome] = None
        self._build_ui()
        self._apply_config(self._config)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        universe_box = QGroupBox("Universo")
        uni = QFormLayout(universe_box)
        uni.setSpacing(8)
        self.source_combo = QComboBox()
        for kind, label in SOURCES:
            self.source_combo.addItem(label, kind)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        self.exchange_edit = QLineEdit("binance")
        self.timeframe_combo = QComboBox()
        self.timeframe_combo.addItems(TIMEFRAMES)
        self.bars_spin = spin(100, 5000, 1000)
        self.bars_spin.setToolTip("Candele scaricate per simbolo: più storia = verifica più solida")
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("Universo predefinito…", None)
        for name in PRESETS:
            self.preset_combo.addItem(name, name)
        self.preset_combo.activated.connect(self._load_preset)
        self.symbols_edit = QPlainTextEdit()
        self.symbols_edit.setPlaceholderText("Simboli separati da spazio, virgola o a capo")
        self.symbols_edit.setMinimumHeight(64)
        self.symbols_edit.textChanged.connect(self._update_count)
        self.count_label = QLabel()
        self.count_label.setObjectName("hint")
        uni.addRow("Sorgente", self.source_combo)
        uni.addRow("Exchange", self.exchange_edit)
        uni.addRow("Timeframe", self.timeframe_combo)
        uni.addRow("Candele", self.bars_spin)
        uni.addRow(self.preset_combo)
        uni.addRow(self.symbols_edit)
        uni.addRow(self.count_label)
        self._universe_form = uni

        score_box = QGroupBox("Punteggio (valori in candele)")
        sc = QFormLayout(score_box)
        sc.setSpacing(8)
        self.vol_window_spin = spin(2, 500, 20)
        self.vol_window_spin.setToolTip("Il volume dell'ultima candela è confrontato con la media "
                                        "di queste candele precedenti")  # fmt: skip
        self.mom_spin = spin(2, 2000, 126)
        self.mom_spin.setToolTip("Variazione di prezzo su questo periodo (126 ≈ 6 mesi su 1d)")
        self.skip_spin = spin(0, 500, 5)
        self.skip_spin.setToolTip(
            "Candele più recenti escluse dal momentum: sul brevissimo i prezzi tendono a "
            "invertire, non a proseguire"
        )
        self.vol_weight_spin = dspin(-100, 100, 50, 10, 0)
        self.mom_weight_spin = dspin(-100, 100, 50, 10, 0)
        for box in (self.vol_weight_spin, self.mom_weight_spin):
            box.setToolTip("Peso nel punteggio; negativo = ordine invertito, 0 = escluso")
        self.horizon_spin = spin(1, 500, 20)
        self.horizon_spin.setToolTip("Candele dopo il segnale su cui si misura il rendimento")
        sc.addRow("Finestra volume", self.vol_window_spin)
        sc.addRow("Periodo momentum", self.mom_spin)
        sc.addRow("Candele escluse", self.skip_spin)
        sc.addRow("Peso volume", self.vol_weight_spin)
        sc.addRow("Peso momentum", self.mom_weight_spin)
        sc.addRow("Orizzonte verifica", self.horizon_spin)
        weights_hint = QLabel(
            "Cambiare i pesi finché la verifica migliora è overfitting: decidili prima."
        )
        weights_hint.setObjectName("hint")
        weights_hint.setWordWrap(True)
        sc.addRow(weights_hint)

        config = QHBoxLayout()
        config.setSpacing(16)
        config.addWidget(universe_box, 3)
        config.addWidget(score_box, 2)

        self.run_button = QPushButton("Avvia scansione")
        self.run_button.clicked.connect(self._run)
        self.cancel_button = QPushButton("Annulla")
        self.cancel_button.setObjectName("secondaryButton")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        run_row = QHBoxLayout()
        run_row.setSpacing(8)
        run_row.addWidget(self.run_button)
        run_row.addWidget(self.cancel_button)
        run_row.addWidget(self.progress, 1)

        self.verdict_label = QLabel(
            "La scansione ordina i simboli per punteggio e verifica sullo storico se punteggi "
            "alti hanno davvero preceduto rendimenti migliori."
        )
        self.verdict_label.setWordWrap(True)

        check_box = QGroupBox("Verifica storica")
        check = QFormLayout()
        check.setSpacing(8)
        self.check_labels: dict[str, QLabel] = {}
        for key, label in (
            ("periods", "Periodi indipendenti"),
            ("symbols", "Simboli per periodo"),
            ("ic", "IC medio punteggio"),
            ("tstat", "t-stat"),
            ("hit", "Periodi con IC > 0"),
            ("spread", "Quantile alto − basso"),
        ):
            value = QLabel("—")
            value.setObjectName("metricValue")
            self.check_labels[key] = value
            check.addRow(label, value)
        self.factors_label = QLabel("")
        self.factors_label.setObjectName("hint")
        self.factors_label.setWordWrap(True)
        self.buckets_label = QLabel("")
        self.buckets_label.setObjectName("hint")
        self.buckets_label.setWordWrap(True)
        # Le etichette a capo stanno fuori dal QFormLayout, che ne sottostima l'altezza
        check_layout = QVBoxLayout(check_box)
        check_layout.setSpacing(12)
        check_layout.addLayout(check)
        check_layout.addWidget(self.factors_label)
        check_layout.addWidget(self.buckets_label)
        check_layout.addStretch(1)

        self.rank_table = QTableWidget(0, len(RANK_COLUMNS))
        self.rank_table.setHorizontalHeaderLabels(RANK_COLUMNS)
        self.rank_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rank_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rank_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.rank_table.setAlternatingRowColors(True)
        self.rank_table.verticalHeader().setVisible(False)
        self.rank_table.setMinimumHeight(220)
        self.rank_table.setToolTip("Doppio clic per aprire il simbolo nel grafico")
        self.rank_table.cellDoubleClicked.connect(self._open_row)
        self.rank_table.itemSelectionChanged.connect(self._update_buttons)
        header = self.rank_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)

        results = QHBoxLayout()
        results.setSpacing(16)
        check_box.setMaximumWidth(400)
        results.addWidget(self.rank_table, 1)
        results.addWidget(check_box)

        self.errors_label = QLabel("")
        self.errors_label.setObjectName("hint")
        self.errors_label.setWordWrap(True)
        self.add_button = QPushButton("Aggiungi alla watchlist")
        self.add_button.setToolTip("Aggiunge i simboli selezionati al monitoraggio live")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self._add_selected)
        self.open_button = QPushButton("Apri nel grafico")
        self.open_button.setObjectName("secondaryButton")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(lambda: self._open_row(self.rank_table.currentRow(), 0))
        close_button = QPushButton("Chiudi")
        close_button.setObjectName("secondaryButton")
        close_button.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addWidget(self.errors_label, 1)
        bottom.addWidget(self.open_button)
        bottom.addWidget(self.add_button)
        bottom.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addLayout(config)
        layout.addLayout(run_row)
        layout.addWidget(self.verdict_label)
        layout.addLayout(results, 1)
        layout.addLayout(bottom)

    # ------------------------------------------------------------ input
    def _apply_config(self, cfg: ScreenerConfig) -> None:
        self.source_combo.setCurrentIndex(max(0, self.source_combo.findData(cfg.kind)))
        self.exchange_edit.setText(cfg.exchange)
        if cfg.timeframe in TIMEFRAMES:
            self.timeframe_combo.setCurrentText(cfg.timeframe)
        self.bars_spin.setValue(cfg.bars)
        self.symbols_edit.setPlainText(cfg.symbols)
        p = cfg.params
        self.vol_window_spin.setValue(p.volume_window)
        self.mom_spin.setValue(p.momentum_bars)
        self.skip_spin.setValue(p.skip_bars)
        self.vol_weight_spin.setValue(p.volume_weight)
        self.mom_weight_spin.setValue(p.momentum_weight)
        self.horizon_spin.setValue(p.horizon)
        self._on_source_changed()
        self._update_count()

    def _on_source_changed(self) -> None:
        is_ccxt = self.source_combo.currentData() is SourceKind.CCXT
        self._universe_form.setRowVisible(self.exchange_edit, is_ccxt)

    def _load_preset(self) -> None:
        name = self.preset_combo.currentData()
        if name is None:
            return
        kind, symbols = PRESETS[name]
        self.source_combo.setCurrentIndex(self.source_combo.findData(kind))
        if kind is SourceKind.CCXT:
            self.exchange_edit.setText("binance")
        self.symbols_edit.setPlainText(" ".join(symbols))
        self.preset_combo.setCurrentIndex(0)

    def _update_count(self) -> None:
        n = len(parse_symbols(self.symbols_edit.toPlainText()))
        warn = f" (massimo {MAX_SYMBOLS})" if n > MAX_SYMBOLS else ""
        self.count_label.setText(
            f"{n} simboli{warn}. Un universo scelto oggi esclude i titoli falliti o delistati: "
            "la verifica storica risulta ottimistica."
        )

    def params(self) -> ScreenerParams:
        return ScreenerParams(
            volume_window=self.vol_window_spin.value(),
            momentum_bars=self.mom_spin.value(),
            skip_bars=self.skip_spin.value(),
            horizon=self.horizon_spin.value(),
            volume_weight=self.vol_weight_spin.value(),
            momentum_weight=self.mom_weight_spin.value(),
        )

    def request(self) -> ScreenerRequest:
        return ScreenerRequest(
            kind=self.source_combo.currentData(),
            symbols=parse_symbols(self.symbols_edit.toPlainText()),
            timeframe=self.timeframe_combo.currentText(),
            bars=self.bars_spin.value(),
            exchange=self.exchange_edit.text().strip() or "binance",
        )

    def _save_config(self, request: ScreenerRequest, params: ScreenerParams) -> None:
        cfg = ScreenerConfig(
            kind=request.kind,
            exchange=request.exchange,
            timeframe=request.timeframe,
            bars=request.bars,
            symbols=self.symbols_edit.toPlainText(),
            params=params,
        )
        with contextlib.suppress(OSError):  # preferenza non essenziale: si prosegue
            cfg.save()

    # ------------------------------------------------------------ esecuzione
    @property
    def is_running(self) -> bool:
        return self._worker is not None

    def _run(self) -> None:
        try:
            params = self.params()
            request = self.request()
        except ValueError as exc:
            QMessageBox.warning(self, "Screener", str(exc))
            return
        if request.bars <= params.warmup_bars + params.horizon:
            QMessageBox.warning(
                self,
                "Screener",
                f"Servono più di {params.warmup_bars + params.horizon} candele per calcolare "
                "punteggio e verifica: aumenta le candele o riduci i periodi.",
            )
            return
        self._save_config(request, params)
        self._cancel.clear()
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setValue(0)
        self.verdict_label.setText(f"Scansione di {len(request.symbols)} simboli in corso…")
        worker = Worker(
            run_screener, request, params, with_progress=True, cancel=self._cancel.is_set
        )
        worker.signals.finished.connect(self._on_done)
        worker.signals.failed.connect(self._on_failed)
        worker.signals.progress.connect(self.progress.setValue)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    def _finish(self) -> None:
        self._worker = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)

    def _on_failed(self, message: str) -> None:
        self._finish()
        self.progress.setValue(0)
        if self._cancel.is_set():
            self.verdict_label.setText("Scansione annullata")
        else:
            self.verdict_label.setText(f"Scansione non riuscita: {message}")

    def _on_done(self, outcome: ScreenerOutcome) -> None:
        self._finish()
        self.show_outcome(outcome)

    # ------------------------------------------------------------ risultati
    def show_outcome(self, outcome: ScreenerOutcome) -> None:
        self._outcome = outcome
        self._fill_ranking(outcome.rows)
        self._fill_validation(outcome.validation)
        if outcome.errors:
            names = ", ".join(sorted(outcome.errors))
            self.errors_label.setText(f"Simboli non caricati ({len(outcome.errors)}): {names}")
            self.errors_label.setToolTip(
                "\n".join(f"{s}: {e}" for s, e in sorted(outcome.errors.items()))
            )
        else:
            self.errors_label.setText("")
            self.errors_label.setToolTip("")
        self._update_buttons()

    def _fill_ranking(self, rows: list[RankRow]) -> None:
        palette = current_palette()
        up, down = QColor(palette.up), QColor(palette.down)
        table = self.rank_table
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            last_bar = f"{row.last_bar:%d/%m/%Y %H:%M}" + (" ⚠" if row.stale else "")
            items = (
                NumericItem(str(i + 1), i + 1),
                QTableWidgetItem(row.symbol),
                NumericItem(_num(row.score, ".1f"), row.score),
                NumericItem(_num(row.rel_volume, ".2f", "×"), row.rel_volume),
                NumericItem(_num(row.momentum, "+.1f"), row.momentum),
                NumericItem(_num(row.atr_pct, ".2f"), row.atr_pct),
                NumericItem(fmt_price(row.last_close), row.last_close),
                QTableWidgetItem(last_bar),
            )
            if math.isfinite(row.momentum) and row.momentum != 0:
                items[4].setForeground(up if row.momentum > 0 else down)
            if row.stale:
                items[7].setToolTip("Dati meno recenti del resto dell'universo")
            if math.isnan(row.score):
                items[2].setToolTip("Dati insufficienti per una feature con peso")
            items[1].setData(Qt.ItemDataRole.UserRole, row.symbol)
            for col, item in enumerate(items):
                table.setItem(i, col, item)
        table.setSortingEnabled(True)
        table.sortItems(0, Qt.SortOrder.AscendingOrder)  # ordine della classifica

    def _fill_validation(self, res: ValidationResult) -> None:
        s = res.score
        labels = self.check_labels
        labels["periods"].setText(str(res.periods))
        labels["symbols"].setText(_num(res.avg_symbols, ".1f"))
        labels["ic"].setText(_num(s.mean_ic, "+.3f"))
        labels["tstat"].setText(_num(s.ic_tstat, "+.2f"))
        labels["hit"].setText(_num(s.hit_rate * 100, ".0f", " %"))
        labels["spread"].setText(_num(res.spread, "+.2f", " %") + " per periodo")
        tone = ""
        if res.significant:
            tone = "up" if s.ic_tstat > 0 else "down"
        for key in ("ic", "tstat"):
            labels[key].setProperty("tone", tone)
            labels[key].style().unpolish(labels[key])
            labels[key].style().polish(labels[key])
        icon = "✔" if res.significant and s.ic_tstat > 0 else "⚠" if not res.significant else "✖"
        self.verdict_label.setText(f"{icon} {res.verdict()}")
        self.factors_label.setText(
            "Singoli fattori:\n"
            + "\n".join(
                f"• {f.name}: IC {_num(f.mean_ic, '+.3f')} · t {_num(f.ic_tstat, '+.1f')}"
                for f in res.factors
            )
        )
        if res.periods:
            quantiles = " · ".join(
                f"Q{i + 1} {_num(v, '+.2f', '%')}" for i, v in enumerate(res.buckets)
            )
            self.buckets_label.setText(
                f"Rendimento a {res.horizon} candele rispetto alla media, da punteggio basso "
                f"(Q1) ad alto:\n{quantiles}\n"
                f"Dal {res.first_date:%d/%m/%Y} al {res.last_date:%d/%m/%Y}; costi esclusi."
            )
        else:
            self.buckets_label.setText("")

    # ------------------------------------------------------------ azioni
    def _selected_symbols(self) -> list[str]:
        rows = sorted({i.row() for i in self.rank_table.selectedIndexes()})
        return [
            self.rank_table.item(r, 1).data(Qt.ItemDataRole.UserRole)
            for r in rows
            if self.rank_table.item(r, 1) is not None
        ]

    def _update_buttons(self) -> None:
        selected = bool(self._selected_symbols())
        self.add_button.setEnabled(selected)
        self.open_button.setEnabled(selected)

    def _watch_item(self, symbol: str) -> Optional[WatchItem]:
        if self._outcome is None:
            return None
        req = self._outcome.request
        return WatchItem(
            kind=req.kind,
            symbol=symbol,
            timeframe=req.timeframe,
            exchange=req.exchange if req.kind is SourceKind.CCXT else "",
        )

    def _add_selected(self) -> None:
        items = [self._watch_item(s) for s in self._selected_symbols()]
        valid = [i for i in items if i is not None]
        if valid:
            self.add_requested.emit(valid)

    def _open_row(self, row: int, _col: int) -> None:
        cell = self.rank_table.item(row, 1) if row >= 0 else None
        if cell is None:
            return
        item = self._watch_item(cell.data(Qt.ItemDataRole.UserRole))
        if item is not None:
            self.open_requested.emit(item)

    def cancel(self) -> None:
        """Interrompe la scansione in corso al prossimo simbolo."""
        self._cancel.set()

    def reject(self) -> None:
        self.cancel()
        super().reject()
