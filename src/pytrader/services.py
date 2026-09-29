"""Casi d'uso senza dipendenze Qt: caricamento dati, analisi, export JSON."""

from __future__ import annotations

import json
import math
import threading
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union

import pandas as pd

from pytrader.backtest import (
    Backtester,
    BacktestParams,
    BacktestResult,
    MoneyResult,
)
from pytrader.data import CsvDataSource, DataSource, drop_unclosed_candle, validate_ohlcv
from pytrader.data.symbol_search import SymbolMatch, SymbolSearcher
from pytrader.models import ValidationReport
from pytrader.signals import AnalysisResult, SignalEngine, SignalParams
from pytrader.version import get_version


class SourceKind(str, Enum):
    CSV = "csv"
    CCXT = "ccxt"
    YFINANCE = "yfinance"


@dataclass(frozen=True)
class DataRequest:
    kind: SourceKind
    symbol: str = ""
    timeframe: str = "1h"
    limit: Optional[int] = 1000
    exchange: str = "binance"
    csv_path: Optional[str] = None


@dataclass
class LoadedData:
    request: DataRequest
    frame: pd.DataFrame
    report: ValidationReport


@dataclass
class AnalysisBundle:
    analysis: AnalysisResult
    backtest: BacktestResult


def make_source(request: DataRequest) -> DataSource:
    if request.kind is SourceKind.CSV:
        if not request.csv_path:
            raise ValueError("Seleziona un file CSV")
        return CsvDataSource(request.csv_path)
    if request.kind is SourceKind.CCXT:
        from pytrader.data.ccxt_source import CcxtDataSource

        return CcxtDataSource(request.exchange)
    from pytrader.data.yfinance_source import YFinanceDataSource

    return YFinanceDataSource()


class SymbolSearchService:
    """Mantiene un searcher per sorgente/exchange (la cache dei mercati ccxt resta valida)."""

    def __init__(self) -> None:
        self._searchers: dict[tuple[SourceKind, str], SymbolSearcher] = {}
        self._lock = threading.Lock()  # chiamato da più worker del QThreadPool

    def search(
        self, kind: SourceKind, query: str, exchange: str = "binance", limit: int = 15
    ) -> list[SymbolMatch]:
        if kind is SourceKind.CSV:
            return []
        key = (kind, exchange if kind is SourceKind.CCXT else "")
        with self._lock:
            searcher = self._searchers.get(key)
            if searcher is None:
                searcher = self._make(kind, exchange)
                self._searchers[key] = searcher
            return searcher.search(query, limit)

    @staticmethod
    def _make(kind: SourceKind, exchange: str) -> SymbolSearcher:
        if kind is SourceKind.CCXT:
            from pytrader.data.symbol_search import CcxtSymbolSearcher

            return CcxtSymbolSearcher(exchange)
        from pytrader.data.symbol_search import YFinanceSymbolSearcher

        return YFinanceSymbolSearcher()


def load_data(request: DataRequest, now: Optional[pd.Timestamp] = None) -> LoadedData:
    """Scarica e valida la serie. Per le sorgenti remote esclude la candela in formazione:
    tutte le analisi usano solo candele chiuse."""
    if request.kind is not SourceKind.CSV and not request.symbol.strip():
        raise ValueError("Specifica un simbolo")
    is_csv = request.kind is SourceKind.CSV
    # Il CSV è caricato per intero: limite e timeframe valgono solo per le sorgenti remote
    limit = None if is_csv or request.limit is None else request.limit + 1
    raw = make_source(request).fetch(request.symbol.strip(), request.timeframe, limit=limit)
    timeframe: Optional[str] = None if is_csv else request.timeframe
    frame, report = validate_ohlcv(raw, timeframe)
    if not is_csv:
        frame, dropped = drop_unclosed_candle(frame, request.timeframe, now)
        if dropped:
            report.rows_out = len(frame)
            report.warnings.append("Ultima candela in formazione esclusa dall'analisi")
    if frame.empty:
        raise ValueError("Nessuna candela valida dopo la validazione")
    return LoadedData(request=request, frame=frame, report=report)


def run_analysis(
    frame: pd.DataFrame,
    signal_params: Optional[SignalParams] = None,
    backtest_params: Optional[BacktestParams] = None,
) -> AnalysisBundle:
    analysis = SignalEngine(signal_params).analyze(frame)
    backtest = Backtester(backtest_params).run(frame, analysis.setups)
    return AnalysisBundle(analysis=analysis, backtest=backtest)


def _finite(value: float) -> Optional[float]:
    return value if math.isfinite(value) else None


def bundle_to_dict(
    bundle: AnalysisBundle,
    meta: Optional[dict[str, Any]] = None,
    money: Optional[MoneyResult] = None,
) -> dict[str, Any]:
    """Struttura JSON: metadati, livelli correnti, metriche e trade con esito."""
    trades = []
    plans = money.plans if money is not None else [None] * len(bundle.backtest.trades)
    for t, plan in zip(bundle.backtest.trades, plans):
        item = t.setup.to_dict()
        item.update(
            outcome=t.outcome.value,
            exit_time=(
                bundle.analysis.data.index[t.exit_index].isoformat()
                if t.exit_index is not None
                else None
            ),
            exit_price=t.exit_price,
            r_multiple=t.r_multiple,
        )
        if plan is not None:
            item.update(
                quantity=plan.quantity,
                notional=plan.notional,
                risk_amount=plan.risk_amount,
                pnl=plan.pnl,
                equity_after=plan.equity_after,
                capped_by_leverage=plan.capped,
            )
        trades.append(item)
    df = bundle.analysis.data
    return {
        "app_version": get_version(),
        "meta": meta or {},
        "range": {
            "start": df.index[0].isoformat() if len(df) else None,
            "end": df.index[-1].isoformat() if len(df) else None,
            "bars": len(df),
        },
        "levels": [asdict(lv) for lv in bundle.analysis.levels],
        "metrics": {k: _finite(v) for k, v in bundle.backtest.summary().items()},
        "money": (
            {
                **asdict(money.params),
                **{k: _finite(v) for k, v in money.summary().items()},
            }
            if money is not None
            else None
        ),
        "trades": trades,
    }


def export_json(
    bundle: AnalysisBundle,
    path: Union[str, Path],
    meta: Optional[dict[str, Any]] = None,
    money: Optional[MoneyResult] = None,
) -> Path:
    target = Path(path)
    payload = bundle_to_dict(bundle, meta, money)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return target
