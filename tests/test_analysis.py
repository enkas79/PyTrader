import numpy as np
import pandas as pd

from pytrader.analysis import (
    LevelParams,
    atr,
    build_levels,
    detect_patterns,
    find_pivots,
    true_range,
    volume_sma,
)
from pytrader.analysis.levels import cluster_prices
from tests.conftest import make_ohlcv


def test_true_range_con_gap() -> None:
    df = make_ohlcv([(10, 11, 9, 10), (13, 14, 12.5, 13.5)])
    assert true_range(df).tolist() == [2.0, 4.0]  # |14 - 10| domina


def test_atr_wilder_manuale() -> None:
    df = make_ohlcv([(10, 10 + r, 10, 10) for r in (1, 2, 3, 4, 5)])  # TR = 1..5
    out = atr(df, period=3)
    assert out.iloc[:2].isna().all()
    assert out.iloc[2] == 2.0  # media(1, 2, 3)
    assert np.isclose(out.iloc[3], (2.0 * 2 + 4) / 3)
    assert np.isclose(out.iloc[4], (out.iloc[3] * 2 + 5) / 3)


def test_volume_sma() -> None:
    df = make_ohlcv([(1, 2, 0.5, 1, v) for v in (10, 20, 30)])
    assert volume_sma(df, 2).tolist()[1:] == [15.0, 25.0]


def test_pivot_confermato_con_ritardo() -> None:
    highs = [1, 2, 3, 9, 3, 2, 1, 1]
    df = make_ohlcv([(h - 0.5, h, h - 1, h - 0.5) for h in highs])
    piv = find_pivots(df, window=2)
    top = piv[piv["kind"] == "high"]
    assert top["index"].tolist() == [3]
    assert top["confirm_index"].tolist() == [5]
    assert top["price"].tolist() == [9]


def test_pivot_senza_lookahead(random_walk: pd.DataFrame) -> None:
    """Troncare la serie non modifica i pivot già confermati."""
    full = find_pivots(random_walk, window=5)
    cut = 900
    part = find_pivots(random_walk.iloc[:cut], window=5)
    expected = full[full["confirm_index"] <= cut - 1].reset_index(drop=True)
    pd.testing.assert_frame_equal(part, expected)


def test_cluster_prices() -> None:
    prices = np.array([100.0, 100.4, 100.2, 110.0, 110.3, 120.0])
    idx = np.arange(len(prices))
    levels = cluster_prices(prices, idx, tolerance=0.5, min_touches=2)
    assert [lv.touches for lv in levels] == [3, 2]
    assert levels[0].lower == 100.0 and levels[0].upper == 100.4
    assert np.isclose(levels[0].price, 100.2)


def test_build_levels_usa_solo_pivot_confermati() -> None:
    pivots = pd.DataFrame(
        {
            "index": [10, 20, 30],
            "confirm_index": [15, 25, 35],
            "price": [100.0, 100.1, 100.2],
            "kind": ["low", "low", "low"],
        }
    )
    params = LevelParams(tolerance_atr=1.0, min_touches=2)
    assert build_levels(pivots, 24, 1.0, params) == []
    assert build_levels(pivots, 25, 1.0, params)[0].touches == 2
    assert build_levels(pivots, 40, 1.0, params)[0].touches == 3


def test_pattern_singoli() -> None:
    df = make_ohlcv(
        [
            (10.0, 10.1, 7.0, 9.9),  # hammer (ombra inferiore lunga)
            (10.0, 13.0, 9.9, 10.1),  # shooting star
            (10.0, 11.0, 9.0, 10.02),  # doji
        ]
    )
    flags = detect_patterns(df)
    assert flags["hammer"].tolist() == [True, False, False]
    assert flags["shooting_star"].tolist() == [False, True, False]
    assert flags["doji"].tolist()[2]


def test_engulfing() -> None:
    df = make_ohlcv(
        [
            (10.0, 10.2, 9.0, 9.2),  # ribassista
            (9.1, 10.6, 9.0, 10.5),  # rialzista che ingloba -> bullish engulfing
            (10.6, 10.8, 8.9, 9.0),  # ribassista che ingloba -> bearish engulfing
        ]
    )
    flags = detect_patterns(df)
    assert flags["bullish_engulfing"].tolist() == [False, True, False]
    assert flags["bearish_engulfing"].tolist() == [False, False, True]


def test_morning_evening_star() -> None:
    morning = make_ohlcv([(12, 12.1, 9.9, 10), (9.9, 10.1, 9.5, 9.8), (9.9, 11.8, 9.8, 11.6)])
    evening = make_ohlcv([(10, 12.1, 9.9, 12), (12.1, 12.5, 11.9, 12.2), (12.1, 12.2, 10.2, 10.4)])
    assert detect_patterns(morning)["morning_star"].tolist() == [False, False, True]
    assert detect_patterns(evening)["evening_star"].tolist() == [False, False, True]


def test_pattern_senza_lookahead(random_walk: pd.DataFrame) -> None:
    full = detect_patterns(random_walk)
    part = detect_patterns(random_walk.iloc[:700])
    pd.testing.assert_frame_equal(part, full.iloc[:700])
