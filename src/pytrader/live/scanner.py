"""Scansione di un mercato: ultime candele chiuse -> eventuale setup sull'ultima candela."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from pytrader.data import parse_timeframe
from pytrader.live.watchlist import WatchItem
from pytrader.models import TradeSetup
from pytrader.services import DataRequest, LoadedData, load_data
from pytrader.signals import SignalEngine, SignalParams

_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")
_EPOCH_MONDAY = pd.Timestamp("1970-01-05", tz="UTC")  # candele settimanali: lunedì 00:00 UTC
CLOSE_DELAY = pd.Timedelta(seconds=20)  # attesa perché l'exchange consolidi la candela
MAX_POLL = pd.Timedelta(minutes=5)  # tetto: copre sessioni non allineate (azioni, Yahoo)

Loader = Callable[..., LoadedData]


def next_check_time(timeframe: str, now: pd.Timestamp, aligned: bool = True) -> pd.Timestamp:
    """Prossimo controllo: chiusura della candela corrente + breve ritardo.

    ``aligned=True`` (exchange crypto): candele allineate all'epoch UTC, basta il confine.
    ``aligned=False`` (Yahoo: azioni/ETF con sessioni proprie): il confine UTC può non
    coincidere con la chiusura reale, quindi si controlla almeno ogni 5 minuti; i duplicati
    sono comunque filtrati dallo storico.
    """
    tf = parse_timeframe(timeframe)
    origin = _EPOCH_MONDAY if tf == pd.Timedelta(weeks=1) else _EPOCH
    periods = (now - origin) // tf
    boundary = origin + (periods + 1) * tf + CLOSE_DELAY
    return boundary if aligned else min(boundary, now + MAX_POLL)


@dataclass(frozen=True)
class ScanResult:
    item: WatchItem
    last_closed: pd.Timestamp  # apertura dell'ultima candela chiusa
    last_close: float
    setup: Optional[TradeSetup]  # setup generato proprio dall'ultima candela chiusa


class LiveScanner:
    """Riusa ``load_data`` e ``SignalEngine``: stessa logica del backtest, niente duplicati."""

    def __init__(self, loader: Loader = load_data, min_bars: int = 600) -> None:
        self._loader = loader
        self.min_bars = min_bars

    def bars_needed(self, params: SignalParams) -> int:
        return max(
            self.min_bars, params.levels.lookback + params.atr_period + 2 * params.pivot_window
        )

    def scan(
        self, item: WatchItem, params: SignalParams, now: Optional[pd.Timestamp] = None
    ) -> ScanResult:
        request = DataRequest(
            kind=item.kind,
            symbol=item.symbol,
            timeframe=item.timeframe,
            limit=self.bars_needed(params),
            exchange=item.exchange or "binance",
        )
        loaded = self._loader(request, now=now)
        frame = loaded.frame
        analysis = SignalEngine(params).analyze(frame)
        last = len(frame) - 1
        setup = next((s for s in analysis.setups if s.signal_index == last), None)
        return ScanResult(
            item=item,
            last_closed=frame.index[last],
            last_close=float(frame["close"].iat[last]),
            setup=setup,
        )
