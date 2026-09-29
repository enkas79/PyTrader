"""Finestra principale: barra laterale (sorgente, capitale, riepilogo parametri, metriche),
grafico, tabelle, pannello parametri staccabile, barra degli strumenti e menu."""

from __future__ import annotations

import logging
import math
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import Qt, QThreadPool, QTimer, QUrl
from PyQt6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QColor,
    QDesktopServices,
    QGuiApplication,
    QKeySequence,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from pytrader.backtest import (
    BacktestParams,
    MoneyParams,
    MoneyResult,
    PositionPlan,
    TradeOutcome,
    TradeResult,
    simulate_money,
)
from pytrader.gui.app import apply_theme
from pytrader.gui.chart import ChartWidget
from pytrader.gui.dialogs import HelpDialog
from pytrader.gui.formatting import fmt_money, fmt_price, fmt_qty, it_num
from pytrader.gui.icons import app_icon
from pytrader.gui.live_panel import LivePanel
from pytrader.gui.params_panel import ParamsPanel, money_summary
from pytrader.gui.symbol_completer import SearchJob, SymbolSearchController
from pytrader.gui.theme import current_palette
from pytrader.gui.ui_state import STATE_VERSION, UiState
from pytrader.gui.walkforward_dialog import WalkForwardDialog
from pytrader.gui.widgets import ClickableLabel, ColumnChooser, MetricCard, dspin, set_tone, spin
from pytrader.gui.workers import Worker
from pytrader.live import LiveSignal, WatchItem
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
from pytrader.settings import AppSettings, ThemeMode
from pytrader.signals import SignalParams
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
DEFAULT_HIDDEN_COLUMNS = ("Livello", "Target")  # già leggibili sul grafico
METRICS = (  # chiave, didascalia: griglia 2 × 5 nella barra laterale
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
)
OUTCOME_LABELS = {
    TradeOutcome.WIN: "Vinto",
    TradeOutcome.LOSS: "Perso",
    TradeOutcome.OPEN: "In corso",
    TradeOutcome.PENDING: "In attesa",
    TradeOutcome.SKIPPED: "Saltato",
}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {get_version()}")
        self.setWindowIcon(app_icon())
        self.resize(1400, 860)
        self._quitting = False
        self._tray_hint_shown = False
        self._pool = QThreadPool.globalInstance()
        self._workers: set[Worker] = set()
        self._data: Optional[LoadedData] = None
        self._bundle: Optional[AnalysisBundle] = None
        self._table_trades: list[TradeResult] = []
        self._money: Optional[MoneyResult] = None
        self._symbol_service = SymbolSearchService()
        self._settings = AppSettings.load()
        self._ui = UiState()
        self._wf_dialog: Optional[WalkForwardDialog] = None
        self._analyzed: Optional[tuple[SignalParams, float]] = None  # parametri dell'analisi
        self.params_stale = False  # analisi mostrata con parametri diversi da quelli correnti

        self._build_actions()
        self._build_ui()
        self._build_params_dock()
        self._build_menu()
        self._build_toolbar()
        self._build_tray()
        self._capture_default_layout()
        self._restore_ui_state()
        self._on_params_changed()
        self._update_money()
        self.statusBar().showMessage("Pronto")
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):  # Qt >= 6.5: segue il tema del sistema
            hints.colorSchemeChanged.connect(self._on_system_scheme_changed)
        if self.live.watchlist.active and self.live.watchlist.items:
            # Il monitoraggio era attivo alla chiusura precedente: riparte da solo
            QTimer.singleShot(2000, self.live.start)
        # Verifica aggiornamenti silenziosa in background all'avvio
        QTimer.singleShot(1500, lambda: self.check_updates(manual=False))

    # ------------------------------------------------------------------ UI
    def _build_actions(self) -> None:
        """Azioni condivise da menu e barra degli strumenti."""
        self.load_act = QAction("Carica dati", self)
        self.load_act.setShortcut(QKeySequence("Ctrl+L"))
        self.load_act.setToolTip("Scarica o legge la serie dalla sorgente selezionata (Ctrl+L)")
        self.load_act.triggered.connect(self._load)
        self.analyze_act = QAction("Analizza", self)
        self.analyze_act.setShortcut(QKeySequence("F5"))
        self.analyze_act.setToolTip("Ricalcola livelli, setup e backtest (F5)")
        self.analyze_act.setEnabled(False)
        self.analyze_act.triggered.connect(self._analyze)
        self.wf_act = QAction("Walk-forward…", self)
        self.wf_act.setToolTip("Ottimizzazione dei parametri con verifica fuori campione")
        self.wf_act.setEnabled(False)
        self.wf_act.triggered.connect(self._open_walk_forward)

    def _build_params_dock(self) -> None:
        self.params_panel = ParamsPanel()
        self.params_panel.changed.connect(self._on_params_changed)
        self.params_panel.analyze_requested.connect(self._analyze)
        analysis_box = QGroupBox("Analisi")
        analysis_layout = QVBoxLayout(analysis_box)
        analysis_layout.setContentsMargins(0, 0, 0, 0)
        analysis_layout.addWidget(self.params_panel)
        container = QWidget()
        container.setObjectName("paramsPanel")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(12, 8, 12, 12)
        container_layout.setSpacing(8)
        container_layout.addWidget(analysis_box)
        container_layout.addWidget(self._money_box)
        container_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(container)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        dock = QDockWidget("Parametri e rischio", self)
        dock.setObjectName("paramsDock")  # necessario per saveState/restoreState
        dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        dock.setWidget(scroll)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        dock.hide()  # di default chiuso: si apre con Ctrl+P, dalla barra o dal riepilogo
        self.params_dock = dock
        self.params_act = dock.toggleViewAction()
        self.params_act.setText("Parametri")
        self.params_act.setShortcut(QKeySequence("Ctrl+P"))
        self.params_act.setToolTip(
            "Mostra/nasconde il pannello dei parametri: agganciabile ai lati o staccabile come "
            "finestra (Ctrl+P)"
        )

    def _build_toolbar(self) -> None:
        bar = QToolBar("Barra degli strumenti", self)
        bar.setObjectName("mainToolbar")
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        bar.addAction(self.load_act)
        bar.addAction(self.analyze_act)
        bar.addSeparator()
        bar.addAction(self.params_act)
        bar.addAction(self.wf_act)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        self.toolbar = bar
        toggle = bar.toggleViewAction()
        toggle.setText("Barra degli strumenti")
        self._view_menu.insertAction(self._view_menu.actions()[0], toggle)

    def show_params(self) -> None:
        self.params_dock.show()
        self.params_dock.raise_()
        self.params_panel.atr_spin.setFocus()

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
        quit_act.triggered.connect(self.quit_app)
        file_menu.addActions([open_act, self.export_act])
        file_menu.addSeparator()
        file_menu.addAction(quit_act)

        view_menu = bar.addMenu("&Visualizza")
        self._view_menu = view_menu
        view_menu.addAction(self.params_act)
        reset_act = QAction("Ripristina disposizione", self)
        reset_act.triggered.connect(self.reset_layout)
        view_menu.addAction(reset_act)
        view_menu.addSeparator()
        theme_menu = view_menu.addMenu("Tema")
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        self.theme_actions: dict[ThemeMode, QAction] = {}
        for mode, label in (
            (ThemeMode.DARK, "Scuro"),
            (ThemeMode.LIGHT, "Chiaro"),
            (ThemeMode.SYSTEM, "Come il sistema"),
        ):
            act = QAction(label, self, checkable=True)
            act.setChecked(mode is self._settings.theme)
            act.triggered.connect(lambda _checked, m=mode: self.set_theme(m))
            self._theme_group.addAction(act)
            theme_menu.addAction(act)
            self.theme_actions[mode] = act

        tools_menu = bar.addMenu("&Strumenti")
        tools_menu.addActions([self.load_act, self.analyze_act])
        tools_menu.addSeparator()
        tools_menu.addAction(self.wf_act)

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
        side_layout.setContentsMargins(12, 4, 12, 8)
        side_layout.setSpacing(8)
        side_layout.addWidget(self._build_source_box())
        self._money_box = self._build_money_box()  # ospitato nel pannello staccabile
        side_layout.addWidget(self._build_params_summary())
        side_layout.addWidget(self._build_metrics_box())
        side_layout.addStretch(1)
        # Scorrimento solo come riserva per schermi piccoli (a 1080p non serve)
        scroll = QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        self.setup_columns = ColumnChooser(self.table, TABLE_COLUMNS)
        self.setup_columns.on_change = lambda hidden: self._ui.set_names("columns/setup", hidden)
        self.tabs.addTab(self.table, "Setup")
        self.report_text = QPlainTextEdit()
        self.report_text.setReadOnly(True)
        self.tabs.addTab(self.report_text, "Dati")
        self.live = LivePanel(self._signal_params, self._money_params, self._current_watch_item)
        self.live.status_message.connect(self.statusBar().showMessage)
        self.live.new_signal.connect(self._on_live_signal)
        self.live.open_requested.connect(self._open_watch_item)
        self.live.running_changed.connect(self._on_live_running)
        self.tabs.addTab(self.live, "Live")

        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.chart)
        right.addWidget(self.tabs)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 2)

        main = QSplitter(Qt.Orientation.Horizontal)
        main.addWidget(scroll)
        main.addWidget(right)
        main.setStretchFactor(1, 1)
        self.setCentralWidget(main)
        self.splitters = {"main": main, "right": right, "live": self.live.splitter}

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
        self.limit_spin = spin(100, 50_000, 1500)
        self.limit_spin.setSingleStep(100)
        self.limit_spin.setToolTip("Numero di candele da scaricare")
        tf_row = QWidget()  # timeframe e candele sulla stessa riga: barra laterale più bassa
        tf_layout = QHBoxLayout(tf_row)
        tf_layout.setContentsMargins(0, 0, 0, 0)
        tf_layout.setSpacing(8)
        tf_layout.addWidget(self.timeframe_combo, 1)
        candles_label = QLabel("Candele")
        candles_label.setObjectName("inlineLabel")
        tf_layout.addWidget(candles_label)
        tf_layout.addWidget(self.limit_spin, 1)
        self._tf_row = tf_row
        csv_row = QWidget()
        csv_layout = QHBoxLayout(csv_row)
        csv_layout.setContentsMargins(0, 0, 0, 0)
        csv_layout.setSpacing(8)
        self.csv_edit = QLineEdit()
        self.csv_edit.setPlaceholderText("Percorso file .csv")
        browse = QPushButton("…")
        browse.setObjectName("iconButton")
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
        form.addRow("Timeframe", tf_row)
        form.addRow(self.load_button)
        self._source_form = form
        self._csv_row = csv_row
        self._on_source_changed()
        return box

    def _build_money_box(self) -> QGroupBox:
        """Capitale e rischio: nel pannello staccabile, sotto i parametri di analisi."""
        box = QGroupBox("Capitale e rischio")
        form = QFormLayout(box)
        form.setSpacing(8)
        d = MoneyParams()
        self.capital_spin = dspin(1.0, 1e12, d.initial_capital, 1000.0)
        self.capital_spin.setGroupSeparatorShown(True)
        self.capital_spin.setToolTip(
            "Capitale iniziale, nella valuta di quotazione dello strumento"
        )
        self.risk_spin = dspin(0.1, 100.0, d.risk_pct, 0.25)
        self.risk_spin.setSuffix(" %")
        self.risk_spin.setToolTip("Quota del capitale persa se viene colpito lo stop loss")
        self.leverage_spin = dspin(0.1, 100.0, d.max_leverage, 0.5, decimals=1)
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
        for money_spin in (self.capital_spin, self.risk_spin, self.leverage_spin):
            money_spin.valueChanged.connect(self._update_money)
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

    def _build_params_summary(self) -> QGroupBox:
        box = QGroupBox("Impostazioni")
        layout = QVBoxLayout(box)
        layout.setSpacing(4)
        self.money_summary = ClickableLabel()
        self.money_summary.setObjectName("paramsSummary")
        self.money_summary.setWordWrap(True)
        self.money_summary.setToolTip("Clic per modificare capitale e rischio (Ctrl+P)")
        self.money_summary.clicked.connect(self.show_params)
        layout.addWidget(self.money_summary)
        self.params_summary = ClickableLabel()
        self.params_summary.setObjectName("paramsSummary")
        self.params_summary.setWordWrap(True)
        self.params_summary.setToolTip("Clic per modificare i parametri (Ctrl+P)")
        self.params_summary.clicked.connect(self.show_params)
        layout.addWidget(self.params_summary)
        return box

    def _build_metrics_box(self) -> QGroupBox:
        box = QGroupBox("Backtest")
        box.setToolTip(
            "Simulazione a barre: una posizione alla volta; se SL e TP cadono nella stessa "
            "candela si assume lo SL. Slippage non simulato."
        )
        grid = QGridLayout(box)
        grid.setSpacing(4)
        self.metric_labels: dict[str, QLabel] = {}
        for pos, (key, caption) in enumerate(METRICS):
            card = MetricCard(caption)
            self.metric_labels[key] = card.value
            grid.addWidget(card, pos // 2, pos % 2)
        return box

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
        ready = not busy and self._data is not None
        self.load_button.setEnabled(not busy)
        self.load_act.setEnabled(not busy)
        self.analyze_act.setEnabled(ready)
        self.params_panel.analyze_button.setEnabled(ready)
        self.wf_act.setEnabled(ready)
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
        for widget in (self.symbol_edit, self._tf_row):
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
        return self.params_panel.params()

    def _on_params_changed(self) -> None:
        """Aggiorna il riepilogo e segnala se l'analisi mostrata usa parametri diversi."""
        self.params_summary.setText(self.params_panel.summary())
        try:
            current = (self.params_panel.params(), self.params_panel.fee_rate())
        except ValueError:
            current = None
        stale = self._analyzed is not None and current != self._analyzed
        if stale == self.params_stale:
            return
        self.params_stale = stale
        # Nessun avviso nella barra laterale (resterebbe senza spazio): si evidenzia l'azione
        button = (
            self.toolbar.widgetForAction(self.analyze_act) if hasattr(self, "toolbar") else None
        )
        for widget in (button, self.params_panel.analyze_button):
            if widget is not None:
                widget.setProperty("attention", stale)
                widget.style().unpolish(widget)
                widget.style().polish(widget)
        tip = "Parametri modificati dopo l'ultima analisi: premi Analizza (F5)"
        self.analyze_act.setToolTip(tip if stale else "Ricalcola livelli, setup e backtest (F5)")
        if stale:
            self.statusBar().showMessage(tip)

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
        bt_params = BacktestParams(fee_rate=self.params_panel.fee_rate())
        self._analyzed = (params, bt_params.fee_rate)
        self._on_params_changed()
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
        if hasattr(self, "money_summary"):
            try:
                self.money_summary.setText(money_summary(self._money_params()))
            except ValueError as exc:
                self.money_summary.setText(str(exc))
        if hasattr(self, "live"):
            self.live.refresh_sizes()
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
        palette = current_palette()
        up, down = QColor(palette.up), QColor(palette.down)
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
                it_num(f"1:{s.risk_reward:.2f}"),
                "Strutturale" if s.target_source.value == "structural" else "R:R fisso",
                OUTCOME_LABELS[t.outcome],
                it_num(f"{t.r_multiple:+.2f}") if t.r_multiple is not None else "—",
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
        labels = self.metric_labels
        labels["trades"].setText(str(int(m["trades"])))
        labels["win_rate"].setText(it_num(f"{m['win_rate'] * 100:.1f}") + " %")
        labels["expectancy_r"].setText(it_num(f"{m['expectancy_r']:+.3f}"))
        labels["total_r"].setText(it_num(f"{m['total_r']:+.2f}"))
        pf = m["profit_factor"]
        labels["profit_factor"].setText("∞" if math.isinf(pf) else it_num(f"{pf:.2f}"))
        labels["max_drawdown_r"].setText(it_num(f"{m['max_drawdown_r']:.2f}"))
        labels["final_equity"].setText(fmt_money(money.final_equity))
        labels["net_profit"].setText(fmt_money(money.net_profit, signed=True))
        labels["return_pct"].setText(it_num(f"{money.return_pct:+.2f}") + " %")
        labels["max_drawdown_money"].setText(it_num(f"{money.max_drawdown_pct:.2f}") + " %")
        labels["max_drawdown_money"].setToolTip(fmt_money(money.max_drawdown_amount))
        for key, value in (
            ("expectancy_r", m["expectancy_r"]),
            ("total_r", m["total_r"]),
            ("net_profit", money.net_profit),
            ("return_pct", money.return_pct),
        ):
            set_tone(labels[key], "up" if value > 0 else "down" if value < 0 else "")

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

    # ------------------------------------------------------------ tema
    def set_theme(self, mode: ThemeMode) -> None:
        """Applica e salva il tema; grafico e tabelle vengono ricolorati subito."""
        self._settings.theme = mode
        self.theme_actions[mode].setChecked(True)
        try:
            self._settings.save()
        except OSError as exc:
            logger.warning("Salvataggio impostazioni non riuscito: %s", exc)
        self._refresh_theme()

    def _refresh_theme(self) -> None:
        palette = apply_theme(self._settings.theme)
        self.chart.apply_palette(palette)
        self._update_money()  # ricolora tabella setup e storico segnali

    def _on_system_scheme_changed(self, *_args: Any) -> None:
        if self._settings.theme is ThemeMode.SYSTEM:
            self._refresh_theme()

    # ------------------------------------------------------- walk-forward
    def _open_walk_forward(self) -> None:
        if self._data is None:
            return
        try:
            params = self._signal_params()
        except ValueError as exc:
            QMessageBox.warning(self, "Parametri", str(exc))
            return
        if self._wf_dialog is not None and self._wf_dialog.is_running:
            self._wf_dialog.show()
            self._wf_dialog.raise_()
            return
        dialog = WalkForwardDialog(
            self._data.frame,
            params,
            BacktestParams(fee_rate=self.params_panel.fee_rate()),
            self,
        )
        dialog.apply_requested.connect(self._apply_signal_params)
        self._wf_dialog = dialog  # riferimento mantenuto finché il worker può rispondere
        dialog.open()

    def _apply_signal_params(self, params: SignalParams) -> None:
        """Imposta nel pannello i parametri scelti dal walk-forward e rianalizza."""
        self.params_panel.set_params(params)
        self.statusBar().showMessage("Parametri del walk-forward applicati")
        self._analyze()

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
                self.quit_app()
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

    # ------------------------------------------------------------------ live
    def _current_watch_item(self) -> Optional[WatchItem]:
        kind = self.source_combo.currentData()
        if kind is SourceKind.CSV:
            return None
        exchange = self.exchange_edit.text().strip() or "binance"
        return WatchItem(
            kind=kind,
            symbol=self.symbol_edit.text().strip(),
            timeframe=self.timeframe_combo.currentText(),
            exchange=exchange if kind is SourceKind.CCXT else "",
        )

    def _open_watch_item(self, item: WatchItem) -> None:
        """Apre nel grafico il mercato di un segnale (doppio clic nello storico)."""
        self.source_combo.setCurrentIndex(self.source_combo.findData(item.kind))
        if item.exchange:
            self.exchange_edit.setText(item.exchange)
        self.symbol_edit.setText(item.symbol)
        self.timeframe_combo.setCurrentText(item.timeframe)
        self.tabs.setCurrentIndex(0)
        self.show_window()
        self._load()

    def _on_live_signal(self, signal: LiveSignal) -> None:
        direction = "ACQUISTO (long)" if signal.direction == "long" else "VENDITA (short)"
        title = f"{direction} · {signal.item_label}"
        body = (
            f"{signal.pattern.replace('_', ' ').title()} sul livello {fmt_price(signal.level)}\n"
            f"Entry ~{fmt_price(signal.entry)}  SL {fmt_price(signal.stop_loss)}  "
            f"TP {fmt_price(signal.take_profit)}  (R:R {it_num(f'1:{signal.risk_reward:.2f}')})"
        )
        QApplication.beep()
        self.statusBar().showMessage(f"Nuovo segnale: {title}")
        if self.tray is not None:
            self.tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 20_000)
        elif self.isHidden() or self.isMinimized():
            self.show_window()

    def _on_live_running(self, running: bool) -> None:
        if self.tray is not None:
            state = "monitoraggio attivo" if running else "monitoraggio fermo"
            self.tray.setToolTip(f"{APP_NAME} — {state}")

    # ------------------------------------------------------------ tray/chiusura
    def _build_tray(self) -> None:
        self.tray: Optional[QSystemTrayIcon] = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(app_icon(), self)
        tray.setToolTip(APP_NAME)
        menu = QMenu(self)
        show_act = QAction("Mostra PyTrader", self)
        show_act.triggered.connect(self.show_window)
        live_act = QAction("Segnali live", self)
        live_act.triggered.connect(self._show_live_tab)
        quit_act = QAction("Esci", self)
        quit_act.triggered.connect(self.quit_app)
        menu.addActions([show_act, live_act])
        menu.addSeparator()
        menu.addAction(quit_act)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.messageClicked.connect(self._show_live_tab)
        tray.show()
        self.tray = tray

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_window()

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _show_live_tab(self) -> None:
        self.tabs.setCurrentWidget(self.live)
        self.show_window()

    def quit_app(self) -> None:
        """Uscita definitiva (menu Esci, tray, installazione aggiornamento)."""
        self._quitting = True
        self.close()

    # ---------------------------------------------------- stato interfaccia
    def _capture_default_layout(self) -> None:
        """Disposizione iniziale, per 'Ripristina disposizione'."""
        self._default_state = self.saveState(STATE_VERSION)
        self._default_sizes = {"main": [330, 1070], "right": [520, 340], "live": [180, 260]}

    def _restore_ui_state(self) -> None:
        ui = self._ui
        geometry = ui.bytes("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        state = ui.bytes("window/state")
        if state is not None:
            self.restoreState(state, STATE_VERSION)  # versione diversa: ignorato
        for name, splitter in self.splitters.items():
            saved = ui.bytes(f"splitter/{name}")
            if saved is None or not splitter.restoreState(saved):
                splitter.setSizes(self._default_sizes[name])
        self.setup_columns.set_hidden(ui.names("columns/setup", list(DEFAULT_HIDDEN_COLUMNS)))
        self.live.signal_columns.set_hidden(ui.names("columns/signals", []))

    def _save_ui_state(self) -> None:
        ui = self._ui
        ui.set_bytes("window/geometry", self.saveGeometry())
        ui.set_bytes("window/state", self.saveState(STATE_VERSION))
        for name, splitter in self.splitters.items():
            ui.set_bytes(f"splitter/{name}", splitter.saveState())
        ui.set_names("columns/setup", self.setup_columns.hidden())
        ui.set_names("columns/signals", self.live.signal_columns.hidden())
        ui.sync()

    def reset_layout(self) -> None:
        """Pannelli, divisori e colonne tornano alla disposizione predefinita."""
        self.restoreState(self._default_state, STATE_VERSION)
        self.params_dock.setFloating(False)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.params_dock)
        self.params_dock.hide()
        self.toolbar.show()
        for name, splitter in self.splitters.items():
            splitter.setSizes(self._default_sizes[name])
        self.setup_columns.set_hidden(DEFAULT_HIDDEN_COLUMNS)
        self.live.signal_columns.set_hidden([])
        self._save_ui_state()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (API Qt)
        self._save_ui_state()
        if self.live.is_running and self.tray is not None and not self._quitting:
            # Monitoraggio attivo: la finestra si nasconde e l'app resta nella tray
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.showMessage(
                    APP_NAME,
                    "Il monitoraggio continua in background. Usa il menu dell'icona per uscire.",
                    QSystemTrayIcon.MessageIcon.Information,
                    8_000,
                )
            return
        self.live.shutdown()
        if self.tray is not None:
            self.tray.hide()
        event.accept()
        QApplication.quit()
