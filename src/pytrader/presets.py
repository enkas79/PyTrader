"""Valori consigliati per famiglia di asset (crypto, azioni, ETF/indici, forex, materie prime).

I valori NON sono ottimizzati sui rendimenti passati (sarebbe overfitting): derivano dalla
struttura del mercato.

- **Costi**: commissione tipica per lato più metà dello spread, perché lo slippage non è
  simulato.
- **Leva**: i limiti ESMA per i clienti al dettaglio sui CFD (30× forex principali, 20× oro e
  indici principali, 10× altre materie prime, 5× azioni, 2× crypto). Per azioni, ETF e crypto
  il valore consigliato è 1×, cioè acquisto a pronti senza leva.
- **Calendario**: ore e giorni di contrattazione, per convertire mesi e settimane in candele.
- **Volume**: il forex non ha volume centralizzato, quindi lo screener lo esclude.
- **R:R minimo**: calcolato dal costo di un trade espresso in R sulla serie caricata, così che
  il rapporto netto dopo i costi resti almeno 2:1.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from pytrader.analysis import atr
from pytrader.data import parse_timeframe
from pytrader.screener import MIN_PERIODS, ScreenerParams
from pytrader.services import SourceKind
from pytrader.signals import SignalParams

TARGET_NET_RR = 2.0  # rapporto rendimento/rischio desiderato al netto dei costi
MAX_MIN_RR = 6.0
MAX_SCREENER_BARS = 5000
ATR_SAMPLE = 500  # candele recenti usate per stimare l'ATR% tipico


class AssetFamily(str, Enum):
    CRYPTO = "crypto"
    STOCK = "stock"
    ETF_INDEX = "etf_index"
    FOREX = "forex"
    COMMODITY = "commodity"


@dataclass(frozen=True)
class FamilyProfile:
    label: str
    examples: str
    fee_pct: float  # commissione per lato in %, metà spread inclusa
    fee_note: str
    max_leverage: float
    leverage_note: str
    session_hours: float  # ore di contrattazione al giorno
    days_per_week: int
    has_volume: bool


PROFILES: dict[AssetFamily, FamilyProfile] = {
    AssetFamily.CRYPTO: FamilyProfile(
        label="Crypto",
        examples="BTC/USDT, ETH/USDT, BTC-USD",
        fee_pct=0.10,
        fee_note="commissione taker spot tipica degli exchange (0,10 %); più bassa come maker",
        max_leverage=1.0,
        leverage_note="acquisto a pronti; i derivati crypto per i clienti retail UE sono "
        "limitati a 2×",
        session_hours=24,
        days_per_week=7,
        has_volume=True,
    ),
    AssetFamily.STOCK: FamilyProfile(
        label="Azioni",
        examples="AAPL, ENI.MI, SAP.DE",
        fee_pct=0.10,
        fee_note="commissioni retail tipiche 0,05-0,19 % più metà spread; usa quelle del tuo "
        "broker",
        max_leverage=1.0,
        leverage_note="acquisto a pronti; i CFD su azioni arrivano al massimo a 5× (limite ESMA)",
        session_hours=8,
        days_per_week=5,
        has_volume=True,
    ),
    AssetFamily.ETF_INDEX: FamilyProfile(
        label="ETF e indici",
        examples="VWCE.DE, SPY, ^GSPC, FTSEMIB.MI",
        fee_pct=0.05,
        fee_note="ETF molto liquidi e CFD/futures sugli indici: spread stretto",
        max_leverage=1.0,
        leverage_note="ETF a pronti; i CFD sugli indici principali arrivano a 20× (limite ESMA)",
        session_hours=8,
        days_per_week=5,
        has_volume=True,
    ),
    AssetFamily.FOREX: FamilyProfile(
        label="Forex",
        examples="EURUSD=X, GBPUSD=X, USDJPY=X",
        fee_pct=0.01,
        fee_note="spread di circa 1 pip sulle coppie principali (≈ 0,01 % per lato)",
        max_leverage=30.0,
        leverage_note="limite ESMA per le coppie principali; 20× per le altre",
        session_hours=24,
        days_per_week=5,
        has_volume=False,
    ),
    AssetFamily.COMMODITY: FamilyProfile(
        label="Materie prime",
        examples="GC=F (oro), CL=F (petrolio), SI=F (argento)",
        fee_pct=0.03,
        fee_note="futures e CFD su oro e petrolio: spread tipico 0,02-0,05 %",
        max_leverage=10.0,
        leverage_note="limite ESMA per le materie prime diverse dall'oro (oro: 20×)",
        session_hours=23,
        days_per_week=5,
        has_volume=True,
    ),
}

_YAHOO_CRYPTO = re.compile(r"^[A-Z0-9]{2,10}-(USD|USDT|EUR|GBP|BTC|ETH)$")


def detect_family(kind: SourceKind, symbol: str) -> AssetFamily:
    """Famiglia probabile dal simbolo. Gli ETF non sono riconoscibili dal ticker: risultano
    azioni e vanno scelti a mano."""
    s = symbol.strip().upper()
    if kind is SourceKind.CCXT or _YAHOO_CRYPTO.match(s):
        return AssetFamily.CRYPTO
    if s.endswith("=X"):
        return AssetFamily.FOREX
    if s.endswith("=F"):
        return AssetFamily.COMMODITY
    if s.startswith("^"):
        return AssetFamily.ETF_INDEX
    return AssetFamily.STOCK


# ------------------------------------------------------------------ calendario
def bars_per_day(family: AssetFamily, timeframe: str) -> float:
    """Candele per giorno di contrattazione (frazione per i timeframe oltre il giorno)."""
    profile = PROFILES[family]
    tf = parse_timeframe(timeframe)
    if tf >= pd.Timedelta(days=1):
        days = tf / pd.Timedelta(days=1)
        if days >= 7:  # settimanale: una candela ogni ``days_per_week`` giorni di borsa
            return 7.0 / (days * profile.days_per_week)
        return 1.0 / days  # le candele giornaliere esistono solo nei giorni di borsa
    hours = tf / pd.Timedelta(hours=1)
    return float(math.ceil(profile.session_hours / hours))


def bars_for_days(family: AssetFamily, timeframe: str, trading_days: float) -> int:
    return max(1, round(trading_days * bars_per_day(family, timeframe)))


def trading_days_per_month(family: AssetFamily) -> float:
    return 30.4 if PROFILES[family].days_per_week == 7 else 21.0


# ------------------------------------------------------------------ risultati
@dataclass(frozen=True)
class Suggestion:
    """Una riga dell'anteprima: parametro, valore proposto e motivazione."""

    name: str
    value: str
    reason: str


