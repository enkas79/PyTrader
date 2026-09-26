"""Backtest della strategia con commissioni, slippage e vincoli dell'exchange.

Riusa **gli stessi** componenti del bot live (:class:`strategy.Strategy` per i
segnali e :class:`strategy.PositionSizer` per la size), così che il backtest
misuri il codice che andrà in produzione e non una sua reimplementazione.

Modello di esecuzione (conservativo, allineato al comportamento live):

* **Ingresso**: il segnale nasce alla chiusura della candela *i*; l'ordine
  market viene eseguito all'**apertura della candela i+1** (± slippage). SL e
  TP sono ancorati al prezzo di esecuzione con l'ATR della candela di segnale.
* **Uscite** (ordini condizionali market sull'exchange), valutate a partire
  dalla candela di ingresso:

  1. gap in apertura oltre lo SL → uscita all'apertura (perdita > 1R);
  2. gap in apertura oltre il TP → uscita all'apertura;
  3. SL e TP entrambi toccati nella stessa candela → si assume **prima lo SL**
     (con dati OHLC l'ordine intrabar non è osservabile: ipotesi pessimistica);
  4. altrimenti uscita al livello toccato (± slippage).

* **Costi**: commissione taker su ingresso e uscita, slippage in bps su ogni
  fill market. Il funding dei perpetual **non** è modellato.
* **Capitale**: la size usa il saldo realizzato (come ``fetch_equity`` live)
  e un solo trade alla volta; dopo un'uscita intrabar un nuovo segnale sulla
  chiusura della stessa candela è ammesso, come nel bot.

Uso da riga di comando::

    python src/backtest.py --fetch --since 2024-01-01 --save data/btc_15m.csv
    python src/backtest.py --csv data/btc_15m.csv --fee 0.0005 --slippage-bps 2
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import math
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import ccxt.async_support as ccxt_async
import numpy as np
import pandas as pd

from config import TIMEFRAME_SECONDS, RiskConfig, StrategyConfig
from execution import retry_async
from strategy import MarketLimits, PositionSizer, Side, SizingError, Strategy

OHLCV_HEADER: list[str] = ["timestamp", "open", "high", "low", "close", "volume"]


# ---------------------------------------------------------------------- #
# Dati storici
# ---------------------------------------------------------------------- #
def index_to_ms(index: pd.Index) -> np.ndarray:
    """Timestamp in millisecondi epoch, indipendentemente dall'unità interna.

    pandas 3 usa di default i microsecondi (pandas 2 i nanosecondi): la
    conversione esplicita a ``ms`` evita errori di scala.

    Args:
        index: ``DatetimeIndex`` da convertire.

    Returns:
        Array ``int64`` di millisecondi.
    """
    return pd.DatetimeIndex(index).as_unit("ms").asi8


def ohlcv_to_frame(rows: list[list[float]]) -> pd.DataFrame:
    """Converte righe OHLCV ccxt in DataFrame con ``DatetimeIndex`` UTC.

    Args:
        rows: Liste ``[timestamp_ms, open, high, low, close, volume]``.

    Returns:
        DataFrame ordinato, senza duplicati, con colonne OHLCV float.
    """
    df = pd.DataFrame(rows, columns=OHLCV_HEADER)
    df = df.drop_duplicates("timestamp").sort_values("timestamp")
    df.index = pd.to_datetime(df.pop("timestamp"), unit="ms", utc=True)
    df.index.name = "datetime"
    return df.astype(float)


async def fetch_history(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    since_ms: int,
    until_ms: int | None = None,
    batch_limit: int = 1000,
    exchange: ccxt_async.Exchange | None = None,
) -> pd.DataFrame:
    """Scarica lo storico OHLCV paginando ``fetch_ohlcv`` (solo API pubbliche).

    Args:
        exchange_id: Id ccxt dell'exchange.
        symbol: Simbolo ccxt.
        timeframe: Timeframe (es. ``15m``).
        since_ms: Inizio dello storico (ms).
        until_ms: Fine dello storico (ms); default: adesso.
        batch_limit: Candele per richiesta.
        exchange: Istanza ccxt da usare (iniezione per i test); se ``None``
            ne viene creata e chiusa una.

    Returns:
        DataFrame OHLCV di sole candele chiuse.
    """
    own = exchange is None
    ex = exchange or getattr(ccxt_async, exchange_id)({"enableRateLimit": True})
    tf_ms = TIMEFRAME_SECONDS[timeframe] * 1000
    end = until_ms if until_ms is not None else int(time.time() * 1000)
    rows: list[list[float]] = []
    cursor = since_ms
    try:
        while cursor < end:
            batch = await retry_async(
                functools.partial(
                    ex.fetch_ohlcv, symbol, timeframe, since=cursor, limit=batch_limit
                )
            )
            if not batch:
                break
            rows.extend(batch)
            last_ts = int(batch[-1][0])
            if last_ts < cursor:  # l'exchange non avanza: evita loop infiniti
                break
            cursor = last_ts + tf_ms
    finally:
        if own:
            await ex.close()
    df = ohlcv_to_frame(rows)
    closed = (index_to_ms(df.index) + tf_ms) <= end
    return df[closed & (df.index >= pd.Timestamp(since_ms, unit="ms", tz="UTC"))]


def load_csv(path: Path) -> pd.DataFrame:
    """Legge un CSV ``timestamp,open,high,low,close,volume`` (timestamp in ms).

    Args:
        path: Percorso del file.

    Returns:
        DataFrame OHLCV con indice UTC.
    """
    raw = pd.read_csv(path)
    return ohlcv_to_frame(raw[OHLCV_HEADER].values.tolist())


def save_csv(df: pd.DataFrame, path: Path) -> None:
    """Salva un DataFrame OHLCV nel formato letto da :func:`load_csv`.

    Args:
        df: DataFrame OHLCV con indice UTC.
        path: File di destinazione (le directory mancanti vengono create).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df[["open", "high", "low", "close", "volume"]].copy()
    out.insert(0, "timestamp", index_to_ms(df.index))
    out.to_csv(path, index=False)


