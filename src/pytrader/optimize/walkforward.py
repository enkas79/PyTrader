"""Ottimizzazione walk-forward dei parametri del ``SignalEngine``.

La serie è divisa in fold: per ciascuno si sceglie la combinazione migliore sulla finestra
in-sample (IS) e la si valuta sulla finestra out-of-sample (OOS) immediatamente successiva,
mai vista durante la scelta. Solo le metriche OOS aggregate stimano il rendimento atteso;
il divario IS/OOS misura l'overfitting.

Assenza di look-ahead:
- i setup sono calcolati una volta sull'intera serie: indicatori, pivot (confermati) e livelli
  sono causali, quindi un setup alla candela ``i`` dipende solo dalle candele ``<= i``;
- il backtest IS usa la serie troncata alla fine dell'IS: i trade non chiusi entro quel punto
  restano aperti e non contribuiscono allo score;
- il backtest OOS usa la serie completa: un trade aperto nell'OOS può chiudersi dopo, come
  accadrebbe dal vivo.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import Enum
from itertools import product
from typing import Optional

import numpy as np
import pandas as pd

from pytrader.backtest import Backtester, BacktestParams, BacktestResult
from pytrader.models import TradeSetup
from pytrader.signals import Features, SignalEngine, SignalParams

MIN_OOS_BARS = 50
SQN_TRADE_CAP = 100  # come nella SQN di Van Tharp: oltre 100 trade la radice non cresce


class WalkForwardCancelled(Exception):
    """Ottimizzazione interrotta dall'utente."""


class Objective(str, Enum):
    SQN = "sqn"  # √n × media(R) / dev.std(R): premia rendimento costante
    EXPECTANCY = "expectancy"  # R medio per trade
    TOTAL_R = "total_r"  # somma degli R


# Parametri ottimizzabili: nome del campo -> (etichetta, appartiene a LevelParams)
GRID_FIELDS: dict[str, tuple[str, bool]] = {
    "proximity_atr": ("Prossimità (×ATR)", False),
    "sl_buffer_atr": ("Buffer SL (×ATR)", False),
    "min_rr": ("R:R minimo", False),
    "tolerance_atr": ("Tolleranza (×ATR)", True),
    "pivot_window": ("Finestra pivot", False),
}


@dataclass(frozen=True)
class ParamGrid:
    """Valori candidati per ciascun parametro; un solo valore = parametro fisso."""

    proximity_atr: tuple[float, ...] = (0.3, 0.5, 0.8)
    sl_buffer_atr: tuple[float, ...] = (1.0, 1.5, 2.0)
    min_rr: tuple[float, ...] = (1.5, 2.0, 3.0)
    tolerance_atr: tuple[float, ...] = (0.5,)
    pivot_window: tuple[int, ...] = (5,)

    def __post_init__(self) -> None:
        for name in GRID_FIELDS:
            if not getattr(self, name):
                raise ValueError(f"Nessun valore per {GRID_FIELDS[name][0]}")

    @property
    def size(self) -> int:
        return math.prod(len(getattr(self, name)) for name in GRID_FIELDS)

    @property
    def varied(self) -> list[str]:
        """Parametri con più di un valore candidato."""
        return [name for name in GRID_FIELDS if len(set(getattr(self, name))) > 1]

    def combinations(self, base: SignalParams) -> list[SignalParams]:
        """Prodotto cartesiano applicato a ``base`` (gli altri parametri restano invariati)."""
        combos = []
        for prox, buffer, rr, tol, window in product(
            *(getattr(self, name) for name in GRID_FIELDS)
        ):
            combos.append(
                replace(
                    base,
                    proximity_atr=float(prox),
                    sl_buffer_atr=float(buffer),
                    min_rr=float(rr),
                    pivot_window=int(window),
                    levels=replace(base.levels, tolerance_atr=float(tol)),
                )
            )
        return combos


def param_value(params: SignalParams, name: str) -> float:
    """Valore di un parametro della griglia (anche se annidato in ``levels``)."""
    source = params.levels if GRID_FIELDS[name][1] else params
    return float(getattr(source, name))


