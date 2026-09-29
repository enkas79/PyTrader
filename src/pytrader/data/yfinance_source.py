"""Sorgente OHLCV da Yahoo Finance tramite ``yfinance`` (azioni, indici, forex)."""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from pytrader.data.base import OHLCV_COLUMNS, DataSource, DataSourceError

# Timeframe interno -> intervallo yfinance
_INTERVALS: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "60m",
    "1d": "1d",
    "1w": "1wk",
}
# Periodo massimo consentito da Yahoo per gli intraday
_DEFAULT_PERIOD: dict[str, str] = {
    "1m": "7d",
    "5m": "60d",
    "15m": "60d",
    "30m": "60d",
    "1h": "730d",
}


class YFinanceDataSource(DataSource):
    """Nota: per forex e alcuni indici Yahoo restituisce volume nullo."""

    name = "yfinance"

    def __init__(self) -> None:
        try:
            import yfinance
        except ImportError as exc:
            raise DataSourceError("yfinance non installato (pip install yfinance)") from exc
        self._yf: Any = yfinance

    def fetch(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[pd.Timestamp] = None,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        interval = _INTERVALS.get(timeframe)
        if interval is None:
            raise DataSourceError(f"Timeframe non supportato da yfinance: {timeframe}")
        kwargs: dict[str, Any] = {"interval": interval, "auto_adjust": False}
        if since is not None:
            kwargs["start"] = pd.Timestamp(since).to_pydatetime()
        else:
            kwargs["period"] = _DEFAULT_PERIOD.get(timeframe, "max")
        try:
            raw = self._yf.Ticker(symbol).history(**kwargs)
        except Exception as exc:  # yfinance solleva eccezioni eterogenee
            raise DataSourceError(f"Errore yfinance: {exc}") from exc
        if raw is None or raw.empty:
            raise DataSourceError(f"Nessun dato per {symbol} {timeframe}")

        frame = raw.rename(columns=str.lower)[list(OHLCV_COLUMNS)].astype(float)
        index = pd.DatetimeIndex(frame.index)
        frame.index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
        frame.index.name = "timestamp"
        if limit is not None:
            frame = frame.tail(limit)
        return frame
