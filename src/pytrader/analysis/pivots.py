"""Swing pivot con conferma ritardata (nessun look-ahead).

Un massimo alla posizione ``i`` è un pivot se supera le ``window`` candele successive
ed è >= alle ``window`` precedenti. Poiché servono le candele successive, il pivot
diventa *noto* solo alla chiusura della candela ``i + window`` (``confirm_index``):
qualsiasi logica al tempo ``t`` deve usare solo i pivot con ``confirm_index <= t``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PIVOT_COLUMNS: tuple[str, ...] = ("index", "confirm_index", "price", "kind")


def _forward_max(values: pd.Series, window: int) -> pd.Series:
    """Massimo delle ``window`` posizioni successive (NaN se incomplete)."""
    return values[::-1].shift(1).rolling(window, min_periods=window).max()[::-1]


def find_pivots(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """Restituisce i pivot ordinati per ``confirm_index``.

    Colonne: ``index`` (posizione del pivot), ``confirm_index`` (posizione a cui è noto),
    ``price``, ``kind`` (``"high"`` | ``"low"``).
    """
    if window < 1:
        raise ValueError("window deve essere >= 1")
    high = df["high"].reset_index(drop=True)
    low = df["low"].reset_index(drop=True)

    left_max = high.shift(1).rolling(window, min_periods=window).max()
    right_max = _forward_max(high, window)
    is_high = (high >= left_max) & (high > right_max)

    neg_low = -low
    left_min = neg_low.shift(1).rolling(window, min_periods=window).max()
    right_min = _forward_max(neg_low, window)
    is_low = (neg_low >= left_min) & (neg_low > right_min)

    frames = []
    for kind, mask, series in (("high", is_high, high), ("low", is_low, low)):
        pos = np.flatnonzero(mask.to_numpy())
        frames.append(
            pd.DataFrame(
                {
                    "index": pos,
                    "confirm_index": pos + window,
                    "price": series.to_numpy()[pos],
                    "kind": kind,
                }
            )
        )
    pivots = pd.concat(frames, ignore_index=True)
    pivots = pivots.astype({"index": "int64", "confirm_index": "int64", "price": "float64"})
    return pivots.sort_values(["confirm_index", "index"], kind="mergesort").reset_index(drop=True)
