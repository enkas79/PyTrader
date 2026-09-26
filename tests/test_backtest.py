"""Backtest: modello di esecuzione, costi, assenza di look-ahead, dati storici."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest import (
    BacktestConfig,
    Backtester,
    fetch_history,
    load_csv,
    main,
    save_csv,
)
from config import StrategyConfig
from strategy import PositionSizer, Strategy
from tests.conftest import make_ohlcv


class ScriptedSignals(Strategy):
    """Strategia con segnali e ATR prefissati (livelli reali da Strategy.levels)."""

    def __init__(self, long: list[bool], short: list[bool], atr: float = 10.0) -> None:
        super().__init__(StrategyConfig())
        self.long, self.short, self.atr_value = long, short, atr

    def generate_signals(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        df = ohlcv.copy()
        df["atr"] = self.atr_value
        df["long_signal"] = self.long
        df["short_signal"] = self.short
        return df


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="15min", tz="UTC")
    o, h, lo, c = map(list, zip(*rows, strict=True))
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": 1.0}, index=idx)


FREE = BacktestConfig(fee_rate=0.0, slippage_bps=0.0)


def _run(rows, long=None, short=None, cfg: BacktestConfig = FREE):
    n = len(rows)
    strat = ScriptedSignals(long or [True] + [False] * (n - 1), short or [False] * n)
    return Backtester(strat, PositionSizer(0.01, 3.0), cfg).run(_bars(rows))


# Segnale long su 0 (ATR 10 → SL 80, TP 150 dall'ingresso a 100 sulla candela 1).
FLAT = (100.0, 105.0, 95.0, 100.0)


def test_take_profit_is_exactly_2_5_r_without_costs() -> None:
    res = _run([FLAT, FLAT, (100, 151, 99, 140)])
    t = res.trades.iloc[0]
    assert t.entry_time == res.equity.index[1]  # apertura della candela successiva
    assert (t.entry_price, t.amount) == (100.0, 5.0)  # 1% di 10 000 / (2·10)
    assert t.exit_reason == "take_profit" and t.exit_price == 150.0
    assert t.r_multiple == pytest.approx(2.5)
    assert res.equity.iloc[-1] == pytest.approx(10_250.0)


def test_sl_and_tp_in_same_candle_assumes_stop_first() -> None:
    t = _run([FLAT, FLAT, (100, 160, 70, 100)]).trades.iloc[0]
    assert t.exit_reason == "stop_loss" and t.r_multiple == pytest.approx(-1.0)


def test_gap_through_stop_fills_at_open() -> None:
    t = _run([FLAT, FLAT, (70, 72, 65, 68)]).trades.iloc[0]
    assert t.exit_reason == "stop_loss_gap" and t.exit_price == 70.0
    assert t.r_multiple == pytest.approx(-1.5)


def test_fees_reduce_r_multiple() -> None:
    cfg = BacktestConfig(fee_rate=0.001, slippage_bps=0)
    res = _run([FLAT, FLAT, (100, 151, 99, 140)], cfg=cfg)
    t = res.trades.iloc[0]
    assert t.fees == pytest.approx(100 * 5 * 0.001 + 150 * 5 * 0.001)
    assert t.r_multiple == pytest.approx((250 - 1.25) / 100)
    assert res.equity.iloc[-1] == pytest.approx(10_248.75)


def test_slippage_makes_stop_worse_than_1r() -> None:
    cfg = BacktestConfig(fee_rate=0, slippage_bps=10)
    t = _run([FLAT, FLAT, (100, 101, 50, 60)], cfg=cfg).trades.iloc[0]
    assert t.entry_price == pytest.approx(100.1)
    assert t.exit_price == pytest.approx((100.1 - 20) * 0.999)
    assert t.r_multiple < -1.0


def test_short_take_profit() -> None:
    rows = [FLAT, FLAT, (100, 101, 49, 60)]
    t = _run(rows, long=[False] * 3, short=[True, False, False]).trades.iloc[0]
    assert t.side == "short" and t.exit_reason == "take_profit"
    assert t.r_multiple == pytest.approx(2.5)


def test_signal_on_last_candle_is_not_executed() -> None:
    res = _run([FLAT, FLAT], long=[False, True])
    assert res.trades.empty


def test_open_position_closed_at_end_of_data() -> None:
    t = _run([FLAT, FLAT, (100, 110, 95, 110)]).trades.iloc[0]
    assert t.exit_reason == "end_of_data" and t.pnl == pytest.approx(50.0)


def test_signal_ignored_while_in_position() -> None:
    res = _run([FLAT] * 5, long=[True] * 5)
    assert len(res.trades) == 1


def test_real_strategy_run_is_consistent() -> None:
    ohlcv = make_ohlcv(3_000, seed=11)
    res = Backtester(Strategy(StrategyConfig()), PositionSizer()).run(ohlcv)
    assert len(res.equity) == len(ohlcv)
    t = res.trades
    assert len(t) > 0
    # Nessuna sovrapposizione e ingresso sempre alla candela successiva al segnale.
    assert (t["entry_time"].iloc[1:].to_numpy() > t["exit_time"].iloc[:-1].to_numpy()).all()
    assert (t["entry_time"] - t["signal_time"] == pd.Timedelta("15min")).all()
    # Il rischio per trade non supera l'1% del saldo al momento dell'ingresso.
    assert (t["risk_amount"] <= 0.01 * res.equity.max() + 1e-9).all()
    s = res.summary()
    assert s["trade"] == len(t) and -1 <= s["max_drawdown"] <= 0
    assert set(res.yearly().columns) == {"trade", "win_rate", "expectancy_R", "pnl"}


def test_no_lookahead_future_data_does_not_change_past_trades() -> None:
    ohlcv = make_ohlcv(2_000, seed=3)
    bt = Backtester(Strategy(StrategyConfig()), PositionSizer())
    full = bt.run(ohlcv).trades
    cut = ohlcv.index[1_500]
    part = bt.run(ohlcv.loc[:cut]).trades
    done_before = full[full["exit_time"] < cut].reset_index(drop=True)
    part = part[part["exit_reason"] != "end_of_data"].reset_index(drop=True)
    pd.testing.assert_frame_equal(done_before, part.iloc[: len(done_before)])


class PagedExchange:
    """fetch_ohlcv paginato con limite rigido per richiesta."""

    def __init__(self, rows: list[list[float]]) -> None:
        self.rows, self.calls = rows, 0

    async def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        self.calls += 1
        return [r for r in self.rows if r[0] >= since][:limit]


async def test_fetch_history_paginates_and_drops_forming_candle() -> None:
    t0 = 1_704_067_200_000  # 2024-01-01 00:00 UTC
    rows = [[t0 + i * 900_000, 1.0, 2.0, 0.5, 1.5, 10.0] for i in range(10)]
    ex = PagedExchange(rows)
    until = t0 + 9 * 900_000 + 60_000  # la 10ª candela è ancora aperta
    df = await fetch_history("x", "BTC/USDT:USDT", "15m", t0, until, batch_limit=3, exchange=ex)
    assert len(df) == 9 and df.index.is_unique and ex.calls >= 3


def test_csv_roundtrip_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ohlcv = make_ohlcv(1_500, seed=5)
    path = tmp_path / "btc.csv"
    save_csv(ohlcv, path)
    loaded = load_csv(path)
    assert np.allclose(loaded.to_numpy(), ohlcv.to_numpy())
    assert (loaded.index == ohlcv.index).all()
    trades_out = tmp_path / "trades.csv"
    assert main(["--csv", str(path), "--trades-out", str(trades_out)]) == 0
    out = capsys.readouterr().out
    assert "expectancy_R" in out and "max_drawdown" in out
    assert trades_out.exists()
