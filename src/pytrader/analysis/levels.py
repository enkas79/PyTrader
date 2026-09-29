"""Livelli di supporto/resistenza dal clustering dei pivot confermati.

I massimi e i minimi confluiscono negli stessi cluster: un livello rotto cambia ruolo
(supporto <-> resistenza), quindi il ruolo si decide rispetto al prezzo corrente.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from pytrader.models import Level


@dataclass(frozen=True)
class LevelParams:
    """Parametri del clustering."""

    tolerance_atr: float = 0.5  # ampiezza massima di un cluster in multipli di ATR
    min_touches: int = 2  # pivot minimi per considerare valido un livello
    lookback: int = 300  # candele di storia considerate


def cluster_prices(
    prices: np.ndarray, indices: np.ndarray, tolerance: float, min_touches: int
) -> list[Level]:
    """Clustering 1D greedy: prezzi ordinati, nuovo cluster quando la distanza dal
    minimo del cluster supera ``tolerance`` (ampiezza della fascia <= tolerance)."""
    if len(prices) == 0 or tolerance <= 0:
        return []
    order = np.argsort(prices, kind="mergesort")
    sorted_prices = prices[order]
    sorted_idx = indices[order]

    levels: list[Level] = []
    start = 0
    for i in range(1, len(sorted_prices) + 1):
        if i == len(sorted_prices) or sorted_prices[i] - sorted_prices[start] > tolerance:
            chunk = sorted_prices[start:i]
            if len(chunk) >= min_touches:
                chunk_idx = sorted_idx[start:i]
                lower, upper = float(chunk[0]), float(chunk[-1])
                price = min(max(float(chunk.mean()), lower), upper)
                levels.append(
                    Level(
                        price=price,
                        lower=lower,
                        upper=upper,
                        touches=len(chunk),
                        first_index=int(chunk_idx.min()),
                        last_index=int(chunk_idx.max()),
                    )
                )
            start = i
    return levels


def build_levels(
    pivots: pd.DataFrame, at_index: int, atr_value: float, params: LevelParams
) -> list[Level]:
    """Livelli noti alla chiusura della candela ``at_index``.

    Usa solo pivot con ``confirm_index <= at_index`` e ``index >= at_index - lookback``;
    la tolleranza è ``tolerance_atr × ATR(at_index)``.
    """
    if not np.isfinite(atr_value) or atr_value <= 0:
        return []
    known = pivots[
        (pivots["confirm_index"] <= at_index) & (pivots["index"] >= at_index - params.lookback)
    ]
    return cluster_prices(
        known["price"].to_numpy(dtype=float),
        known["index"].to_numpy(dtype=np.int64),
        params.tolerance_atr * atr_value,
        params.min_touches,
    )