# ---------------------------------------------------------------------- #
# Motore di backtest
# ---------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Ipotesi di costo e vincoli di mercato del backtest.

    I default riflettono Binance USDⓈ-M BTCUSDT (taker 0,05%, passo 0,001 BTC,
    nozionale minimo 100 USDT).

    Attributes:
        initial_balance: Capitale iniziale in valuta di quotazione.
        fee_rate: Commissione per lato sul nozionale (taker).
        slippage_bps: Slippage sfavorevole su ogni fill market, in bps.
        limits: Vincoli di mercato per il sizing.
        amount_step: Passo di quantità (troncamento per difetto).
    """

    initial_balance: float = 10_000.0
    fee_rate: float = 0.0005
    slippage_bps: float = 2.0
    limits: MarketLimits = field(
        default_factory=lambda: MarketLimits(min_amount=0.001, min_cost=100.0)
    )
    amount_step: float = 0.001

    def round_amount(self, amount: float) -> float:
        """Tronca la quantità al passo di mercato.

        Args:
            amount: Quantità da troncare.

        Returns:
            La quantità troncata (tolleranza 1e-9 sugli errori di virgola mobile).
        """
        steps = math.floor(amount / self.amount_step + 1e-9)
        return round(steps * self.amount_step, 12)

    def slip(self, price: float, order_side: str) -> float:
        """Applica lo slippage sfavorevole a un fill market.

        Args:
            price: Prezzo teorico.
            order_side: ``buy`` (paga di più) o ``sell`` (incassa di meno).

        Returns:
            Il prezzo di esecuzione simulato.
        """
        factor = self.slippage_bps / 10_000
        return price * (1 + factor) if order_side == "buy" else price * (1 - factor)


@dataclass(slots=True)
class BacktestTrade:
    """Trade simulato."""

    side: str
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    entry_price: float
    amount: float
    stop_loss: float
    take_profit: float
    atr: float
    risk_amount: float
    capped: bool
    entry_index: int
    exit_time: pd.Timestamp | None = None
    exit_price: float = math.nan
    exit_reason: str = ""
    fees: float = 0.0
    gross_pnl: float = 0.0
    pnl: float = 0.0
    r_multiple: float = 0.0
    bars_held: int = 0


@dataclass(slots=True)
class BacktestResult:
    """Esito del backtest.

    Attributes:
        trades: Un trade per riga.
        equity: Equity mark-to-market alla chiusura di ogni candela.
        config: Ipotesi di costo usate.
        skipped_signals: Segnali scartati dal sizing (sotto i minimi exchange).
        buy_and_hold_return: Rendimento del buy & hold sullo stesso periodo.
    """

    trades: pd.DataFrame
    equity: pd.Series
    config: BacktestConfig
    skipped_signals: int
    buy_and_hold_return: float

    def summary(self) -> dict[str, Any]:
        """Metriche aggregate.

        Returns:
            Dizionario con trade, win rate, expectancy in R, profit factor,
            rendimento, max drawdown, Sharpe (giornaliero annualizzato su 365
            giorni), commissioni e win rate di pareggio osservato.
        """
        t = self.trades
        eq = self.equity
        drawdown = eq / eq.cummax() - 1.0
        daily = eq.resample("D").last().pct_change().dropna()
        sharpe = (
            float(daily.mean() / daily.std() * math.sqrt(365))
            if len(daily) > 1 and daily.std() > 0
            else math.nan
        )
        base: dict[str, Any] = {
            "periodo": f"{eq.index[0]:%Y-%m-%d} → {eq.index[-1]:%Y-%m-%d}",
            "trade": len(t),
            "segnali_scartati": self.skipped_signals,
            "rendimento_totale": float(eq.iloc[-1] / self.config.initial_balance - 1.0),
            "buy_and_hold": self.buy_and_hold_return,
            "max_drawdown": float(drawdown.min()),
            "sharpe": sharpe,
            "saldo_finale": float(eq.iloc[-1]),
        }
        if t.empty:
            return base
        wins, losses = t[t["pnl"] > 0], t[t["pnl"] <= 0]
        gross_loss = -losses["pnl"].sum()
        avg_win_r = float(wins["r_multiple"].mean()) if len(wins) else 0.0
        avg_loss_r = float(-losses["r_multiple"].mean()) if len(losses) else 0.0
        base.update(
            {
                "long": int((t["side"] == Side.LONG).sum()),
                "short": int((t["side"] == Side.SHORT).sum()),
                "win_rate": len(wins) / len(t),
                "win_rate_pareggio": (
                    avg_loss_r / (avg_win_r + avg_loss_r) if avg_win_r + avg_loss_r else math.nan
                ),
                "expectancy_R": float(t["r_multiple"].mean()),
                "R_medio_vincite": avg_win_r,
                "R_medio_perdite": -avg_loss_r,
                "profit_factor": (
                    float(wins["pnl"].sum() / gross_loss) if gross_loss > 0 else math.inf
                ),
                "commissioni": float(t["fees"].sum()),
                "commissioni_su_pnl_lordo": (
                    float(t["fees"].sum() / t["gross_pnl"].abs().sum())
                    if t["gross_pnl"].abs().sum() > 0
                    else math.nan
                ),
                "trade_con_tetto_nozionale": int(t["capped"].sum()),
                "candele_medie_in_posizione": float(t["bars_held"].mean()),
            }
        )
        return base

    def yearly(self) -> pd.DataFrame:
        """Scomposizione per anno di uscita (stabilità tra regimi di mercato).

        Returns:
            DataFrame indicizzato per anno con trade, win rate, expectancy e PnL.
        """
        if self.trades.empty:
            return pd.DataFrame(columns=["trade", "win_rate", "expectancy_R", "pnl"])
        t = self.trades.assign(anno=pd.DatetimeIndex(self.trades["exit_time"]).year)
        return t.groupby("anno").agg(
            trade=("pnl", "size"),
            win_rate=("pnl", lambda s: float((s > 0).mean())),
            expectancy_R=("r_multiple", "mean"),
            pnl=("pnl", "sum"),
        )


class Backtester:
    """Simulatore candela per candela della strategia live.

    Attributes:
        strategy: Strategia (stessa classe usata dal bot).
        sizer: Position sizer (stessa classe usata dal bot).
        config: Ipotesi di costo e vincoli di mercato.
    """

    def __init__(
        self, strategy: Strategy, sizer: PositionSizer, config: BacktestConfig | None = None
    ) -> None:
        """Inizializza il simulatore.

        Args:
            strategy: Strategia da testare.
            sizer: Position sizer.
            config: Ipotesi di costo; default :class:`BacktestConfig`.
        """
        self.strategy = strategy
        self.sizer = sizer
        self.config = config or BacktestConfig()

    def run(self, ohlcv: pd.DataFrame) -> BacktestResult:
        """Esegue il backtest.

        Gli indicatori sono causali (finestre mobili, RMA, VWAP cumulato),
        quindi calcolarli una sola volta sull'intero storico non introduce
        look-ahead: il valore alla candela *i* usa solo dati fino a *i*.

        Args:
            ohlcv: DataFrame OHLCV di candele chiuse, indice UTC crescente.

        Returns:
            Il :class:`BacktestResult`.
        """
        cfg = self.config
        df = self.strategy.generate_signals(ohlcv)
        idx = pd.DatetimeIndex(df.index)
        o, h, lo, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        atr = df["atr"].to_numpy(float)
        long_sig = df["long_signal"].to_numpy(bool)
        short_sig = df["short_signal"].to_numpy(bool)

        balance = cfg.initial_balance
        equity = np.empty(len(df))
        trades: list[BacktestTrade] = []
        open_trade: BacktestTrade | None = None
        # (lato, indice candela di segnale, quantità, tetto nozionale attivo)
        pending: tuple[Side, int, float, bool] | None = None
        skipped = 0

        for i in range(len(df)):
            # 1) Ingresso all'apertura della candela successiva al segnale.
            if pending is not None:
                side, sig_i, qty, capped = pending
                pending = None
                open_trade = self._open(side, sig_i, i, qty, capped, o[i], atr[sig_i], idx)
                balance -= open_trade.fees

            # 2) Uscite intrabar (inclusa la candela di ingresso).
            if open_trade is not None:
                exit_ = self._exit_price(open_trade, o[i], h[i], lo[i])
                if exit_ is not None:
                    price, reason = exit_
                    exit_fee = self._close(open_trade, price, reason, idx[i], i)
                    balance += open_trade.gross_pnl - exit_fee
                    trades.append(open_trade)
                    open_trade = None

            # 3) Mark-to-market alla chiusura.
            unrealized = 0.0
            if open_trade is not None:
                direction = 1.0 if open_trade.side == Side.LONG else -1.0
                unrealized = (c[i] - open_trade.entry_price) * open_trade.amount * direction
            equity[i] = balance + unrealized

            # 4) Nuovo segnale alla chiusura (eseguibile solo se esiste i+1).
            if open_trade is None and i + 1 < len(df) and balance > 0:
                side_sig = Side.LONG if long_sig[i] else Side.SHORT if short_sig[i] else None
                if side_sig is not None and math.isfinite(atr[i]) and atr[i] > 0:
                    try:
                        sizing = self.sizer.size(
                            balance,
                            self.strategy.config.sl_atr_mult * atr[i],
                            c[i],
                            cfg.limits,
                            cfg.round_amount,
                        )
                    except SizingError:
                        skipped += 1
                    else:
                        pending = (side_sig, i, sizing.base_amount, sizing.capped)

        if open_trade is not None:  # chiusura forzata a fine storico
            price = cfg.slip(c[-1], Side(open_trade.side).exit_order_side)
            exit_fee = self._close(open_trade, price, "end_of_data", idx[-1], len(df) - 1)
            balance += open_trade.gross_pnl - exit_fee
            trades.append(open_trade)
            equity[-1] = balance

        trades_df = pd.DataFrame([asdict(t) for t in trades])
        return BacktestResult(
            trades=trades_df,
            equity=pd.Series(equity, index=idx, name="equity"),
            config=cfg,
            skipped_signals=skipped,
            buy_and_hold_return=float(c[-1] / o[0] - 1.0),
        )

    # ------------------------------------------------------------------ #
    # Helper
    # ------------------------------------------------------------------ #
    def _open(
        self,
        side: Side,
        signal_i: int,
        i: int,
        qty: float,
        capped: bool,
        open_price: float,
        atr: float,
        idx: pd.DatetimeIndex,
    ) -> BacktestTrade:
        """Crea il trade eseguito all'apertura della candela ``i``."""
        entry = self.config.slip(open_price, side.entry_order_side)
        stop_loss, take_profit = self.strategy.levels(side, entry, atr)
        trade = BacktestTrade(
            side=side.value,
            signal_time=idx[signal_i],
            entry_time=idx[i],
            entry_price=entry,
            amount=qty,
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr=atr,
            risk_amount=qty * self.strategy.config.sl_atr_mult * atr,
            capped=capped,
            entry_index=i,
        )
        trade.fees = entry * qty * self.config.fee_rate
        return trade

    def _exit_price(
        self, trade: BacktestTrade, o: float, h: float, lo: float
    ) -> tuple[float, str] | None:
        """Prezzo e motivo di uscita nella candela, se SL o TP sono toccati.

        Args:
            trade: Trade aperto.
            o: Apertura della candela.
            h: Massimo della candela.
            lo: Minimo della candela.

        Returns:
            ``(prezzo_con_slippage, motivo)`` oppure ``None``.
        """
        side = Side(trade.side)
        sl, tp = trade.stop_loss, trade.take_profit
        if side is Side.LONG:
            if o <= sl:
                raw, reason = o, "stop_loss_gap"
            elif o >= tp:
                raw, reason = o, "take_profit_gap"
            elif lo <= sl:
                raw, reason = sl, "stop_loss"
            elif h >= tp:
                raw, reason = tp, "take_profit"
            else:
                return None
        else:
            if o >= sl:
                raw, reason = o, "stop_loss_gap"
            elif o <= tp:
                raw, reason = o, "take_profit_gap"
            elif h >= sl:
                raw, reason = sl, "stop_loss"
            elif lo <= tp:
                raw, reason = tp, "take_profit"
            else:
                return None
        return self.config.slip(raw, side.exit_order_side), reason

    def _close(
        self,
        trade: BacktestTrade,
        price: float,
        reason: str,
        when: pd.Timestamp,
        i: int,
    ) -> float:
        """Chiude il trade calcolando commissioni, PnL netto e multiplo di R.

        Returns:
            La commissione pagata sull'uscita.
        """
        direction = 1.0 if trade.side == Side.LONG else -1.0
        trade.exit_time = when
        trade.exit_price = price
        trade.exit_reason = reason
        trade.gross_pnl = (price - trade.entry_price) * trade.amount * direction
        exit_fee = price * trade.amount * self.config.fee_rate
        trade.fees += exit_fee
        trade.pnl = trade.gross_pnl - trade.fees
        trade.r_multiple = trade.pnl / trade.risk_amount if trade.risk_amount else math.nan
        trade.bars_held = i - trade.entry_index + 1
        return exit_fee


