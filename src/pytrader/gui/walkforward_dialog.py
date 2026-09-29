"""Dialogo di ottimizzazione walk-forward: configurazione, esecuzione in background, esiti."""

from __future__ import annotations

import math
import threading
from typing import Optional

import pandas as pd
from PyQt6.QtCore import QThreadPool, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pytrader.backtest import BacktestParams, BacktestResult
from pytrader.gui.formatting import fmt_num, fmt_values, it_num, parse_values
from pytrader.gui.theme import current_palette
from pytrader.gui.workers import Worker
from pytrader.optimize import (
    GRID_FIELDS,
    Objective,
    ParamGrid,
    WalkForwardParams,
    WalkForwardResult,
    param_value,
    run_walk_forward,
)
from pytrader.signals import SignalParams

MAX_COMBINATIONS = 400
MIN_OOS_TRADES = 30  # sotto questa soglia le metriche OOS sono dominate dal caso
FOLD_COLUMNS = (
    "Fold", "In-sample", "Out-of-sample", "Parametri scelti", "Score IS",
    "Trade IS", "Exp. IS", "Trade OOS", "Exp. OOS", "Tot. R OOS",
)  # fmt: skip
SHORT_NAMES = {
    "proximity_atr": "Pross.",
    "sl_buffer_atr": "Buffer",
    "min_rr": "R:R",
    "tolerance_atr": "Toll.",
    "pivot_window": "Pivot",
}
OBJECTIVES = (
    (Objective.SQN, "SQN (costanza dei risultati)"),
    (Objective.EXPECTANCY, "Expectancy (R medio)"),
    (Objective.TOTAL_R, "Totale R"),
)


def default_grid(base: SignalParams) -> ParamGrid:
    """Griglia 3×3×3 centrata sui parametri correnti."""
    p, b, r = base.proximity_atr, base.sl_buffer_atr, base.min_rr
    return ParamGrid(
        proximity_atr=tuple(sorted({round(p * 0.6, 2), p, round(p * 1.6, 2)})),
        sl_buffer_atr=tuple(sorted({round(max(0.0, b - 0.5), 2), b, round(b + 0.5, 2)})),
        min_rr=tuple(sorted({round(max(0.5, r - 0.5), 2), r, round(r + 1.0, 2)})),
        tolerance_atr=(base.levels.tolerance_atr,),
        pivot_window=(base.pivot_window,),
    )


def describe(params: SignalParams, names: list[str]) -> str:
    if not names:
        return "parametri correnti"
    return " · ".join(f"{SHORT_NAMES[n]} {fmt_num(param_value(params, n))}" for n in names)


