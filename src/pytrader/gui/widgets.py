"""Widget riutilizzabili: spinbox senza frecce, riquadri metrica, scelta delle colonne."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Optional

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QDoubleSpinBox,
    QFrame,
    QLabel,
    QMenu,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)


def spin(lo: int, hi: int, value: int) -> QSpinBox:
    box = QSpinBox()
    box.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)  # rotella/frecce tastiera
    box.setRange(lo, hi)
    box.setValue(value)
    return box


def dspin(lo: float, hi: float, value: float, step: float, decimals: int = 2) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    box.setRange(lo, hi)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setValue(value)
    return box


def set_tone(label: QLabel, tone: str) -> None:
    """Colore semantico via proprietà QSS (``up``, ``down`` o ``""``): nessun colore fisso nel
    codice, il tema decide la tinta."""
    if label.property("tone") == tone:
        return
    label.setProperty("tone", tone)
    label.style().unpolish(label)
    label.style().polish(label)


class MetricCard(QFrame):
    """Riquadro compatto: valore in evidenza sopra, didascalia sotto."""

    def __init__(self, caption: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("metricCard")
        self.value = QLabel("—")
        self.value.setObjectName("metricValue")
        self.caption = QLabel(caption)
        self.caption.setObjectName("metricCaption")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(0)
        layout.addWidget(self.value)
        layout.addWidget(self.caption)


class ClickableLabel(QLabel):
    """Etichetta che emette ``clicked`` (riepilogo dei parametri nella barra laterale)."""

    clicked = pyqtSignal()

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (API Qt)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ColumnChooser:
    """Clic destro sull'intestazione di una tabella: mostra/nasconde le colonne.

    ``on_change`` riceve i nomi delle colonne nascoste, per salvarli.
    """

    def __init__(self, table: QTableWidget, columns: Iterable[str]) -> None:
        self.table = table
        self.columns = list(columns)
        self.on_change: Optional[Callable[[list[str]], None]] = None
        header = table.horizontalHeader()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._menu)
        header.setToolTip("Clic destro per scegliere le colonne visibili")

    def hidden(self) -> list[str]:
        return [c for i, c in enumerate(self.columns) if self.table.isColumnHidden(i)]

    def set_hidden(self, names: Iterable[str]) -> None:
        wanted = set(names)
        for i, name in enumerate(self.columns):
            self.table.setColumnHidden(i, name in wanted)
        if not any(not self.table.isColumnHidden(i) for i in range(len(self.columns))):
            self.table.setColumnHidden(0, False)  # almeno una colonna resta visibile

    def _menu(self, pos: QPoint) -> None:
        menu = QMenu(self.table)
        visible = len(self.columns) - len(self.hidden())
        for i, name in enumerate(self.columns):
            act = QAction(name, menu, checkable=True)
            shown = not self.table.isColumnHidden(i)
            act.setChecked(shown)
            act.setEnabled(not (shown and visible == 1))  # non si nasconde l'ultima
            act.toggled.connect(lambda on, col=i: self._toggle(col, on))
            menu.addAction(act)
        menu.exec(self.table.horizontalHeader().mapToGlobal(pos))

    def _toggle(self, col: int, shown: bool) -> None:
        self.table.setColumnHidden(col, not shown)
        if self.on_change is not None:
            self.on_change(self.hidden())
