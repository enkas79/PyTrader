"""Verifica numerica degli indicatori contro implementazioni di riferimento."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from indicators import IndicatorEngine


def test_donchian_matches_rolling_extremes(ohlcv: pd.DataFrame) -> None:
    df = IndicatorEngine(donchian_length=55).compute(ohlcv)
    exp_upper = ohlcv["high"].rolling(55).max()
    exp_lower = ohlcv["low"].rolling(55).min()
    pd.testing.assert_series_equal(df["dcu"], exp_upper, check_names=False)
    pd.testing.assert_series_equal(df["dcl"], exp_lower, check_names=False)
    pd.testing.assert_series_equal(df["dcm"], (exp_upper + exp_lower) / 2, check_names=False)


def test_atr_is_wilder_rma(ohlcv: pd.DataFrame) -> None:
    df = IndicatorEngine(atr_length=14).compute(ohlcv)
    prev_close = ohlcv["close"].shift(1)
    tr = pd.concat(
        [
            ohlcv["high"] - ohlcv["low"],
            (ohlcv["high"] - prev_close).abs(),
            (ohlcv["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    # Dopo un warm-up lungo l'RMA converge indipendentemente dal seed iniziale.
    rma = tr.ewm(alpha=1 / 14, adjust=False).mean()
    assert np.allclose(df["atr"].iloc[-50:], rma.iloc[-50:], rtol=1e-3)
    assert df["atr"].iloc[14:].notna().all()


def test_vwap_and_bands_match_bruteforce(ohlcv: pd.DataFrame) -> None:
    engine = IndicatorEngine(vwap_band_std=2.0)
    df = engine.compute(ohlcv)
    tp = (ohlcv["high"] + ohlcv["low"] + ohlcv["close"]) / 3
    for ts in [df.index[5], df.index[95], df.index[96], df.index[200], df.index[-1]]:
        day = ohlcv[(ohlcv.index >= ts.floor("D")) & (ohlcv.index <= ts)]
        w = day["volume"].to_numpy()
        x = tp.loc[day.index].to_numpy()
        vwap = np.sum(w * x) / np.sum(w)
        sigma = np.sqrt(np.sum(w * (x - vwap) ** 2) / np.sum(w))
        assert df.at[ts, "vwap"] == pytest.approx(vwap, rel=1e-10)
        assert df.at[ts, "vwap_upper"] == pytest.approx(vwap + 2 * sigma, rel=1e-9)
        assert df.at[ts, "vwap_lower"] == pytest.approx(vwap - 2 * sigma, rel=1e-9)


def test_vwap_resets_at_utc_midnight(ohlcv: pd.DataFrame) -> None:
    df = IndicatorEngine().compute(ohlcv)
    first_of_day = df.index[df.index.normalize() == df.index][1]  # 2° giorno 00:00
    row = ohlcv.loc[first_of_day]
    assert df.at[first_of_day, "vwap"] == pytest.approx((row.high + row.low + row.close) / 3)
    assert df.at[first_of_day, "vwap_upper"] == pytest.approx(df.at[first_of_day, "vwap"])


def test_rejects_non_utc_index(ohlcv: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="UTC"):
        IndicatorEngine().compute(ohlcv.tz_convert("Europe/Rome"))


def test_rejects_insufficient_data(ohlcv: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="insufficienti"):
        IndicatorEngine().compute(ohlcv.iloc[:30])
