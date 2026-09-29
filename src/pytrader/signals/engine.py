"""Generazione dei setup: pattern candlestick in confluenza con livelli S/R.

Regole (tutte valutate alla chiusura della candela del segnale ``i``):
- Pattern rialzista con minimo entro ``proximity_atr × ATR`` da un supporto (livello sotto
  la chiusura); pattern ribassista con massimo vicino a una resistenza (livello sopra).
- Entry all'apertura della candela ``i + 1``; se non esiste ancora, stima = chiusura di ``i``.
- SL oltre la zona (e oltre l'estremo del pattern) di ``sl_buffer_atr × ATR``.
- TP sul livello strutturale successivo; se l'R:R risultante è < ``min_rr`` il setup è
  scartato. Senza livelli oltre l'entry (prezzo in scoperta) si usa ``min_rr × rischio``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from pytrader.analysis import (
    LevelParams,
    PatternParams,
    atr,
    build_levels,
    detect_patterns,
    find_pivots,
    volume_sma,
)
from pytrader.models import Direction, Level, PatternType, TargetSource, TradeSetup


class TargetMode(str, Enum):
    STRUCTURAL = "structural"
    FIXED_RR = "fixed_rr"


@dataclass(frozen=True)
class SignalParams:
    """Parametri del motore di segnali."""

    atr_period: int = 14
    pivot_window: int = 5
    proximity_atr: float = 0.5
    sl_buffer_atr: float = 1.5
    min_rr: float = 2.0
    target_mode: TargetMode = TargetMode.STRUCTURAL
    volume_period: int = 20
    min_volume_ratio: Optional[float] = None  # es. 1.2: volume >= 1.2 × media; None = off
    levels: LevelParams = field(default_factory=LevelParams)
    patterns: PatternParams = field(default_factory=PatternParams)

    def __post_init__(self) -> None:
        if self.min_rr <= 0 or self.sl_buffer_atr < 0 or self.proximity_atr < 0:
            raise ValueError("min_rr > 0, sl_buffer_atr >= 0 e proximity_atr >= 0 richiesti")


@dataclass
class AnalysisResult:
    """Output completo dell'analisi, consumato da GUI, backtest ed export."""

    data: pd.DataFrame
    atr: pd.Series
    volume_avg: pd.Series
    pivots: pd.DataFrame
    patterns: pd.DataFrame
    levels: list[Level]  # livelli noti all'ultima candela chiusa
    setups: list[TradeSetup]


class SignalEngine:
    """Calcola indicatori, livelli, pattern e setup su una serie validata."""

    def __init__(self, params: Optional[SignalParams] = None) -> None:
        self.params = params or SignalParams()

    def analyze(self, df: pd.DataFrame) -> AnalysisResult:
        p = self.params
        atr_s = atr(df, p.atr_period)
        vol_s = volume_sma(df, p.volume_period)
        pivots = find_pivots(df, p.pivot_window)
        flags = detect_patterns(df, p.patterns)

        setups: list[TradeSetup] = []
        directional = sorted(
            (pt for pt in PatternType if pt.bias is not None), key=lambda pt: -pt.strength
        )
        flag_matrix = flags[[pt.value for pt in directional]].to_numpy()
        candidate_rows = np.flatnonzero(flag_matrix.any(axis=1))

        atr_v = atr_s.to_numpy()
        vol_v = df["volume"].to_numpy()
        vol_avg_v = vol_s.to_numpy()
        for i in candidate_rows:
            if not np.isfinite(atr_v[i]):
                continue
            if p.min_volume_ratio is not None and not (
                np.isfinite(vol_avg_v[i]) and vol_v[i] >= p.min_volume_ratio * vol_avg_v[i]
            ):
                continue
            levels = build_levels(pivots, int(i), float(atr_v[i]), p.levels)
            if not levels:
                continue
            for col, ptype in enumerate(directional):
                if not flag_matrix[i, col]:
                    continue
                setup = self._build_setup(df, int(i), ptype, levels, float(atr_v[i]))
                if setup is not None:
                    setups.append(setup)
                    break  # un solo setup per candela: vince il pattern più forte

        last = len(df) - 1
        final_levels = build_levels(pivots, last, float(atr_v[last]), p.levels) if last >= 0 else []
        return AnalysisResult(
            data=df,
            atr=atr_s,
            volume_avg=vol_s,
            pivots=pivots,
            patterns=flags,
            levels=final_levels,
            setups=setups,
        )

    def _build_setup(
        self, df: pd.DataFrame, i: int, ptype: PatternType, levels: list[Level], atr_value: float
    ) -> Optional[TradeSetup]:
        p = self.params
        direction = ptype.bias
        assert direction is not None
        start = max(0, i - ptype.n_bars + 1)
        close_i = float(df["close"].iat[i])
        pat_low = float(df["low"].iloc[start : i + 1].min())
        pat_high = float(df["high"].iloc[start : i + 1].max())
        max_dist = p.proximity_atr * atr_value

        if direction is Direction.LONG:
            zones = [
                lv for lv in levels if lv.price <= close_i and lv.distance(pat_low) <= max_dist
            ]
            anchor = pat_low
        else:
            zones = [
                lv for lv in levels if lv.price >= close_i and lv.distance(pat_high) <= max_dist
            ]
            anchor = pat_high
        if not zones:
            return None
        zone = min(zones, key=lambda lv: (lv.distance(anchor), -lv.touches))

        has_next = i + 1 < len(df)
        entry = float(df["open"].iat[i + 1]) if has_next else close_i
        buffer = p.sl_buffer_atr * atr_value
        if direction is Direction.LONG:
            stop = min(zone.lower, pat_low) - buffer
        else:
            stop = max(zone.upper, pat_high) + buffer
        risk = entry - stop if direction is Direction.LONG else stop - entry
        if risk <= 0:
            return None  # l'apertura successiva ha già superato lo stop

        target, source = self._target(direction, entry, risk, zone, levels)
        if target is None:
            return None
        rr = abs(target - entry) / risk
        if rr + 1e-9 < p.min_rr:
            return None
        try:
            return TradeSetup(
                signal_index=i,
                signal_time=df.index[i],
                direction=direction,
                pattern=ptype,
                level=zone,
                entry=entry,
                stop_loss=stop,
                take_profit=target,
                atr=atr_value,
                target_source=source,
                entry_is_estimate=not has_next,
            )
        except ValueError:
            return None

    def _target(
        self, direction: Direction, entry: float, risk: float, zone: Level, levels: list[Level]
    ) -> tuple[Optional[float], TargetSource]:
        p = self.params
        fixed = entry + p.min_rr * risk if direction is Direction.LONG else entry - p.min_rr * risk
        if p.target_mode is TargetMode.FIXED_RR:
            return fixed, TargetSource.FIXED_RR
        if direction is Direction.LONG:
            above = [lv.lower for lv in levels if lv is not zone and lv.lower > entry]
            if above:
                return min(above), TargetSource.STRUCTURAL
        else:
            below = [lv.upper for lv in levels if lv is not zone and lv.upper < entry]
            if below:
                return max(below), TargetSource.STRUCTURAL
        # Nessuna struttura oltre l'entry: target a R:R minimo
        return fixed, TargetSource.FIXED_RR