# ---------------------------------------------------------------------- #
# CLI
# ---------------------------------------------------------------------- #
def _format_summary(summary: dict[str, Any]) -> str:
    """Formatta il riepilogo per la console."""
    pct = {
        "rendimento_totale", "buy_and_hold", "max_drawdown", "win_rate",
        "win_rate_pareggio", "commissioni_su_pnl_lordo",
    }
    lines = []
    for key, value in summary.items():
        if key in pct and isinstance(value, float):
            text = f"{value:.2%}"
        elif isinstance(value, float):
            text = f"{value:,.3f}"
        else:
            text = str(value)
        lines.append(f"  {key:<28} {text}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Parser degli argomenti da riga di comando."""
    p = argparse.ArgumentParser(description="Backtest PyTrader (Donchian + VWAP, 15m)")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", type=Path, help="CSV timestamp(ms),open,high,low,close,volume")
    src.add_argument("--fetch", action="store_true", help="scarica lo storico via ccxt")
    p.add_argument("--exchange", default="binanceusdm")
    p.add_argument("--symbol", default="BTC/USDT:USDT")
    p.add_argument("--since", default="2024-01-01", help="data inizio (YYYY-MM-DD, UTC)")
    p.add_argument("--until", default=None, help="data fine (YYYY-MM-DD, UTC)")
    p.add_argument("--save", type=Path, help="salva lo storico scaricato in CSV")
    p.add_argument("--balance", type=float, default=10_000.0)
    p.add_argument("--risk", type=float, default=0.01, help="frazione rischiata per trade")
    p.add_argument("--max-notional", type=float, default=3.0, help="tetto nozionale × saldo")
    p.add_argument("--fee", type=float, default=0.0005, help="commissione per lato")
    p.add_argument("--slippage-bps", type=float, default=2.0)
    p.add_argument("--no-short", action="store_true", help="solo long (mercato spot)")
    p.add_argument("--trades-out", type=Path, help="esporta i trade in CSV")
    return p


def _date_ms(text: str) -> int:
    """Converte ``YYYY-MM-DD`` (UTC) in millisecondi epoch."""
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def main(argv: list[str] | None = None) -> int:
    """Entry point CLI.

    Args:
        argv: Argomenti (default ``sys.argv[1:]``).

    Returns:
        Codice di uscita del processo.
    """
    args = build_parser().parse_args(argv)
    strat_cfg = StrategyConfig(allow_short=not args.no_short)

    if args.fetch:
        until = _date_ms(args.until) if args.until else None
        ohlcv = asyncio.run(
            fetch_history(
                args.exchange, args.symbol, strat_cfg.timeframe, _date_ms(args.since), until
            )
        )
        if args.save:
            save_csv(ohlcv, args.save)
    else:
        ohlcv = load_csv(args.csv)

    risk_cfg = RiskConfig(risk_per_trade=args.risk, max_notional_multiple=args.max_notional)
    result = Backtester(
        Strategy(strat_cfg),
        PositionSizer(risk_cfg.risk_per_trade, risk_cfg.max_notional_multiple),
        BacktestConfig(
            initial_balance=args.balance, fee_rate=args.fee, slippage_bps=args.slippage_bps
        ),
    ).run(ohlcv)

    print(f"Backtest {args.symbol} {strat_cfg.timeframe} — {len(ohlcv)} candele")
    print(_format_summary(result.summary()))
    yearly = result.yearly()
    if not yearly.empty:
        print("\nPer anno:")
        print(yearly.to_string(float_format=lambda x: f"{x:,.3f}"))
    if args.trades_out:
        args.trades_out.parent.mkdir(parents=True, exist_ok=True)
        result.trades.to_csv(args.trades_out, index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
