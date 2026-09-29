"""Smoke test della GUI in modalità offscreen."""

import os

import pandas as pd
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)
pytest.importorskip("pyqtgraph", exc_type=ImportError)

from PyQt6.QtWidgets import QApplication  # noqa: E402

from pytrader.gui.app import load_stylesheet  # noqa: E402
from pytrader.services import DataRequest, LoadedData, SourceKind, run_analysis  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(load_stylesheet())
    return app  # type: ignore[return-value]


def test_main_window_popola_tabella(qapp: QApplication, random_walk: pd.DataFrame) -> None:
    from pytrader.data import validate_ohlcv
    from pytrader.gui.main_window import MainWindow

    window = MainWindow()
    frame, report = validate_ohlcv(random_walk)
    window._data = LoadedData(DataRequest(kind=SourceKind.CSV), frame, report)
    window.chart.set_data(frame)
    bundle = run_analysis(frame)
    window._on_analyzed(bundle)
    assert window.table.rowCount() == len(bundle.backtest.trades) > 0
    window.table.selectRow(0)  # centra il grafico sul setup
    assert window.export_act.isEnabled()
    menus = [a.text() for a in window.menuBar().actions()]
    assert menus == ["&File", "&Visualizza", "&Strumenti", "&Aiuto"]
    window.close()


def test_ricerca_simbolo_popola_lista_e_inserisce_ticker(qapp: QApplication) -> None:
    from PyQt6.QtCore import QThreadPool

    from pytrader.data.symbol_search import SymbolMatch
    from pytrader.gui.main_window import MainWindow
    from pytrader.services import SourceKind

    window = MainWindow()
    calls: list[tuple[SourceKind, str]] = []

    def fake_search(kind, query, exchange="binance", limit=15):
        calls.append((kind, query))
        return [SymbolMatch("VWCE.DE", "Vanguard FTSE All-World", "XETRA", "ETF")]

    window._symbol_service.search = fake_search  # type: ignore[method-assign]
    window.source_combo.setCurrentIndex(window.source_combo.findData(SourceKind.YFINANCE))
    window.symbol_search._timer.setInterval(0)
    window.symbol_edit.setText("vanguard all")
    window.symbol_edit.textEdited.emit("vanguard all")
    for _ in range(50):
        qapp.processEvents()
        QThreadPool.globalInstance().waitForDone(50)
        if window.symbol_search.model.rowCount():
            break
    assert calls == [(SourceKind.YFINANCE, "vanguard all")]
    model = window.symbol_search.model
    assert model.rowCount() == 1
    index = window.symbol_search.completer.completionModel().index(0, 0)
    assert "Vanguard" in index.data()
    window.symbol_search.completer.activated[type(index)].emit(index)
    assert window.symbol_edit.text() == "VWCE.DE"
    window.close()


def test_ricerca_disattivata_per_csv(qapp: QApplication) -> None:
    from pytrader.gui.main_window import MainWindow
    from pytrader.services import SourceKind

    window = MainWindow()
    window.source_combo.setCurrentIndex(window.source_combo.findData(SourceKind.CSV))
    assert window._symbol_job("apple") is None
    window.close()


def test_capitale_ricalcola_importi(qapp: QApplication, random_walk: pd.DataFrame) -> None:
    from pytrader.data import validate_ohlcv
    from pytrader.gui.formatting import fmt_money
    from pytrader.gui.main_window import MainWindow

    window = MainWindow()
    frame, report = validate_ohlcv(random_walk)
    window._data = LoadedData(DataRequest(kind=SourceKind.CSV), frame, report)
    window._on_analyzed(run_analysis(frame))
    assert window._money is not None
    first = window._money.final_equity
    window.capital_spin.setValue(window.capital_spin.value() * 2)
    assert window._money.params.initial_capital == 20_000
    assert window.metric_labels["final_equity"].text() == fmt_money(window._money.final_equity)
    assert window._money.final_equity != first
    assert window.table.horizontalHeaderItem(13).text() == "P&L"
    assert fmt_money(12345.678, signed=True) == "+12.345,68"
    window.close()


def test_live_segnale_notificato_e_deduplicato(qapp: QApplication) -> None:
    from pytrader.data import validate_ohlcv
    from pytrader.gui.main_window import MainWindow
    from pytrader.live import LiveScanner, ScanResult, WatchItem
    from pytrader.signals import SignalParams
    from tests.test_signals import _scenario

    df = _scenario()
    cut = df.iloc[: len(df) - 11]  # ultima candela = hammer sul supporto

    def loader(request, now=None):
        clean, report = validate_ohlcv(cut)
        return LoadedData(request, clean, report)

    window = MainWindow()
    window._signal_params = lambda: SignalParams(pivot_window=3, min_rr=1.5)  # type: ignore
    live = window.live
    live._params_provider = window._signal_params
    live.scanner = LiveScanner(loader)
    item = WatchItem(SourceKind.CCXT, "BTC/USDT", "1h", "binance")
    live.watchlist.add(item)

    received = []
    live.new_signal.connect(received.append)
    result = live.scanner.scan(item, window._signal_params())
    assert isinstance(result, ScanResult) and result.setup is not None
    live._on_result(result)
    live._on_result(result)  # stessa candela: nessuna seconda notifica
    assert len(received) == 1
    assert live.signal_table.rowCount() == 1
    assert live.signal_table.item(0, 3).text() == "Long"
    assert live.signal_table.item(0, 9).text() != "—"  # quantità suggerita dal capitale
    assert "Segnale LONG" in live.watch_table.item(0, 5).text()
    window.quit_app()


