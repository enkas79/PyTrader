"""Preferenze dell'applicazione salvate in ``~/.pytrader/settings.json``."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from pytrader.storage import app_data_dir, read_json, write_json_atomic


class ThemeMode(str, Enum):
    DARK = "dark"
    LIGHT = "light"
    SYSTEM = "system"  # segue il tema del sistema operativo


@dataclass
class AppSettings:
    theme: ThemeMode = ThemeMode.DARK
    path: Optional[Path] = None

    @classmethod
    def load(cls, path: Optional[Path] = None) -> AppSettings:
        target = path or app_data_dir() / "settings.json"
        raw = read_json(target, {})
        theme = ThemeMode.DARK
        if isinstance(raw, dict):
            with contextlib.suppress(ValueError):  # valore sconosciuto: default
                theme = ThemeMode(raw.get("theme", ThemeMode.DARK.value))
        return cls(theme=theme, path=target)

    def save(self) -> None:
        write_json_atomic(
            self.path or app_data_dir() / "settings.json", {"theme": self.theme.value}
        )
