"""Finestra principale: pannello sorgente/parametri, grafico, tabelle risultati e menu."""

from __future__ import annotations

import logging
import math
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import Qt, QThreadPool, QTimer, QUrl
from PyQt6.QtGui import QAction, QColor, QDesktopServices, QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pytrader.analysis import LevelParams
from pytrader.backtest import (
    BacktestParams,
    MoneyParams,
    MoneyResult,
    PositionPlan,
    TradeOutcome,
    TradeResult,
    simulate_money,
)
from pytrader.gui.chart import ChartWidget
from pytrader.gui.dialogs import HelpDialog
from pytrader.gui.symbol_completer import SearchJob, SymbolSearchController
from pytrader.gui.workers import Worker
from pytrader.models import Direction
from pytrader.services import (
    AnalysisBundle,
    DataRequest,
    LoadedData,
    SourceKind,
    SymbolSearchService,
    export_json,
    load_data,
    run_analysis,
)
from pytrader.signals import SignalParams, TargetMode
from pytrader.updater import (
    ReleaseInfo,
    UpdateError,
    download_asset,
    fetch_latest_release,
    is_newer,
    launch_installer,
    select_asset,
)
from pytrader.version import APP_AUTHOR, APP_NAME, get_version

logger = logging.getLogger(__name__)

TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w")
TABLE_COLUMNS = (
    "Segnale",
    "Direzione",
    "Pattern",
    "Livello",
    "Entry",
    "Stop Loss",
    "Take Profit",
    "R:R",
    "Target",
    "Esito",
    "R",
    "Quantità",
    "Rischio",
    "P&L",
)
OUTCOME_LABELS = {
    TradeOutcome.WIN: "Vinto",
    TradeOutcome.LOSS: "Perso",
    TradeOutcome.OPEN: "In corso",
    TradeOutcome.PENDING: "In attesa",
    TradeOutcome.SKIPPED: "Saltato",
}


def _it(text: str) -> str:
    """Separatori italiani: 12,345.67 -> 12.345,67."""
    return text.translate(str.maketrans({",": ".", ".": ","}))


def fmt_price(value: Optional[float]) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return _it(f"{value:,.{6 if abs(value) < 1 else 4 if abs(value) < 100 else 2}f}")


def fmt_money(value: Optional[float], signed: bool = False) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return _it(f"{value:+,.2f}" if signed else f"{value:,.2f}")


def fmt_qty(value: float) -> str:
    if value == 0:
        return "—"
    decimals = 2 if value >= 100 else 4 if value >= 1 else 6
    return _it(f"{value:,.{decimals}f}")


def _spin(lo: int, hi: int, value: int) -> QSpinBox:
    box = QSpinBox()
    box.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)  # rotella/frecce tastiera
    box.setRange(lo, hi)
    box.setValue(value)
    return box


