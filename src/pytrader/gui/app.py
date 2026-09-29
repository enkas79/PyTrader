"""Avvio dell'applicazione Qt."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from pytrader.version import APP_AUTHOR, APP_NAME, get_version, resource_path


def _setup_logging() -> None:
    log_dir = Path.home() / f".{APP_NAME.lower()}"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_dir / "app.log", maxBytes=1_000_000, backupCount=3))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def load_stylesheet() -> str:
    try:
        return resource_path("styles.qss").read_text(encoding="utf-8")
    except OSError:
        logging.getLogger(__name__).warning("styles.qss non trovato: stile di default")
        return ""


def run() -> int:
    _setup_logging()
    from pytrader.gui.main_window import MainWindow  # import dopo il logging

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(get_version())
    app.setOrganizationName(APP_AUTHOR)
    app.setStyle("Fusion")
    app.setStyleSheet(load_stylesheet())
    window = MainWindow()
    window.show()
    return app.exec()
