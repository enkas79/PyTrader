"""Interfaccia comune delle sorgenti dati OHLCV."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

OHLCV_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")


class DataSourceError(RuntimeError):
    """Errore di acquisizione dati (rete, file, formato)."""


class DataSource(ABC):
    """Sorgente OHLCV. Restituisce un DataFrame con indice UTC e colonne ``OHLCV_COLUMNS``.

    La validazione (duplicati, incoerenze, buchi) è demandata a ``validate_ohlcv``.
    """

    name: str = "base"

    @abstractmethod
    def fetch(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[pd.Timestamp] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """Scarica/carica le candele. Operazione bloccante: eseguirla fuori dal thread GUI."""
