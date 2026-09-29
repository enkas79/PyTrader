"""Storico persistente dei segnali live, usato anche per non notificare due volte."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from pytrader.live.watchlist import WatchItem
from pytrader.models import TradeSetup
from pytrader.storage import app_data_dir, read_json, write_json_atomic

MAX_HISTORY = 500


@dataclass(frozen=True)
class LiveSignal:
    item_key: str
    item_label: str
    symbol: str
    timeframe: str
    signal_time: str  # ISO, candela chiusa che genera il segnale
    detected_at: str  # ISO, momento della rilevazione
    direction: str
    pattern: str
    entry: float  # stima: chiusura della candela del segnale
    stop_loss: float
    take_profit: float
    risk_reward: float
    level: float

    @property
    def dedup_key(self) -> tuple[str, str]:
        return (self.item_key, self.signal_time)

    @classmethod
    def from_setup(
        cls, item: WatchItem, setup: TradeSetup, detected_at: Optional[pd.Timestamp] = None
    ) -> LiveSignal:
        now = detected_at or pd.Timestamp.now(tz="UTC")
        return cls(
            item_key=item.key,
            item_label=item.label,
            symbol=item.symbol,
            timeframe=item.timeframe,
            signal_time=setup.signal_time.isoformat(),
            detected_at=now.isoformat(),
            direction=setup.direction.value,
            pattern=setup.pattern.value,
            entry=setup.entry,
            stop_loss=setup.stop_loss,
            take_profit=setup.take_profit,
            risk_reward=setup.risk_reward,
            level=setup.level.price,
        )

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop_loss)


class SignalHistory:
    """Elenco dei segnali (più recenti in fondo), salvato su JSON."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or app_data_dir() / "signals.json"
        self.signals: list[LiveSignal] = []
        self._keys: set[tuple[str, str]] = set()

    @classmethod
    def load(cls, path: Optional[Path] = None) -> SignalHistory:
        history = cls(path)
        raw = read_json(history.path, [])
        for entry in raw if isinstance(raw, list) else []:
            try:
                history._append(LiveSignal(**entry))
            except TypeError:
                continue
        return history

    def _append(self, signal: LiveSignal) -> None:
        self.signals.append(signal)
        self._keys.add(signal.dedup_key)
        if len(self.signals) > MAX_HISTORY:
            dropped = self.signals.pop(0)
            self._keys.discard(dropped.dedup_key)

    def add(self, signal: LiveSignal) -> bool:
        """Registra il segnale; ``False`` se già noto (nessuna nuova notifica)."""
        if signal.dedup_key in self._keys:
            return False
        self._append(signal)
        return True

    def save(self) -> None:
        write_json_atomic(self.path, [asdict(s) for s in self.signals])

    def clear(self) -> None:
        self.signals.clear()
        self._keys.clear()

    def to_records(self) -> list[dict[str, Any]]:
        return [asdict(s) for s in self.signals]
