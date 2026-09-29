"""Fixture e helper condivisi."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd
import pytest


def make_ohlcv(
    rows: Sequence[Sequence[float]], freq: str = "1h", start: str = "2024-01-01"
) -> pd.DataFrame:
    """Costruisce un DataFrame OHLCV da tuple (open, high, low, close[, volume])."""
    data = [tuple(r) + (1.0,) * (5 - len(r)) for r in rows]
    index = pd.date_range(start, periods=len(data), freq=freq, tz="UTC", name="timestamp")
    return pd.DataFrame(data, columns=["open", "high", "low", "close", "volume"], index=index)


@pytest.fixture
def random_walk() -> pd.DataFrame:
    """Serie sintetica riproducibile con swing marcati (onda + rumore)."""
    rng = np.random.default_rng(42)
    n = 1500
    t = np.arange(n)
    base = 100 + 8 * np.sin(t / 25) + np.cumsum(rng.normal(0, 0.3, n))
    close = base + rng.normal(0, 0.4, n)
    open_ = np.concatenate([[close[0]], close[:-1]]) + rng.normal(0, 0.1, n)
    high = np.maximum(open_, close) + rng.exponential(0.5, n)
    low = np.minimum(open_, close) - rng.exponential(0.5, n)
    volume = rng.uniform(100, 1000, n)
    index = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC", name="timestamp")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=index
    )


@pytest.fixture(autouse=True)
def _isolated_app_home(tmp_path, monkeypatch) -> None:
    """Watchlist, storico e log dei test non toccano la home reale."""
    monkeypatch.setenv("PYTRADER_HOME", str(tmp_path / "pytrader_home"))
