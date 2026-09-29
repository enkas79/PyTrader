from __future__ import annotations

import pandas as pd
import pytest

from pytrader.data import validate_ohlcv
from pytrader.live import (
    LiveScanner,
    LiveSignal,
    SignalHistory,
    WatchItem,
    Watchlist,
    next_check_time,
)
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


# ------------------------------------------------------- intervallo di controllo
def test_intervalli_ammessi_per_mercato() -> None:
    from pytrader.live import allowed_intervals

    assert allowed_intervals(SourceKind.CCXT, "1h") == [1, 2, 5, 10, 15, 30, 60]
    # Yahoo: minimo 2 minuti per non superare i limiti di richieste
    assert allowed_intervals(SourceKind.YFINANCE, "1h") == [2, 5, 10, 15, 30, 60]
    assert allowed_intervals(SourceKind.CCXT, "1m") == [1]
    assert allowed_intervals(SourceKind.YFINANCE, "1m") == []  # solo automatico
    # Mai oltre un giorno, e solo divisori del timeframe (nessuna chiusura saltata)
    assert allowed_intervals(SourceKind.CCXT, "1w")[-1] == 1440
    assert all(240 % v == 0 for v in allowed_intervals(SourceKind.CCXT, "4h"))


def test_next_check_con_intervallo_fisso() -> None:
    now = pd.Timestamp("2024-01-01 10:07:30", tz=UTC)
    assert next_check_time("1h", now, interval_min=15) == pd.Timestamp(
        "2024-01-01 10:15:20", tz=UTC
    )
    assert next_check_time("1h", now, interval_min=5) == pd.Timestamp("2024-01-01 10:10:20", tz=UTC)
    # Yahoo con intervallo esplicito: nessun tetto dei 5 minuti
    assert next_check_time("1d", now, aligned=False, interval_min=60) == pd.Timestamp(
        "2024-01-01 11:00:20", tz=UTC
    )


def test_watch_item_intervallo() -> None:
    item = WatchItem(SourceKind.CCXT, "BTC/USDT", "1h", "binance", interval_min=15)
    assert item.key == WatchItem(SourceKind.CCXT, "BTC/USDT", "1h", "binance").key
    assert WatchItem.from_dict(item.to_dict()) == item
    with pytest.raises(ValueError):
        WatchItem(SourceKind.CCXT, "BTC/USDT", "1h", "binance", interval_min=7)
    with pytest.raises(ValueError):
        WatchItem(SourceKind.YFINANCE, "AAPL", "1h", interval_min=1)
    # Valore non più ammesso nel file: si torna all'automatico senza perdere il mercato
    data = item.to_dict() | {"interval_min": 7}
    assert WatchItem.from_dict(data).interval_min is None