@dataclass(frozen=True)
class AnalysisDefaults:
    family: AssetFamily
    signal: SignalParams
    fee_pct: float
    max_leverage: float
    cost_r: Optional[float]  # costo di un trade in R stimato sulla serie (None senza dati)
    notes: tuple[Suggestion, ...]


@dataclass(frozen=True)
class ScreenerDefaults:
    family: AssetFamily
    params: ScreenerParams
    bars: int
    notes: tuple[Suggestion, ...]


def _it(value: float, decimals: int = 2) -> str:
    return f"{value:.{decimals}f}".replace(".", ",")


def typical_atr_pct(frame: pd.DataFrame, period: int) -> Optional[float]:
    """Mediana dell'ATR in % del prezzo sulle ultime ``ATR_SAMPLE`` candele."""
    if len(frame) <= period:
        return None
    pct = (atr(frame, period) / frame["close"] * 100).iloc[-ATR_SAMPLE:].dropna()
    pct = pct[np.isfinite(pct) & (pct > 0)]
    return float(pct.median()) if len(pct) else None


def estimate_cost_r(fee_pct: float, atr_pct: float, sl_buffer_atr: float) -> float:
    """Costo di entrata più uscita in multipli del rischio.

    Lo stop dista in media circa ``buffer + 0,5`` ATR dall'entry (buffer oltre la fascia più
    metà della fascia), quindi il rischio vale circa ``(buffer + 0,5) × ATR%`` del prezzo.
    """
    stop_pct = (sl_buffer_atr + 0.5) * atr_pct
    return 2.0 * fee_pct / stop_pct if stop_pct > 0 else math.inf


def min_rr_for_costs(cost_r: float) -> float:
    """R:R lordo che lascia almeno ``TARGET_NET_RR`` al netto: (rr - c) / (1 + c) >= 2,
    cioè rr >= 2 + 3c, arrotondato al quarto superiore."""
    raw = TARGET_NET_RR + (TARGET_NET_RR + 1.0) * cost_r
    return min(MAX_MIN_RR, math.ceil(raw * 4 - 1e-9) / 4)


