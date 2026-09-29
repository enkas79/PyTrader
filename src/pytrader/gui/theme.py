"""Palette dei temi chiaro/scuro e generazione del foglio di stile da ``styles.qss``.

Il file ``styles.qss`` è un modello con token ``@nome`` sostituiti dai colori della palette:
un solo foglio di stile centralizzato per entrambi i temi. Modulo senza dipendenze Qt.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from pytrader.settings import ThemeMode
from pytrader.version import resource_path

__all__ = [
    "DARK",
    "LIGHT",
    "Palette",
    "ThemeMode",
    "contrast_ratio",
    "current_palette",
    "load_template",
    "render_stylesheet",
    "resolve_palette",
    "set_current_palette",
]

_TOKEN = re.compile(r"@([a-z_]+)")


@dataclass(frozen=True)
class Palette:
    name: str
    window: str  # sfondo generale
    surface: str  # riquadri (group box, menu, intestazioni)
    input: str  # campi di testo, barra menu, grafico
    menubar: str
    border: str
    border_soft: str
    separator: str
    text: str
    text_label: str  # etichette dei form
    text_muted: str  # suggerimenti, titoli secondari
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_text: str
    focus: str
    disabled_bg: str
    disabled_text: str
    table_alt: str
    secondary: str  # pulsanti secondari
    secondary_hover: str
    scroll_handle: str
    scroll_hover: str
    up: str  # rialzo / long / profitto
    down: str  # ribasso / short / perdita
    chart_bg: str
    chart_fg: str
    level_rgb: str  # "r, g, b" delle fasce S/R (trasparenza applicata dal grafico)
    entry: str


# Tema scuro storico. Testo #e6e9ef su #1c1f26 = 13,9:1; bianco su #2f6fd6 = 4,9:1
DARK = Palette(
    name="dark",
    window="#1c1f26",
    surface="#232730",
    input="#16181d",
    menubar="#16181d",
    border="#353b48",
    border_soft="#2f3440",
    separator="#2a2f3a",
    text="#e6e9ef",
    text_label="#c9ced6",
    text_muted="#9aa3b2",
    accent="#2f6fd6",
    accent_hover="#2a63c0",
    accent_pressed="#255bb3",
    accent_text="#ffffff",
    focus="#5b8def",
    disabled_bg="#2a2f3a",
    disabled_text="#7c8594",
    table_alt="#20242c",
    secondary="#2a2f3a",
    secondary_hover="#353b48",
    scroll_handle="#353b48",
    scroll_hover="#4a5162",
    up="#26a69a",
    down="#f2605d",
    chart_bg="#16181d",
    chart_fg="#c9ced6",
    level_rgb="95, 145, 255",
    entry="#e0e0e0",
)

# Tema chiaro: verde/rosso scuriti per restare leggibili su bianco (>= 4,5:1)
LIGHT = Palette(
    name="light",
    window="#f5f6f8",
    surface="#ffffff",
    input="#ffffff",
    menubar="#e9ecf1",
    border="#c3c8d1",
    border_soft="#dde1e7",
    separator="#dde1e7",
    text="#1c1f26",
    text_label="#333a45",
    text_muted="#555d6b",
    accent="#2f6fd6",
    accent_hover="#2a63c0",
    accent_pressed="#1f4f9e",
    accent_text="#ffffff",
    focus="#2f6fd6",
    disabled_bg="#e3e6eb",
    disabled_text="#6b7380",
    table_alt="#eef0f4",
    secondary="#e3e6eb",
    secondary_hover="#d5d9e0",
    scroll_handle="#c3c8d1",
    scroll_hover="#a9b0bc",
    up="#00796b",
    down="#c62828",
    chart_bg="#ffffff",
    chart_fg="#333a45",
    level_rgb="47, 111, 214",
    entry="#37474f",
)

_current = DARK


def current_palette() -> Palette:
    """Palette attiva (usata da grafico e tabelle per i colori dinamici)."""
    return _current


def set_current_palette(palette: Palette) -> None:
    global _current
    _current = palette


def resolve_palette(mode: ThemeMode, system_dark: bool) -> Palette:
    if mode is ThemeMode.SYSTEM:
        return DARK if system_dark else LIGHT
    return DARK if mode is ThemeMode.DARK else LIGHT


def load_template() -> str:
    return resource_path("styles.qss").read_text(encoding="utf-8")


def render_stylesheet(template: str, palette: Palette) -> str:
    """Sostituisce i token ``@nome``; un token sconosciuto solleva ``KeyError``."""
    values = asdict(palette)
    return _TOKEN.sub(lambda m: values[m.group(1)], template)


def _luminance(color: str) -> float:
    rgb = [int(color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast_ratio(fg: str, bg: str) -> float:
    """Rapporto di contrasto WCAG 2.x tra due colori ``#rrggbb``."""
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)
