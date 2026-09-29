"""Caricamento OHLCV da file CSV locale."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import pandas as pd

from pytrader.data.base import OHLCV_COLUMNS, DataSource, DataSourceError

# Nomi alternativi accettati per le colonne (confronto case-insensitive)
_ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp": ("timestamp", "time", "date", "datetime", "open_time", "ts"),
    "open": ("open", "o"),
    "high": ("high", "h"),
    "low": ("low", "l"),
    "close": ("close", "c", "adj close"),
    "volume": ("volume", "vol", "v"),
}


def _parse_timestamps(raw: pd.Series) -> pd.DatetimeIndex:
    """Interpreta epoch (s/ms) o stringhe ISO, restituendo un indice UTC."""
    if pd.api.types.is_numeric_dtype(raw):
        values = pd.to_numeric(raw)
        # Epoch in millisecondi se oltre ~ anno 2286 in secondi
        unit = "ms" if values.abs().max() > 1e11 else "s"
        return pd.DatetimeIndex(pd.to_datetime(values, unit=unit, utc=True))
    return pd.DatetimeIndex(pd.to_datetime(raw, utc=True))


class CsvDataSource(DataSource):
    """Sorgente CSV. ``symbol`` e ``timeframe`` sono ignorati: il file è già una serie."""

    name = "csv"

    def __init__(self, path: Union[str, Path]) -> None:
        self.path = Path(path)

    def fetch(
        self,
        symbol: str = "",
        timeframe: str = "",
        since: Optional[pd.Timestamp] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        try:
            raw = pd.read_csv(self.path)
        except (OSError, ValueError) as exc:
            raise DataSourceError(f"Impossibile leggere {self.path}: {exc}") from exc

        lookup = {str(c).strip().lower(): c for c in raw.columns}
        mapping: dict[str, str] = {}
        for target, aliases in _ALIASES.items():
            found = next((lookup[a] for a in aliases if a in lookup), None)
            if found is not None:
                mapping[target] = found

        missing = [c for c in ("timestamp", "open", "high", "low", "close") if c not in mapping]
        if missing:
            raise DataSourceError(f"Colonne mancanti nel CSV: {', '.join(missing)}")

        try:
            index = _parse_timestamps(raw[mapping["timestamp"]])
        except (ValueError, TypeError) as exc:
            raise DataSourceError(f"Timestamp non interpretabili: {exc}") from exc

        frame = pd.DataFrame(index=index)
        for col in OHLCV_COLUMNS:
            if col in mapping:
                frame[col] = pd.to_numeric(raw[mapping[col]], errors="coerce").to_numpy()
            else:
                frame[col] = 0.0
        frame.index.name = "timestamp"

        if since is not None:
            frame = frame[frame.index >= pd.Timestamp(since)]
        if limit is not None:
            frame = frame.tail(limit)
        return frame
