import pandas as pd
import pytest

from pytrader.models import Direction, Level, PatternType, TargetSource, TradeSetup

LEVEL = Level(price=100, lower=99, upper=101, touches=3, first_index=0, last_index=10)


def _setup(direction: Direction, entry: float, sl: float, tp: float) -> TradeSetup:
    return TradeSetup(
        signal_index=5,
        signal_time=pd.Timestamp("2024-01-01", tz="UTC"),
        direction=direction,
        pattern=PatternType.HAMMER,
        level=LEVEL,
        entry=entry,
        stop_loss=sl,
        take_profit=tp,
        atr=1.0,
        target_source=TargetSource.STRUCTURAL,
    )


def test_level_distance() -> None:
    assert LEVEL.distance(100) == 0
    assert LEVEL.distance(97) == 2
    assert LEVEL.distance(103.5) == 2.5


def test_level_incoerente() -> None:
    with pytest.raises(ValueError):
        Level(price=105, lower=99, upper=101, touches=2, first_index=0, last_index=1)


def test_setup_risk_reward() -> None:
    s = _setup(Direction.LONG, 100, 98, 106)
    assert s.risk == 2 and s.reward == 6 and s.risk_reward == 3
    d = s.to_dict()
    assert d["direction"] == "long" and d["risk_reward"] == 3


def test_setup_livelli_incoerenti() -> None:
    with pytest.raises(ValueError):
        _setup(Direction.LONG, 100, 101, 106)
    with pytest.raises(ValueError):
        _setup(Direction.SHORT, 100, 98, 95)


def test_pattern_bias() -> None:
    assert PatternType.HAMMER.bias is Direction.LONG
    assert PatternType.EVENING_STAR.bias is Direction.SHORT
    assert PatternType.DOJI.bias is None
    assert PatternType.MORNING_STAR.n_bars == 3
