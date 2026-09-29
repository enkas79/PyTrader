"""Watchlist persistente dei mercati da monitorare."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from pytrader.data import parse_timeframe
from pytrader.services import SourceKind
from pytrader.storage import app_data_dir, read_json, write_json_atomic


@dataclass(frozen=True)
class WatchItem:
    kind: SourceKind
    symbol: str
    timeframe: str
    exchange: str = ""  # solo ccxt

    def __post_init__(self) -> None:
        if self.kind is SourceKind.CSV:
            raise ValueError("Il monitoraggio live richiede una sorgente remota")
        if not self.symbol.strip():
            raise ValueError("Simbolo mancante")
        parse_timeframe(self.timeframe)  # solleva ValueError se non valido

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.exchange}:{self.symbol}:{self.timeframe}"

    @property
    def label(self) -> str:
        venue = self.exchange if self.kind is SourceKind.CCXT else "Yahoo"
        return f"{self.symbol} {self.timeframe} ({venue})"

    @classmethod
    def from_key(cls, key: str) -> WatchItem:
        """Inverso di ``key``; il simbolo può contenere ``:`` (es. ``BTC/USDT:USDT``)."""
        kind, exchange, rest = key.split(":", 2)
        symbol, timeframe = rest.rsplit(":", 1)
        return cls(kind=SourceKind(kind), symbol=symbol, timeframe=timeframe, exchange=exchange)

    def to_dict(self) -> dict[str, str]:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WatchItem:
        return cls(
            kind=SourceKind(data["kind"]),
            symbol=str(data["symbol"]),
            timeframe=str(data["timeframe"]),
            exchange=str(data.get("exchange", "")),
        )


@dataclass
class Watchlist:
    items: list[WatchItem] = field(default_factory=list)
    active: bool = False  # monitoraggio attivo alla chiusura: riparte all'avvio
    path: Optional[Path] = None

    @classmethod
    def load(cls, path: Optional[Path] = None) -> Watchlist:
        target = path or app_data_dir() / "watchlist.json"
        raw = read_json(target, {})
        items: list[WatchItem] = []
        for entry in raw.get("items", []) if isinstance(raw, dict) else []:
            try:
                items.append(WatchItem.from_dict(entry))
            except (KeyError, ValueError, TypeError):
                continue  # voce corrotta: ignorata
        active = bool(raw.get("active", False)) if isinstance(raw, dict) else False
        return cls(items=items, active=active, path=target)

    def save(self) -> None:
        target = self.path or app_data_dir() / "watchlist.json"
        write_json_atomic(
            target, {"active": self.active, "items": [i.to_dict() for i in self.items]}
        )

    def add(self, item: WatchItem) -> bool:
        if any(i.key == item.key for i in self.items):
            return False
        self.items.append(item)
        return True

    def remove(self, key: str) -> None:
        self.items = [i for i in self.items if i.key != key]
