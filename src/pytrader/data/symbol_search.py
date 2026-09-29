"""Ricerca di ticker per nome (azienda, fondo, ETF, crypto) con suggerimenti selezionabili."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Optional

from pytrader.data.base import DataSourceError

# Traduzione dei tipi Yahoo in etichette leggibili
_YAHOO_KINDS: dict[str, str] = {
    "EQUITY": "Azione",
    "ETF": "ETF",
    "MUTUALFUND": "Fondo",
    "INDEX": "Indice",
    "CRYPTOCURRENCY": "Crypto",
    "CURRENCY": "Valuta",
    "FUTURE": "Future",
    "OPTION": "Opzione",
}


@dataclass(frozen=True)
class SymbolMatch:
    """Risultato di ricerca: ``symbol`` è il ticker da usare per scaricare i dati."""

    symbol: str
    name: str = ""
    exchange: str = ""
    kind: str = ""

    @property
    def label(self) -> str:
        """Testo mostrato nella lista, es. ``AAPL — Apple Inc. (NASDAQ · Azione)``."""
        details = " · ".join(x for x in (self.exchange, self.kind) if x)
        text = self.symbol
        if self.name:
            text += f" — {self.name}"
        if details:
            text += f" ({details})"
        return text


class SymbolSearcher(ABC):
    """Ricerca bloccante: va eseguita in un worker, mai nel thread GUI."""

    min_query_length: int = 2

    @abstractmethod
    def search(self, query: str, limit: int = 15) -> list[SymbolMatch]: ...


def parse_yahoo_quotes(quotes: Iterable[Mapping[str, Any]]) -> list[SymbolMatch]:
    """Converte la lista ``quotes`` dell'API di ricerca Yahoo, eliminando i duplicati."""
    seen: set[str] = set()
    out: list[SymbolMatch] = []
    for q in quotes:
        symbol = str(q.get("symbol") or "").strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        quote_type = str(q.get("quoteType") or "").upper()
        out.append(
            SymbolMatch(
                symbol=symbol,
                name=str(q.get("longname") or q.get("shortname") or "").strip(),
                exchange=str(q.get("exchDisp") or q.get("exchange") or "").strip(),
                kind=_YAHOO_KINDS.get(quote_type, str(q.get("typeDisp") or quote_type).strip()),
            )
        )
    return out


class YFinanceSymbolSearcher(SymbolSearcher):
    """Ricerca per nome o ticker tramite Yahoo Finance (tollera errori di battitura)."""

    def __init__(self, timeout: float = 10.0) -> None:
        try:
            import yfinance
        except ImportError as exc:
            raise DataSourceError("yfinance non installato (pip install yfinance)") from exc
        self._yf: Any = yfinance
        self.timeout = timeout

    def search(self, query: str, limit: int = 15) -> list[SymbolMatch]:
        query = query.strip()
        if len(query) < self.min_query_length:
            return []
        try:
            result = self._yf.Search(
                query,
                max_results=limit,
                news_count=0,
                lists_count=0,
                enable_fuzzy_query=True,
                timeout=self.timeout,
            )
            quotes = result.quotes
        except Exception as exc:  # yfinance solleva eccezioni eterogenee
            raise DataSourceError(f"Ricerca non riuscita: {exc}") from exc
        return parse_yahoo_quotes(quotes)[:limit]


def rank_markets(
    markets: Iterable[Mapping[str, Any]], query: str, limit: int = 15
) -> list[SymbolMatch]:
    """Filtra e ordina i mercati ccxt: prima corrispondenza esatta della base, poi prefisso,
    poi sottostringa; a parità, coppie spot prima dei derivati."""
    q = query.strip().upper().replace("-", "/")
    if not q:
        return []
    scored: list[tuple[int, int, str, SymbolMatch]] = []
    for m in markets:
        symbol = str(m.get("symbol") or "")
        if not symbol or m.get("active") is False:
            continue
        base = str(m.get("base") or "").upper()
        haystack = symbol.upper()
        if base == q or haystack == q:
            score = 0
        elif base.startswith(q) or haystack.startswith(q):
            score = 1
        elif q in haystack:
            score = 2
        else:
            continue
        kind = str(m.get("type") or "spot")
        scored.append(
            (score, 0 if kind == "spot" else 1, symbol, SymbolMatch(symbol, base, "", kind))
        )
    scored.sort(key=lambda t: t[:3])
    return [t[3] for t in scored[:limit]]


class CcxtSymbolSearcher(SymbolSearcher):
    """Ricerca tra i mercati di un exchange ccxt (per base, es. ``BTC``, o coppia)."""

    min_query_length = 1

    def __init__(self, exchange_id: str = "binance") -> None:
        try:
            import ccxt
        except ImportError as exc:
            raise DataSourceError("ccxt non installato (pip install ccxt)") from exc
        if not hasattr(ccxt, exchange_id):
            raise DataSourceError(f"Exchange sconosciuto: {exchange_id}")
        self._ccxt: Any = ccxt
        self.exchange: Any = getattr(ccxt, exchange_id)({"enableRateLimit": True})
        self._markets: Optional[list[Mapping[str, Any]]] = None

    def search(self, query: str, limit: int = 15) -> list[SymbolMatch]:
        if len(query.strip()) < self.min_query_length:
            return []
        if self._markets is None:
            try:
                self._markets = list(self.exchange.load_markets().values())
            except self._ccxt.BaseError as exc:
                raise DataSourceError(f"Caricamento mercati non riuscito: {exc}") from exc
        return rank_markets(self._markets, query, limit)