class WalkForwardDialog(QDialog):
    """Esegue ``run_walk_forward`` nel thread pool e mostra i risultati per fold."""

    apply_requested = pyqtSignal(object)  # SignalParams consigliati

    def __init__(
        self,
        frame: pd.DataFrame,
        base: SignalParams,
        backtest: BacktestParams,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ottimizzazione walk-forward")
        self.resize(1100, 900)
        self._frame = frame
        self._base = base
        self._backtest = backtest
        self._cancel = threading.Event()
        self._worker: Optional[Worker] = None
        self._result: Optional[WalkForwardResult] = None
        self._build_ui()
        self._update_count()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        grid_box = QGroupBox("Valori da provare (separati da ;)")
        grid_form = QFormLayout(grid_box)
        grid_form.setSpacing(8)
        defaults = default_grid(self._base)
        self.grid_edits: dict[str, QLineEdit] = {}
        for name, (label, _nested) in GRID_FIELDS.items():
            edit = QLineEdit(fmt_values(getattr(defaults, name)))
            edit.textChanged.connect(self._update_count)
            self.grid_edits[name] = edit
            grid_form.addRow(label, edit)
        self.count_label = QLabel()
        self.count_label.setObjectName("hint")
        grid_form.addRow(self.count_label)

        wf_box = QGroupBox("Validazione")
        wf_form = QFormLayout(wf_box)
        wf_form.setSpacing(8)
        d = WalkForwardParams()
        self.folds_spin = self._spin(1, 20, d.folds)
        self.folds_spin.setToolTip("Numero di finestre fuori campione consecutive")
        self.ratio_spin = QDoubleSpinBox()
        self.ratio_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.ratio_spin.setRange(0.5, 10.0)
        self.ratio_spin.setSingleStep(0.5)
        self.ratio_spin.setDecimals(1)
        self.ratio_spin.setValue(d.is_oos_ratio)
        self.ratio_spin.setSuffix(" ×")
        self.ratio_spin.setToolTip("Lunghezza della finestra in-sample rispetto a quella OOS")
        self.anchored_check = QCheckBox("In-sample ancorato all'inizio")
        self.anchored_check.setToolTip(
            "Ogni finestra di ottimizzazione parte dalla prima candela e si allunga a ogni fold"
        )
        self.objective_combo = QComboBox()
        for objective, label in OBJECTIVES:
            self.objective_combo.addItem(label, objective)
        self.min_trades_spin = self._spin(1, 500, d.min_trades)
        self.min_trades_spin.setToolTip(
            "Combinazioni con meno trade in-sample sono scartate: evita di premiare pochi "
            "trade fortunati"
        )
        wf_form.addRow("Fold", self.folds_spin)
        wf_form.addRow("Rapporto IS/OOS", self.ratio_spin)
        wf_form.addRow(self.anchored_check)
        wf_form.addRow("Obiettivo", self.objective_combo)
        wf_form.addRow("Trade IS minimi", self.min_trades_spin)

        config = QHBoxLayout()
        config.setSpacing(16)
        config.addWidget(grid_box, 3)
        config.addWidget(wf_box, 2)

        idx = self._frame.index
        fee = self._backtest.fee_rate * 100
        data_hint = QLabel(
            f"{len(self._frame)} candele dal {idx[0]:%d/%m/%Y} al {idx[-1]:%d/%m/%Y} · "
            f"commissione {it_num(f'{fee:.3f}')} % per lato · slippage non simulato "
            "(risultati ottimistici)"
        )
        data_hint.setObjectName("hint")
        data_hint.setWordWrap(True)

        self.run_button = QPushButton("Avvia ottimizzazione")
        self.run_button.clicked.connect(self._run)
        self.cancel_button = QPushButton("Annulla")
        self.cancel_button.setObjectName("secondaryButton")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel.set)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        run_row = QHBoxLayout()
        run_row.setSpacing(8)
        run_row.addWidget(self.run_button)
        run_row.addWidget(self.cancel_button)
        run_row.addWidget(self.progress, 1)

        summary_box = QGroupBox("Risultato fuori campione (tutti i fold)")
        summary_form = QFormLayout(summary_box)
        summary_form.setSpacing(8)
        self.summary_labels: dict[str, QLabel] = {}
        for key, label in (
            ("trades", "Trade OOS"),
            ("win_rate", "Win rate"),
            ("expectancy", "Expectancy OOS (R)"),
            ("is_expectancy", "Expectancy IS media (R)"),
            ("efficiency", "Efficienza walk-forward"),
            ("total_r", "Totale OOS (R)"),
            ("profit_factor", "Profit factor"),
            ("drawdown", "Max drawdown (R)"),
        ):
            value = QLabel("—")
            value.setObjectName("metricValue")
            self.summary_labels[key] = value
            summary_form.addRow(label, value)

        side_widget = QWidget()  # contenitore: le etichette a capo calcolano bene l'altezza
        side = QVBoxLayout(side_widget)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(8)
        self.verdict_label = QLabel(
            "Avvia l'ottimizzazione: per ogni fold i parametri vengono scelti sulla finestra "
            "in-sample e verificati sulla successiva, mai vista durante la scelta."
        )
        self.verdict_label.setWordWrap(True)
        self.stability_label = QLabel("")
        self.stability_label.setObjectName("hint")
        self.stability_label.setWordWrap(True)
        self.recommended_label = QLabel("")
        self.recommended_label.setWordWrap(True)
        side.addWidget(self.verdict_label)
        side.addWidget(self.stability_label)
        side.addWidget(self.recommended_label)
        side.addStretch(1)

        results = QHBoxLayout()
        results.setSpacing(16)
        results.addWidget(summary_box, 2)
        results.addWidget(side_widget, 3)

        self.fold_table = QTableWidget(0, len(FOLD_COLUMNS))
        self.fold_table.setHorizontalHeaderLabels(FOLD_COLUMNS)
        self.fold_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.fold_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.fold_table.setAlternatingRowColors(True)
        self.fold_table.verticalHeader().setVisible(False)
        self.fold_table.setMinimumHeight(200)
        header = self.fold_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)

        self.apply_button = QPushButton("Applica parametri consigliati")
        self.apply_button.setEnabled(False)
        self.apply_button.setToolTip(
            "Scelti sulla finestra in-sample più recente, con le stesse regole dei fold"
        )
        self.apply_button.clicked.connect(self._apply)
        close_button = QPushButton("Chiudi")
        close_button.setObjectName("secondaryButton")
        close_button.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.apply_button)
        bottom.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addLayout(config)
        layout.addWidget(data_hint)
        layout.addLayout(run_row)
        layout.addLayout(results)
        layout.addWidget(self.fold_table, 1)
        layout.addLayout(bottom)

    @staticmethod
    def _spin(lo: int, hi: int, value: int) -> QSpinBox:
        box = QSpinBox()
        box.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        box.setRange(lo, hi)
        box.setValue(value)
        return box

    # ------------------------------------------------------------ input
    def grid(self) -> ParamGrid:
        """Griglia dai campi di testo; ``ValueError`` con messaggio leggibile se non valida."""
        values: dict[str, tuple[float, ...]] = {}
        for name, edit in self.grid_edits.items():
            label = GRID_FIELDS[name][0]
            try:
                parsed = parse_values(edit.text())
            except ValueError as exc:
                raise ValueError(f"{label}: {exc}") from None
            if name == "pivot_window":
                if any(v != int(v) or v < 1 for v in parsed):
                    raise ValueError(f"{label}: servono interi >= 1")
                values[name] = tuple(sorted({int(v) for v in parsed}))
                continue
            if name in ("min_rr", "tolerance_atr") and any(v <= 0 for v in parsed):
                raise ValueError(f"{label}: servono valori > 0")
            if any(v < 0 for v in parsed):
                raise ValueError(f"{label}: servono valori >= 0")
            values[name] = parsed
        return ParamGrid(**values)  # type: ignore[arg-type]

    def wf_params(self) -> WalkForwardParams:
        return WalkForwardParams(
            folds=self.folds_spin.value(),
            is_oos_ratio=self.ratio_spin.value(),
            anchored=self.anchored_check.isChecked(),
            objective=self.objective_combo.currentData(),
            min_trades=self.min_trades_spin.value(),
        )

    def _update_count(self) -> None:
        try:
            size = self.grid().size
        except ValueError as exc:
            self.count_label.setText(str(exc))
            return
        warn = f" (massimo {MAX_COMBINATIONS})" if size > MAX_COMBINATIONS else ""
        self.count_label.setText(
            f"{size} combinazioni{warn}. Più combinazioni = più rischio di trovare parametri "
            "buoni solo per caso."
        )

    # ------------------------------------------------------------ esecuzione
    @property
    def is_running(self) -> bool:
        return self._worker is not None

    def _run(self) -> None:
        try:
            grid = self.grid()
            wf = self.wf_params()
            grid.combinations(self._base)  # valida i valori (es. R:R minimo > 0)
        except ValueError as exc:
            QMessageBox.warning(self, "Walk-forward", str(exc))
            return
        if grid.size > MAX_COMBINATIONS:
            QMessageBox.warning(
                self,
                "Walk-forward",
                f"{grid.size} combinazioni: riduci i valori (massimo {MAX_COMBINATIONS}).",
            )
            return
        self._cancel.clear()
        self._result = None
        self.apply_button.setEnabled(False)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setValue(0)
        self.verdict_label.setText("Ottimizzazione in corso…")
        worker = Worker(
            run_walk_forward,
            self._frame,
            self._base,
            grid,
            wf,
            self._backtest,
            with_progress=True,
            cancel=self._cancel.is_set,
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
        self.verdict_label.setText(f"Ottimizzazione non completata: {message}")

    def _on_done(self, result: WalkForwardResult) -> None:
        self._finish()
        self.show_result(result)

    # ------------------------------------------------------------ risultati
    def show_result(self, result: WalkForwardResult) -> None:
        self._result = result
        oos = result.oos
        labels = self.summary_labels
        labels["trades"].setText(str(oos.n_trades))
        labels["win_rate"].setText(it_num(f"{oos.win_rate * 100:.1f}") + " %")
        labels["expectancy"].setText(it_num(f"{oos.expectancy:+.3f}"))
        labels["is_expectancy"].setText(it_num(f"{result.is_expectancy:+.3f}"))
        eff = result.efficiency
        labels["efficiency"].setText("—" if math.isnan(eff) else it_num(f"{eff * 100:.0f}") + " %")
        labels["total_r"].setText(it_num(f"{oos.total_r:+.2f}"))
        pf = oos.profit_factor
        labels["profit_factor"].setText("∞" if math.isinf(pf) else it_num(f"{pf:.2f}"))
        labels["drawdown"].setText(it_num(f"{oos.max_drawdown_r:.2f}"))
        self.verdict_label.setText(self._verdict(result, oos))
        self._fill_folds(result)
        self._fill_stability(result)
        if result.recommended is not None:
            self.recommended_label.setText(
                "<b>Consigliati</b> (finestra più recente): "
                + describe(result.recommended, result.grid.varied)
            )
            self.apply_button.setEnabled(True)
        else:
            self.recommended_label.setText(
                "Nessuna combinazione raggiunge i trade minimi sulla finestra più recente."
            )

    @staticmethod
    def _verdict(result: WalkForwardResult, oos: BacktestResult) -> str:
        eff = result.efficiency
        if oos.n_trades < MIN_OOS_TRADES:
            return (
                f"⚠ Solo {oos.n_trades} trade fuori campione: troppo pochi per giudicare. "
                "Carica più candele o allarga i parametri."
            )
        if oos.expectancy <= 0:
            return (
                "✖ Nessun vantaggio fuori campione: i risultati in-sample erano dovuti a "
                "overfitting o a un regime di mercato passato. Non usare questi parametri."
            )
        if math.isnan(eff) or eff < 0.5:
            return (
                "⚠ Vantaggio fuori campione inferiore alla metà di quello in-sample: "
                "overfitting marcato, aspettati risultati peggiori del backtest."
            )
        return (
            "✔ Vantaggio confermato fuori campione. Prima di operare verifica l'impatto "
            "dello slippage e la tenuta su altri mercati e periodi."
        )

    def _fill_folds(self, result: WalkForwardResult) -> None:
        palette = current_palette()
        up, down = QColor(palette.up), QColor(palette.down)
        names = result.grid.varied
        self.fold_table.setRowCount(len(result.folds))
        for row, fold in enumerate(result.folds):
            chosen = fold.params is not None
            values = (
                str(fold.number),
                f"{fold.is_period[0]:%d/%m/%y} → {fold.is_period[1]:%d/%m/%y}",
                f"{fold.oos_period[0]:%d/%m/%y} → {fold.oos_period[1]:%d/%m/%y}",
                describe(fold.params, names) if fold.params is not None else "nessuna valida",
                it_num(f"{fold.is_score:.2f}") if chosen else "—",
                str(fold.is_result.n_trades) if chosen else "—",
                it_num(f"{fold.is_result.expectancy:+.3f}") if chosen else "—",
                str(fold.oos.n_trades),
                it_num(f"{fold.oos.expectancy:+.3f}") if fold.oos.n_trades else "—",
                it_num(f"{fold.oos.total_r:+.2f}") if fold.oos.n_trades else "—",
            )
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col in (8, 9) and fold.oos.total_r != 0:
                    item.setForeground(up if fold.oos.total_r > 0 else down)
                self.fold_table.setItem(row, col, item)

    def _fill_stability(self, result: WalkForwardResult) -> None:
        stability = result.stability()
        chosen = sum(f.params is not None for f in result.folds)
        if not stability or not chosen:
            self.stability_label.setText("")
            return
        lines = []
        for name, counts in stability.items():
            parts = ", ".join(f"{fmt_num(v)} ({n}/{chosen})" for v, n in sorted(counts.items()))
            lines.append(f"{GRID_FIELDS[name][0]}: {parts}")
        self.stability_label.setText(
            "Stabilità delle scelte — valori che cambiano a ogni fold indicano parametri "
            "guidati dal rumore:\n" + "\n".join(lines)
        )

    def _apply(self) -> None:
        if self._result is not None and self._result.recommended is not None:
            self.apply_requested.emit(self._result.recommended)
            self.accept()

    def reject(self) -> None:
        self._cancel.set()  # il worker si ferma alla prossima combinazione
        super().reject()
