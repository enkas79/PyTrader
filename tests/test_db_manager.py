"""Persistenza SQLite e recupero dopo riavvio."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from db_manager import DatabaseManager, Trade, TradeStatus

SYMBOL = "BTC/USDT:USDT"


def _trade(**kw) -> Trade:
    base = dict(
        symbol=SYMBOL, side="long", entry_price=100.0, amount=2.0, stop_loss=80.0,
        take_profit=150.0, atr=10.0, risk_amount=40.0, signal_candle=1,
    )
    base.update(kw)
    return Trade(**base)


def test_open_trade_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "bot.sqlite3"
    with DatabaseManager(path) as db:
        trade = db.insert_trade(_trade(entry_order_id="e1"))
        db.update_protective_orders(trade.id, "sl1", "tp1")
    with DatabaseManager(path) as db:  # "riavvio"
        recovered = db.get_open_trade(SYMBOL)
    assert recovered is not None
    assert recovered.entry_price == 100.0 and recovered.amount == 2.0
    assert (recovered.sl_order_id, recovered.tp_order_id) == ("sl1", "tp1")
    assert recovered.status is TradeStatus.OPEN


def test_single_open_trade_per_symbol(tmp_path: Path) -> None:
    with DatabaseManager(tmp_path / "x.sqlite3") as db:
        db.insert_trade(_trade())
        with pytest.raises(sqlite3.IntegrityError):
            db.insert_trade(_trade())


def test_close_trade_and_realized_pnl(tmp_path: Path) -> None:
    with DatabaseManager(tmp_path / "x.sqlite3") as db:
        t = db.insert_trade(_trade())
        db.close_trade(t.id, 150.0, "take_profit", t.gross_pnl(150.0))
        assert db.get_open_trade(SYMBOL) is None
        closed = db.get_trade(t.id)
        assert closed.status is TradeStatus.CLOSED and closed.pnl == 100.0
        assert db.realized_pnl(SYMBOL) == 100.0
        db.insert_trade(_trade(side="short"))  # nuovo trade consentito dopo la chiusura


def test_short_pnl_sign() -> None:
    assert _trade(side="short").gross_pnl(90.0) == pytest.approx(20.0)


def test_state_upsert(tmp_path: Path) -> None:
    with DatabaseManager(tmp_path / "x.sqlite3") as db:
        assert db.get_state("k") is None
        db.set_state("k", "1")
        db.set_state("k", "2")
        assert db.get_state("k") == "2"
