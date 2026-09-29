"""Walk-forward: finestre, griglia, selezione in-sample senza look-ahead."""

from __future__ import annotations

import math
import threading

import numpy as np
import pandas as pd
import pytest

from pytrader.backtest import BacktestResult, TradeOutcome, TradeResult
from pytrader.optimize import (
    Objective,
    ParamGrid,
    WalkForwardCancelled,
    WalkForwardParams,
    make_windows,
    run_walk_forward,
    score,
)
from pytrader.signals import SignalParams


def test_finestre_mobili_contigue() -> None:
    windows = make_windows(1000, WalkForwardParams(folds=4, is_oos_ratio=3.0))
    assert len(windows) == 4
    oos_len = 1000 // 7
    for k, (is_w, oos_w) in enumerate(windows):
        assert is_w.end == oos_w.start  # l'OOS segue subito l'IS
        assert is_w.end - is_w.start == 3 * oos_len
        assert oos_w.end - oos_w.start == oos_len
        assert is_w.start == k * oos_len
    # OOS consecutivi e non sovrapposti
    assert all(a[1].end == b[1].start for a, b in zip(windows, windows[1:]))
    assert windows[-1][1].end <= 1000


def test_finestre_ancorate_partono_da_zero() -> None:
    windows = make_windows(1000, WalkForwardParams(folds=3, anchored=True))
    assert all(is_w.start == 0 for is_w, _ in windows)
    assert windows[1][0].end > windows[0][0].end


def test_serie_troppo_corta() -> None:
    with pytest.raises(ValueError):
        make_windows(200, WalkForwardParams(folds=5))


def test_griglia_combinazioni_preserva_parametri_base() -> None:
    base = SignalParams(atr_period=21)
    grid = ParamGrid(proximity_atr=(0.3, 0.6), min_rr=(1.5, 2.0, 3.0), sl_buffer_atr=(1.0,))
    combos = grid.combinations(base)
    assert grid.size == len(combos) == 6
    assert {c.atr_period for c in combos} == {21}
    assert {(c.proximity_atr, c.min_rr) for c in combos} == {
        (p, r) for p in (0.3, 0.6) for r in (1.5, 2.0, 3.0)
    }


def test_griglia_vuota_non_valida() -> None:
    with pytest.raises(ValueError):
        ParamGrid(proximity_atr=())


def _result(rs: list[float]) -> BacktestResult:
    dummy = object()
    trades = [
        TradeResult(dummy, TradeOutcome.WIN if r > 0 else TradeOutcome.LOSS, 1, 1.0, r)  # type: ignore[arg-type]
        for r in rs
    ]
    return BacktestResult(trades=trades)


def test_score_trade_minimi_e_obiettivi() -> None:
    few = _result([1.0, 2.0])
    assert score(few, Objective.EXPECTANCY, min_trades=3) == -math.inf
    many = _result([2.0, -1.0, 2.0, -1.0])
    assert score(many, Objective.EXPECTANCY, min_trades=3) == pytest.approx(0.5)
    assert score(many, Objective.TOTAL_R, min_trades=3) == pytest.approx(2.0)
    sqn = 0.5 / np.std([2.0, -1.0, 2.0, -1.0], ddof=1) * 2
    assert score(many, Objective.SQN, min_trades=3) == pytest.approx(sqn)


GRID = ParamGrid(proximity_atr=(0.3, 0.8), sl_buffer_atr=(1.0, 1.5), min_rr=(1.5,))
WF = WalkForwardParams(folds=3, is_oos_ratio=2.0, min_trades=1)


def test_walk_forward_struttura(random_walk: pd.DataFrame) -> None:
    progress: list[int] = []
    result = run_walk_forward(random_walk, SignalParams(), GRID, WF, progress=progress.append)
    assert len(result.folds) == 3
    assert progress and progress[-1] == 100
    combos = GRID.combinations(SignalParams())
    for fold in result.folds:
        if fold.params is not None:
            assert fold.params in combos
        for t in fold.oos.trades:
            # Solo segnali nati nella finestra fuori campione
            assert fold.oos_window.start <= t.setup.signal_index < fold.oos_window.end
    assert result.oos.n_trades == sum(f.oos.n_trades for f in result.folds)
    assert result.recommended in combos


def test_selezione_in_sample_senza_look_ahead(random_walk: pd.DataFrame) -> None:
    """I parametri scelti nel primo fold non cambiano se si alterano i dati successivi
    alla fine del suo in-sample."""
    base = run_walk_forward(random_walk, SignalParams(), GRID, WF)
    cut = base.folds[0].is_window.end
    altered = random_walk.copy()
    rng = np.random.default_rng(3)
    tail = altered.iloc[cut:]
    shuffled = tail.to_numpy()[rng.permutation(len(tail))]
    altered.iloc[cut:] = shuffled
    other = run_walk_forward(altered, SignalParams(), GRID, WF)
    assert other.folds[0].params == base.folds[0].params
    assert other.folds[0].is_score == base.folds[0].is_score


def test_annullamento(random_walk: pd.DataFrame) -> None:
    stop = threading.Event()
    stop.set()
    with pytest.raises(WalkForwardCancelled):
        run_walk_forward(random_walk, SignalParams(), GRID, WF, cancel=stop.is_set)


def test_stabilita_parametri(random_walk: pd.DataFrame) -> None:
    result = run_walk_forward(random_walk, SignalParams(), GRID, WF)
    stability = result.stability()
    chosen = [f for f in result.folds if f.params is not None]
    assert set(stability) == {"proximity_atr", "sl_buffer_atr"}  # solo i parametri variati
    assert sum(stability["proximity_atr"].values()) == len(chosen)
