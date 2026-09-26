"""Regole di ingresso e livelli SL/TP."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config import StrategyConfig
from strategy import Side, Strategy


class StubIndicators:
    """Restituisce indicatori prefissati per isolare la logica dei segnali."""

    def __init__(self, columns: dict[str, list[float]]) -> None:
        self.columns = columns

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        df = ohlcv.copy()
        for name, values in self.columns.items():
            df[name] = values
        return df


def _frame(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="15min", tz="UTC")
    c = np.array(closes)
    return pd.DataFrame(
        {"open": c, "high": c, "low": c, "close": c, "volume": 1.0}, index=idx
    )


def _strategy(cols: dict[str, list[float]], allow_short: bool = True) -> Strategy:
    return Strategy(StrategyConfig(allow_short=allow_short), StubIndicators(cols))  # type: ignore[arg-type]


BASE = {
    "atr": [10.0, 10.0, 10.0],
    "dcu": [105.0, 105.0, 130.0],
    "dcl": [95.0, 95.0, 80.0],
    "vwap_upper": [200.0, 200.0, 200.0],
    "vwap_lower": [0.0, 0.0, 0.0],
}


def test_long_breakout_uses_previous_upper_channel() -> None:
    # close=110 > dcu.shift(1)=105 → long (il dcu corrente 130 non conta).
    sig = _strategy(BASE).evaluate(_frame([100, 100, 110]))
    assert sig is not None and sig.side is Side.LONG
    assert sig.stop_loss == pytest.approx(110 - 20)
    assert sig.take_profit == pytest.approx(110 + 50)
    assert sig.stop_distance == pytest.approx(20)


def test_long_blocked_by_vwap_upper_band() -> None:
    cols = {**BASE, "vwap_upper": [200.0, 200.0, 109.0]}
    assert _strategy(cols).evaluate(_frame([100, 100, 110])) is None


def test_short_breakdown_and_levels() -> None:
    sig = _strategy(BASE).evaluate(_frame([100, 100, 90]))
    assert sig is not None and sig.side is Side.SHORT
    assert sig.stop_loss == pytest.approx(90 + 20)
    assert sig.take_profit == pytest.approx(90 - 50)


def test_short_blocked_by_vwap_lower_band() -> None:
    cols = {**BASE, "vwap_lower": [0.0, 0.0, 91.0]}
    assert _strategy(cols).evaluate(_frame([100, 100, 90])) is None


def test_short_disabled_on_spot() -> None:
    assert _strategy(BASE, allow_short=False).evaluate(_frame([100, 100, 90])) is None


def test_no_signal_inside_channel() -> None:
    assert _strategy(BASE).evaluate(_frame([100, 100, 100])) is None


def test_nan_atr_suppresses_signal() -> None:
    cols = {**BASE, "atr": [np.nan, np.nan, np.nan]}
    assert _strategy(cols).evaluate(_frame([100, 100, 110])) is None


def test_reward_risk_is_2_5() -> None:
    s = Strategy(StrategyConfig())
    sl, tp = s.levels(Side.LONG, 1000.0, 4.0)
    assert (tp - 1000.0) / (1000.0 - sl) == pytest.approx(2.5)


def test_vectorized_signals_on_real_indicators(ohlcv: pd.DataFrame) -> None:
    df = Strategy(StrategyConfig()).generate_signals(ohlcv)
    expected_long = (df["close"] > df["dcu"].shift(1)) & (df["close"] < df["vwap_upper"])
    assert (df["long_signal"] == expected_long.fillna(False)).all()
    assert not (df["long_signal"] & df["short_signal"]).any()
