"""Dialogo dei valori consigliati per famiglia di asset: scelta, anteprima motivata, conferma."""

from __future__ import annotations

from collections.abc import Callable
from typing import Optional

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pytrader.presets import PROFILES, AssetFamily, Suggestion

PREVIEW_COLUMNS = ("Parametro", "Valore", "Perché")


class FamilyDefaultsDialog(QDialog):
    """Mostra cosa verrebbe impostato per la famiglia scelta prima di applicarlo."""

    def __init__(
        self,
        compute: Callable[[AssetFamily], tuple[Suggestion, ...]],
        detected: AssetFamily,
        context: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Valori per famiglia di asset")
        self.resize(820, 460)
        self._compute = compute

        self.family_combo = QComboBox()
        for family, profile in PROFILES.items():
            self.family_combo.addItem(profile.label, family)
        self.family_combo.setCurrentIndex(self.family_combo.findData(detected))
        self.family_combo.currentIndexChanged.connect(self._refresh)
        self.examples_label = QLabel()
        self.examples_label.setObjectName("hint")
        form = QFormLayout()
        form.setSpacing(8)
        form.addRow("Famiglia", self.family_combo)
        form.addRow("", self.examples_label)

        context_label = QLabel(
            f"{context}<br>Proposta riconosciuta dal simbolo: <b>{PROFILES[detected].label}</b>"
            " (gli ETF non si distinguono dalle azioni: sceglili a mano). I valori derivano da "
            "costi, calendario e limiti di leva, non dai rendimenti passati."
        )
        context_label.setWordWrap(True)

        self.table = QTableWidget(0, len(PREVIEW_COLUMNS))
        self.table.setHorizontalHeaderLabels(PREVIEW_COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel
        )
        apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        apply_button.setText("Applica")
        apply_button.clicked.connect(self.accept)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_button.setText("Annulla")
        cancel_button.setObjectName("secondaryButton")
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(context_label)
        layout.addLayout(form)
        layout.addWidget(self.table, 1)
        layout.addWidget(buttons)
        self._refresh()

    def family(self) -> AssetFamily:
        return self.family_combo.currentData()

    def _refresh(self) -> None:
        family = self.family()
        self.examples_label.setText(f"Esempi: {PROFILES[family].examples}")
        notes = self._compute(family)
        self.table.setRowCount(len(notes))
        for row, note in enumerate(notes):
            for col, text in enumerate((note.name, note.value, note.reason)):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeRowsToContents()
