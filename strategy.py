"""Strategia breakout Donchian(55) con filtro VWAP giornaliero ± 2σ (15m).

Regole (valutate solo su candele chiuse):

Long
    * Trigger: ``close > dcu.shift(1)`` (rottura del massimo a 55 barre
      precedente, esclusa la candela corrente).
    * Filtro: ``close < vwap_upper`` (niente acquisti in iper-estensione).
    * SL = entry − 2·ATR, TP = entry + 5·ATR (R:R 1:2.5).

Short
    * Trigger: ``close < dcl.shift(1)``.
    * Filtro: ``close > vwap_lower``.
    * SL = entry + 2·ATR, TP = entry − 5·ATR.

L'ATR usato per i livelli è quello della candela di segnale; i livelli sono
poi ancorati al prezzo di esecuzione reale (vedi :meth:`Strategy.levels`),
così la distanza dello stop resta esattamente 2·ATR e il rischio resta quello
calcolato dal position sizing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

import pandas as pd

from config import StrategyConfig
from indicators import IndicatorEngine


class Side(StrEnum):
    """Direzione del trade."""

    LONG = "long"
    SHORT = "short"

    @property
    def entry_order_side(self) -> str:
        """Lato ccxt dell'ordine di ingresso (``buy``/``sell``)."""
        return "buy" if self is Side.LONG else "sell"

    @property
    def exit_order_side(self) -> str:
        """Lato ccxt dell'ordine di uscita (``sell``/``buy``)."""
        return "sell" if self is Side.LONG else "buy"


@dataclass(frozen=True, slots=True)
class Signal:
    """Segnale operativo generato sulla candela chiusa più recente.

    Attributes:
        side: Direzione.
        candle_ts: Timestamp (ms) di apertura della candela di segnale.
        close: Prezzo di chiusura della candela di segnale.
        atr: ATR della candela di segnale.
        stop_distance: Distanza dello stop (sl_atr_mult · ATR).
        stop_loss: SL teorico calcolato su ``close``.
        take_profit: TP teorico calcolato su ``close``.
    """

    side: Side
    candle_ts: int
    close: float
    atr: float
    stop_distance: float
    stop_loss: float
    take_profit: float


class Strategy:
    """Generatore di segnali vettorizzato.

    Attributes:
        config: Parametri della strategia.
        indicators: Motore di calcolo degli indicatori.
    """

    def __init__(self, config: StrategyConfig, indicators: IndicatorEngine | None = None) -> None:
        """Inizializza la strategia.

        Args:
            config: Parametri della strategia.
            indicators: Motore indicatori; se ``None`` viene creato dai parametri.
        """
        self.config = config
        self.indicators = indicators or IndicatorEngine(
            atr_length=config.atr_length,
            donchian_length=config.donchian_length,
            vwap_band_std=config.vwap_band_std,
        )

    def generate_signals(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        """Calcola indicatori e vettori booleani di segnale su tutto lo storico.

        Args:
            ohlcv: DataFrame OHLCV (solo candele chiuse, indice UTC).

        Returns:
            DataFrame con indicatori e colonne ``long_signal``/``short_signal``.
            I confronti con NaN (warm-up) valgono ``False``.
        """
        df = self.indicators.compute(ohlcv)
        close = df["close"]
        long_trigger = close > df["dcu"].shift(1)
        long_filter = close < df["vwap_upper"]
        short_trigger = close < df["dcl"].shift(1)
        short_filter = close > df["vwap_lower"]

        df["long_signal"] = (long_trigger & long_filter).fillna(False).astype(bool)
        df["short_signal"] = (short_trigger & short_filter).fillna(False).astype(bool)
        if not self.config.allow_short:
            df["short_signal"] = False
        return df

    def evaluate(self, ohlcv: pd.DataFrame) -> Signal | None:
        """Valuta l'ultima candela chiusa.

        Args:
            ohlcv: DataFrame OHLCV (solo candele chiuse, indice UTC).

        Returns:
            Un :class:`Signal` se l'ultima candela genera un ingresso, altrimenti
            ``None`` (anche se l'ATR non è ancora valido).
        """
        df = self.generate_signals(ohlcv)
        last = df.iloc[-1]
        atr = float(last["atr"])
        if not math.isfinite(atr) or atr <= 0:
            return None

        if bool(last["long_signal"]):
            side = Side.LONG
        elif bool(last["short_signal"]):
            side = Side.SHORT
        else:
            return None

        close = float(last["close"])
        stop_loss, take_profit = self.levels(side, close, atr)
        return Signal(
            side=side,
            candle_ts=int(df.index[-1].timestamp() * 1000),
            close=close,
            atr=atr,
            stop_distance=self.config.sl_atr_mult * atr,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    def levels(self, side: Side, entry_price: float, atr: float) -> tuple[float, float]:
        """Calcola stop loss e take profit a partire dal prezzo di ingresso.

        Args:
            side: Direzione del trade.
            entry_price: Prezzo di ingresso (effettivo o teorico).
            atr: ATR della candela di segnale.

        Returns:
            Tupla ``(stop_loss, take_profit)``.
        """
        sl_dist = self.config.sl_atr_mult * atr
        tp_dist = self.config.tp_atr_mult * atr
        if side is Side.LONG:
            return entry_price - sl_dist, entry_price + tp_dist
        return entry_price + sl_dist, entry_price - tp_dist
