"""Formattazione numerica italiana condivisa dai widget."""

from __future__ import annotations

import math
from typing import Optional


def it_num(text: str) -> str:
    """Separatori italiani: 12,345.67 -> 12.345,67."""
    return text.translate(str.maketrans({",": ".", ".": ","}))


def fmt_price(value: Optional[float]) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return it_num(f"{value:,.{6 if abs(value) < 1 else 4 if abs(value) < 100 else 2}f}")


def fmt_money(value: Optional[float], signed: bool = False) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return it_num(f"{value:+,.2f}" if signed else f"{value:,.2f}")


def fmt_qty(value: float) -> str:
    if value == 0:
        return "—"
    decimals = 2 if value >= 100 else 4 if value >= 1 else 6
    return it_num(f"{value:,.{decimals}f}")
