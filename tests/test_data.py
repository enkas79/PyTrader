import pandas as pd
import pytest

from pytrader.data import CsvDataSource, DataSourceError, parse_timeframe, validate_ohlcv
from tests.conftest import make_ohlcv


def test_parse_timeframe() -> None:
    assert parse_timeframe("15m") == pd.Timedelta(minutes=15)
    assert parse_timeframe("4h") == pd.Timedelta(hours=4)
    assert parse_timeframe("1d") == pd.Timedelta(days=1)
    with pytest.raises(ValueError):
        parse_timeframe("abc")


def test_validazione_pulisce_e_segnala() -> None:
    df = make_ohlcv([(10, 11, 9, 10.5)] * 6)
    df.iloc[2] = [10, 9, 11, 10, 1]  # high < low: incoerente
    df.iloc[3, 0] = float("nan")  # open mancante
    df = pd.concat([df, df.iloc[[5]]])  # duplicato
    df = df.drop(df.index[4])  # buco di 1 candela
    clean, report = validate_ohlcv(df, "1h")
    assert report.duplicates_removed == 1
    assert report.nan_rows_removed == 1
    assert report.inconsistent_rows_removed == 1
    assert len(clean) == 3
    assert clean.index.is_monotonic_increasing and str(clean.index.tz) == "UTC"
    assert sum(g.missing_bars for g in report.gaps) == 3  # righe 2, 3, 4 assenti
    assert not report.is_clean


def test_validazione_inferisce_timeframe_e_volume() -> None:
    df = make_ohlcv([(10, 11, 9, 10.5, 0)] * 5, freq="15min")
    _, report = validate_ohlcv(df)
    assert report.timeframe == pd.Timedelta(minutes=15)
    assert report.volume_available is False
    assert report.is_clean


def test_csv_epoch_ms_e_alias(tmp_path) -> None:
    path = tmp_path / "data.csv"
    path.write_text(
        "Time,Open,High,Low,Close,Vol\n1704067200000,1,2,0.5,1.5,10\n1704070800000,1.5,2.5,1,2,20\n"
    )
    df = CsvDataSource(path).fetch()
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index[0] == pd.Timestamp("2024-01-01", tz="UTC")
    assert df["volume"].tolist() == [10, 20]


def test_csv_colonne_mancanti(tmp_path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("date,open,close\n2024-01-01,1,2\n")
    with pytest.raises(DataSourceError):
        CsvDataSource(path).fetch()
