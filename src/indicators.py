"""Calcolo vettorizzato degli indicatori tecnici con ``pandas_ta``.

Indicatori prodotti (colonne aggiunte al DataFrame OHLCV):

* ``atr``: Average True Range (smoothing RMA di Wilder, come ``pandas_ta``).
* ``dcl`` / ``dcm`` / ``dcu``: canali di Donchian Lower / Middle / Upper.
* ``vwap``: VWAP ancorato alla sessione giornaliera (00:00 UTC).
* ``vwap_upper`` / ``vwap_lower``: VWAP ± k·σ, con σ deviazione standard
  ponderata per volume dei prezzi tipici dall'inizio della sessione.

Nota sulle bande VWAP: ``pandas_ta.vwap(bands=...)`` stima la varianza come
Σ vᵢ·(tpᵢ − VWAPᵢ)² / Σ vᵢ, dove VWAPᵢ è il VWAP *al momento della barra i*
(stima dipendente dal percorso). Qui si usa la varianza ponderata esatta
rispetto al VWAP corrente, σ² = Σ vᵢ·tpᵢ² / Σ vᵢ − VWAP², calcolata in
forma numericamente stabile (centrando sul primo prezzo tipico della sessione).
È la definizione usata dalle piattaforme di charting più diffuse.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pandas_ta as ta

OHLCV_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")


class IndicatorEngine:
    """Calcolatore degli indicatori della strategia.

    Attributes:
        atr_length: Periodi dell'ATR.
        donchian_length: Periodi dei canali di Donchian.
        vwap_band_std: Moltiplicatore k delle bande VWAP ± k·σ.
    """

    def __init__(
        self, atr_length: int = 14, donchian_length: int = 55, vwap_band_std: float = 2.0
    ) -> None:
        """Inizializza il calcolatore.

        Args:
            atr_length: Periodi dell'ATR.
            donchian_length: Periodi dei canali di Donchian.
            vwap_band_std: Moltiplicatore della deviazione standard.

        Raises:
            ValueError: Se un parametro non è positivo.
        """
        if atr_length <= 0 or donchian_length <= 0 or vwap_band_std <= 0:
            raise ValueError("I parametri degli indicatori devono essere positivi")
        self.atr_length = atr_length
        self.donchian_length = donchian_length
        self.vwap_band_std = vwap_band_std

    @property
    def warmup(self) -> int:
        """Numero minimo di candele prima che tutti gli indicatori siano validi."""
        return max(self.atr_length, self.donchian_length) + 1

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        """Calcola tutti gli indicatori.

        Args:
            ohlcv: DataFrame con colonne ``open, high, low, close, volume`` e
                ``DatetimeIndex`` UTC ordinato in modo crescente, contenente
                solo candele chiuse.

        Returns:
            Una copia del DataFrame con le colonne ``atr, dcl, dcm, dcu, vwap,
            vwap_upper, vwap_lower`` aggiunte.

        Raises:
            ValueError: Se mancano colonne, l'indice non è datetime ordinato o
                i dati sono insufficienti per il warm-up.
        """
        self._validate(ohlcv)
        df = ohlcv.copy()
        high, low, close, volume = df["high"], df["low"], df["close"], df["volume"]

        df["atr"] = ta.atr(high, low, close, length=self.atr_length)

        dc = ta.donchian(
            high, low, lower_length=self.donchian_length, upper_length=self.donchian_length
        )
        n = self.donchian_length
        df["dcl"] = dc[f"DCL_{n}_{n}"]
        df["dcm"] = dc[f"DCM_{n}_{n}"]
        df["dcu"] = dc[f"DCU_{n}_{n}"]

        df["vwap"] = ta.vwap(high, low, close, volume, anchor="D")
        sigma = self.session_vwap_std(df)
        df["vwap_upper"] = df["vwap"] + self.vwap_band_std * sigma
        df["vwap_lower"] = df["vwap"] - self.vwap_band_std * sigma
        return df

    @staticmethod
    def session_vwap_std(df: pd.DataFrame) -> pd.Series:
        """Deviazione standard ponderata per volume, ancorata a 00:00 UTC.

        Per ogni barra *t* della sessione *d*:
        σₜ² = Σᵢ≤ₜ vᵢ·xᵢ² / Σᵢ≤ₜ vᵢ − (Σᵢ≤ₜ vᵢ·xᵢ / Σᵢ≤ₜ vᵢ)², con
        xᵢ = tpᵢ − tp₀ (centratura sul primo prezzo tipico della sessione per
        evitare la cancellazione catastrofica in virgola mobile).

        Args:
            df: DataFrame OHLCV con ``DatetimeIndex`` UTC.

        Returns:
            Serie di σ allineata all'indice (NaN finché il volume cumulato è 0).
        """
        tp = (df["high"] + df["low"] + df["close"]) / 3.0
        vol = df["volume"]
        session = pd.DatetimeIndex(df.index).floor("D")
        x = tp - tp.groupby(session).transform("first")
        cum_v = vol.groupby(session).cumsum().replace(0.0, np.nan)
        mean_x = (vol * x).groupby(session).cumsum() / cum_v
        mean_x2 = (vol * x * x).groupby(session).cumsum() / cum_v
        variance = (mean_x2 - mean_x**2).clip(lower=0.0)
        return variance.pow(0.5)

    def _validate(self, ohlcv: pd.DataFrame) -> None:
        """Valida struttura e lunghezza del DataFrame in ingresso.

        Args:
            ohlcv: DataFrame da validare.

        Raises:
            ValueError: Se il DataFrame non è utilizzabile.
        """
        missing = [c for c in OHLCV_COLUMNS if c not in ohlcv.columns]
        if missing:
            raise ValueError(f"Colonne OHLCV mancanti: {missing}")
        if not isinstance(ohlcv.index, pd.DatetimeIndex):
            raise ValueError("L'indice deve essere un DatetimeIndex")
        if str(ohlcv.index.tz) != "UTC":
            raise ValueError("L'indice deve essere in timezone UTC (ancoraggio VWAP 00:00 UTC)")
        if not ohlcv.index.is_monotonic_increasing or ohlcv.index.has_duplicates:
            raise ValueError("L'indice deve essere strettamente crescente")
        if len(ohlcv) < self.warmup + 1:
            raise ValueError(
                f"Dati insufficienti: {len(ohlcv)} candele, servono almeno {self.warmup + 1}"
            )
