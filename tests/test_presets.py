"""Valori consigliati per famiglia di asset."""

from __future__ import annotations

import pytest

from pytrader.presets import (
    MAX_SCREENER_BARS,
    PROFILES,
    AssetFamily,
    analysis_defaults,
    bars_per_day,
    detect_family,
    estimate_cost_r,
    min_rr_for_costs,
    screener_defaults,
    typical_atr_pct,
)
from pytrader.screener import MIN_PERIODS
from pytrader.services import SourceKind
from pytrader.signals import SignalParams


@pytest.mark.parametrize(
    ("kind", "symbol", "family"),
    [
        (SourceKind.CCXT, "SOL/USDT", AssetFamily.CRYPTO),
        (SourceKind.YFINANCE, "btc-usd", AssetFamily.CRYPTO),
        (SourceKind.YFINANCE, "EURUSD=X", AssetFamily.FOREX),
        (SourceKind.YFINANCE, "GC=F", AssetFamily.COMMODITY),
        (SourceKind.YFINANCE, "^GSPC", AssetFamily.ETF_INDEX),
        (SourceKind.YFINANCE, "ENI.MI", AssetFamily.STOCK),
        (SourceKind.YFINANCE, "BRK-B", AssetFamily.STOCK),
    ],
)
def test_riconoscimento_famiglia(kind: SourceKind, symbol: str, family: AssetFamily) -> None:
    assert detect_family(kind, symbol) is family


def test_candele_per_giorno_di_borsa() -> None:
    assert bars_per_day(AssetFamily.CRYPTO, "1h") == 24
    assert bars_per_day(AssetFamily.STOCK, "1h") == 8
    assert bars_per_day(AssetFamily.STOCK, "4h") == 2
    assert bars_per_day(AssetFamily.STOCK, "1d") == 1
    assert bars_per_day(AssetFamily.STOCK, "1w") == pytest.approx(1 / 5)
    assert bars_per_day(AssetFamily.CRYPTO, "1w") == pytest.approx(1 / 7)


def test_costo_in_r_e_rr_minimo() -> None:
    # ATR 2 %, buffer 1,5 → rischio ≈ 4 %; commissione 0,1 % per lato → 0,05 R
    assert estimate_cost_r(0.1, 2.0, 1.5) == pytest.approx(0.05)
    assert min_rr_for_costs(0.0) == 2.0
    assert min_rr_for_costs(0.05) == 2.25  # 2 + 3 × 0,05 = 2,15 → quarto superiore
    rr = min_rr_for_costs(0.2)
    assert (rr - 0.2) / (1 + 0.2) >= 2.0  # netto almeno 2:1
    assert min_rr_for_costs(10.0) == 6.0  # limite superiore


def test_analisi_senza_dati_usa_predefiniti(random_walk) -> None:
    res = analysis_defaults(AssetFamily.FOREX, "1h")
    assert res.signal == SignalParams()
    assert res.fee_pct == PROFILES[AssetFamily.FOREX].fee_pct
    assert res.max_leverage == 30 and res.cost_r is None
    assert any("carica i dati" in n.reason for n in res.notes)


def test_analisi_con_dati_alza_rr_se_i_costi_pesano(random_walk) -> None:
    atr_pct = typical_atr_pct(random_walk, 14)
    assert atr_pct is not None and atr_pct > 0
    cheap = analysis_defaults(AssetFamily.FOREX, "15m", random_walk)
    pricey = analysis_defaults(AssetFamily.CRYPTO, "15m", random_walk)
    assert cheap.cost_r is not None and pricey.cost_r is not None
    assert pricey.cost_r == pytest.approx(cheap.cost_r * 10)
    assert pricey.signal.min_rr >= cheap.signal.min_rr >= 2.0
    assert pricey.max_leverage == 1


def test_screener_giornaliero_azioni() -> None:
    res = screener_defaults(AssetFamily.STOCK, "1d")
    p = res.params
    assert (p.volume_window, p.momentum_bars, p.skip_bars, p.horizon) == (21, 126, 5, 21)
    assert res.bars >= p.momentum_bars + 1 + p.horizon * MIN_PERIODS
    assert p.volume_weight == 50


def test_screener_crypto_e_forex() -> None:
    crypto = screener_defaults(AssetFamily.CRYPTO, "1d").params
    assert (crypto.momentum_bars, crypto.skip_bars) == (182, 7)  # calendario 7 giorni su 7
    forex = screener_defaults(AssetFamily.FOREX, "1d").params
    assert forex.volume_weight == 0  # niente volume centralizzato


def test_screener_intraday_ridotto_nei_limiti() -> None:
    res = screener_defaults(AssetFamily.CRYPTO, "1h")
    p = res.params
    assert res.bars <= MAX_SCREENER_BARS
    assert p.momentum_bars + 1 + p.horizon * MIN_PERIODS <= res.bars
    assert any(n.name == "Attenzione" for n in res.notes)


def test_screener_settimanale() -> None:
    p = screener_defaults(AssetFamily.STOCK, "1w").params
    assert (p.volume_window, p.momentum_bars, p.skip_bars, p.horizon) == (4, 25, 1, 4)
