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
