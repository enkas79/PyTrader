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
    assert menus == ["&File", "&Aiuto"]
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
    from pytrader.gui.main_window import MainWindow, fmt_money

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
