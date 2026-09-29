"""Validazione della serie OHLCV: indice, coerenza dei prezzi, duplicati e buchi."""

from __future__ import annotations

import re
from typing import Optional, Union

import numpy as np
import pandas as pd

from pytrader.data.base import OHLCV_COLUMNS
from pytrader.models import Gap, ValidationReport

_TF_RE = re.compile(r"^\s*(\d+)\s*([smhdwM])\s*$")
_TF_UNITS: dict[str, str] = {"s": "s", "m": "min", "h": "h", "d": "D", "w": "W"}


def parse_timeframe(timeframe: str) -> pd.Timedelta:
    """Converte ``15m``, ``1h``, ``1d``, ``1w`` in ``Timedelta``."""
    match = _TF_RE.match(timeframe)
    if match is None or match.group(2) not in _TF_UNITS:
        raise ValueError(f"Timeframe non valido: {timeframe!r}")
    amount = int(match.group(1))
    if amount <= 0:
        raise ValueError(f"Timeframe non valido: {timeframe!r}")
    return pd.Timedelta(amount, unit=_TF_UNITS[match.group(2)])


def _infer_timeframe(index: pd.DatetimeIndex) -> Optional[pd.Timedelta]:
    """Timeframe dominante (moda delle differenze)."""
    if len(index) < 3:
        return None
    diffs = pd.Series(index[1:] - index[:-1])
    return pd.Timedelta(diffs.mode().iloc[0])


def validate_ohlcv(
    frame: pd.DataFrame,
    timeframe: Union[str, pd.Timedelta, None] = None,
    gap_tolerance: float = 1.5,
) -> tuple[pd.DataFrame, ValidationReport]:
    """Restituisce una copia pulita della serie e il report delle anomalie.

    - Indice convertito in ``DatetimeIndex`` UTC, ordinato, senza duplicati (vince l'ultimo).
    - Rimosse le righe con OHLC mancanti o incoerenti (high/low che non contengono open/close,
      prezzi non positivi, volume negativo).
    - I buchi temporali vengono solo segnalati: riempirli creerebbe candele fittizie.
      ``gap_tolerance`` evita falsi positivi su serie con sessioni (es. azioni, weekend):
      è segnalato un buco solo se la distanza supera ``gap_tolerance`` × timeframe.
    """
    report = ValidationReport(rows_in=len(frame))
    missing_cols = [c for c in ("open", "high", "low", "close") if c not in frame.columns]
    if missing_cols:
        raise ValueError(f"Colonne OHLC mancanti: {', '.join(missing_cols)}")

    df = frame.copy()
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df = df[list(OHLCV_COLUMNS)].apply(pd.to_numeric, errors="coerce").astype(float)

    index = pd.DatetimeIndex(pd.to_datetime(df.index, utc=True))
    df.index = index
    df.index.name = "timestamp"
    df = df.sort_index(kind="mergesort")

    dup_mask = df.index.duplicated(keep="last")
    report.duplicates_removed = int(dup_mask.sum())
    df = df[~dup_mask]

    nan_mask = df[["open", "high", "low", "close"]].isna().any(axis=1).to_numpy()
    report.nan_rows_removed = int(nan_mask.sum())
    df = df[~nan_mask]
    df["volume"] = df["volume"].fillna(0.0)

    o, h, lo, c, v = (df[col].to_numpy() for col in OHLCV_COLUMNS)
    bad = (
        (h < np.maximum(o, c))
        | (lo > np.minimum(o, c))
        | (h < lo)
        | (np.minimum.reduce([o, h, lo, c]) <= 0)
        | (v < 0)
    )
    report.inconsistent_rows_removed = int(bad.sum())
    df = df[~bad]

    report.rows_out = len(df)
    report.volume_available = bool(len(df) > 0 and (df["volume"] > 0).any())
    if report.duplicates_removed:
        report.warnings.append("Timestamp duplicati: mantenuta l'ultima occorrenza")

    if isinstance(timeframe, str):
        tf: Optional[pd.Timedelta] = parse_timeframe(timeframe)
    elif timeframe is None:
        tf = _infer_timeframe(pd.DatetimeIndex(df.index))
    else:
        tf = pd.Timedelta(timeframe)
    report.timeframe = tf

    if tf is not None and len(df) > 1:
        idx = pd.DatetimeIndex(df.index)
        deltas = idx[1:] - idx[:-1]
        for pos in np.flatnonzero(deltas > tf * gap_tolerance):
            missing = int(deltas[pos] / tf) - 1
            report.gaps.append(Gap(start=idx[pos], end=idx[pos + 1], missing_bars=missing))
        if report.gaps:
            report.warnings.append(
                f"{len(report.gaps)} buchi temporali: indicatori e pattern li attraversano"
            )
    return df, report


def drop_unclosed_candle(
    frame: pd.DataFrame,
    timeframe: Union[str, pd.Timedelta],
    now: Optional[pd.Timestamp] = None,
) -> tuple[pd.DataFrame, bool]:
    """Rimuove l'ultima candela se non ancora chiusa (apertura + timeframe > ``now``).

    Exchange e Yahoo restituiscono la candela in formazione: analizzarla produrrebbe
    pattern che possono sparire prima della chiusura (repainting).
    """
    if frame.empty:
        return frame, False
    tf = parse_timeframe(timeframe) if isinstance(timeframe, str) else pd.Timedelta(timeframe)
    current = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if current.tzinfo is None:
        current = current.tz_localize("UTC")
    if frame.index[-1] + tf > current:
        return frame.iloc[:-1], True
    return frame, False