def _dspin(lo: float, hi: float, value: float, step: float, decimals: int = 2) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    box.setRange(lo, hi)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setValue(value)
    return box


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {get_version()}")
        self.resize(1400, 860)
        self._pool = QThreadPool.globalInstance()
        self._workers: set[Worker] = set()
        self._data: Optional[LoadedData] = None
        self._bundle: Optional[AnalysisBundle] = None
        self._table_trades: list[TradeResult] = []
        self._money: Optional[MoneyResult] = None
        self._symbol_service = SymbolSearchService()

        self._build_menu()
        self._build_ui()
        self.statusBar().showMessage("Pronto")
        # Verifica aggiornamenti silenziosa in background all'avvio
        QTimer.singleShot(1500, lambda: self.check_updates(manual=False))

    # ------------------------------------------------------------------ UI
    def _build_menu(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        open_act = QAction("Apri CSV…", self)
        open_act.setShortcut(QKeySequence.StandardKey.Open)
        open_act.triggered.connect(self._browse_csv)
        self.export_act = QAction("Esporta JSON…", self)
        self.export_act.setShortcut(QKeySequence("Ctrl+E"))
        self.export_act.setEnabled(False)
        self.export_act.triggered.connect(self._export_json)
        quit_act = QAction("Esci", self)
        quit_act.setShortcut(QKeySequence.StandardKey.Quit)
        quit_act.triggered.connect(self.close)
        file_menu.addActions([open_act, self.export_act])
        file_menu.addSeparator()
        file_menu.addAction(quit_act)

        help_menu = bar.addMenu("&Aiuto")
        guide_act = QAction("Guida", self)
        guide_act.setShortcut(QKeySequence.StandardKey.HelpContents)
        guide_act.triggered.connect(lambda: HelpDialog(self).exec())
        update_act = QAction("Controlla aggiornamenti", self)
        update_act.triggered.connect(lambda: self.check_updates(manual=True))
        about_act = QAction("Informazioni", self)
        about_act.triggered.connect(self._about)
        help_menu.addActions([guide_act, update_act])
        help_menu.addSeparator()
        help_menu.addAction(about_act)

    def _build_ui(self) -> None:
        side = QWidget()
        side.setObjectName("sidePanel")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(16, 16, 16, 16)
        side_layout.setSpacing(16)
        side_layout.addWidget(self._build_source_box())
        side_layout.addWidget(self._build_money_box())
        side_layout.addWidget(self._build_params_box())
        side_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(300)
        scroll.setMaximumWidth(360)

        self.chart = ChartWidget()
        self.tabs = QTabWidget()
        self.table = QTableWidget(0, len(TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels(TABLE_COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.tabs.addTab(self.table, "Setup")
        self.tabs.addTab(self._build_metrics_tab(), "Backtest")
        self.report_text = QPlainTextEdit()
        self.report_text.setReadOnly(True)
        self.tabs.addTab(self.report_text, "Dati")

        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.chart)
        right.addWidget(self.tabs)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 1)

        main = QSplitter(Qt.Orientation.Horizontal)
        main.addWidget(scroll)
        main.addWidget(right)
        main.setStretchFactor(1, 1)
        self.setCentralWidget(main)

    def _build_source_box(self) -> QGroupBox:
        box = QGroupBox("Sorgente dati")
        form = QFormLayout(box)
        form.setSpacing(8)
        self.source_combo = QComboBox()
        self.source_combo.addItem("CSV locale", SourceKind.CSV)
        self.source_combo.addItem("Exchange crypto (ccxt)", SourceKind.CCXT)
        self.source_combo.addItem("Yahoo Finance", SourceKind.YFINANCE)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        self.exchange_edit = QLineEdit("binance")
        self.symbol_edit = QLineEdit("BTC/USDT")
        self.symbol_edit.setClearButtonEnabled(True)
        self.symbol_search = SymbolSearchController(self.symbol_edit, self._symbol_job)
        self.symbol_search.status.connect(self.statusBar().showMessage)
        self.exchange_edit.editingFinished.connect(self.symbol_search.clear)
        self.timeframe_combo = QComboBox()
        self.timeframe_combo.addItems(TIMEFRAMES)
        self.timeframe_combo.setCurrentText("1h")
        self.limit_spin = _spin(100, 50_000, 1500)
        self.limit_spin.setSingleStep(100)
        csv_row = QWidget()
        csv_layout = QHBoxLayout(csv_row)
        csv_layout.setContentsMargins(0, 0, 0, 0)
        csv_layout.setSpacing(8)
        self.csv_edit = QLineEdit()
        self.csv_edit.setPlaceholderText("Percorso file .csv")
        browse = QPushButton("…")
        browse.setObjectName("secondaryButton")
        browse.setFixedWidth(32)
        browse.clicked.connect(self._browse_csv)
        csv_layout.addWidget(self.csv_edit)
        csv_layout.addWidget(browse)
        self.load_button = QPushButton("Carica dati")
        self.load_button.clicked.connect(self._load)

        form.addRow("Sorgente", self.source_combo)
        form.addRow("File", csv_row)
        form.addRow("Exchange", self.exchange_edit)
        form.addRow("Simbolo", self.symbol_edit)
        form.addRow("Timeframe", self.timeframe_combo)
        form.addRow("Candele", self.limit_spin)
        form.addRow(self.load_button)
        self._source_form = form
        self._csv_row = csv_row
        self._on_source_changed()
        return box

    def _build_money_box(self) -> QGroupBox:
        box = QGroupBox("Capitale e rischio")
        form = QFormLayout(box)
        form.setSpacing(8)
        d = MoneyParams()
        self.capital_spin = _dspin(1.0, 1e12, d.initial_capital, 1000.0)
        self.capital_spin.setGroupSeparatorShown(True)
        self.capital_spin.setToolTip(
            "Capitale iniziale, nella valuta di quotazione dello strumento"
        )
        self.risk_spin = _dspin(0.1, 100.0, d.risk_pct, 0.25)
        self.risk_spin.setSuffix(" %")
        self.risk_spin.setToolTip("Quota del capitale persa se viene colpito lo stop loss")
        self.leverage_spin = _dspin(0.1, 100.0, d.max_leverage, 0.5, decimals=1)
        self.leverage_spin.setSuffix(" ×")
        self.leverage_spin.setToolTip(
            "Nozionale massimo = capitale × leva. Con 1× non si usa leva: se lo stop è molto "
            "vicino la posizione viene ridotta e il rischio effettivo scende sotto la soglia."
        )
        self.compound_check = QCheckBox("Reinvesti i profitti")
        self.compound_check.setChecked(d.compounding)
        self.compound_check.setToolTip(
            "Rischio calcolato sul capitale corrente invece che su quello iniziale"
        )
        for spin in (self.capital_spin, self.risk_spin, self.leverage_spin):
            spin.valueChanged.connect(self._update_money)
        self.compound_check.toggled.connect(self._update_money)
        form.addRow("Capitale", self.capital_spin)
        form.addRow("Rischio/trade", self.risk_spin)
        form.addRow("Leva massima", self.leverage_spin)
        form.addRow(self.compound_check)
        return box

    def _money_params(self) -> MoneyParams:
        return MoneyParams(
            initial_capital=self.capital_spin.value(),
            risk_pct=self.risk_spin.value(),
            max_leverage=self.leverage_spin.value(),
            compounding=self.compound_check.isChecked(),
        )

    def _build_params_box(self) -> QGroupBox:
        box = QGroupBox("Parametri analisi")
        form = QFormLayout(box)
        form.setSpacing(8)
        d = SignalParams()
        self.atr_spin = _spin(2, 200, d.atr_period)
        self.pivot_spin = _spin(1, 50, d.pivot_window)
        self.tol_spin = _dspin(0.05, 5.0, d.levels.tolerance_atr, 0.05)
        self.touches_spin = _spin(1, 20, d.levels.min_touches)
        self.lookback_spin = _spin(50, 5000, d.levels.lookback)
        self.prox_spin = _dspin(0.0, 5.0, d.proximity_atr, 0.05)
        self.buffer_spin = _dspin(0.0, 10.0, d.sl_buffer_atr, 0.1)
        self.rr_spin = _dspin(0.5, 10.0, d.min_rr, 0.25)
        self.target_combo = QComboBox()
        self.target_combo.addItem("Livello strutturale", TargetMode.STRUCTURAL)
        self.target_combo.addItem("R:R fisso", TargetMode.FIXED_RR)
        self.fee_spin = _dspin(0.0, 1.0, 0.0, 0.01, decimals=3)
        self.fee_spin.setSuffix(" %")
        self.analyze_button = QPushButton("Analizza")
        self.analyze_button.setEnabled(False)
        self.analyze_button.clicked.connect(self._analyze)

        form.addRow("Periodo ATR", self.atr_spin)
        form.addRow("Finestra pivot", self.pivot_spin)
        form.addRow("Tolleranza (×ATR)", self.tol_spin)
        form.addRow("Tocchi minimi", self.touches_spin)
        form.addRow("Storico livelli", self.lookback_spin)
        form.addRow("Prossimità (×ATR)", self.prox_spin)
        form.addRow("Buffer SL (×ATR)", self.buffer_spin)
        form.addRow("R:R minimo", self.rr_spin)
        form.addRow("Target", self.target_combo)
        form.addRow("Commissione/lato", self.fee_spin)
        form.addRow(self.analyze_button)
        return box

    def _build_metrics_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(8)
        self.metric_labels: dict[str, QLabel] = {}
        for key, label in (
            ("trades", "Trade chiusi"),
            ("win_rate", "Win rate"),
            ("expectancy_r", "Expectancy (R)"),
            ("total_r", "Totale (R)"),
            ("profit_factor", "Profit factor"),
            ("max_drawdown_r", "Max drawdown (R)"),
            ("final_equity", "Capitale finale"),
            ("net_profit", "Profitto netto"),
            ("return_pct", "Rendimento"),
            ("max_drawdown_money", "Max drawdown"),
        ):
            value = QLabel("—")
            value.setObjectName("metricValue")
            self.metric_labels[key] = value
            form.addRow(label, value)
        note = QLabel(
            "Simulazione a barre: una posizione alla volta; se SL e TP cadono nella stessa "
            "candela si assume lo SL."
        )
        note.setWordWrap(True)
        note.setObjectName("hint")
        form.addRow(note)
        return widget

    # ------------------------------------------------------------ helpers
    def _start(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_done: Callable[[Any], None],
        on_error: Callable[[str], None],
        on_progress: Optional[Callable[[int], None]] = None,
    ) -> None:
        worker = Worker(fn, *args, with_progress=on_progress is not None)
        self._workers.add(worker)

        def done(result: Any) -> None:
            self._workers.discard(worker)
            on_done(result)

        def fail(message: str) -> None:
            self._workers.discard(worker)
            on_error(message)

        worker.signals.finished.connect(done)
        worker.signals.failed.connect(fail)
        if on_progress is not None:
            worker.signals.progress.connect(on_progress)
        self._pool.start(worker)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.load_button.setEnabled(not busy)
        self.analyze_button.setEnabled(not busy and self._data is not None)
        if message:
            self.statusBar().showMessage(message)

    def _symbol_job(self, query: str) -> Optional[SearchJob]:
        """Eseguito nel thread GUI: legge sorgente/exchange e prepara il job per il worker."""
        kind = self.source_combo.currentData()
        if kind is SourceKind.CSV or not query:
            return None
        if kind is SourceKind.YFINANCE and len(query) < 2:
            return None
        exchange = self.exchange_edit.text().strip() or "binance"
        service = self._symbol_service
        return lambda: service.search(kind, query, exchange)

    def _on_source_changed(self) -> None:
        if hasattr(self, "symbol_search"):
            self.symbol_search.clear()
        kind = self.source_combo.currentData()
        is_csv = kind is SourceKind.CSV
        for widget in (self._csv_row,):
            self._source_form.setRowVisible(widget, is_csv)
        self._source_form.setRowVisible(self.exchange_edit, kind is SourceKind.CCXT)
        for widget in (self.symbol_edit, self.timeframe_combo, self.limit_spin):
            self._source_form.setRowVisible(widget, not is_csv)
        placeholder = {
            SourceKind.YFINANCE: "Nome o ticker (es. Apple, Vanguard, Eni)",
            SourceKind.CCXT: "Coppia o valuta (es. BTC, ETH/USDT)",
        }.get(kind, "")
        self.symbol_edit.setPlaceholderText(placeholder)
        if kind is SourceKind.YFINANCE and "/" in self.symbol_edit.text():
            self.symbol_edit.setText("AAPL")
        elif kind is SourceKind.CCXT and "/" not in self.symbol_edit.text():
            self.symbol_edit.setText("BTC/USDT")

    def _browse_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Apri CSV", "", "CSV (*.csv);;Tutti (*)")
        if path:
            self.source_combo.setCurrentIndex(self.source_combo.findData(SourceKind.CSV))
            self.csv_edit.setText(path)

    def _signal_params(self) -> SignalParams:
        return SignalParams(
            atr_period=self.atr_spin.value(),
            pivot_window=self.pivot_spin.value(),
            proximity_atr=self.prox_spin.value(),
            sl_buffer_atr=self.buffer_spin.value(),
            min_rr=self.rr_spin.value(),
            target_mode=self.target_combo.currentData(),
            levels=LevelParams(
                tolerance_atr=self.tol_spin.value(),
                min_touches=self.touches_spin.value(),
                lookback=self.lookback_spin.value(),
            ),
        )

    # ------------------------------------------------------------ azioni
    def _load(self) -> None:
        request = DataRequest(
            kind=self.source_combo.currentData(),
            symbol=self.symbol_edit.text(),
            timeframe=self.timeframe_combo.currentText(),
            limit=self.limit_spin.value(),
            exchange=self.exchange_edit.text().strip() or "binance",
            csv_path=self.csv_edit.text().strip() or None,
        )
        self._set_busy(True, "Caricamento dati…")
        self._start(load_data, request, on_done=self._on_loaded, on_error=self._on_load_error)

    def _on_loaded(self, data: LoadedData) -> None:
        self._data = data
        self._bundle = None
        self.export_act.setEnabled(False)
        self.chart.set_data(data.frame)
        report = data.report
        lines = [report.summary(), ""]
        lines += [f"Da {data.frame.index[0]} a {data.frame.index[-1]}"]
        if report.timeframe is not None:
            lines.append(f"Timeframe: {report.timeframe}")
        lines += [f"Avviso: {w}" for w in report.warnings]
        for gap in report.gaps[:200]:
            lines.append(f"Buco: {gap.start} → {gap.end} ({gap.missing_bars} candele)")
        self.report_text.setPlainText("\n".join(lines))
        self._set_busy(False, f"Dati caricati: {report.summary()}")
        self._analyze()

    def _on_load_error(self, message: str) -> None:
        self._set_busy(False, "Caricamento non riuscito")
        QMessageBox.warning(self, "Caricamento dati", message)

    def _analyze(self) -> None:
        if self._data is None:
            return
        try:
            params = self._signal_params()
        except ValueError as exc:
            QMessageBox.warning(self, "Parametri", str(exc))
            return
        bt_params = BacktestParams(fee_rate=self.fee_spin.value() / 100.0)
        self._set_busy(True, "Analisi in corso…")
        self._start(
            run_analysis,
            self._data.frame,
            params,
            bt_params,
            on_done=self._on_analyzed,
            on_error=self._on_analyze_error,
        )

    def _on_analyzed(self, bundle: AnalysisBundle) -> None:
        self._bundle = bundle
        self.chart.set_levels(bundle.analysis.levels)
        self.chart.set_setups(bundle.analysis.setups)
        self._update_money()
        self.export_act.setEnabled(True)
        n_active = sum(
            t.outcome in (TradeOutcome.OPEN, TradeOutcome.PENDING) for t in bundle.backtest.trades
        )
        self._set_busy(
            False,
            f"{len(bundle.analysis.setups)} setup, {len(bundle.analysis.levels)} livelli, "
            f"{n_active} attivi",
        )

    def _on_analyze_error(self, message: str) -> None:
        self._set_busy(False, "Analisi non riuscita")
        QMessageBox.critical(self, "Analisi", message)

    def _update_money(self) -> None:
        """Ricalcola importi e metriche monetarie (operazione leggera, senza rianalisi)."""
        if self._bundle is None:
            return
        try:
            self._money = simulate_money(self._bundle.backtest.trades, self._money_params())
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))
            return
        self._fill_table(self._bundle, self._money)
        self._fill_metrics(self._bundle, self._money)

    def _fill_table(self, bundle: AnalysisBundle, money: MoneyResult) -> None:
        plans: dict[int, PositionPlan] = {id(p.trade): p for p in money.plans}
        # Più recenti in alto: i setup attivi sono i primi
        trades = sorted(bundle.backtest.trades, key=lambda t: -t.setup.signal_index)
        self._table_trades = trades
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(trades))
        up, down = QColor("#26a69a"), QColor("#ef5350")
        for row, t in enumerate(trades):
            s = t.setup
            plan = plans[id(t)]
            values = (
                s.signal_time.strftime("%Y-%m-%d %H:%M"),
                "Long" if s.direction is Direction.LONG else "Short",
                s.pattern.value.replace("_", " ").title(),
                f"{fmt_price(s.level.price)} ({s.level.touches}×)",
                fmt_price(s.entry) + (" *" if s.entry_is_estimate else ""),
                fmt_price(s.stop_loss),
                fmt_price(s.take_profit),
                _it(f"1:{s.risk_reward:.2f}"),
                "Strutturale" if s.target_source.value == "structural" else "R:R fisso",
                OUTCOME_LABELS[t.outcome],
                _it(f"{t.r_multiple:+.2f}") if t.r_multiple is not None else "—",
                fmt_qty(plan.quantity) + (" ⚠" if plan.capped else ""),
                fmt_money(plan.risk_amount) if plan.quantity else "—",
                fmt_money(plan.pnl, signed=True),
            )
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 1:
                    item.setForeground(up if s.direction is Direction.LONG else down)
                if col == 10 and t.r_multiple is not None:
                    item.setForeground(up if t.r_multiple > 0 else down)
                if col == 13 and plan.pnl is not None:
                    item.setForeground(up if plan.pnl > 0 else down)
                if col == 11 and plan.capped:
                    item.setToolTip("Quantità ridotta dal limite di leva: rischio effettivo minore")
                self.table.setItem(row, col, item)

    def _fill_metrics(self, bundle: AnalysisBundle, money: MoneyResult) -> None:
        m = bundle.backtest.summary()
        self.metric_labels["trades"].setText(str(int(m["trades"])))
        self.metric_labels["win_rate"].setText(_it(f"{m['win_rate'] * 100:.1f}") + " %")
        self.metric_labels["expectancy_r"].setText(_it(f"{m['expectancy_r']:+.3f}"))
        self.metric_labels["total_r"].setText(_it(f"{m['total_r']:+.2f}"))
        pf = m["profit_factor"]
        self.metric_labels["profit_factor"].setText("∞" if math.isinf(pf) else _it(f"{pf:.2f}"))
        self.metric_labels["max_drawdown_r"].setText(_it(f"{m['max_drawdown_r']:.2f}"))
        self.metric_labels["final_equity"].setText(fmt_money(money.final_equity))
        self.metric_labels["net_profit"].setText(fmt_money(money.net_profit, signed=True))
        self.metric_labels["return_pct"].setText(_it(f"{money.return_pct:+.2f}") + " %")
        self.metric_labels["max_drawdown_money"].setText(
            f"{fmt_money(money.max_drawdown_amount)} ({_it(f'{money.max_drawdown_pct:.2f}')} %)"
        )

    def _on_row_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or self._bundle is None:
            return
        trade = self._table_trades[rows[0].row()]
        self.chart.focus_setup(trade.setup, self._bundle.analysis.setups)

    def _export_json(self) -> None:
        if self._bundle is None or self._data is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Esporta JSON", "setups.json", "JSON (*.json)")
        if not path:
            return
        req = self._data.request
        meta = {"source": req.kind.value, "symbol": req.symbol, "timeframe": req.timeframe}
        try:
            export_json(self._bundle, path, meta, self._money)
        except OSError as exc:
            QMessageBox.warning(self, "Esporta JSON", f"Salvataggio non riuscito: {exc}")
            return
        self.statusBar().showMessage(f"Esportato in {path}")

    def _about(self) -> None:
        QMessageBox.about(
            self,
            f"Informazioni su {APP_NAME}",
            f"<h3>{APP_NAME}</h3>"
            f"<p>Versione {get_version()}</p>"
            f"<p>Autore: {APP_AUTHOR}</p>"
            "<p>Analisi tecnica OHLCV: supporti/resistenze, pattern candlestick e setup con "
            "gestione del rischio.</p>",
        )

    # --------------------------------------------------------- aggiornamenti
    def check_updates(self, manual: bool) -> None:
        def on_done(release: ReleaseInfo) -> None:
            if is_newer(release.version, get_version()):
                self._offer_update(release)
            elif manual:
                QMessageBox.information(
                    self, "Aggiornamenti", f"{APP_NAME} {get_version()} è aggiornato."
                )

        def on_error(message: str) -> None:
            logger.info("Verifica aggiornamenti: %s", message)
            if manual:
                QMessageBox.warning(self, "Aggiornamenti", message)

        self._start(fetch_latest_release, on_done=on_done, on_error=on_error)

    def _offer_update(self, release: ReleaseInfo) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Aggiornamento disponibile")
        box.setText(
            f"È disponibile {APP_NAME} {release.version} (installata: {get_version()}).\n"
            "Vuoi scaricarla ora?"
        )
        if release.notes:
            box.setDetailedText(release.notes)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        asset = select_asset(release)
        if asset is None:
            QDesktopServices.openUrl(QUrl(release.html_url))
            return

        progress = QProgressDialog("Download in corso…", "", 0, 100, self)
        progress.setWindowTitle("Aggiornamento")
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setAutoClose(True)
        progress.show()
        dest = Path(tempfile.gettempdir()) / f"{APP_NAME}-update"

        def on_done(path: Path) -> None:
            progress.close()
            try:
                started = launch_installer(path)
            except UpdateError as exc:
                QMessageBox.warning(self, "Aggiornamento", str(exc))
                return
            if started:
                self.close()
            else:
                QMessageBox.information(
                    self,
                    "Aggiornamento",
                    f"Pacchetto scaricato in:\n{path}\nInstallalo manualmente.",
                )
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

        def on_error(message: str) -> None:
            progress.close()
            QMessageBox.warning(self, "Aggiornamento", message)

        self._start(
            download_asset,
            asset,
            dest,
            on_done=on_done,
            on_error=on_error,
            on_progress=progress.setValue,
        )
