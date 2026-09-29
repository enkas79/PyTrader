"""Grafico a candele pyqtgraph con volumi, fasce S/R e livelli dei setup."""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QColor, QPainter, QPicture

from pytrader.gui.theme import Palette, current_palette
from pytrader.models import Direction, Level, TradeSetup

GRID_ALPHA = 0.15
SETUP_SPAN = 15  # candele su cui disegnare i livelli di un setup


class ChartColors:
    """Colori del grafico derivati dalla palette del tema."""

    def __init__(self, palette: Palette) -> None:
        r, g, b = (int(v) for v in palette.level_rgb.split(","))
        self.bg = palette.chart_bg
        self.fg = palette.chart_fg
        self.up = QColor(palette.up)
        self.down = QColor(palette.down)
        self.level_fill = QColor(r, g, b, 40)
        self.level_edge = QColor(r, g, b, 110)
        self.entry = QColor(palette.entry)


def _num(value: float) -> str:
    """Numero compatto con virgola decimale."""
    return f"{value:.6g}".replace(".", ",")


class CandlestickItem(pg.GraphicsObject):
    """Candele pre-renderizzate in un ``QPicture`` (x = posizione della candela)."""

    def __init__(self, df: pd.DataFrame, colors: ChartColors) -> None:
        super().__init__()
        self._picture = QPicture()
        self._bounds = QRectF()
        self._render(df, colors)

    def _render(self, df: pd.DataFrame, colors: ChartColors) -> None:
        painter = QPainter(self._picture)
        o, h, lo, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
        width = 0.35
        for i in range(len(df)):
            color = colors.up if c[i] >= o[i] else colors.down
            painter.setPen(pg.mkPen(color, width=1))
            painter.drawLine(QPointF(i, lo[i]), QPointF(i, h[i]))
            painter.setBrush(pg.mkBrush(color))
            top, bottom = max(o[i], c[i]), min(o[i], c[i])
            painter.drawRect(QRectF(i - width, bottom, 2 * width, max(top - bottom, 1e-12)))
        painter.end()
        if len(df):
            self._bounds = QRectF(-1, float(lo.min()), len(df) + 1, float(h.max() - lo.min()))

    def paint(self, painter: QPainter, *args: Any) -> None:
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self) -> QRectF:  # noqa: N802 (API Qt)
        return self._bounds


