"""Dialoghi: guida all'uso con indice, ricerca ed esportazione PDF."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from PyQt6.QtCore import QMarginsF, Qt, QThreadPool, QUrl
from PyQt6.QtGui import QDesktopServices, QPageLayout, QPageSize, QPdfWriter, QTextDocument
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from pytrader.gui.help_content import SECTIONS, full_html
from pytrader.gui.workers import Worker
from pytrader.version import APP_NAME, get_version

# Stile della stampa: testo scuro su carta bianca, indipendente dal tema dell'app
_PDF_CSS = """
body { font-size: 10pt; color: #1a1a1a; }
h1 { font-size: 22pt; } h2 { font-size: 15pt; margin-top: 18px; } h3 { font-size: 12pt; }
th { background-color: #e8e8e8; }
a { color: #1a1a1a; text-decoration: none; }
"""


def export_help_pdf(path: Union[str, Path], version: Optional[str] = None) -> Path:
    """Scrive la guida completa in un PDF A4. Eseguibile fuori dal thread GUI."""
    target = Path(path)
    doc = QTextDocument()
    doc.setDefaultStyleSheet(_PDF_CSS)
    doc.setHtml(full_html(version or get_version()))
    writer = QPdfWriter(str(target))
    writer.setTitle(f"Guida a {APP_NAME}")
    writer.setCreator(APP_NAME)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(18, 18, 18, 18), QPageLayout.Unit.Millimeter)
    doc.print(writer)
    del writer  # chiude il file
    if not target.exists() or target.stat().st_size == 0:
        raise OSError(f"Impossibile scrivere {target}")
    return target


class HelpDialog(QDialog):
    """Guida completa: indice a sinistra, testo a destra, ricerca ed esportazione PDF."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Guida a {APP_NAME}")
        self.resize(1040, 760)
        self._worker: Optional[Worker] = None

        self.toc = QListWidget()
        for number, section in enumerate(SECTIONS, 1):
            item = QListWidgetItem(f"{number}. {section.title}")
            item.setData(Qt.ItemDataRole.UserRole, section.anchor)
            self.toc.addItem(item)
        self.toc.currentItemChanged.connect(self._go_to_section)
        self.toc.setMinimumWidth(260)
        self.toc.setWordWrap(True)
        self.toc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)  # i link interni scorrono, quelli esterni al browser
        self.browser.anchorClicked.connect(self._on_link)
        self.browser.setHtml(full_html(get_version()))

        split = QSplitter()
        split.addWidget(self.toc)
        split.addWidget(self.browser)
        split.setStretchFactor(1, 1)
        split.setSizes([280, 760])

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Cerca nella guida (Invio = successivo)")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.returnPressed.connect(self.find_next)
        prev_button = QPushButton("Precedente")
        prev_button.setObjectName("secondaryButton")
        prev_button.clicked.connect(lambda: self.find_next(backward=True))
        next_button = QPushButton("Successivo")
        next_button.setObjectName("secondaryButton")
        next_button.clicked.connect(lambda: self.find_next())
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(prev_button)
        search_row.addWidget(next_button)

        self.pdf_button = QPushButton("Esporta PDF…")
        self.pdf_button.setToolTip("Salva la guida completa in PDF, da leggere o stampare")
        self.pdf_button.clicked.connect(self._export_pdf)
        close_button = QPushButton("Chiudi")
        close_button.setObjectName("secondaryButton")
        close_button.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addStretch(1)
        bottom.addWidget(self.pdf_button)
        bottom.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        layout.addLayout(search_row)
        layout.addWidget(split, 1)
        layout.addLayout(bottom)

    # ------------------------------------------------------------ navigazione
    def show_section(self, anchor: str) -> None:
        for row in range(self.toc.count()):
            if self.toc.item(row).data(Qt.ItemDataRole.UserRole) == anchor:
                self.toc.setCurrentRow(row)
                return

    def _go_to_section(self, item: Optional[QListWidgetItem], _prev: object = None) -> None:
        if item is not None:
            self.browser.scrollToAnchor(item.data(Qt.ItemDataRole.UserRole))

    def _on_link(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
        elif url.fragment():
            self.show_section(url.fragment())
            self.browser.scrollToAnchor(url.fragment())

    def find_next(self, backward: bool = False) -> bool:
        """Evidenzia l'occorrenza successiva (o precedente); ricomincia da capo se serve."""
        text = self.search_edit.text().strip()
        if not text:
            return False
        flag = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        if self.browser.find(text, flag):
            return True
        cursor = self.browser.textCursor()  # fine documento: riparte dall'altra estremità
        cursor.movePosition(cursor.MoveOperation.End if backward else cursor.MoveOperation.Start)
        self.browser.setTextCursor(cursor)
        found = self.browser.find(text, flag)
        if not found:
            self.search_edit.setToolTip(f"«{text}» non trovato")
        return found

    # ------------------------------------------------------------ PDF
    def _export_pdf(self) -> None:
        default = str(Path.home() / f"Guida {APP_NAME} {get_version()}.pdf")
        path, _ = QFileDialog.getSaveFileName(self, "Esporta guida in PDF", default, "PDF (*.pdf)")
        if path:
            self.export_pdf(path)

    def export_pdf(self, path: str) -> None:
        """Genera il PDF nel thread pool e lo apre al termine."""
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        self.pdf_button.setEnabled(False)
        worker = Worker(export_help_pdf, path)
        worker.signals.finished.connect(self._on_pdf_done)
        worker.signals.failed.connect(self._on_pdf_failed)
        self._worker = worker
        QThreadPool.globalInstance().start(worker)

    def _on_pdf_done(self, path: Path) -> None:
        self._worker = None
        self.pdf_button.setEnabled(True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_pdf_failed(self, message: str) -> None:
        self._worker = None
        self.pdf_button.setEnabled(True)
        QMessageBox.warning(self, "Esporta PDF", f"PDF non creato: {message}")
