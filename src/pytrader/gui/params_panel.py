"""Pannello dei parametri di analisi, ospitato in un ``QDockWidget`` staccabile."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pytrader.analysis import LevelParams
from pytrader.backtest import MoneyParams
from pytrader.gui.formatting import fmt_money, fmt_num
from pytrader.gui.widgets import dspin, spin
from pytrader.signals import SignalParams, TargetMode


def params_summary(params: SignalParams, fee_pct: float) -> str:
    """Riepilogo su una riga dei parametri attivi (barra laterale)."""
    target = "Strutturale" if params.target_mode is TargetMode.STRUCTURAL else "R:R fisso"
    parts = [
        f"ATR {params.atr_period}",
        f"Pivot {params.pivot_window}",
        f"Toll. {fmt_num(params.levels.tolerance_atr)}",
        f"Tocchi {params.levels.min_touches}",
        f"Storico {params.levels.lookback}",
        f"Pross. {fmt_num(params.proximity_atr)}",
        f"Buffer {fmt_num(params.sl_buffer_atr)}",
        f"R:R {fmt_num(params.min_rr)}",
        target,
        f"Comm. {fmt_num(fee_pct)} %",
    ]
    return " · ".join(parts)


def money_summary(money: MoneyParams) -> str:
    """Riepilogo su una riga di capitale e rischio (barra laterale)."""
    parts = [
        f"Capitale {fmt_money(money.initial_capital)}",
        f"Rischio {fmt_num(money.risk_pct)} %",
        f"Leva {fmt_num(money.max_leverage)}×",
        "reinvesti" if money.compounding else "senza reinvestimento",
    ]
    return " · ".join(parts)


class ParamsPanel(QWidget):
    """Campi dei parametri del ``SignalEngine`` e della commissione del backtest."""

    changed = pyqtSignal()  # un qualsiasi valore modificato
    analyze_requested = pyqtSignal()
    family_requested = pyqtSignal()  # valori consigliati per famiglia di asset

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        d = SignalParams()
        self.atr_spin = spin(2, 200, d.atr_period)
        self.pivot_spin = spin(1, 50, d.pivot_window)
        self.pivot_spin.setToolTip("Candele a destra e a sinistra per confermare un massimo/minimo")
        self.tol_spin = dspin(0.05, 5.0, d.levels.tolerance_atr, 0.05)
        self.touches_spin = spin(1, 20, d.levels.min_touches)
        self.lookback_spin = spin(50, 5000, d.levels.lookback)
        self.prox_spin = dspin(0.0, 5.0, d.proximity_atr, 0.05)
        self.buffer_spin = dspin(0.0, 10.0, d.sl_buffer_atr, 0.1)
        self.rr_spin = dspin(0.5, 10.0, d.min_rr, 0.25)
        self.target_combo = QComboBox()
        self.target_combo.addItem("Livello strutturale", TargetMode.STRUCTURAL)
        self.target_combo.addItem("R:R fisso", TargetMode.FIXED_RR)
        self.fee_spin = dspin(0.0, 1.0, 0.0, 0.01, decimals=3)
        self.fee_spin.setSuffix(" %")
        self.fee_spin.setToolTip("Commissione per lato, in percentuale del controvalore")
        for box in (
            self.atr_spin, self.pivot_spin, self.tol_spin, self.touches_spin,
            self.lookback_spin, self.prox_spin, self.buffer_spin, self.rr_spin, self.fee_spin,
        ):  # fmt: skip
            box.valueChanged.connect(self.changed)
        self.target_combo.currentIndexChanged.connect(self.changed)

        self.analyze_button = QPushButton("Analizza")
        self.analyze_button.setEnabled(False)
        self.analyze_button.clicked.connect(self.analyze_requested)
        reset = QPushButton("Predefiniti")
        reset.setObjectName("secondaryButton")
        reset.setToolTip("Ripristina i valori predefiniti (commissione esclusa)")
        reset.clicked.connect(lambda: self.set_params(SignalParams()))
        self.family_button = QPushButton("Valori per famiglia di asset…")
        self.family_button.setObjectName("secondaryButton")
        self.family_button.setToolTip(
            "Commissione, leva e R:R minimo adatti a crypto, azioni, ETF/indici, forex o "
            "materie prime, con anteprima motivata"
        )
        self.family_button.clicked.connect(self.family_requested)

        form = QFormLayout()
        form.setSpacing(8)
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
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(reset)
        buttons.addWidget(self.analyze_button, 1)
        hint = QLabel("Le modifiche valgono dalla prossima analisi (F5).")
        hint.setObjectName("hint")
        hint.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(8)
        layout.addLayout(form)
        layout.addLayout(buttons)
        layout.addWidget(self.family_button)
        layout.addWidget(hint)

    def params(self) -> SignalParams:
        """Parametri correnti; ``ValueError`` se incoerenti."""
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

    def fee_rate(self) -> float:
        """Commissione per lato come frazione (0,1 % -> 0.001)."""
        return self.fee_spin.value() / 100.0

    def set_params(self, params: SignalParams) -> None:
        """Imposta tutti i campi emettendo ``changed`` una sola volta."""
        self.blockSignals(True)
        try:
            self.atr_spin.setValue(params.atr_period)
            self.pivot_spin.setValue(params.pivot_window)
            self.tol_spin.setValue(params.levels.tolerance_atr)
            self.touches_spin.setValue(params.levels.min_touches)
            self.lookback_spin.setValue(params.levels.lookback)
            self.prox_spin.setValue(params.proximity_atr)
            self.buffer_spin.setValue(params.sl_buffer_atr)
            self.rr_spin.setValue(params.min_rr)
            self.target_combo.setCurrentIndex(self.target_combo.findData(params.target_mode))
        finally:
            self.blockSignals(False)
        self.changed.emit()

    def summary(self) -> str:
        try:
            return params_summary(self.params(), self.fee_spin.value())
        except ValueError as exc:
            return f"Parametri non validi: {exc}"
