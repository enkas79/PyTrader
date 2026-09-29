import numpy as np
import pandas as pd
import pytest

from pytrader.backtest import Backtester, BacktestParams, TradeOutcome
from pytrader.models import Direction
from pytrader.signals import SignalEngine, SignalParams, TargetMode
from tests.conftest import make_ohlcv


def _setups_key(setups):
    return [
        (s.signal_index, s.direction, s.pattern, s.entry, s.stop_loss, s.take_profit)
        for s in setups
    ]


def test_segnali_senza_lookahead(random_walk: pd.DataFrame) -> None:
    """I setup passati non cambiano se si aggiungono candele future."""
    engine = SignalEngine()
    full = engine.analyze(random_walk).setups
    assert full, "la serie di test deve generare setup"
    cut = 1000
    part = engine.analyze(random_walk.iloc[:cut]).setups
    # Il segnale sull'ultima candela del troncamento ha entry stimata: si esclude
    part_done = [s for s in part if not s.entry_is_estimate]
    full_done = [s for s in full if s.signal_index < cut - 1]
    assert _setups_key(part_done) == _setups_key(full_done)


def test_proprieta_setup(random_walk: pd.DataFrame) -> None:
    params = SignalParams(min_rr=2.0, sl_buffer_atr=1.5)
    result = SignalEngine(params).analyze(random_walk)
    for s in result.setups:
        assert s.risk_reward >= 2.0 - 1e-9
        assert s.entry == random_walk["open"].iat[s.signal_index + 1]
        if s.direction is Direction.LONG:
            assert s.stop_loss <= s.level.lower - 1.5 * s.atr + 1e-9
            assert s.level.price <= random_walk["close"].iat[s.signal_index]
        else:
            assert s.stop_loss >= s.level.upper + 1.5 * s.atr - 1e-9
            assert s.level.price >= random_walk["close"].iat[s.signal_index]


def test_target_fixed_rr(random_walk: pd.DataFrame) -> None:
    result = SignalEngine(SignalParams(target_mode=TargetMode.FIXED_RR, min_rr=3)).analyze(
        random_walk
    )
    assert result.setups
    assert all(np.isclose(s.risk_reward, 3.0) for s in result.setups)


def _scenario() -> pd.DataFrame:
    """Due minimi a ~100 (supporto), massimi a ~110 (resistenza), poi hammer sul supporto."""
    rows = []
    for _ in range(3):
        for p in np.linspace(110, 100, 10)[:-1]:
            rows.append((p + 0.3, p + 0.8, p - 0.2, p))
        for p in np.linspace(100, 110, 10)[:-1]:
            rows.append((p - 0.3, p + 0.2, p - 0.8, p))
    for p in np.linspace(110, 101, 8):
        rows.append((p + 0.3, p + 0.8, p - 0.2, p))
    rows.append((101.0, 101.3, 99.6, 101.2))  # hammer che testa il supporto
    rows.append((101.2, 102.0, 101.0, 101.8))  # candela d'ingresso
    for p in np.linspace(102, 111, 10):
        rows.append((p - 0.2, p + 0.3, p - 0.4, p))
    return make_ohlcv(rows)


def test_scenario_hammer_su_supporto_vince() -> None:
    df = _scenario()
    params = SignalParams(pivot_window=3, min_rr=1.5)
    result = SignalEngine(params).analyze(df)
    hammer_idx = len(df) - 12
    setup = next(s for s in result.setups if s.signal_index == hammer_idx)
    assert setup.direction is Direction.LONG
    assert setup.level.lower <= 100.0 <= setup.level.upper + 1
    assert setup.take_profit > 108  # resistenza strutturale ~110
    bt = Backtester().run(df, [setup])
    assert bt.trades[0].outcome is TradeOutcome.WIN
    assert bt.trades[0].r_multiple == pytest.approx(setup.risk_reward)


def test_backtest_metriche_e_commissioni() -> None:
    df = _scenario()
    setup = next(
        s
        for s in SignalEngine(SignalParams(pivot_window=3, min_rr=1.5)).analyze(df).setups
        if s.signal_index == len(df) - 12
    )
    no_fee = Backtester().run(df, [setup])
    fee = Backtester(BacktestParams(fee_rate=0.001)).run(df, [setup])
    assert fee.total_r < no_fee.total_r
    s = no_fee.summary()
    assert s["trades"] == 1 and s["win_rate"] == 1.0 and s["max_drawdown_r"] == 0.0


def test_backtest_sl_prima_del_tp_e_pending(random_walk: pd.DataFrame) -> None:
    result = SignalEngine().analyze(random_walk)
    bt = Backtester().run(random_walk, result.setups)
    closed = bt.closed
    assert closed
    for t in closed:
        if t.outcome is TradeOutcome.LOSS and t.exit_price == t.setup.stop_loss:
            assert t.r_multiple == pytest.approx(-1.0)
    # posizione singola: le finestre dei trade non si sovrappongono
    windows = [(t.setup.signal_index + 1, t.exit_index) for t in closed]
    for (_, end_a), (start_b, _) in zip(windows, windows[1:]):
        assert start_b > end_a
