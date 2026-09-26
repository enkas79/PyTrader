"""Position sizing: rischio esatto, arrotondamento per difetto, minimi exchange."""

from __future__ import annotations

import math

import pytest

from risk import MarketLimits, PositionSizer, SizingError


def truncate(step: float):
    def _round(x: float) -> float:
        return math.floor(x / step + 1e-9) * step
    return _round


def test_raw_size_formula() -> None:
    # (10 000 · 0.01) / (2 · 150) = 0.3333…
    assert PositionSizer(0.01).raw_size(10_000, 300) == pytest.approx(100 / 300)


def test_risk_never_exceeds_target_after_rounding() -> None:
    res = PositionSizer(0.01, 10).size(
        10_000, 300, 60_000, MarketLimits(min_amount=0.001, min_cost=5), truncate(0.001)
    )
    assert res.order_amount == pytest.approx(0.333)
    assert res.risk_amount <= 100.0
    assert res.risk_fraction == pytest.approx(0.00999)
    assert not res.capped


def test_notional_cap_reduces_risk() -> None:
    # Size teorica 0.333 BTC = 20 000 USDT > tetto 1× saldo = 10 000 USDT.
    res = PositionSizer(0.01, 1.0).size(
        10_000, 300, 60_000, MarketLimits(min_amount=0.001), truncate(0.001)
    )
    assert res.capped
    assert res.notional <= 10_000
    assert res.risk_fraction < 0.01


def test_below_min_amount_is_rejected_not_rounded_up() -> None:
    with pytest.raises(SizingError, match="minimo"):
        PositionSizer(0.01).size(100, 300, 60_000, MarketLimits(min_amount=0.01), truncate(0.001))


def test_below_min_cost_is_rejected() -> None:
    with pytest.raises(SizingError, match="Nozionale"):
        PositionSizer(0.01).size(
            1_000, 3_000, 60_000, MarketLimits(min_amount=0.001, min_cost=200), truncate(0.001)
        )


def test_contract_size_conversion() -> None:
    # 1 contratto = 0.01 BTC → 0.3333 BTC = 33 contratti.
    res = PositionSizer(0.01, 10).size(
        10_000, 300, 60_000, MarketLimits(min_amount=1, contract_size=0.01), truncate(1)
    )
    assert res.order_amount == 33
    assert res.base_amount == pytest.approx(0.33)


@pytest.mark.parametrize("balance,dist", [(0, 100), (-1, 100), (1000, 0), (1000, float("nan"))])
def test_invalid_inputs(balance: float, dist: float) -> None:
    with pytest.raises(SizingError):
        PositionSizer().raw_size(balance, dist)


def test_config_validation_rules(bot_config) -> None:
    from dataclasses import replace

    from config import ConfigError

    spot = replace(bot_config.exchange, symbol="BTC/USDT")
    with pytest.raises(ConfigError, match="spot"):
        replace(bot_config, exchange=spot).validate()  # short abilitato sullo spot
    low_lev = replace(bot_config.exchange, leverage=2)
    with pytest.raises(ConfigError, match="LEVERAGE"):
        replace(bot_config, exchange=low_lev).validate()
    live = replace(bot_config.runtime, dry_run=False)
    with pytest.raises(ConfigError, match="API_KEY"):
        replace(bot_config, runtime=live).validate()
    bot_config.validate()
