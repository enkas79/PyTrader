"""Indicatori di contesto: True Range, ATR di Wilder, volume medio.

Tutti i valori alla posizione ``i`` dipendono solo dalle candele ``<= i``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    """max(high-low, |high-close_prev|, |low-close_prev|); sulla prima candela vale high-low."""
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1, skipna=True).rename("tr")


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR di Wilder: seme = media semplice dei primi ``period`` TR, poi RMA (alpha = 1/period).

    Le prime ``period - 1`` posizioni sono ``NaN``.
    """
    if period < 1:
        raise ValueError("period deve essere >= 1")
    tr = true_range(df)
    out = pd.Series(np.nan, index=df.index, name=f"atr_{period}")
    if len(tr) < period:
        return out
    seeded = tr.iloc[period - 1 :].copy()
    seeded.iloc[0] = tr.iloc[:period].mean()
    out.iloc[period - 1 :] = seeded.ewm(alpha=1.0 / period, adjust=False).mean().to_numpy()
    return out


def volume_sma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Media mobile semplice del volume."""
    if period < 1:
        raise ValueError("period deve essere >= 1")
    return df["volume"].rolling(period, min_periods=period).mean().rename(f"vol_sma_{period}")
