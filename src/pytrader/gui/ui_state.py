"""Stato dell'interfaccia tra un avvio e l'altro (geometria, pannelli, divisori, colonne).

File INI in ``~/.pytrader/ui.ini``: segue ``PYTRADER_HOME`` come gli altri dati, quindi i test
non toccano le preferenze reali.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QByteArray, QSettings

from pytrader.storage import app_data_dir

STATE_VERSION = 1  # da incrementare se cambiano dock/toolbar: lo stato vecchio viene ignorato


class UiState:
    def __init__(self) -> None:
        path = app_data_dir() / "ui.ini"
        self._settings = QSettings(str(path), QSettings.Format.IniFormat)

    def bytes(self, key: str) -> Optional[QByteArray]:
        value = self._settings.value(key)
        return value if isinstance(value, QByteArray) and not value.isEmpty() else None

    def set_bytes(self, key: str, value: QByteArray) -> None:
        self._settings.setValue(key, value)

    def names(self, key: str, default: list[str]) -> list[str]:
        """Elenco di stringhe; ``default`` se la chiave non è mai stata salvata."""
        if not self._settings.contains(key):
            return list(default)
        value = self._settings.value(key)
        if value is None or value == "":
            return []  # elenco salvato vuoto
        return [str(v) for v in value] if isinstance(value, list) else [str(value)]

    def set_names(self, key: str, names: list[str]) -> None:
        self._settings.setValue(key, names if names else "")

    def sync(self) -> None:
        self._settings.sync()
