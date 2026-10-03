"""Screener OHLCV: feature senza look-ahead, punteggio cross-sezionale e verifica storica."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from pytrader.data import validate_ohlcv
from pytrader.screener import (
    ScreenerCancelled,
    ScreenerParams,
    ScreenerRequest,
    combine_scores,
    compute_features,
    parse_symbols,
    percentile_ranks,
    run_screener,
    validate,
)
from pytrader.services import LoadedData, SourceKind


def _walk(
    seed: int, n: int = 400, drift: float = 0.0, start: str = "2023-01-02 05:00"
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    index = pd.date_range(start, periods=n, freq="1D", tz="UTC", name="timestamp")
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) * 1.002,
            "low": np.minimum(open_, close) * 0.998,
            "close": close,
            "volume": rng.uniform(900, 1100, n),
        },
        index=index,
    )


SMALL = ScreenerParams(volume_window=5, momentum_bars=20, skip_bars=2, atr_period=5, horizon=5)


# ------------------------------------------------------------------ feature
def test_feature_non_guardano_al_futuro() -> None:
    df = _walk(1)
    base = compute_features(df, SMALL)
    altered = df.copy()
    altered.iloc[300:, :] *= 3.0  # cambia solo il futuro rispetto alla candela 299
    after = compute_features(altered, SMALL)
    cols = ["volume", "momentum", "atr_pct"]
    pd.testing.assert_frame_equal(base.iloc[:300][cols], after.iloc[:300][cols])


def test_volume_relativo_esclude_la_candela_corrente() -> None:
    df = _walk(2, n=30)
    df["volume"] = 100.0
    df.iloc[-1, df.columns.get_loc("volume")] = 300.0
    feats = compute_features(df, SMALL)
    assert feats["volume"].iat[-1] == pytest.approx(3.0)
    assert feats["volume"].iat[-2] == pytest.approx(1.0)


def test_volume_nullo_non_genera_infiniti() -> None:
    df = _walk(3, n=30)
    df["volume"] = 0.0
    assert compute_features(df, SMALL)["volume"].isna().all()


def test_momentum_salta_le_candele_recenti() -> None:
    df = _walk(4, n=60)
    feats = compute_features(df, SMALL)
    close = df["close"]
    expected = (close.iat[-1 - 2] / close.iat[-1 - 20] - 1) * 100
    assert feats["momentum"].iat[-1] == pytest.approx(expected)


def test_rendimento_futuro_da_apertura_successiva() -> None:
    df = _walk(5, n=60)
    feats = compute_features(df, SMALL)
    expected = (df["close"].iat[10 + 5] / df["open"].iat[11] - 1) * 100
    assert feats["fwd_return"].iat[10] == pytest.approx(expected)
    assert feats["fwd_return"].iloc[-5:].isna().all()


def test_parametri_non_validi() -> None:
    with pytest.raises(ValueError):
        ScreenerParams(momentum_bars=10, skip_bars=10)
    with pytest.raises(ValueError):
        ScreenerParams(volume_weight=0, momentum_weight=0)
    with pytest.raises(ValueError):
        ScreenerParams(horizon=0)


# ------------------------------------------------------------------ punteggio
def test_percentili_simmetrici_e_nan_esclusi() -> None:
    ranks = percentile_ranks(pd.Series([3.0, 1.0, np.nan, 2.0], index=list("abcd")))
    assert ranks.to_dict() == pytest.approx({"a": 5 / 6, "b": 1 / 6, "d": 0.5})


def test_punteggio_pesi_e_inversione() -> None:
    feats = pd.DataFrame(
        {"volume": [1.0, 2.0, 3.0], "momentum": [-5.0, 0.0, 5.0]}, index=list("abc")
    )
    up = combine_scores(feats, {"volume": 50, "momentum": 50})
    assert list(up.sort_values().index) == ["a", "b", "c"]
    assert up["b"] == pytest.approx(50.0)
    down = combine_scores(feats, {"volume": 0, "momentum": -100})
    assert list(down.sort_values().index) == ["c", "b", "a"]
    assert 0 < down.min() < down.max() < 100


def test_punteggio_escluso_se_manca_una_feature_pesata() -> None:
    feats = pd.DataFrame(
        {"volume": [np.nan, 2.0, 3.0], "momentum": [1.0, 2.0, 3.0]}, index=list("abc")
    )
    assert math.isnan(combine_scores(feats, {"volume": 50, "momentum": 50})["a"])
    assert not combine_scores(feats, {"volume": 0, "momentum": 50}).isna().any()


# ------------------------------------------------------------------ verifica
def _panels(rel: float, seed: int = 0, dates: int = 300, symbols: int = 20):
    """Rendimenti futuri = rel × momentum + rumore; il volume è puro rumore."""
    rng = np.random.default_rng(seed)
    index = pd.date_range("2020-01-01", periods=dates, freq="1D", tz="UTC")
    cols = [f"S{i}" for i in range(symbols)]
    mom = pd.DataFrame(rng.normal(0, 1, (dates, symbols)), index=index, columns=cols)
    vol = pd.DataFrame(rng.normal(1, 0.2, (dates, symbols)), index=index, columns=cols)
    fwd = rel * mom + pd.DataFrame(rng.normal(0, 1, (dates, symbols)), index=index, columns=cols)
    return {"volume": vol, "momentum": mom}, fwd


def test_verifica_riconosce_relazione_positiva() -> None:
    panels, fwd = _panels(rel=0.5)
    res = validate(panels, fwd, {"volume": 0, "momentum": 100}, horizon=5)
    assert res.periods == 60
    assert res.score.mean_ic > 0.2 and res.score.ic_tstat > 2
    assert res.significant and "positiva" in res.verdict()
    assert res.buckets[-1] > res.buckets[0] and res.spread > 0
    mom = next(f for f in res.factors if f.name == "Momentum")
    vol = next(f for f in res.factors if f.name == "Volume relativo")
    assert mom.ic_tstat > 2 and abs(vol.mean_ic) < 0.1


def test_verifica_rumore_senza_vantaggio() -> None:
    panels, fwd = _panels(rel=0.0, seed=7)
    res = validate(panels, fwd, {"volume": 50, "momentum": 50}, horizon=5)
    assert abs(res.score.ic_tstat) < 2
    assert not res.significant and "Nessun vantaggio" in res.verdict()


def test_verifica_relazione_inversa() -> None:
    panels, fwd = _panels(rel=-0.5)
    res = validate(panels, fwd, {"volume": 0, "momentum": 100}, horizon=5)
    assert res.score.ic_tstat < -2 and "INVERSA" in res.verdict()


def test_verifica_campione_insufficiente() -> None:
    panels, fwd = _panels(rel=0.5, dates=50)
    res = validate(panels, fwd, {"volume": 0, "momentum": 100}, horizon=5)
    assert res.periods == 10 and not res.enough_data
    assert "insufficiente" in res.verdict()


# ------------------------------------------------------------------ esecuzione
def _loader(frames: dict[str, pd.DataFrame], seen: list | None = None):
    def load(request, now=None):
        if seen is not None:
            seen.append(request)
        if request.symbol not in frames:
            raise ValueError("simbolo sconosciuto")
        frame, report = validate_ohlcv(frames[request.symbol])
        return LoadedData(request, frame, report)

    return load


def test_scansione_classifica_e_raccoglie_errori() -> None:
    frames = {f"S{i}": _walk(10 + i, drift=0.002 * (i - 3)) for i in range(8)}
    frames["S7"] = frames["S7"].iloc[:-3]  # dati fermi a 3 giorni prima
    seen: list = []
    progress: list[int] = []
    request = ScreenerRequest(
        SourceKind.YFINANCE, tuple(frames) + ("XXX",), timeframe="1d", bars=400
    )
    params = ScreenerParams(
        volume_window=5, momentum_bars=60, skip_bars=0, horizon=5, volume_weight=0
    )
    out = run_screener(request, params, _loader(frames, seen), progress=progress.append)
    assert out.errors.keys() == {"XXX"}
    assert all(r.adjusted for r in seen)  # prezzi rettificati per split e dividendi
    assert progress[-1] == 100
    scores = [r.score for r in out.rows]
    assert scores == sorted(scores, reverse=True)
    assert out.rows[0].momentum == max(r.momentum for r in out.rows)
    assert [r.symbol for r in out.rows if r.stale] == ["S7"]
    assert out.validation.periods > 0


def test_scansione_allinea_date_di_borse_diverse() -> None:
    # Yahoo: Milano alle 23:00 UTC del giorno prima, New York alle 05:00 UTC
    frames = {f"US{i}": _walk(i) for i in range(3)}
    frames.update({f"MI{i}": _walk(20 + i, start="2023-01-01 23:00") for i in range(3)})
    request = ScreenerRequest(SourceKind.YFINANCE, tuple(frames), timeframe="1d")
    out = run_screener(request, SMALL, _loader(frames))
    assert len({r.last_bar for r in out.rows}) == 1
    assert out.validation.avg_symbols == 6


def test_scansione_annullata_e_dati_insufficienti() -> None:
    frames = {"A": _walk(1), "B": _walk(2)}
    request = ScreenerRequest(SourceKind.CCXT, ("A", "B"), timeframe="1d")
    with pytest.raises(ScreenerCancelled):
        run_screener(request, SMALL, _loader(frames), cancel=lambda: True)
    short = {"A": _walk(1, n=10), "B": _walk(2)}
    with pytest.raises(ValueError, match="meno di 2 simboli"):
        run_screener(request, SMALL, _loader(short))


def test_richiesta_non_valida() -> None:
    with pytest.raises(ValueError):
        ScreenerRequest(SourceKind.CSV, ("A", "B"))
    with pytest.raises(ValueError):
        ScreenerRequest(SourceKind.CCXT, ("A",))


def test_parse_simboli() -> None:
    assert parse_symbols("aapl, msft;AAPL\n eni.mi  ") == ("AAPL", "MSFT", "ENI.MI")


def test_scansione_si_ferma_se_la_sorgente_non_risponde() -> None:
    calls: list[str] = []

    def down(request, now=None):
        calls.append(request.symbol)
        raise ConnectionError("exchange irraggiungibile")

    request = ScreenerRequest(SourceKind.CCXT, tuple(f"S{i}" for i in range(10)), "1d")
    with pytest.raises(ValueError, match="I primi 3 simboli"):
        run_screener(request, SMALL, down)
    assert len(calls) == 3


def test_sorgente_unica_per_tutta_la_scansione(monkeypatch: pytest.MonkeyPatch) -> None:
    from pytrader.screener import runner

    frames = {f"S{i}": _walk(i) for i in range(4)}
    created: list[str] = []

    class FakeSource:
        def fetch(self, symbol, timeframe, since=None, limit=None):
            return frames[symbol]

    def make(request):
        created.append(request.symbol)
        return FakeSource()

    monkeypatch.setattr(runner, "make_source", make)
    out = run_screener(ScreenerRequest(SourceKind.CCXT, tuple(frames), "1d"), SMALL)
    assert len(out.rows) == 4 and len(created) == 1
