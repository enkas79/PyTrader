from __future__ import annotations

import pandas as pd
import pytest

from pytrader.data import validate_ohlcv
from pytrader.live import LiveScanner, LiveSignal, SignalHistory, WatchItem, Watchlist
from pytrader.live.scanner import next_check_time
from pytrader.services import DataRequest, LoadedData, SourceKind
from pytrader.signals import SignalParams
from tests.test_signals import _scenario

UTC = "UTC"
BTC = WatchItem(SourceKind.CCXT, "BTC/USDT", "15m", "binance")


def test_next_check_allineato_alla_chiusura() -> None:
    now = pd.Timestamp("2024-01-01 10:07:30", tz=UTC)
    assert next_check_time("15m", now) == pd.Timestamp("2024-01-01 10:15:20", tz=UTC)
    # Yahoo (sessioni non allineate): tetto di 5 minuti
    assert next_check_time("1d", now, aligned=False) == now + pd.Timedelta(minutes=5)
    assert next_check_time("1m", now, aligned=False) == pd.Timestamp("2024-01-01 10:08:20", tz=UTC)


def test_next_check_settimanale_lunedi() -> None:
    sunday = pd.Timestamp("2024-01-07 23:57:00", tz=UTC)  # domenica
    assert next_check_time("1w", sunday) == pd.Timestamp("2024-01-08 00:00:20", tz=UTC)


def test_watch_item_validazione() -> None:
    with pytest.raises(ValueError):
        WatchItem(SourceKind.CSV, "x", "1h")
    with pytest.raises(ValueError):
        WatchItem(SourceKind.YFINANCE, "AAPL", "3x")
    assert BTC.label == "BTC/USDT 15m (binance)"


def test_watchlist_persistenza(tmp_path) -> None:
    path = tmp_path / "wl.json"
    wl = Watchlist(path=path)
    assert wl.add(BTC) and not wl.add(BTC)  # niente duplicati
    wl.add(WatchItem(SourceKind.YFINANCE, "VWCE.DE", "1d"))
    wl.active = True
    wl.save()
    loaded = Watchlist.load(path)
    assert [i.key for i in loaded.items] == [i.key for i in wl.items] and loaded.active
    loaded.remove(BTC.key)
    assert len(loaded.items) == 1


def test_watchlist_file_corrotto(tmp_path) -> None:
    path = tmp_path / "wl.json"
    path.write_text("{non json")
    assert Watchlist.load(path).items == []


def _loader_for(frame: pd.DataFrame):
    def loader(request: DataRequest, now=None) -> LoadedData:
        clean, report = validate_ohlcv(frame)
        return LoadedData(request, clean, report)

    return loader


def test_scanner_segnale_solo_su_ultima_candela() -> None:
    df = _scenario()
    hammer_idx = len(df) - 12
    params = SignalParams(pivot_window=3, min_rr=1.5)
    # Serie troncata subito dopo l'hammer: il segnale è sull'ultima candela chiusa
    result = LiveScanner(_loader_for(df.iloc[: hammer_idx + 1])).scan(BTC, params)
    assert result.setup is not None
    assert result.setup.entry_is_estimate
    assert result.last_closed == df.index[hammer_idx]
    # Una candela dopo: il setup è ormai storico, nessun segnale live
    later = LiveScanner(_loader_for(df.iloc[: hammer_idx + 2])).scan(BTC, params)
    assert later.setup is None


def test_scanner_richiede_storico_sufficiente() -> None:
    params = SignalParams()
    assert LiveScanner().bars_needed(params) >= params.levels.lookback


def test_storico_deduplica_e_persiste(tmp_path) -> None:
    df = _scenario()
    params = SignalParams(pivot_window=3, min_rr=1.5)
    setup = LiveScanner(_loader_for(df.iloc[: len(df) - 11])).scan(BTC, params).setup
    assert setup is not None
    signal = LiveSignal.from_setup(BTC, setup)
    history = SignalHistory(tmp_path / "s.json")
    assert history.add(signal)
    assert not history.add(LiveSignal.from_setup(BTC, setup))  # stessa candela: duplicato
    history.save()
    reloaded = SignalHistory.load(tmp_path / "s.json")
    assert reloaded.signals == [signal]
    assert not reloaded.add(signal)  # dopo il riavvio non si rinotifica
