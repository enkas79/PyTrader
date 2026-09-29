"""Avvio dell'applicazione Qt."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

from PyQt6.QtCore import QLocale, Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication

from pytrader.gui.theme import (
    Palette,
    ThemeMode,
    current_palette,
    load_template,
    render_stylesheet,
    resolve_palette,
    set_current_palette,
)
from pytrader.settings import AppSettings
from pytrader.storage import app_data_dir
from pytrader.version import APP_AUTHOR, APP_NAME, get_version


def _setup_logging() -> None:
    log_dir = app_data_dir()
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


def load_stylesheet(palette: Optional[Palette] = None) -> str:
    """Foglio di stile per ``palette`` (default: quella attiva)."""
    try:
        template = load_template()
    except OSError:
        logging.getLogger(__name__).warning("styles.qss non trovato: stile di default")
        return ""
    return render_stylesheet(template, palette or current_palette())


def system_prefers_dark() -> bool:
    """Tema del sistema operativo (Qt >= 6.5); se ignoto si assume scuro."""
    hints = QGuiApplication.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    if scheme is None:
        return True
    return scheme() != Qt.ColorScheme.Light


def apply_theme(mode: ThemeMode) -> Palette:
    """Attiva la palette del tema e aggiorna il foglio di stile dell'applicazione."""
    palette = resolve_palette(mode, system_prefers_dark())
    set_current_palette(palette)
    app = QApplication.instance()
    if isinstance(app, QApplication):
        app.setStyleSheet(load_stylesheet(palette))
    return palette


def run() -> int:
    _setup_logging()
    from pytrader.gui.main_window import MainWindow  # import dopo il logging

    # Formato numerico italiano per spinbox e campi numerici (12.345,67)
    QLocale.setDefault(QLocale(QLocale.Language.Italian, QLocale.Country.Italy))
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(get_version())
    app.setOrganizationName(APP_AUTHOR)
    app.setStyle("Fusion")
    # La chiusura della finestra non termina l'app se il monitoraggio live è nella tray
    app.setQuitOnLastWindowClosed(False)
    apply_theme(AppSettings.load().theme)
    window = MainWindow()
    window.show()
    return app.exec()
