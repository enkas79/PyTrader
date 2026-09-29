"""Suggerimenti di ticker mentre si digita: ricerca in background con debounce."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

from PyQt6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    Qt,
    QThreadPool,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtWidgets import QCompleter, QLineEdit

from pytrader.data.symbol_search import SymbolMatch
from pytrader.gui.workers import Worker

SearchJob = Callable[[], list[SymbolMatch]]
# Chiamata nel thread GUI: restituisce il job da eseguire nel worker, o None se non applicabile
JobFactory = Callable[[str], Optional[SearchJob]]

SYMBOL_ROLE = Qt.ItemDataRole.UserRole + 1


class SymbolListModel(QAbstractListModel):
    """Mostra l'etichetta completa; il completer inserisce solo il ticker (``SYMBOL_ROLE``)."""

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._items: list[SymbolMatch] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        item = self._items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return item.label
        if role == Qt.ItemDataRole.ToolTipRole:
            return item.name or item.symbol
        if role == SYMBOL_ROLE:
            return item.symbol
        return None

    def set_items(self, items: list[SymbolMatch]) -> None:
        self.beginResetModel()
        self._items = list(items)
        self.endResetModel()

    @property
    def items(self) -> list[SymbolMatch]:
        return list(self._items)


class SymbolSearchController(QObject):
    """Collega un ``QLineEdit`` alla ricerca ticker.

    - Ricerca avviata 350 ms dopo l'ultimo tasto (solo su input dell'utente).
    - Le risposte arrivate dopo una nuova digitazione vengono scartate.
    - La selezione sostituisce il testo con il solo ticker.
    """

    status = pyqtSignal(str)
    selected = pyqtSignal(object)  # SymbolMatch scelto

    def __init__(self, line_edit: QLineEdit, job_factory: JobFactory, delay_ms: int = 350) -> None:
        super().__init__(line_edit)
        self._edit = line_edit
        self._job_factory = job_factory
        self._seq = 0
        self._workers: set[Worker] = set()
        self.model = SymbolListModel(self)

        self.completer = QCompleter(self.model, self)
        self.completer.setCompletionRole(SYMBOL_ROLE)
        self.completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setMaxVisibleItems(12)
        self.completer.setWidget(line_edit)
        self.completer.popup().setObjectName("symbolPopup")
        self.completer.activated[QModelIndex].connect(self._on_activated)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self._run_search)
        line_edit.textEdited.connect(self._on_text_edited)

    def clear(self) -> None:
        self._seq += 1
        self._timer.stop()
        self.model.set_items([])
        self.completer.popup().hide()

    def _on_text_edited(self, _text: str) -> None:
        self._seq += 1  # invalida le ricerche in corso
        self._timer.start()

    def _run_search(self) -> None:
        query = self._edit.text().strip()
        job = self._job_factory(query)
        if job is None:
            self.clear()
            return
        seq = self._seq
        worker = Worker(job)
        self._workers.add(worker)

        def done(result: list[SymbolMatch]) -> None:
            self._workers.discard(worker)
            if seq == self._seq:
                self._show(result, query)

        def fail(message: str) -> None:
            self._workers.discard(worker)
            if seq == self._seq:
                self.status.emit(message)

        worker.signals.finished.connect(done)
        worker.signals.failed.connect(fail)
        self.status.emit(f"Ricerca «{query}»…")
        QThreadPool.globalInstance().start(worker)

    def _show(self, matches: list[SymbolMatch], query: str) -> None:
        self.model.set_items(matches)
        if not matches:
            self.completer.popup().hide()
            self.status.emit(f"Nessun risultato per «{query}»")
            return
        self.status.emit(f"{len(matches)} risultati per «{query}»: seleziona dalla lista")
        popup = self.completer.popup()
        # Larghezza sul testo più lungo (+ margini e scrollbar), entro 360-760 px
        content = popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width() + 24
        width = min(max(self._edit.width(), content, 360), 760)
        rect = self._edit.rect()
        rect.setWidth(width)
        self.completer.complete(rect)

    def _on_activated(self, index: QModelIndex) -> None:
        symbol = index.data(SYMBOL_ROLE)
        if not symbol:
            return
        self._seq += 1
        self._timer.stop()
        self._edit.setText(str(symbol))
        match = next((m for m in self.model.items if m.symbol == symbol), None)
        if match is not None:
            self.selected.emit(match)