class TimeAxis(pg.AxisItem):
    """Asse X basato sulla posizione, etichettato con i timestamp (salta i buchi di sessione)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(orientation="bottom", **kwargs)
        self.index: Optional[pd.DatetimeIndex] = None

    def tickStrings(self, values: list[float], scale: float, spacing: float) -> list[str]:  # noqa: N802
        if self.index is None or len(self.index) == 0:
            return ["" for _ in values]
        intraday = len(self.index) > 1 and (self.index[1] - self.index[0]) < pd.Timedelta(days=1)
        fmt = "%d/%m %H:%M" if intraday else "%d/%m/%Y"
        out = []
        for v in values:
            i = int(round(v))
            out.append(self.index[i].strftime(fmt) if 0 <= i < len(self.index) else "")
        return out


class ChartWidget(pg.GraphicsLayoutWidget):
    """Pannello prezzo + pannello volume con asse X condiviso."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent=parent)
        pg.setConfigOptions(antialias=True)
        self._colors = ChartColors(current_palette())
        self._time_axis = TimeAxis()
        self.price_plot: pg.PlotItem = self.addPlot(
            row=0, col=0, axisItems={"bottom": self._time_axis}
        )
        self.volume_plot: pg.PlotItem = self.addPlot(row=1, col=0)
        self.ci.layout.setRowStretchFactor(0, 4)
        self.ci.layout.setRowStretchFactor(1, 1)
        self.volume_plot.setXLink(self.price_plot)
        self.volume_plot.getAxis("bottom").setStyle(showValues=False)
        for plot in (self.price_plot, self.volume_plot):
            plot.showGrid(x=True, y=True, alpha=GRID_ALPHA)
        self._df: Optional[pd.DataFrame] = None
        self._levels: list[Level] = []
        self._setups: list[TradeSetup] = []
        self._overlay: list[Any] = []
        self._setup_items: list[Any] = []
        self._style_axes()

    def _style_axes(self) -> None:
        fg = self._colors.fg
        self.setBackground(self._colors.bg)
        for plot in (self.price_plot, self.volume_plot):
            for axis in ("left", "bottom"):
                plot.getAxis(axis).setTextPen(fg)
                plot.getAxis(axis).setPen(pg.mkPen(fg, width=1))
        self.price_plot.setLabel("left", "Prezzo", color=fg)
        self.volume_plot.setLabel("left", "Volume", color=fg)

    def apply_palette(self, palette: Palette) -> None:
        """Cambio tema: ridisegna candele, livelli e setup mantenendo lo zoom corrente."""
        self._colors = ChartColors(palette)
        self._style_axes()
        if self._df is None or self._df.empty:
            return
        (x0, x1), (y0, y1) = self.price_plot.viewRange()
        levels, setups = list(self._levels), list(self._setups)
        self.set_data(self._df)
        self.set_levels(levels)
        self.set_setups(setups)
        self.price_plot.setXRange(x0, x1, padding=0)
        self.price_plot.setYRange(y0, y1, padding=0)

    def set_data(self, df: pd.DataFrame) -> None:
        self.price_plot.clear()
        self.volume_plot.clear()
        self._overlay.clear()
        self._setup_items.clear()
        self._levels, self._setups = [], []
        self._df = df
        self._time_axis.index = pd.DatetimeIndex(df.index)
        if df.empty:
            return
        self.price_plot.addItem(CandlestickItem(df, self._colors))
        x = np.arange(len(df))
        up, down = self._colors.up, self._colors.down
        colors = [up if c >= o else down for o, c in zip(df["open"], df["close"])]
        bars = pg.BarGraphItem(x=x, height=df["volume"].to_numpy(), width=0.7, brushes=colors)
        bars.setOpacity(0.6)
        self.volume_plot.addItem(bars)
        self.show_last(200)

    def show_last(self, n: int) -> None:
        if self._df is None or self._df.empty:
            return
        end = len(self._df)
        self._zoom(max(0, end - n), end + 2)

    def _zoom(self, x0: int, x1: int) -> None:
        assert self._df is not None
        part = self._df.iloc[max(0, x0) : max(x0 + 1, min(len(self._df), x1))]
        lo, hi = float(part["low"].min()), float(part["high"].max())
        pad = (hi - lo) * 0.08 or hi * 0.01
        self.price_plot.setXRange(x0, x1, padding=0)
        self.price_plot.setYRange(lo - pad, hi + pad, padding=0)
        self.volume_plot.setYRange(0, float(part["volume"].max()) * 1.1 or 1.0, padding=0)

    def set_levels(self, levels: list[Level]) -> None:
        for item in self._overlay:
            self.price_plot.removeItem(item)
        self._overlay.clear()
        self._levels = list(levels)
        for lv in levels:
            lower, upper = lv.lower, lv.upper
            if upper - lower < 1e-12:  # livello puntiforme: fascia minima visibile
                pad = lv.price * 0.0005
                lower, upper = lower - pad, upper + pad
            region = pg.LinearRegionItem(
                values=(lower, upper),
                orientation="horizontal",
                movable=False,
                brush=pg.mkBrush(self._colors.level_fill),
                pen=pg.mkPen(self._colors.level_edge),
            )
            region.setZValue(-10)
            region.setToolTip(f"Livello {_num(lv.price)} — tocchi: {lv.touches}")
            self.price_plot.addItem(region)
            self._overlay.append(region)

    def set_setups(self, setups: list[TradeSetup]) -> None:
        for item in self._setup_items:
            self.price_plot.removeItem(item)
        self._setup_items.clear()
        self._setups = list(setups)
        c = self._colors
        longs = [s for s in setups if s.direction is Direction.LONG]
        shorts = [s for s in setups if s.direction is Direction.SHORT]
        for group, symbol, color in ((longs, "t1", c.up), (shorts, "t", c.down)):
            if not group:
                continue
            xs = [s.signal_index for s in group]
            ys = [s.level.lower if s in longs else s.level.upper for s in group]
            scatter = pg.ScatterPlotItem(
                x=xs, y=ys, symbol=symbol, size=12, brush=pg.mkBrush(color), pen=pg.mkPen(c.bg)
            )
            self.price_plot.addItem(scatter)
            self._setup_items.append(scatter)
        for s in setups:
            self._draw_setup_lines(s, highlight=False)

    def _draw_setup_lines(self, s: TradeSetup, highlight: bool) -> None:
        x0 = s.signal_index + 1
        x1 = x0 + SETUP_SPAN
        width = 2 if highlight else 1
        c = self._colors
        for price, color, label in (
            (s.entry, c.entry, "Entry"),
            (s.stop_loss, c.down, "SL"),
            (s.take_profit, c.up, "TP"),
        ):
            line = pg.PlotDataItem(
                [x0, x1],
                [price, price],
                pen=pg.mkPen(color, width=width, style=pg.QtCore.Qt.PenStyle.DashLine),
            )
            self.price_plot.addItem(line)
            self._setup_items.append(line)
            if highlight:
                text = pg.TextItem(f"{label} {_num(price)}", color=color, anchor=(0, 0.5))
                text.setPos(x1, price)
                self.price_plot.addItem(text)
                self._setup_items.append(text)

    def focus_setup(self, setup: TradeSetup, all_setups: list[TradeSetup]) -> None:
        """Centra il grafico sul setup ed evidenzia i suoi livelli con etichette."""
        self.set_setups(all_setups)
        self._draw_setup_lines(setup, highlight=True)
        if self._df is None:
            return
        x0 = max(0, setup.signal_index - 80)
        x1 = setup.signal_index + SETUP_SPAN + 30
        self._zoom(x0, x1)
        lo, hi = self.price_plot.viewRange()[1]
        lo = min(lo, setup.stop_loss, setup.take_profit)
        hi = max(hi, setup.stop_loss, setup.take_profit)
        self.price_plot.setYRange(lo, hi, padding=0.05)