@dataclass(frozen=True)
class WalkForwardParams:
    folds: int = 5
    is_oos_ratio: float = 3.0  # lunghezza IS = rapporto × lunghezza OOS
    anchored: bool = False  # True: l'IS parte sempre dall'inizio della serie
    objective: Objective = Objective.SQN
    min_trades: int = 10  # trade IS minimi perché una combinazione sia considerata

    def __post_init__(self) -> None:
        if self.folds < 1 or self.is_oos_ratio <= 0 or self.min_trades < 1:
            raise ValueError("Fold >= 1, rapporto IS/OOS > 0 e trade minimi >= 1 richiesti")


@dataclass(frozen=True)
class Window:
    """Intervallo di posizioni ``[start, end)``."""

    start: int
    end: int

    def __len__(self) -> int:
        return self.end - self.start


def make_windows(n_bars: int, params: WalkForwardParams) -> list[tuple[Window, Window]]:
    """Coppie (IS, OOS): gli OOS sono consecutivi e coprono la parte finale della serie."""
    oos_len = int(n_bars // (params.folds + params.is_oos_ratio))
    is_len = int(round(params.is_oos_ratio * oos_len))
    if oos_len < MIN_OOS_BARS or is_len < 1:
        raise ValueError(
            f"Serie troppo corta: {n_bars} candele per {params.folds} fold "
            f"(servono almeno {MIN_OOS_BARS} candele per finestra fuori campione)"
        )
    windows = []
    for k in range(params.folds):
        is_end = is_len + k * oos_len
        is_start = 0 if params.anchored else k * oos_len
        windows.append((Window(is_start, is_end), Window(is_end, is_end + oos_len)))
    return windows


def score(result: BacktestResult, objective: Objective, min_trades: int) -> float:
    """Punteggio di una combinazione; ``-inf`` se i trade sono troppo pochi per giudicare."""
    rs = np.array([t.r_multiple for t in result.closed], dtype=float)
    if rs.size < min_trades or rs.size == 0:
        return -math.inf
    if objective is Objective.EXPECTANCY:
        return float(rs.mean())
    if objective is Objective.TOTAL_R:
        return float(rs.sum())
    std = float(rs.std(ddof=1)) if rs.size > 1 else 0.0
    if std == 0.0:
        return math.copysign(math.inf, rs.mean()) if rs.mean() != 0 else 0.0
    return math.sqrt(min(rs.size, SQN_TRADE_CAP)) * float(rs.mean()) / std


@dataclass
class FoldResult:
    number: int
    is_window: Window
    oos_window: Window
    is_period: tuple[pd.Timestamp, pd.Timestamp]
    oos_period: tuple[pd.Timestamp, pd.Timestamp]
    params: Optional[SignalParams]  # None: nessuna combinazione con abbastanza trade
    is_score: float
    is_result: BacktestResult
    oos: BacktestResult
    candidates: int  # combinazioni con trade IS sufficienti


@dataclass
class WalkForwardResult:
    grid: ParamGrid
    params: WalkForwardParams
    folds: list[FoldResult] = field(default_factory=list)
    recommended: Optional[SignalParams] = None  # scelta sull'ultima finestra IS disponibile
    recommended_is: Optional[BacktestResult] = None

    @property
    def oos(self) -> BacktestResult:
        """Trade OOS di tutti i fold, in ordine: la stima del comportamento futuro."""
        return BacktestResult(trades=[t for f in self.folds for t in f.oos.trades])

    @property
    def is_expectancy(self) -> float:
        """Expectancy IS media dei fold con parametri scelti."""
        values = [f.is_result.expectancy for f in self.folds if f.params is not None]
        return float(np.mean(values)) if values else 0.0

    @property
    def efficiency(self) -> float:
        """Walk-forward efficiency: expectancy OOS / expectancy IS (NaN se IS <= 0).
        Sotto ~0,5 il vantaggio visto in-sample è in gran parte overfitting."""
        is_exp = self.is_expectancy
        return self.oos.expectancy / is_exp if is_exp > 0 else math.nan

    def stability(self) -> dict[str, Counter[float]]:
        """Frequenza con cui ogni valore è stato scelto, per i soli parametri variati.
        Parametri che cambiano a ogni fold indicano una scelta guidata dal rumore."""
        chosen = [f.params for f in self.folds if f.params is not None]
        return {name: Counter(param_value(p, name) for p in chosen) for name in self.grid.varied}


Progress = Callable[[int], None]
Cancel = Callable[[], bool]


def run_walk_forward(
    df: pd.DataFrame,
    base: SignalParams,
    grid: ParamGrid,
    params: Optional[WalkForwardParams] = None,
    backtest: Optional[BacktestParams] = None,
    progress: Optional[Progress] = None,
    cancel: Optional[Cancel] = None,
) -> WalkForwardResult:
    """Esegue il walk-forward. ``progress`` riceve 0-100; ``cancel`` interrompe se ``True``."""
    wf = params or WalkForwardParams()
    windows = make_windows(len(df), wf)
    combos = grid.combinations(base)
    backtester = Backtester(backtest)

    def check_cancel() -> None:
        if cancel is not None and cancel():
            raise WalkForwardCancelled("Ottimizzazione annullata")

    # 1) setup di ogni combinazione sull'intera serie (indicatori condivisi quando possibile)
    cache: dict[tuple[object, ...], Features] = {}
    all_setups: list[list[TradeSetup]] = []
    for k, combo in enumerate(combos):
        check_cancel()
        engine = SignalEngine(combo)
        feats = cache.get(combo.feature_key)
        if feats is None:
            feats = cache[combo.feature_key] = engine.features(df)
        all_setups.append(engine.analyze(df, feats).setups)
        if progress is not None:
            progress(int(90 * (k + 1) / len(combos)))

    def run_is(setups: list[TradeSetup], window: Window) -> BacktestResult:
        selected = [s for s in setups if window.start <= s.signal_index < window.end]
        return backtester.run(df.iloc[: window.end], selected)

    def best_on(window: Window) -> tuple[Optional[int], float, BacktestResult, int]:
        best_idx: Optional[int] = None
        best_score = -math.inf
        best_result = BacktestResult()
        valid = 0
        for idx, setups in enumerate(all_setups):
            result = run_is(setups, window)
            value = score(result, wf.objective, wf.min_trades)
            if value == -math.inf:
                continue
            valid += 1
            if best_idx is None or value > best_score:
                best_idx, best_score, best_result = idx, value, result
        return best_idx, best_score, best_result, valid

    # 2) per ogni fold: scelta sull'IS, verifica sull'OOS
    result = WalkForwardResult(grid=grid, params=wf)
    index = df.index
    for number, (is_w, oos_w) in enumerate(windows, start=1):
        check_cancel()
        best_idx, best_score, is_result, valid = best_on(is_w)
        if best_idx is None:
            oos = BacktestResult()
        else:
            selected = [
                s for s in all_setups[best_idx] if oos_w.start <= s.signal_index < oos_w.end
            ]
            oos = backtester.run(df, selected)
        result.folds.append(
            FoldResult(
                number=number,
                is_window=is_w,
                oos_window=oos_w,
                is_period=(index[is_w.start], index[is_w.end - 1]),
                oos_period=(index[oos_w.start], index[oos_w.end - 1]),
                params=combos[best_idx] if best_idx is not None else None,
                is_score=best_score,
                is_result=is_result,
                oos=oos,
                candidates=valid,
            )
        )
        if progress is not None:
            progress(90 + int(9 * number / len(windows)))

    # 3) parametri consigliati per il futuro: scelta sulla finestra più recente
    check_cancel()
    n = len(df)
    is_len = len(windows[0][0]) if not wf.anchored else n
    last = Window(0 if wf.anchored else max(0, n - is_len), n)
    best_idx, _, last_result, _ = best_on(last)
    if best_idx is not None:
        result.recommended = combos[best_idx]
        result.recommended_is = last_result
    if progress is not None:
        progress(100)
    return result
