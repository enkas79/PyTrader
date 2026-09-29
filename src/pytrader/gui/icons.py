"""Icona dell'applicazione disegnata a runtime (nessun file binario nel repository)."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap

_UP = QColor("#26a69a")
_DOWN = QColor("#ef5350")
_BG = QColor("#1c1f26")


def _render(size: int) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_BG)
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.2, size * 0.2)
    u = size / 16.0
    # (x centro, stoppino alto, corpo alto, corpo basso, stoppino basso, colore)
    candles = ((4, 6, 7.5, 12, 13.5, _DOWN), (8, 3.5, 5, 10, 11.5, _UP), (12, 2, 3, 7, 9, _UP))
    for x, wick_top, top, bottom, wick_bottom, color in candles:
        p.setBrush(color)
        p.drawRect(QRectF((x - 0.25) * u, wick_top * u, 0.5 * u, (wick_bottom - wick_top) * u))
        p.drawRoundedRect(
            QRectF((x - 1.25) * u, top * u, 2.5 * u, (bottom - top) * u), u * 0.3, u * 0.3
        )
    p.end()
    return pix


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_render(size))
    return icon
