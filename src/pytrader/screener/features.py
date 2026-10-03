"""Feature OHLCV per lo screener e punteggio cross-sezionale.

Tutte le feature alla posizione ``i`` usano solo candele ``<= i``. Il punteggio confronta i
simboli tra loro (percentili) invece di usare soglie fisse: non dipende dalla scala dei valori
e tratta allo stesso modo valori positivi e negativi.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import pandas as pd

from pytrader.analysis import atr

VOLUME = "volume"
MOMENTUM = "momentum"
FEATURE_LABELS: dict[str, str] = {
    VOLUME: "Volume relativo",
    MOMENTUM: "Momentum",
}


@dataclass(frozen=True)
class ScreenerParams:
    """Parametri in numero di candele: il loro significato dipende dal timeframe."""

    volume_window: int = 20  # media del volume delle candele precedenti
    momentum_bars: int = 126  # ~6 mesi su timeframe giornaliero
    skip_bars: int = 5  # candele recenti escluse dal momentum (reversal di breve)
    atr_period: int = 14
    horizon: int = 20  # candele del rendimento futuro usato nella verifica
    volume_weight: float = 50.0  # pesi in [-100, 100]: negativo = ordine invertito
    momentum_weight: float = 50.0

    def __post_init__(self) -> None:
        if self.volume_window < 2:
            raise ValueError("La finestra del volume deve essere >= 2")
        if self.momentum_bars < 2:
            raise ValueError("Il periodo del momentum deve essere >= 2")
        if not 0 <= self.skip_bars < self.momentum_bars:
            raise ValueError("Le candele escluse devono essere meno del periodo del momentum")
        if self.atr_period < 1:
            raise ValueError("Il periodo ATR deve essere >= 1")
        if self.horizon < 1:
            raise ValueError("L'orizzonte di verifica deve essere >= 1")
        if self.volume_weight == 0 and self.momentum_weight == 0:
            raise ValueError("Almeno un peso deve essere diverso da zero")

    @property
    def weights(self) -> dict[str, float]:
        return {VOLUME: self.volume_weight, MOMENTUM: self.momentum_weight}

    @property
    def warmup_bars(self) -> int:
        """Candele necessarie prima che tutte le feature siano definite."""
        return max(self.volume_window + 1, self.momentum_bars + 1, self.atr_period)


def compute_features(df: pd.DataFrame, params: ScreenerParams) -> pd.DataFrame:
    """Feature per candela di un singolo simbolo, più il rendimento futuro per la verifica.

    - ``volume``: volume / media delle ``volume_window`` candele *precedenti* (NaN se la media
      è nulla, es. forex senza volume);
    - ``momentum``: variazione % da ``momentum_bars`` a ``skip_bars`` candele fa;
    - ``atr_pct``: ATR in % del prezzo (solo informativo, non entra nel punteggio);
    - ``fwd_return``: da apertura della candela successiva a chiusura dopo ``horizon`` candele.
      È l'unica colonna che guarda al futuro e serve solo alla verifica storica.
    """
    close = df["close"]
    prev_mean = df["volume"].shift(1).rolling(params.volume_window).mean()
    rel_volume = df["volume"] / prev_mean.where(prev_mean > 0)
    start = close.shift(params.momentum_bars)
    momentum = close.shift(params.skip_bars) / start.where(start > 0) - 1.0
    atr_pct = atr(df, params.atr_period) / close * 100.0
    entry = df["open"].shift(-1)
    fwd = close.shift(-params.horizon) / entry.where(entry > 0) - 1.0
    out = pd.DataFrame(
        {
            VOLUME: rel_volume,
            MOMENTUM: momentum * 100.0,
            "atr_pct": atr_pct,
            "fwd_return": fwd * 100.0,
        },
        index=df.index,
    )
    return out.replace([np.inf, -np.inf], np.nan)


def percentile_ranks(values: pd.Series) -> pd.Series:
    """Percentile in (0, 1) dei valori non nulli: (rango - 0,5) / n, simmetrico attorno a 0,5."""
    valid = values.dropna()
    if valid.empty:
        return valid.astype(float)
    return (valid.rank(method="average") - 0.5) / len(valid)


def combine_scores(features: pd.DataFrame, weights: Mapping[str, float]) -> pd.Series:
    """Punteggio 0-100 per riga (simbolo): media pesata dei percentili, 50 = neutro.

    Un peso negativo inverte l'ordine della feature. Le righe senza tutte le feature con peso
    non nullo restano escluse (NaN): niente punteggi calcolati su dati parziali.
    """
    active = {name: w for name, w in weights.items() if w != 0}
    total = sum(abs(w) for w in active.values())
    if not active:
        raise ValueError("Almeno un peso deve essere diverso da zero")
    complete = features[list(active)].dropna()
    score = pd.Series(0.0, index=complete.index)
    for name, w in active.items():
        score += w * (percentile_ranks(complete[name]) - 0.5)
    return (50.0 + 100.0 * score / total).reindex(features.index)
