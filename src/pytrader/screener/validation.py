"""Verifica storica del punteggio: ha davvero anticipato i rendimenti successivi?

Su date distanziate di ``horizon`` candele (periodi che non si sovrappongono) si confronta il
punteggio di ogni simbolo con il suo rendimento futuro:

- **IC** (information coefficient): correlazione di Spearman tra punteggio e rendimento
  futuro sullo stesso periodo. 0 = nessuna relazione;
- **t-stat** dell'IC medio: sotto |2| la relazione non si distingue dal caso;
- **quantili**: rendimento futuro medio in eccesso rispetto alla media dell'universo, dal
  quantile con punteggio più basso al più alto.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from pytrader.screener.features import FEATURE_LABELS, combine_scores

MIN_CROSS_SECTION = 5  # simboli minimi per confrontare i punteggi in una data
MIN_PERIODS = 20  # periodi indipendenti minimi per giudicare
T_SIGNIFICANT = 2.0


@dataclass(frozen=True)
class FactorStats:
    name: str
    periods: int
    mean_ic: float
    ic_tstat: float
    hit_rate: float  # quota di periodi con IC > 0


@dataclass(frozen=True)
class ValidationResult:
    horizon: int
    periods: int
    avg_symbols: float
    score: FactorStats
    factors: tuple[FactorStats, ...]
    buckets: tuple[float, ...]  # eccesso medio % per quantile, dal punteggio più basso
    spread: float  # quantile alto - quantile basso, % per periodo
    spread_tstat: float
    first_date: Optional[pd.Timestamp]
    last_date: Optional[pd.Timestamp]

    @property
    def enough_data(self) -> bool:
        return self.periods >= MIN_PERIODS

    @property
    def significant(self) -> bool:
        return self.enough_data and abs(self.score.ic_tstat) >= T_SIGNIFICANT

    def verdict(self) -> str:
        t = self.score.ic_tstat
        if not self.enough_data:
            return (
                f"Campione insufficiente: {self.periods} periodi indipendenti, ne servono "
                f"almeno {MIN_PERIODS}. Aumenta le candele scaricate o riduci l'orizzonte. "
                "Finché non è verificato, il punteggio è solo un ordinamento descrittivo."
            )
        if t >= T_SIGNIFICANT:
            return (
                f"Relazione positiva statisticamente rilevante (t = {t:.1f}): in passato i "
                "punteggi alti hanno preceduto rendimenti migliori della media dell'universo. "
                "Non include costi e l'universo è scelto oggi (survivorship): il vantaggio "
                "reale è inferiore."
            )
        if t <= -T_SIGNIFICANT:
            return (
                f"Relazione INVERSA statisticamente rilevante (t = {t:.1f}): i punteggi alti "
                "hanno preceduto rendimenti PEGGIORI. Usato così il punteggio è "
                "controproducente; controlla quale fattore la causa."
            )
        return (
            f"Nessun vantaggio misurabile (t = {t:.1f}): il punteggio non ha anticipato i "
            "rendimenti successivi. Usalo solo come ordinamento descrittivo, non come segnale."
        )


def _spearman(a: pd.Series, b: pd.Series) -> float:
    ra, rb = a.rank().to_numpy(), b.rank().to_numpy()
    if np.std(ra) == 0 or np.std(rb) == 0:
        return math.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def _tstat(values: np.ndarray) -> float:
    n = len(values)
    if n < 2:
        return math.nan
    sd = float(np.std(values, ddof=1))
    if sd == 0:
        return math.nan
    return float(np.mean(values)) / sd * math.sqrt(n)


def _stats(name: str, ics: list[float]) -> FactorStats:
    arr = np.array([x for x in ics if not math.isnan(x)])
    if len(arr) == 0:
        return FactorStats(name, 0, math.nan, math.nan, math.nan)
    return FactorStats(name, len(arr), float(arr.mean()), _tstat(arr), float((arr > 0).mean()))


def evaluation_dates(valid_counts: pd.Series, horizon: int) -> list[pd.Timestamp]:
    """Date con abbastanza simboli, a passo ``horizon`` partendo dalla più recente."""
    eligible = valid_counts[valid_counts >= MIN_CROSS_SECTION].index
    return list(eligible[::-1][::horizon][::-1])


def validate(
    panels: dict[str, pd.DataFrame],
    fwd: pd.DataFrame,
    weights: dict[str, float],
    horizon: int,
) -> ValidationResult:
    """``panels``: feature -> DataFrame (date × simboli); ``fwd``: rendimenti futuri in %."""
    active = [name for name, w in weights.items() if w != 0]
    complete = fwd.notna()
    for name in active:
        complete &= panels[name].notna()
    dates = evaluation_dates(complete.sum(axis=1), horizon)

    score_ics: list[float] = []
    factor_ics: dict[str, list[float]] = {name: [] for name in panels}
    sizes: list[int] = []
    rows: list[tuple[pd.Series, pd.Series]] = []
    for date in dates:
        mask = complete.loc[date]
        cross = pd.DataFrame({name: panels[name].loc[date, mask] for name in panels})
        future = fwd.loc[date, mask]
        score = combine_scores(cross, weights)
        score_ics.append(_spearman(score, future))
        for name in panels:
            col = cross[name].dropna()
            factor_ics[name].append(_spearman(col, future[col.index]))
        sizes.append(int(mask.sum()))
        rows.append((score, future))

    n_buckets = 5 if sizes and float(np.median(sizes)) >= 15 else 3
    bucket_sums = np.zeros(n_buckets)
    bucket_counts = np.zeros(n_buckets)
    spreads: list[float] = []
    for score, future in rows:
        pct = (score.rank(method="first") - 0.5) / len(score)
        bucket = np.minimum((pct * n_buckets).astype(int), n_buckets - 1)
        excess = future - future.mean()
        means = excess.groupby(bucket).mean().reindex(range(n_buckets))
        valid = means.notna().to_numpy()
        bucket_sums[valid] += means.to_numpy()[valid]
        bucket_counts[valid] += 1
        if valid[0] and valid[-1]:
            spreads.append(float(means.iat[-1] - means.iat[0]))
    with np.errstate(invalid="ignore", divide="ignore"):
        buckets = tuple(float(x) for x in bucket_sums / bucket_counts)

    spread_arr = np.array(spreads)
    return ValidationResult(
        horizon=horizon,
        periods=len(dates),
        avg_symbols=float(np.mean(sizes)) if sizes else 0.0,
        score=_stats("Punteggio", score_ics),
        factors=tuple(_stats(FEATURE_LABELS[name], factor_ics[name]) for name in panels),
        buckets=buckets,
        spread=float(spread_arr.mean()) if len(spread_arr) else math.nan,
        spread_tstat=_tstat(spread_arr),
        first_date=dates[0] if dates else None,
        last_date=dates[-1] if dates else None,
    )
