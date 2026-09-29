import json

import pandas as pd
import pytest

from pytrader.services import DataRequest, SourceKind, export_json, load_data, run_analysis


def test_load_csv_ed_export(tmp_path, random_walk: pd.DataFrame) -> None:
    csv = tmp_path / "rw.csv"
    random_walk.reset_index().to_csv(csv, index=False)
    loaded = load_data(DataRequest(kind=SourceKind.CSV, csv_path=str(csv)))
    assert len(loaded.frame) == len(random_walk)
    assert loaded.report.timeframe == pd.Timedelta(minutes=15)

    bundle = run_analysis(loaded.frame)
    out = export_json(bundle, tmp_path / "out.json", {"symbol": "TEST"})
    payload = json.loads(out.read_text())
    assert payload["meta"]["symbol"] == "TEST"
    assert len(payload["trades"]) == len(bundle.backtest.trades)
    assert set(payload["metrics"]) >= {"win_rate", "expectancy_r"}


def test_load_senza_simbolo() -> None:
    with pytest.raises(ValueError):
        load_data(DataRequest(kind=SourceKind.CCXT, symbol=" "))
