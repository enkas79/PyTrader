"""Esecuzione di funzioni bloccanti nel ``QThreadPool`` con notifica tramite segnali."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal, pyqtSlot

logger = logging.getLogger(__name__)


class WorkerSignals(QObject):
    finished = pyqtSignal(object)  # risultato della funzione
    failed = pyqtSignal(str)  # messaggio d'errore
    progress = pyqtSignal(int)  # 0-100


class Worker(QRunnable):
    """Esegue ``fn(*args, **kwargs)`` fuori dal thread GUI.

    Se ``fn`` accetta ``progress``, riceve una callback che emette ``signals.progress``.
    """

    def __init__(
        self, fn: Callable[..., Any], *args: Any, with_progress: bool = False, **kwargs: Any
    ) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        if with_progress:
            self.kwargs["progress"] = self.signals.progress.emit
        # Il riferimento è mantenuto dal chiamante fino al termine (vedi MainWindow._start)
        self.setAutoDelete(False)

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # il worker non deve mai far crashare l'app
            logger.warning("Worker %s fallito: %s", getattr(self.fn, "__name__", "?"), exc)
            logger.debug("Traceback del worker", exc_info=True)
            self._emit(self.signals.failed, str(exc) or exc.__class__.__name__)
        else:
            self._emit(self.signals.finished, result)

    @staticmethod
    def _emit(signal: Any, value: Any) -> None:
        try:
            signal.emit(value)
        except RuntimeError:
            # Oggetto Qt già distrutto (finestra chiusa durante l'esecuzione): risultato scartato
            logger.debug("Risultato del worker scartato: destinatario distrutto")
