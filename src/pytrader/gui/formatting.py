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


def fmt_num(value: float) -> str:
    """Numero compatto con virgola decimale (0.5 -> '0,5', 2.0 -> '2')."""
    return f"{value:g}".replace(".", ",")


def fmt_values(values: tuple[float, ...]) -> str:
    return "; ".join(fmt_num(v) for v in values)


def parse_values(text: str) -> tuple[float, ...]:
    """Elenco di numeri separati da ';' con virgola o punto decimale: '0,3; 0,5' -> (0.3, 0.5).
    Duplicati rimossi, ordine crescente; ``ValueError`` se vuoto o non numerico."""
    parts = [p.strip().replace(",", ".") for p in text.split(";")]
    parts = [p for p in parts if p]
    if not parts:
        raise ValueError("Inserisci almeno un valore")
    try:
        values = sorted({float(p) for p in parts})
    except ValueError:
        raise ValueError(f"Valori non validi: «{text}»") from None
    if not all(math.isfinite(v) for v in values):
        raise ValueError(f"Valori non validi: «{text}»")
    return tuple(values)
