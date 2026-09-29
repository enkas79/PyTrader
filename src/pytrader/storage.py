"""Cartella dati dell'applicazione (log, watchlist, storico segnali) e scrittura JSON atomica."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pytrader.version import APP_NAME


def app_data_dir() -> Path:
    """``~/.pytrader`` (sovrascrivibile con la variabile ``PYTRADER_HOME``, usata nei test)."""
    override = os.environ.get("PYTRADER_HOME")
    return Path(override) if override else Path.home() / f".{APP_NAME.lower()}"


def write_json_atomic(path: Path, payload: Any) -> None:
    """Scrive su file temporaneo e rinomina: un crash non lascia file troncati."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
