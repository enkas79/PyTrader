"""Fixture condivise: dati OHLCV sintetici e configurazioni di test."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config import BotConfig, ExchangeConfig, RiskConfig, RuntimeConfig, StrategyConfig


def make_ohlcv(n: int = 300, start: str = "2024-01-01 00:00", seed: int = 7) -> pd.DataFrame:
    """Random walk OHLCV a 15m con indice UTC."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    close = 40_000 + np.cumsum(rng.normal(0, 60, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    spread = np.abs(rng.normal(0, 40, n))
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = rng.uniform(5, 50, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx
    )


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    return make_ohlcv()


@pytest.fixture
def bot_config(tmp_path) -> BotConfig:
    return BotConfig(
        exchange=ExchangeConfig(symbol="BTC/USDT:USDT"),
        strategy=StrategyConfig(),
        risk=RiskConfig(),
        runtime=RuntimeConfig(
            dry_run=True,
            paper_balance=10_000.0,
            db_path=tmp_path / "test.sqlite3",
            log_path=tmp_path / "test.log",
        ),
    )