def analysis_defaults(
    family: AssetFamily, timeframe: str, frame: Optional[pd.DataFrame] = None
) -> AnalysisDefaults:
    """Parametri di analisi, commissione e leva consigliati per la famiglia."""
    profile = PROFILES[family]
    base = SignalParams()
    notes = [
        Suggestion(
            "Parametri tecnici",
            "predefiniti",
            "ATR, pivot, tolleranza, prossimità e buffer sono già in multipli di ATR: si "
            "adattano da soli alla volatilità di ogni mercato",
        ),
        Suggestion("Commissione/lato", f"{_it(profile.fee_pct, 3)} %", profile.fee_note),
        Suggestion("Leva massima", f"{_it(profile.max_leverage, 0)}×", profile.leverage_note),
    ]
    cost_r: Optional[float] = None
    atr_pct = typical_atr_pct(frame, base.atr_period) if frame is not None else None
    if atr_pct is not None:
        cost_r = estimate_cost_r(profile.fee_pct, atr_pct, base.sl_buffer_atr)
        min_rr = min_rr_for_costs(cost_r)
        reason = (
            f"ATR tipico {_it(atr_pct)} % del prezzo: un trade costa circa {_it(cost_r)} R; "
            f"con {_it(min_rr)} lordo il rapporto netto resta almeno {_it(TARGET_NET_RR, 0)}:1"
        )
        if min_rr >= MAX_MIN_RR:
            reason += ". Costi troppo alti per questo timeframe: usane uno più lungo"
        base = replace(base, min_rr=min_rr)
    else:
        reason = (
            "nessuna serie caricata: carica i dati per calcolarlo dai costi in R "
            f"(predefinito {_it(base.min_rr)})"
        )
    notes.append(Suggestion("R:R minimo", _it(base.min_rr), reason))
    return AnalysisDefaults(
        family=family,
        signal=base,
        fee_pct=profile.fee_pct,
        max_leverage=profile.max_leverage,
        cost_r=cost_r,
        notes=tuple(notes),
    )


def screener_defaults(family: AssetFamily, timeframe: str) -> ScreenerDefaults:
    """Periodi dello screener convertiti da tempo di calendario a candele.

    Volume medio su 1 mese, momentum su 6 mesi escludendo l'ultima settimana, verifica a
    1 mese. Se la storia necessaria supera il massimo scaricabile (timeframe intraday), tutti i
    periodi vengono ridotti in proporzione.
    """
    profile = PROFILES[family]
    month = trading_days_per_month(family)
    week = float(profile.days_per_week)
    spans = {
        "volume_window": bars_for_days(family, timeframe, month),
        "momentum_bars": bars_for_days(family, timeframe, 6 * month),
        "skip_bars": bars_for_days(family, timeframe, week),
        "horizon": bars_for_days(family, timeframe, month),
    }
    reserve = MIN_PERIODS + 5  # periodi di verifica con un margine

    def needed(s: dict[str, int]) -> int:
        return s["momentum_bars"] + 1 + s["horizon"] * reserve

    scaled = needed(spans) > MAX_SCREENER_BARS
    if scaled:
        factor = MAX_SCREENER_BARS / needed(spans)
        spans = {k: max(1, int(v * factor)) for k, v in spans.items()}
    spans["volume_window"] = max(2, spans["volume_window"])
    spans["momentum_bars"] = max(spans["skip_bars"] + 2, spans["momentum_bars"])
    params = ScreenerParams(
        volume_window=spans["volume_window"],
        momentum_bars=spans["momentum_bars"],
        skip_bars=spans["skip_bars"],
        horizon=spans["horizon"],
        volume_weight=50.0 if profile.has_volume else 0.0,
        momentum_weight=50.0,
    )
    bars = min(MAX_SCREENER_BARS, max(1000, needed(spans)))
    per_day = bars_per_day(family, timeframe)
    calendar = (
        f"{_it(profile.session_hours, 0)} h × {profile.days_per_week} giorni a settimana"
        f" → {_it(per_day, 2)} candele {timeframe} per giorno di borsa"
    )
    notes = [
        Suggestion("Calendario", profile.label, calendar),
        Suggestion("Finestra volume", str(params.volume_window), "circa 1 mese di candele"),
        Suggestion(
            "Periodo momentum",
            str(params.momentum_bars),
            "circa 6 mesi: il momentum documentato in letteratura opera tra 3 e 12 mesi",
        ),
        Suggestion(
            "Candele escluse",
            str(params.skip_bars),
            "circa 1 settimana: sul brevissimo i prezzi tendono a invertire",
        ),
        Suggestion("Orizzonte verifica", str(params.horizon), "circa 1 mese"),
        Suggestion(
            "Peso volume",
            _it(params.volume_weight, 0),
            "attivo" if profile.has_volume else "0: il forex non ha volume centralizzato",
        ),
        Suggestion(
            "Candele",
            str(bars),
            f"storia per il momentum più almeno {reserve} periodi di verifica",
        ),
    ]
    if scaled:
        notes.append(
            Suggestion(
                "Attenzione",
                "periodi ridotti",
                f"con {timeframe} servirebbero più di {MAX_SCREENER_BARS} candele: periodi "
                "accorciati in proporzione. Per lo screener preferisci 1d",
            )
        )
    return ScreenerDefaults(family=family, params=params, bars=bars, notes=tuple(notes))
