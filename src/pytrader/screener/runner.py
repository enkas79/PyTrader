"""Esecuzione dello screener: download dell'universo, classifica attuale e verifica storica."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from pytrader.data import DataSource, parse_timeframe
from pytrader.screener.features import (
    FEATURE_LABELS,
    ScreenerParams,
    combine_scores,
    compute_features,
)
from pytrader.screener.validation import ValidationResult, validate
from pytrader.services import DataRequest, LoadedData, SourceKind, load_data, make_source

Loader = Callable[..., LoadedData]
Progress = Callable[[int], None]
Cancel = Callable[[], bool]

MAX_SYMBOLS = 200
FAIL_FAST = 3  # se i primi simboli falliscono tutti, sorgente o rete sono il problema

# Universi di partenza modificabili. Sono scelti oggi: la verifica storica soffre di
# survivorship bias (mancano i titoli falliti o usciti dagli indici).
_CRYPTO = (
    "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "TRX", "AVAX", "LINK", "DOT", "LTC", "BCH",
    "XLM", "ATOM", "ETC", "FIL", "NEAR", "UNI", "AAVE",
)  # fmt: skip
_US = (
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "JPM", "V", "UNH", "XOM",
    "JNJ", "WMT", "PG", "MA", "HD", "CVX", "KO", "PEP", "ABBV", "MRK", "COST", "AVGO", "ORCL",
    "BAC", "CSCO", "MCD", "DIS", "INTC",
)  # fmt: skip
_MIB = (
    "ENI", "ENEL", "ISP", "UCG", "STLAM", "G", "RACE", "STMMI", "TIT", "PST", "LDO", "PRY", "TEN",
    "SRG", "TRN", "MONC", "BAMI", "BPE", "BMPS", "FBK", "A2A", "HER", "IG", "NEXI", "REC", "DIA",
    "AMP", "INW", "UNI", "CPR",
)  # fmt: skip
PRESETS: dict[str, tuple[SourceKind, tuple[str, ...]]] = {
    "Crypto principali (Binance, USDT)": (SourceKind.CCXT, tuple(f"{b}/USDT" for b in _CRYPTO)),
    "Azioni USA large cap": (SourceKind.YFINANCE, _US),
    "Azioni Italia (FTSE MIB)": (SourceKind.YFINANCE, tuple(f"{t}.MI" for t in _MIB)),
}


class ScreenerCancelled(RuntimeError):
    pass


def parse_symbols(text: str) -> tuple[str, ...]:
    """Simboli separati da spazi, virgole, punto e virgola o a capo; duplicati rimossi."""
    seen: dict[str, None] = {}
    for token in text.replace(",", " ").replace(";", " ").split():
        seen.setdefault(token.strip().upper(), None)
    return tuple(seen)


@dataclass(frozen=True)
class ScreenerRequest:
    kind: SourceKind
    symbols: tuple[str, ...]
    timeframe: str = "1d"
    bars: int = 1000
    exchange: str = "binance"

    def __post_init__(self) -> None:
        if self.kind is SourceKind.CSV:
            raise ValueError("Lo screener richiede una sorgente remota (ccxt o Yahoo)")
        if len(self.symbols) < 2:
            raise ValueError("Servono almeno 2 simboli da confrontare")
        if len(self.symbols) > MAX_SYMBOLS:
            raise ValueError(f"Massimo {MAX_SYMBOLS} simboli per scansione")
        if self.bars < 50:
            raise ValueError("Servono almeno 50 candele per simbolo")


@dataclass(frozen=True)
class RankRow:
    symbol: str
    score: float  # NaN se mancano dati per una feature con peso
    rel_volume: float
    momentum: float  # %
    atr_pct: float
    last_close: float
    last_bar: pd.Timestamp
    stale: bool  # ultima candela più vecchia di quella più recente dell'universo


@dataclass
class ScreenerOutcome:
    request: ScreenerRequest
    params: ScreenerParams
    rows: list[RankRow]
    validation: ValidationResult
    errors: dict[str, str] = field(default_factory=dict)


def shared_source_loader() -> Loader:
    """``load_data`` con una sola sorgente per tutta la scansione (creata al primo uso)."""
    source: Optional[DataSource] = None

    def load(request: DataRequest, now: Optional[pd.Timestamp] = None) -> LoadedData:
        nonlocal source
        if source is None:
            source = make_source(request)
        return load_data(request, now=now, source=source)

    return load


def run_screener(
    request: ScreenerRequest,
    params: ScreenerParams,
    loader: Optional[Loader] = None,
    progress: Optional[Progress] = None,
    cancel: Optional[Cancel] = None,
    now: Optional[pd.Timestamp] = None,
) -> ScreenerOutcome:
    """Scarica ogni simbolo (errori raccolti, non bloccanti), classifica e verifica."""
    load = loader or shared_source_loader()
    features: dict[str, pd.DataFrame] = {}
    closes: dict[str, float] = {}
    errors: dict[str, str] = {}
    total = len(request.symbols)
    daily = parse_timeframe(request.timeframe) >= pd.Timedelta(days=1)
    for k, symbol in enumerate(request.symbols):
        if cancel is not None and cancel():
            raise ScreenerCancelled("Scansione annullata")
        try:
            loaded = load(
                DataRequest(
                    kind=request.kind,
                    symbol=symbol,
                    timeframe=request.timeframe,
                    limit=request.bars,
                    exchange=request.exchange,
                    adjusted=True,  # split e dividendi non devono sembrare crolli di prezzo
                ),
                now=now,
            )
            frame = loaded.frame
            if len(frame) <= params.warmup_bars:
                raise ValueError(
                    f"solo {len(frame)} candele, ne servono più di {params.warmup_bars}"
                )
            feats = compute_features(frame, params)
            if daily:
                # Yahoo data le candele giornaliere alla mezzanotte locale della borsa (es. 23:00
                # o 05:00 UTC): arrotondando al giorno, mercati diversi si allineano per data
                feats.index = feats.index.round("D")
                feats = feats[~feats.index.duplicated(keep="last")]
            features[symbol] = feats
            closes[symbol] = float(frame["close"].iat[-1])
        except Exception as exc:  # un simbolo non valido non deve fermare la scansione
            errors[symbol] = str(exc) or exc.__class__.__name__
        if progress is not None:
            progress(int(90 * (k + 1) / total))
        if not features and len(errors) == FAIL_FAST < total:
            first = next(iter(errors.values()))
            raise ValueError(f"I primi {FAIL_FAST} simboli non sono stati caricati: {first}")
    if len(features) < 2:
        detail = "; ".join(f"{s}: {e}" for s, e in list(errors.items())[:5])
        raise ValueError(f"Dati validi per meno di 2 simboli. {detail}")

    rows = _rank(features, closes, params)
    panels = {
        name: pd.DataFrame({s: f[name] for s, f in features.items()}) for name in FEATURE_LABELS
    }
    fwd = pd.DataFrame({s: f["fwd_return"] for s, f in features.items()})
    validation = validate(panels, fwd, params.weights, params.horizon)
    if progress is not None:
        progress(100)
    return ScreenerOutcome(request, params, rows, validation, errors)


def _rank(
    features: dict[str, pd.DataFrame], closes: dict[str, float], params: ScreenerParams
) -> list[RankRow]:
    """Classifica sull'ultima candela chiusa di ogni simbolo."""
    last = pd.DataFrame({s: f.iloc[-1] for s, f in features.items()}).T
    last_bar = {s: f.index[-1] for s, f in features.items()}
    newest = max(last_bar.values())
    scores = combine_scores(last[list(FEATURE_LABELS)], params.weights)
    rows = [
        RankRow(
            symbol=s,
            score=float(scores[s]),
            rel_volume=float(last.at[s, "volume"]),
            momentum=float(last.at[s, "momentum"]),
            atr_pct=float(last.at[s, "atr_pct"]),
            last_close=closes[s],
            last_bar=last_bar[s],
            stale=last_bar[s] < newest,
        )
        for s in last.index
    ]
    rows.sort(key=lambda r: (math.isnan(r.score), -r.score if not math.isnan(r.score) else 0))
    return rows
