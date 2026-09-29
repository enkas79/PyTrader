"""Lettura della versione corrente da ``version.txt`` e percorsi delle risorse."""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

APP_NAME = "PyTrader"
APP_AUTHOR = "enkas79"
GITHUB_REPO = "enkas79/PyTrader"
_FALLBACK_VERSION = "0.0.0"


def resource_root() -> Path:
    """Root delle risorse: cartella di estrazione PyInstaller o root del progetto."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root is not None:
        return Path(frozen_root)
    # src/pytrader/version.py -> root del repository
    return Path(__file__).resolve().parents[2]


def resource_path(name: str) -> Path:
    """Percorso assoluto di una risorsa (``version.txt``, ``styles.qss``, ...)."""
    return resource_root() / name


@lru_cache(maxsize=1)
def get_version() -> str:
    """Versione letta dinamicamente da ``version.txt`` (fallback ``0.0.0``)."""
    try:
        text = resource_path("version.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return _FALLBACK_VERSION
    return text or _FALLBACK_VERSION
