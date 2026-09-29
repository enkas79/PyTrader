from __future__ import annotations

import pandas as pd
import pytest

from pytrader.backtest import (
    BacktestResult,
    MoneyParams,
    TradeOutcome,
    TradeResult,
    simulate_money,
)
from pytrader.models import Direction, Level, PatternType, TargetSource, TradeSetup

LEVEL = Level(price=100, lower=99, upper=101, touches=2, first_index=0, last_index=1)


def _trade(outcome: TradeOutcome, r: float | None, entry=100.0, sl=98.0, tp=104.0, idx=1):
    setup = TradeSetup(
        signal_index=idx,
        signal_time=pd.Timestamp("2024-01-01", tz="UTC"),
        direction=Direction.LONG,
        pattern=PatternType.HAMMER,
        level=LEVEL,
        entry=entry,
        stop_loss=sl,
        take_profit=tp,
        atr=1.0,
        target_source=TargetSource.STRUCTURAL,
    )
    return TradeResult(setup, outcome, r_multiple=r)


def test_sizing_a_rischio_fisso() -> None:
    # 10.000 × 1% = 100 di rischio; stop a 2 -> 50 unità, nozionale 5.000 (entro leva 1)
    res = simulate_money([_trade(TradeOutcome.WIN, 2.0)], MoneyParams(10_000, 1.0, 1.0))
    plan = res.plans[0]
    assert plan.quantity == pytest.approx(50)
    assert plan.notional == pytest.approx(5_000)
    assert plan.risk_amount == pytest.approx(100)
    assert plan.reward_amount == pytest.approx(200)
    assert plan.pnl == pytest.approx(200)
    assert res.final_equity == pytest.approx(10_200)
    assert res.return_pct == pytest.approx(2.0)
    assert not plan.capped


def test_limite_di_leva() -> None:
    # stop a 0,5: servirebbero 200 unità (20.000 di nozionale) ma la leva 1 consente 100
    res = simulate_money([_trade(TradeOutcome.LOSS, -1.0, sl=99.5)], MoneyParams(10_000, 1.0))
    plan = res.plans[0]
    assert plan.capped and plan.quantity == pytest.approx(100)
    assert plan.pnl == pytest.approx(-50)  # rischio effettivo 0,5% invece dell'1%


def test_compounding_e_drawdown() -> None:
    trades = [
        _trade(TradeOutcome.LOSS, -1.0, idx=1),
        _trade(TradeOutcome.LOSS, -1.0, idx=2),
        _trade(TradeOutcome.WIN, 2.0, idx=3),
    ]
    comp = simulate_money(trades, MoneyParams(10_000, 10.0, 10.0))
    # 10.000 -> 9.000 -> 8.100 -> 9.720
    assert [p.equity_after for p in comp.plans] == pytest.approx([9_000, 8_100, 9_720])
    assert comp.max_drawdown_amount == pytest.approx(1_900)
    assert comp.max_drawdown_pct == pytest.approx(19.0)
    fixed = simulate_money(trades, MoneyParams(10_000, 10.0, 10.0, compounding=False))
    assert fixed.final_equity == pytest.approx(10_000)  # -1000 -1000 +2000


def test_saltati_aperti_e_pending() -> None:
    trades = [
        _trade(TradeOutcome.SKIPPED, None, idx=1),
        _trade(TradeOutcome.OPEN, None, idx=2),
    ]
    res = simulate_money(trades, MoneyParams(10_000, 1.0))
    assert res.plans[0].quantity == 0 and res.plans[0].pnl is None
    assert res.plans[1].quantity == pytest.approx(50) and res.plans[1].pnl is None
    assert res.final_equity == 10_000


def test_parametri_non_validi() -> None:
    with pytest.raises(ValueError):
        MoneyParams(0)
    with pytest.raises(ValueError):
        MoneyParams(1000, risk_pct=0)


def test_coerenza_con_backtest_reale(random_walk) -> None:
    from pytrader.services import run_analysis

    bundle = run_analysis(random_walk)
    res = simulate_money(bundle.backtest.trades, MoneyParams(10_000, 1.0, 100.0, False))
    # senza compounding e senza limite di leva, P&L = R × 100
    assert isinstance(bundle.backtest, BacktestResult)
    assert res.net_profit == pytest.approx(bundle.backtest.total_r * 100)
