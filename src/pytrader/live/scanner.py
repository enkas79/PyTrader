"""Scansione di un mercato: ultime candele chiuse -> eventuale setup sull'ultima candela."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from pytrader.live.watchlist import WatchItem
from pytrader.models import TradeSetup
from pytrader.services import DataRequest, LoadedData, load_data
from pytrader.signals import SignalEngine, SignalParams

Loader = Callable[..., LoadedData]


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
