"""Rilevamento vettorializzato dei pattern candlestick.

Il pattern alla posizione ``i`` usa solo le candele ``i``, ``i-1``, ``i-2`` (tramite ``shift``):
è noto alla chiusura di ``i``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from pytrader.models import PatternHit, PatternType


@dataclass(frozen=True)
class PatternParams:
    """Soglie dei pattern, espresse come frazioni del range o del corpo."""

    doji_body_max: float = 0.1  # corpo <= 10% del range
    pin_wick_min: float = 0.6  # ombra principale >= 60% del range
    pin_nose_max: float = 0.15  # ombra opposta <= 15% del range
    star_big_body_min: float = 0.5  # prima candela: corpo >= 50% del range
    star_small_body_max: float = 0.3  # stella: corpo <= 30% del corpo della prima


def detect_patterns(df: pd.DataFrame, params: Optional[PatternParams] = None) -> pd.DataFrame:
    """DataFrame booleano (una colonna per ``PatternType.value``) allineato a ``df``."""
    params = params or PatternParams()
    o, h, lo, c = df["open"], df["high"], df["low"], df["close"]
    body = (c - o).abs()
    rng = h - lo
    valid = rng > 0
    upper_wick = h - np.maximum(o, c)
    lower_wick = np.minimum(o, c) - lo
    bull = c > o
    bear = c < o

    o1, c1, body1 = o.shift(1), c.shift(1), body.shift(1)
    bull1, bear1 = bull.shift(1), bear.shift(1)
    o2, c2, body2, rng2, bear2, bull2 = (
        o.shift(2),
        c.shift(2),
        body.shift(2),
        rng.shift(2),
        bear.shift(2),
        bull.shift(2),
    )
    rng1 = rng.shift(1)

    doji = valid & (body <= params.doji_body_max * rng)
    hammer = (
        valid
        & (lower_wick >= params.pin_wick_min * rng)
        & (upper_wick <= params.pin_nose_max * rng)
    )
    shooting = (
        valid
        & (upper_wick >= params.pin_wick_min * rng)
        & (lower_wick <= params.pin_nose_max * rng)
    )
    bull_engulf = (
        bear1.astype("boolean").fillna(False) & bull & (o <= c1) & (c >= o1) & (body > body1)
    )
    bear_engulf = (
        bull1.astype("boolean").fillna(False) & bear & (o >= c1) & (c <= o1) & (body > body1)
    )

    big_first = (rng2 > 0) & (body2 >= params.star_big_body_min * rng2)
    small_star = (rng1 >= 0) & (body1 <= params.star_small_body_max * body2)
    morning = (
        big_first & bear2.astype("boolean").fillna(False) & small_star & bull & (c >= (o2 + c2) / 2)
    )
    evening = (
        big_first & bull2.astype("boolean").fillna(False) & small_star & bear & (c <= (o2 + c2) / 2)
    )

    result = pd.DataFrame(
        {
            PatternType.DOJI.value: doji,
            PatternType.HAMMER.value: hammer,
            PatternType.SHOOTING_STAR.value: shooting,
            PatternType.BULLISH_ENGULFING.value: bull_engulf,
            PatternType.BEARISH_ENGULFING.value: bear_engulf,
            PatternType.MORNING_STAR.value: morning,
            PatternType.EVENING_STAR.value: evening,
        },
        index=df.index,
    )
    return result.fillna(False).astype(bool)


def pattern_hits(
    df: pd.DataFrame, params: Optional[PatternParams] = None, include_neutral: bool = True
) -> list[PatternHit]:
    """Elenco dei pattern trovati, in ordine temporale."""
    flags = detect_patterns(df, params)
    hits: list[PatternHit] = []
    for ptype in PatternType:
        if not include_neutral and ptype.bias is None:
            continue
        for pos in np.flatnonzero(flags[ptype.value].to_numpy()):
            hits.append(PatternHit(index=int(pos), timestamp=df.index[pos], pattern=ptype))
    hits.sort(key=lambda hit: (hit.index, -hit.pattern.strength))
    return hits
