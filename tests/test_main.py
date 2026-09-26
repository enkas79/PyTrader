"""Sincronizzazione temporale e ciclo completo del bot in paper trading."""

from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from config import BotConfig
from db_manager import DatabaseManager
from execution import ExecutionEngine
from main import (
    STATE_LAST_CANDLE,
    TradingBot,
    last_closed_candle_open_ms,
    seconds_until_next_candle,
)
from strategy import Side, Signal, Strategy
from tests.conftest import make_ohlcv
from tests.test_execution import FakeExchange

T0 = 1_700_000_100  # multiplo esatto di 900 s


@pytest.mark.parametrize(
    "now,expected",
    [(T0, 5.0), (T0 + 3, 2.0), (T0 + 5, 900.0), (T0 + 100, 805.0), (T0 + 899.5, 5.5)],
)
def test_seconds_until_next_candle(now: float, expected: float) -> None:
    assert seconds_until_next_candle(now, 900, 5.0) == pytest.approx(expected)


def test_last_closed_candle_open_ms() -> None:
    assert last_closed_candle_open_ms(T0 + 5, 900) == (T0 - 900) * 1000
    assert last_closed_candle_open_ms(T0 + 899, 900) == (T0 - 900) * 1000


class ScriptedStrategy(Strategy):
    """Strategia che emette un segnale prefissato sull'ultima candela."""

    def __init__(self, cfg, side: Side | None) -> None:
        super().__init__(cfg)
        self.side = side

    def evaluate(self, ohlcv: pd.DataFrame) -> Signal | None:
        if self.side is None:
            return None
        close, atr = float(ohlcv["close"].iloc[-1]), 150.0
        sl, tp = self.levels(self.side, close, atr)
        return Signal(self.side, int(ohlcv.index[-1].timestamp() * 1000), close, atr, 300.0, sl, tp)


def _exchange_with_candles(now_s: float) -> FakeExchange:
    ex = FakeExchange()
    last_open = last_closed_candle_open_ms(now_s, 900)
    df = make_ohlcv(300)
    end = pd.Timestamp(last_open, unit="ms", tz="UTC")
    df.index = pd.date_range(end=end, periods=300, freq="15min")
    ex.ohlcv = [
        [int(ts.timestamp() * 1000), r.open, r.high, r.low, r.close, r.volume]
        for ts, r in df.iterrows()
    ]
    return ex


async def _bot(
    cfg: BotConfig, db: DatabaseManager, ex: FakeExchange, side: Side | None
) -> TradingBot:
    engine = ExecutionEngine(cfg, ex)
    await engine.connect()
    return TradingBot(cfg, db, engine, ScriptedStrategy(cfg.strategy, side))


async def test_paper_cycle_opens_persists_and_recovers(bot_config: BotConfig, monkeypatch) -> None:
    now = float(T0 + 5)
    monkeypatch.setattr("main.time.time", lambda: now)
    ex = _exchange_with_candles(now)

    with DatabaseManager(bot_config.runtime.db_path) as db:
        bot = await _bot(bot_config, db, ex, Side.LONG)
        await bot.process_candle(now)
        trade = db.get_open_trade(bot_config.exchange.symbol)
        assert trade is not None and trade.side == "long"
        # Rischio 1% di 10 000 con stop 300 → 0.333 BTC (troncato a 0.001)
        assert trade.amount == pytest.approx(0.333)
        assert trade.stop_loss == pytest.approx(60_000 - 300)
        assert trade.take_profit == pytest.approx(60_000 + 750)
        assert db.get_state(STATE_LAST_CANDLE) == str(last_closed_candle_open_ms(now, 900))
        await bot.engine.close()

    # "Riavvio": nuovo processo, stesso DB → nessun doppio ingresso.
    with DatabaseManager(bot_config.runtime.db_path) as db:
        bot = await _bot(bot_config, db, _exchange_with_candles(now + 900), Side.SHORT)
        await bot.startup_checks()
        monkeypatch.setattr("main.time.time", lambda: now + 900)
        await bot.process_candle(now + 900)
        open_trade = db.get_open_trade(bot_config.exchange.symbol)
        assert open_trade is not None and open_trade.id == trade.id
        await bot.engine.close()


async def test_candle_not_processed_twice(bot_config: BotConfig, monkeypatch) -> None:
    now = float(T0 + 5)
    monkeypatch.setattr("main.time.time", lambda: now)
    ex = _exchange_with_candles(now)
    with DatabaseManager(bot_config.runtime.db_path) as db:
        bot = await _bot(bot_config, db, ex, None)
        await bot.process_candle(now)
        bot.strategy.side = Side.LONG
        await bot.process_candle(now + 1)  # stessa candela
        assert db.get_open_trade(bot_config.exchange.symbol) is None
        await bot.engine.close()


async def test_software_exit_hits_stop_loss(bot_config: BotConfig, monkeypatch) -> None:
    now = float(T0 + 5)
    monkeypatch.setattr("main.time.time", lambda: now)
    ex = _exchange_with_candles(now)
    with DatabaseManager(bot_config.runtime.db_path) as db:
        bot = await _bot(bot_config, db, ex, Side.LONG)
        await bot.process_candle(now)
        trade = db.get_open_trade(bot_config.exchange.symbol)

        async def crash(symbol, params=None):
            return {"last": trade.stop_loss - 1}

        ex.fetch_ticker = crash  # type: ignore[method-assign]
        task = asyncio.create_task(bot._exit_monitor())
        await asyncio.sleep(0.05)
        bot.request_stop()
        await task

        closed = db.get_trade(trade.id)
        assert closed.exit_reason == "stop_loss"
        assert closed.pnl == pytest.approx(-(trade.entry_price - closed.exit_price) * trade.amount)
        assert closed.pnl / trade.risk_amount == pytest.approx(-1.0, abs=0.01)
        await bot.engine.close()


@pytest.mark.parametrize(
    "side,price,reason",
    [("long", 89, "stop_loss"), ("long", 151, "take_profit"), ("long", 100, None),
     ("short", 111, "stop_loss"), ("short", 49, "take_profit"), ("short", 100, None)],
)
def test_exit_reason(side: str, price: float, reason: str | None) -> None:
    from db_manager import Trade

    sl, tp = (90.0, 150.0) if side == "long" else (110.0, 50.0)
    t = Trade("X", side, 100.0, 1.0, sl, tp, 5.0, 10.0, 0)
    assert TradingBot.exit_reason(t, price) == reason
