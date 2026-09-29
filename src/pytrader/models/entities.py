"""Entità del dominio: livelli, pattern, setup di trade, report di validazione."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional

import pandas as pd


class Direction(str, Enum):
    """Direzione di un segnale o di un trade."""

    LONG = "long"
    SHORT = "short"


class PatternType(str, Enum):
    """Pattern candlestick supportati."""

    HAMMER = "hammer"  # pin bar rialzista
    SHOOTING_STAR = "shooting_star"  # pin bar ribassista
    BULLISH_ENGULFING = "bullish_engulfing"
    BEARISH_ENGULFING = "bearish_engulfing"
    MORNING_STAR = "morning_star"
    EVENING_STAR = "evening_star"
    DOJI = "doji"

    @property
    def bias(self) -> Optional[Direction]:
        """Direzione implicita del pattern; ``None`` per i pattern neutri (doji)."""
        return _PATTERN_BIAS[self]

    @property
    def n_bars(self) -> int:
        """Numero di candele che compongono il pattern."""
        return _PATTERN_BARS[self]

    @property
    def strength(self) -> int:
        """Priorità quando più pattern coincidono sulla stessa candela (più alto = più forte)."""
        return _PATTERN_BARS[self]


_PATTERN_BIAS: dict[PatternType, Optional[Direction]] = {
    PatternType.HAMMER: Direction.LONG,
    PatternType.SHOOTING_STAR: Direction.SHORT,
    PatternType.BULLISH_ENGULFING: Direction.LONG,
    PatternType.BEARISH_ENGULFING: Direction.SHORT,
    PatternType.MORNING_STAR: Direction.LONG,
    PatternType.EVENING_STAR: Direction.SHORT,
    PatternType.DOJI: None,
}

_PATTERN_BARS: dict[PatternType, int] = {
    PatternType.HAMMER: 1,
    PatternType.SHOOTING_STAR: 1,
    PatternType.BULLISH_ENGULFING: 2,
    PatternType.BEARISH_ENGULFING: 2,
    PatternType.MORNING_STAR: 3,
    PatternType.EVENING_STAR: 3,
    PatternType.DOJI: 1,
}


class TargetSource(str, Enum):
    """Origine del take profit."""

    STRUCTURAL = "structural"  # livello S/R successivo
    FIXED_RR = "fixed_rr"  # multiplo del rischio


@dataclass(frozen=True)
class Level:
    """Zona di supporto/resistenza ottenuta dal clustering dei pivot confermati."""

    price: float  # prezzo rappresentativo (media dei pivot)
    lower: float  # limite inferiore della fascia
    upper: float  # limite superiore della fascia
    touches: int  # numero di pivot nel cluster
    first_index: int  # posizione del primo pivot
    last_index: int  # posizione dell'ultimo pivot

    def __post_init__(self) -> None:
        if not self.lower <= self.price <= self.upper:
            raise ValueError(f"Livello incoerente: {self.lower} <= {self.price} <= {self.upper}")
        if self.touches < 1:
            raise ValueError("Un livello richiede almeno un tocco")

    def distance(self, price: float) -> float:
        """Distanza assoluta di ``price`` dalla fascia (0 se interno)."""
        if price < self.lower:
            return self.lower - price
        if price > self.upper:
            return price - self.upper
        return 0.0


@dataclass(frozen=True)
class PatternHit:
    """Occorrenza di un pattern sulla candela chiusa ``index``."""

    index: int
    timestamp: pd.Timestamp
    pattern: PatternType

    @property
    def direction(self) -> Optional[Direction]:
        return self.pattern.bias


@dataclass(frozen=True)
class TradeSetup:
    """Setup operativo con livelli di prezzo e metrica rischio/rendimento."""

    signal_index: int  # candela chiusa che genera il segnale
    signal_time: pd.Timestamp
    direction: Direction
    pattern: PatternType
    level: Level  # zona chiave su cui avviene la confluenza
    entry: float
    stop_loss: float
    take_profit: float
    atr: float
    target_source: TargetSource
    entry_is_estimate: bool = False  # True se la candela di ingresso non esiste ancora
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if self.direction is Direction.LONG:
            ok = self.stop_loss < self.entry < self.take_profit
        else:
            ok = self.take_profit < self.entry < self.stop_loss
        if not ok:
            raise ValueError(
                f"Livelli incoerenti per {self.direction.value}: "
                f"SL={self.stop_loss} entry={self.entry} TP={self.take_profit}"
            )

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop_loss)

    @property
    def reward(self) -> float:
        return abs(self.take_profit - self.entry)

    @property
    def risk_reward(self) -> float:
        return self.reward / self.risk

    def to_dict(self) -> dict[str, Any]:
        """Rappresentazione serializzabile in JSON."""
        return {
            "signal_time": self.signal_time.isoformat(),
            "direction": self.direction.value,
            "pattern": self.pattern.value,
            "level": asdict(self.level),
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "risk_reward": round(self.risk_reward, 4),
            "atr": self.atr,
            "target_source": self.target_source.value,
            "entry_is_estimate": self.entry_is_estimate,
        }


@dataclass(frozen=True)
class Gap:
    """Buco nella serie temporale (candele mancanti tra ``start`` ed ``end``)."""

    start: pd.Timestamp  # ultima candela presente prima del buco
    end: pd.Timestamp  # prima candela presente dopo il buco
    missing_bars: int


@dataclass
class ValidationReport:
    """Esito della validazione di una serie OHLCV."""

    rows_in: int = 0
    rows_out: int = 0
    duplicates_removed: int = 0
    nan_rows_removed: int = 0
    inconsistent_rows_removed: int = 0
    gaps: list[Gap] = field(default_factory=list)
    volume_available: bool = True
    timeframe: Optional[pd.Timedelta] = None
    warnings: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return (
            self.duplicates_removed == 0
            and self.nan_rows_removed == 0
            and self.inconsistent_rows_removed == 0
            and not self.gaps
        )

    def summary(self) -> str:
        parts = [f"{self.rows_out}/{self.rows_in} candele valide"]
        if self.duplicates_removed:
            parts.append(f"{self.duplicates_removed} duplicate rimosse")
        if self.nan_rows_removed:
            parts.append(f"{self.nan_rows_removed} con valori mancanti rimosse")
        if self.inconsistent_rows_removed:
            parts.append(f"{self.inconsistent_rows_removed} incoerenti rimosse")
        if self.gaps:
            missing = sum(g.missing_bars for g in self.gaps)
            parts.append(f"{len(self.gaps)} buchi ({missing} candele mancanti)")
        if not self.volume_available:
            parts.append("volume non disponibile")
        return ", ".join(parts)
