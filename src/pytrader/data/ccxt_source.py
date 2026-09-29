"""Sorgente OHLCV da exchange crypto tramite ``ccxt`` (API sincrona, da usare in un worker)."""

from __future__ import annotations

import time
from typing import Any, Optional

import pandas as pd

from pytrader.data.base import OHLCV_COLUMNS, DataSource, DataSourceError

_MAX_RETRIES = 4


class CcxtDataSource(DataSource):
    """Scarica candele con paginazione e backoff esponenziale sugli errori di rete."""

    name = "ccxt"

    def __init__(self, exchange_id: str = "binance", page_size: int = 1000) -> None:
        try:
            import ccxt  # import differito: dipendenza opzionale
        except ImportError as exc:
            raise DataSourceError("ccxt non installato (pip install ccxt)") from exc
        if not hasattr(ccxt, exchange_id):
            raise DataSourceError(f"Exchange sconosciuto: {exchange_id}")
        self._ccxt: Any = ccxt
        self.exchange: Any = getattr(ccxt, exchange_id)({"enableRateLimit": True})
        self.page_size = page_size

    def _fetch_page(self, symbol: str, timeframe: str, since: Optional[int], limit: int) -> list:
        delay = 1.0
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return self.exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
            except self._ccxt.NetworkError as exc:
                if attempt == _MAX_RETRIES:
                    raise DataSourceError(f"Errore di rete persistente: {exc}") from exc
                time.sleep(delay)
                delay *= 2
            except self._ccxt.BaseError as exc:
                raise DataSourceError(f"Errore exchange: {exc}") from exc
        return []

    def fetch(
        self,
        symbol: str,
        timeframe: str,
        since: Optional[pd.Timestamp] = None,
        limit: Optional[int] = 1000,
    ) -> pd.DataFrame:
        target = limit or self.page_size
        tf_ms = int(self.exchange.parse_timeframe(timeframe) * 1000)
        if since is None:
            since_ms: Optional[int] = self.exchange.milliseconds() - target * tf_ms
        else:
            since_ms = int(pd.Timestamp(since).timestamp() * 1000)

        rows: list = []
        while len(rows) < target:
            page = self._fetch_page(symbol, timeframe, since_ms, min(self.page_size, target))
            if not page:
                break
            rows.extend(page)
            next_since = int(page[-1][0]) + tf_ms
            if since_ms is not None and next_since <= since_ms:
                break
            since_ms = next_since
            if len(page) < min(self.page_size, target):
                break

        if not rows:
            raise DataSourceError(f"Nessuna candela per {symbol} {timeframe}")
        frame = pd.DataFrame(rows, columns=["timestamp", *OHLCV_COLUMNS])
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("timestamp"), unit="ms", utc=True))
        frame.index.name = "timestamp"
        return frame.tail(target)
