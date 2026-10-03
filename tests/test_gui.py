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


@pytest.fixture(autouse=True)
def _no_update_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Niente verifica aggiornamenti in rete: la risposta arriverebbe a finestre già distrutte
    dai test precedenti (crash durante i cicli di eventi lunghi)."""
    from pytrader.gui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "check_updates", lambda self, manual: None)


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
    assert window.table.horizontalHeaderItem(14).text() == "P&L"
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
    assert window.params_panel.prox_spin.value() == pytest.approx(recommended.proximity_atr)
    assert window.params_panel.rr_spin.value() == pytest.approx(1.5)
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


def test_layout_pannello_parametri_e_barra(qapp: QApplication, random_walk: pd.DataFrame) -> None:
    from pytrader.data import validate_ohlcv
    from pytrader.gui.main_window import MainWindow

    window = MainWindow()
    window.show()
    assert window.params_dock.isHidden()  # chiuso di default
    assert [window.tabs.tabText(i) for i in range(window.tabs.count())] == [
        "Setup", "Dati", "Live",
    ]  # fmt: skip
    toolbar = [a.text() for a in window.toolbar.actions() if not a.isSeparator()]
    assert toolbar == ["Carica dati", "Analizza", "Parametri", "Walk-forward…", "Screener…"]
    window.show_params()
    assert window.params_dock.isVisible() and window.params_act.isChecked()

    frame, report = validate_ohlcv(random_walk)
    window._data = LoadedData(DataRequest(kind=SourceKind.CSV), frame, report)
    window._analyzed = (window._signal_params(), window.params_panel.fee_rate())
    window._on_analyzed(run_analysis(frame))
    button = window.toolbar.widgetForAction(window.analyze_act)
    assert not window.params_stale
    assert "R:R 2" in window.params_summary.text()
    window.params_panel.rr_spin.setValue(3.0)
    assert window.params_stale  # analisi mostrata non più aggiornata
    assert button.property("attention") is True
    assert "R:R 3" in window.params_summary.text()
    window.params_panel.rr_spin.setValue(2.0)
    assert not window.params_stale  # tornati ai parametri dell'analisi
    assert button.property("attention") is False
    tone = window.metric_labels["expectancy_r"].property("tone")
    assert tone in ("up", "down", "")
    window.quit_app()


def test_disposizione_ricordata_al_riavvio(qapp: QApplication) -> None:
    from pytrader.gui.main_window import DEFAULT_HIDDEN_COLUMNS, MainWindow

    window = MainWindow()
    assert window.setup_columns.hidden() == list(DEFAULT_HIDDEN_COLUMNS)
    window.setup_columns.set_hidden(["P&L"])
    window.live.signal_columns.set_hidden(["Rischio"])
    window.splitters["right"].setSizes([700, 160])
    window.show_params()
    window.quit_app()

    again = MainWindow()
    assert again.setup_columns.hidden() == ["P&L"]
    assert again.live.signal_columns.hidden() == ["Rischio"]
    assert not again.params_dock.isHidden()  # pannello lasciato aperto: riaperto
    again.reset_layout()
    assert again.setup_columns.hidden() == list(DEFAULT_HIDDEN_COLUMNS)
    assert again.params_dock.isHidden()
    again.quit_app()


def test_riepilogo_parametri() -> None:
    from pytrader.gui.params_panel import params_summary
    from pytrader.signals import SignalParams, TargetMode

    text = params_summary(SignalParams(min_rr=2.5, target_mode=TargetMode.FIXED_RR), 0.1)
    assert "R:R 2,5" in text and "R:R fisso" in text and "Comm. 0,1 %" in text


def test_screener_classifica_e_aggiunge_alla_watchlist(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PyQt6.QtCore import QThreadPool

    from pytrader.gui import screener_dialog
    from pytrader.gui.main_window import MainWindow
    from pytrader.screener import ScreenerConfig, run_screener
    from tests.test_screener import _loader, _walk

    frames = {f"S{i}": _walk(10 + i, drift=0.002 * (i - 3)) for i in range(6)}
    monkeypatch.setattr(
        screener_dialog,
        "run_screener",
        lambda req, params, **kw: run_screener(req, params, _loader(frames), **kw),
    )
    window = MainWindow()
    assert window.screener_act in window.toolbar.actions()
    window._open_screener()
    dialog = window._screener_dialog
    assert dialog is not None
    dialog.source_combo.setCurrentIndex(dialog.source_combo.findData(SourceKind.YFINANCE))
    dialog.timeframe_combo.setCurrentText("1d")
    dialog.bars_spin.setValue(400)
    dialog.symbols_edit.setPlainText("s0 s1, s2; s3\ns4 s5 XXX")
    dialog.mom_spin.setValue(60)
    dialog.skip_spin.setValue(0)
    dialog.horizon_spin.setValue(5)
    dialog._run()
    for _ in range(200):
        QThreadPool.globalInstance().waitForDone(50)
        qapp.processEvents()
        if not dialog.is_running:
            break
    assert dialog.rank_table.rowCount() == 6
    assert "XXX" in dialog.errors_label.text()
    assert dialog.check_labels["periods"].text() != "—"
    assert dialog.verdict_label.text()[0] in "✔⚠✖"
    assert ScreenerConfig.load().params.momentum_bars == 60  # configurazione ricordata

    dialog.rank_table.selectRow(0)
    first = dialog._selected_symbols()[0]
    dialog._add_selected()
    dialog._add_selected()  # duplicato ignorato
    assert [i.symbol for i in window.live.watchlist.items] == [first]
    assert window.live.watchlist.items[0].kind is SourceKind.YFINANCE

    opened = []
    window._load = lambda: opened.append(window.symbol_edit.text())  # type: ignore[method-assign]
    dialog._open_row(0, 0)
    assert opened == [first] and window.timeframe_combo.currentText() == "1d"
    window._open_screener()
    assert window._screener_dialog is dialog  # riutilizzato, risultati conservati
    window.quit_app()


def test_screener_parametri_incoerenti_segnalati(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PyQt6.QtWidgets import QMessageBox

    from pytrader.gui.screener_dialog import ScreenerDialog

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    dialog = ScreenerDialog()
    dialog.symbols_edit.setPlainText("AAPL")
    dialog._run()
    assert "almeno 2 simboli" in warnings[-1]
    dialog.symbols_edit.setPlainText("AAPL MSFT")
    dialog.bars_spin.setValue(100)
    dialog._run()  # 100 candele < momentum 126
    assert "Servono più di" in warnings[-1]
    assert not dialog.is_running
    dialog.close()


def test_guida_indice_ricerca_e_pdf(qapp: QApplication, tmp_path) -> None:
    from PyQt6.QtCore import QThreadPool

    from pytrader.gui.dialogs import HelpDialog
    from pytrader.gui.help_content import SECTIONS

    dialog = HelpDialog()
    assert dialog.toc.count() == len(SECTIONS) >= 15
    dialog.show_section("screener")
    assert dialog.toc.currentItem().text().endswith("Screener multi-simbolo")
    dialog.search_edit.setText("survivorship")
    assert dialog.find_next() and dialog.find_next(backward=True)
    dialog.search_edit.setText("parola-che-non-esiste")
    assert not dialog.find_next()

    opened = []
    dialog._on_pdf_done = opened.append  # type: ignore[method-assign]  # niente viewer
    dialog.export_pdf(str(tmp_path / "guida"))  # estensione aggiunta automaticamente
    for _ in range(100):
        QThreadPool.globalInstance().waitForDone(50)
        qapp.processEvents()
        if opened:
            break
    pdf = tmp_path / "guida.pdf"
    assert opened == [pdf] and pdf.read_bytes()[:4] == b"%PDF"
    dialog.close()


def test_valori_per_famiglia_applicati(qapp: QApplication, random_walk: pd.DataFrame) -> None:
    from pytrader.data import validate_ohlcv
    from pytrader.gui.main_window import MainWindow
    from pytrader.presets import AssetFamily, analysis_defaults

    window = MainWindow()
    frame, report = validate_ohlcv(random_walk)
    request = DataRequest(kind=SourceKind.CCXT, symbol="BTC/USDT", timeframe="15m")
    window._data = LoadedData(request, frame, report)
    dialog = window.open_family_defaults()
    assert dialog is not None
    assert dialog.family() is AssetFamily.CRYPTO  # riconosciuta dal simbolo
    assert dialog.table.rowCount() >= 4
    dialog.family_combo.setCurrentIndex(dialog.family_combo.findData(AssetFamily.FOREX))
    assert "30×" in [dialog.table.item(r, 1).text() for r in range(dialog.table.rowCount())]
    dialog.accept()
    expected = analysis_defaults(AssetFamily.FOREX, "15m", frame)
    assert window.params_panel.fee_spin.value() == pytest.approx(expected.fee_pct)
    assert window.leverage_spin.value() == pytest.approx(30)
    assert window.params_panel.rr_spin.value() == pytest.approx(expected.signal.min_rr)
    window.quit_app()


def test_screener_valori_per_famiglia(qapp: QApplication) -> None:
    from pytrader.gui.screener_dialog import ScreenerDialog
    from pytrader.presets import AssetFamily

    dialog = ScreenerDialog()
    dialog.source_combo.setCurrentIndex(dialog.source_combo.findData(SourceKind.YFINANCE))
    dialog.symbols_edit.setPlainText("EURUSD=X GBPUSD=X")
    dialog.timeframe_combo.setCurrentText("1d")
    family = dialog.open_family_defaults()
    assert family.family() is AssetFamily.FOREX
    family.accept()
    assert dialog.vol_weight_spin.value() == 0  # forex senza volume
    assert (dialog.mom_spin.value(), dialog.skip_spin.value()) == (126, 5)
    assert dialog.bars_spin.value() >= 126 + 21 * 20
    dialog.close()


def test_selezione_trade_mostra_uscita(qapp: QApplication, random_walk: pd.DataFrame) -> None:
    import pyqtgraph as pg

    from pytrader.backtest import TradeOutcome
    from pytrader.data import validate_ohlcv
    from pytrader.gui.main_window import COL, MainWindow

    window = MainWindow()
    frame, report = validate_ohlcv(random_walk)
    window._data = LoadedData(DataRequest(kind=SourceKind.CSV), frame, report)
    window.chart.set_data(frame)
    window._on_analyzed(run_analysis(frame))
    trades = window._table_trades
    # Trade chiuso più lungo: l'uscita cade oltre la finestra di zoom predefinita
    row, trade = max(
        ((r, t) for r, t in enumerate(trades) if t.exit_index is not None),
        key=lambda rt: rt[1].exit_index - rt[1].setup.signal_index,  # type: ignore[operator]
    )
    exit_time = frame.index[trade.exit_index]
    assert window.table.item(row, COL["Uscita"]).text().startswith(f"{exit_time:%Y-%m-%d %H:%M}")
    skipped = [r for r, t in enumerate(trades) if t.outcome is TradeOutcome.SKIPPED]
    if skipped:
        assert "aperto" in window.table.item(skipped[0], COL["Esito"]).toolTip()

    window.table.selectRow(row)
    x0, x1 = window.chart.price_plot.viewRange()[0]
    assert x0 <= trade.setup.signal_index and x1 > trade.exit_index
    markers = [
        i
        for i in window.chart._setup_items
        if isinstance(i, pg.ScatterPlotItem) and i.opts["symbol"] == "x"
    ]
    assert len(markers) == 1
    assert markers[0].data["x"][0] == trade.exit_index
    assert markers[0].data["y"][0] == pytest.approx(trade.exit_price)
    assert markers[0].opts["brush"].color().name() == "#ffc800"  # giallo con entrambi i temi
    window.close()