def test_live_aggiungi_mercato_corrente_e_riapri(qapp: QApplication) -> None:
    from pytrader.gui.main_window import MainWindow
    from pytrader.live import Watchlist

    window = MainWindow()
    window.source_combo.setCurrentIndex(window.source_combo.findData(SourceKind.CCXT))
    window.symbol_edit.setText("ETH/USDT")
    window.timeframe_combo.setCurrentText("4h")
    window.live._add_current()
    window.live._add_current()  # duplicato ignorato
    assert [i.key for i in window.live.watchlist.items] == ["ccxt:binance:ETH/USDT:4h"]
    assert Watchlist.load().items[0].symbol == "ETH/USDT"  # persistito su disco
    window.quit_app()


def test_cambio_tema_ricolora_e_salva(qapp: QApplication, random_walk: pd.DataFrame) -> None:
    from pytrader.data import validate_ohlcv
    from pytrader.gui.main_window import MainWindow
    from pytrader.gui.theme import DARK, LIGHT, current_palette
    from pytrader.settings import AppSettings, ThemeMode

    window = MainWindow()
    frame, report = validate_ohlcv(random_walk)
    window._data = LoadedData(DataRequest(kind=SourceKind.CSV), frame, report)
    window.chart.set_data(frame)
    window._on_analyzed(run_analysis(frame))
    try:
        window.set_theme(ThemeMode.LIGHT)
        assert current_palette() is LIGHT
        assert qapp.styleSheet() == load_stylesheet(LIGHT) != load_stylesheet(DARK)
        assert AppSettings.load().theme is ThemeMode.LIGHT
        assert window.theme_actions[ThemeMode.LIGHT].isChecked()
        colors = {
            window.table.item(r, 1).foreground().color().name()
            for r in range(window.table.rowCount())
        }
        assert colors <= {LIGHT.up, LIGHT.down}
        assert window.chart._levels  # livelli ridisegnati dopo il cambio tema
    finally:
        window.set_theme(ThemeMode.DARK)
        window.close()
    assert current_palette() is DARK


def test_walk_forward_dialogo_applica_parametri(
    qapp: QApplication, random_walk: pd.DataFrame
) -> None:
    from PyQt6.QtCore import QThreadPool

    from pytrader.data import validate_ohlcv
    from pytrader.gui.main_window import MainWindow

    window = MainWindow()
    frame, report = validate_ohlcv(random_walk)
    window._on_loaded(LoadedData(DataRequest(kind=SourceKind.CSV), frame, report))
    QThreadPool.globalInstance().waitForDone(5000)
    qapp.processEvents()
    assert window.wf_act.isEnabled()
    window._open_walk_forward()
    dialog = window._wf_dialog
    assert dialog is not None
    dialog.grid_edits["proximity_atr"].setText("0,3; 0,8")
    dialog.grid_edits["sl_buffer_atr"].setText("1")
    dialog.grid_edits["min_rr"].setText("1,5")
    dialog.folds_spin.setValue(3)
    dialog.ratio_spin.setValue(2.0)
    dialog.min_trades_spin.setValue(1)
    assert dialog.grid().size == 2
    dialog._run()
    for _ in range(200):
        QThreadPool.globalInstance().waitForDone(50)
        qapp.processEvents()
        if not dialog.is_running:
            break
    assert dialog.fold_table.rowCount() == 3
    assert dialog.apply_button.isEnabled()
    recommended = dialog._result.recommended  # type: ignore[union-attr]
    dialog._apply()
    assert window.prox_spin.value() == pytest.approx(recommended.proximity_atr)
    assert window.rr_spin.value() == pytest.approx(1.5)
    QThreadPool.globalInstance().waitForDone(5000)
    window.close()


def test_griglia_non_valida_segnalata(qapp: QApplication, random_walk: pd.DataFrame) -> None:
    from pytrader.backtest import BacktestParams
    from pytrader.gui.walkforward_dialog import WalkForwardDialog
    from pytrader.signals import SignalParams

    dialog = WalkForwardDialog(random_walk, SignalParams(), BacktestParams())
    dialog.grid_edits["pivot_window"].setText("2,5")
    with pytest.raises(ValueError, match="interi"):
        dialog.grid()
    assert "interi" in dialog.count_label.text()
    dialog.grid_edits["pivot_window"].setText("3; 5")
    dialog.grid_edits["min_rr"].setText("0; 2")
    with pytest.raises(ValueError, match="> 0"):
        dialog.grid()
    dialog.close()


def test_intervallo_controllo_per_mercato(qapp: QApplication) -> None:
    from pytrader.gui.main_window import MainWindow
    from pytrader.live import WatchItem, Watchlist

    window = MainWindow()
    live = window.live
    crypto = WatchItem(SourceKind.CCXT, "BTC/USDT", "1h", "binance")
    stock = WatchItem(SourceKind.YFINANCE, "AAPL", "15m")
    live.watchlist.add(crypto)
    live.watchlist.add(stock)
    live._refresh_watchlist()
    combo = live.watch_table.cellWidget(0, 1)
    assert [combo.itemData(i) for i in range(combo.count())] == [None, 1, 2, 5, 10, 15, 30, 60]
    yahoo_combo = live.watch_table.cellWidget(1, 1)
    # 15m su Yahoo: 2 e 10 non dividono 15 minuti, quindi restano 5 e 15
    assert [yahoo_combo.itemData(i) for i in range(yahoo_combo.count())] == [None, 5, 15]
    combo.setCurrentIndex(combo.findData(15))
    assert live.watchlist.items[0].interval_min == 15
    assert Watchlist.load(live.watchlist.path).items[0].interval_min == 15
    live._refresh_watchlist()  # aggiornamento testi: il selettore resta lo stesso
    assert live.watch_table.cellWidget(0, 1) is combo
    window.quit_app()
