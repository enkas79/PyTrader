"""Pianificazione dei controlli live: a chiusura candela o a intervallo fisso."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from pytrader.data import parse_timeframe
from pytrader.services import SourceKind

_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")
_EPOCH_MONDAY = pd.Timestamp("1970-01-05", tz="UTC")  # candele settimanali: lunedì 00:00 UTC
CLOSE_DELAY = pd.Timedelta(seconds=20)  # attesa perché l'exchange consolidi la candela
MAX_POLL = pd.Timedelta(minutes=5)  # tetto: copre sessioni non allineate (azioni, Yahoo)

# Intervalli proposti (minuti). Minimo per sorgente: Yahoo limita le richieste per IP,
# quindi sotto i 2 minuti una watchlist ampia rischia il blocco temporaneo.
INTERVAL_CHOICES = (1, 2, 5, 10, 15, 30, 60, 120, 240, 720, 1440)
MIN_INTERVAL = {SourceKind.CCXT: 1, SourceKind.YFINANCE: 2}
MAX_INTERVAL = 1440  # oltre un giorno il ritardo di notifica non è plausibile


def allowed_intervals(kind: SourceKind, timeframe: str) -> list[int]:
    """Intervalli ammessi: tra il minimo della sorgente e la durata della candela, e solo
    divisori del timeframe. Così la griglia dei controlli contiene ogni chiusura di candela
    e lo scanner, che guarda solo l'ultima candela chiusa, non salta alcun segnale."""
    tf_min = int(parse_timeframe(timeframe) / pd.Timedelta(minutes=1))
    low = MIN_INTERVAL.get(kind, 1)
    high = min(tf_min, MAX_INTERVAL)
    return [v for v in INTERVAL_CHOICES if low <= v <= high and tf_min % v == 0]


def format_interval(minutes: int) -> str:
    if minutes % 1440 == 0:
        return f"{minutes // 1440} g"
    if minutes % 60 == 0:
        return f"{minutes // 60} h"
    return f"{minutes} min"


def next_check_time(
    timeframe: str,
    now: pd.Timestamp,
    aligned: bool = True,
    interval_min: Optional[int] = None,
) -> pd.Timestamp:
    """Prossimo controllo.

    Con ``interval_min``: prossimo multiplo dell'intervallo sulla griglia UTC + breve ritardo
    (essendo un divisore del timeframe, la griglia include le chiusure delle candele).
    Senza (automatico): chiusura della candela corrente + breve ritardo.
    ``aligned=True`` (exchange crypto): candele allineate all'epoch UTC, basta il confine.
    ``aligned=False`` (Yahoo: azioni/ETF con sessioni proprie): il confine UTC può non
    coincidere con la chiusura reale, quindi in automatico si controlla almeno ogni 5 minuti;
    i duplicati sono comunque filtrati dallo storico.
    """
    tf = parse_timeframe(timeframe)
    if interval_min is not None:
        step = pd.Timedelta(minutes=interval_min)
        return _EPOCH + ((now - _EPOCH) // step + 1) * step + CLOSE_DELAY
    origin = _EPOCH_MONDAY if tf == pd.Timedelta(weeks=1) else _EPOCH
    periods = (now - origin) // tf
    boundary = origin + (periods + 1) * tf + CLOSE_DELAY
    return boundary if aligned else min(boundary, now + MAX_POLL)
