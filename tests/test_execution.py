"""ExecutionEngine offline: mercati iniettati in un'istanza ccxt reale."""

from __future__ import annotations

from typing import Any

import ccxt.async_support as ccxt_async
import pandas as pd
import pytest

from config import BotConfig
from execution import ExecutionEngine
from strategy import Side, SizingError

MARKET: dict[str, Any] = {
    "id": "BTCUSDT", "symbol": "BTC/USDT:USDT", "base": "BTC", "quote": "USDT",
    "settle": "USDT", "baseId": "BTC", "quoteId": "USDT", "settleId": "USDT",
    "type": "swap", "spot": False, "margin": False, "swap": True, "future": False,
    "option": False, "contract": True, "linear": True, "inverse": False,
    "contractSize": 1.0, "active": True,
    "precision": {"amount": 0.001, "price": 0.1},
    "limits": {"amount": {"min": 0.001, "max": 1000.0}, "cost": {"min": 100.0},
               "price": {}, "leverage": {}},
}


class FakeExchange(ccxt_async.binanceusdm):
    """binanceusdm senza rete: OHLCV, ticker e ordini registrati localmente."""

    def __init__(self) -> None:
        super().__init__()
        self.set_markets([MARKET])
        self.orders: list[tuple] = []
        self.ohlcv: list[list[float]] = []

    async def load_markets(self, reload: bool = False, params: dict | None = None):
        return self.markets

    async def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None):
        return self.ohlcv

    async def fetch_time(self, params=None):
        return self.milliseconds()

    async def fetch_ticker(self, symbol, params=None):
        return {"last": 60_000.0}

    async def create_order(self, symbol, type, side, amount, price=None, params=None):
        self.orders.append((type, side, amount, dict(params or {})))
        return {"id": str(len(self.orders)), "average": 60_010.0, "filled": amount}


@pytest.fixture
async def engine(bot_config: BotConfig):
    eng = ExecutionEngine(bot_config, FakeExchange())
    await eng.connect()
    yield eng
    await eng.close()


async def test_order_size_respects_precision_and_risk(engine: ExecutionEngine) -> None:
    res = engine.compute_order_size(10_000, 300, 60_000)
    assert res.order_amount == pytest.approx(0.333)  # troncato, non arrotondato
    assert res.risk_amount <= 100.0


async def test_order_size_below_exchange_minimum(engine: ExecutionEngine) -> None:
    with pytest.raises(SizingError):
        engine.compute_order_size(50, 300, 60_000)


async def test_fetch_closed_ohlcv_drops_forming_candle(engine: ExecutionEngine) -> None:
    t0 = 1_700_000_100_000  # multiplo di 15m
    engine.exchange.ohlcv = [[t0 + i * 900_000, 1, 2, 0.5, 1.5, 10] for i in range(3)]
    now_ms = t0 + 2 * 900_000 + 5_000  # 3ª candela appena iniziata
    df = await engine.fetch_closed_ohlcv(now_ms)
    assert len(df) == 2
    assert df.index[-1] == pd.Timestamp(t0 + 900_000, unit="ms", tz="UTC")


async def test_paper_orders_do_not_hit_exchange(engine: ExecutionEngine) -> None:
    fill = await engine.open_position(Side.LONG, 0.333)
    assert fill.order_id.startswith("paper-") and fill.price == 60_000.0
    assert engine.exchange.orders == []


async def test_live_protection_orders_are_reduce_only(bot_config: BotConfig) -> None:
    from dataclasses import replace

    cfg = replace(bot_config, runtime=replace(bot_config.runtime, dry_run=False))
    eng = ExecutionEngine(cfg, FakeExchange())
    eng._market = eng.exchange.market(cfg.exchange.symbol)  # evita set_leverage in rete
    fill = await eng.open_position(Side.SHORT, 0.3337)
    sl_id, tp_id = await eng.place_protection(Side.SHORT, fill.amount, 60_600.04, 58_500.06)
    entry, sl, tp = eng.exchange.orders
    assert entry[:3] == ("market", "sell", 0.333)
    assert sl[1] == "buy" and sl[3] == {"stopLossPrice": 60_600.0, "reduceOnly": True}
    assert tp[1] == "buy" and tp[3] == {"takeProfitPrice": 58_500.1, "reduceOnly": True}
    assert (sl_id, tp_id) == ("2", "3")
    await eng.close()


async def test_backoff_retries_transient_errors(monkeypatch) -> None:
    from ccxt.base.errors import RateLimitExceeded

    from execution import with_backoff

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("execution.asyncio.sleep", no_sleep)
    calls = {"n": 0}

    @with_backoff(attempts=4)
    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RateLimitExceeded("429")
        return "ok"

    assert await flaky() == "ok" and calls["n"] == 3


async def test_backoff_does_not_retry_permanent_errors(monkeypatch) -> None:
    from ccxt.base.errors import AuthenticationError

    from execution import with_backoff

    calls = {"n": 0}

    @with_backoff(attempts=4)
    async def bad_key() -> None:
        calls["n"] += 1
        raise AuthenticationError("invalid api key")

    with pytest.raises(AuthenticationError):
        await bad_key()
    assert calls["n"] == 1
